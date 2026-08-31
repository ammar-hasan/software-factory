"""Resolving a tier's `provider:` name to something that can serve a completion.

A `Tier` in a definition names a provider and a model. Until this module existed the name
resolved to nothing: the definition validated, `sf plan` printed it, and the factory could
not actually call it. That is the failure mode this project keeps finding under its own
name -- a control that existed and was not wired in -- so the registry is the wire.

Three rules shape it.

**Credentials come from the environment, never from a definition file.** A definition is
committed, diffed and reviewed; a key in one is a key in the history. Each entry names the
variable it reads, so `sf doctor` can say *which* variable is unset rather than reporting a
generic authentication failure.

**A missing key is a configuration error, not a runtime one.** `available()` answers it
before a run starts. Discovering it mid-run wastes the setup and reports the cause as
whatever the endpoint said.

**Local providers are ordinary.** `local` resolves to an Ollama-shaped endpoint on the
loopback with no key at all, because PR-2 makes the local path the reference path and a
reference path that needs an account is not one.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from software_factory.providers.anthropic import DEFAULT_BASE_URL as ANTHROPIC_BASE_URL
from software_factory.providers.anthropic import AnthropicProvider
from software_factory.providers.base import Provider, ProviderError
from software_factory.providers.openai_compatible import OpenAICompatibleProvider
from software_factory.providers.stub import StubProvider
from software_factory.providers.transport import Transport

OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
"""Loopback by name, not `localhost`.

`localhost` resolves through the name service, which is exactly what the offline test
guard blocks -- so a default spelled `localhost` turns "no model running" into "name
resolution failed" and sends the reader looking at DNS.
"""


class Wire:
    """Which request shape a provider speaks."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    SCRIPTED = "scripted"


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """What is known about a provider without contacting it.

    Enough to answer three questions offline: where does it send data (egress), what does
    it need to work (doctor), and is it usable with no account (PR-2)?
    """

    name: str
    wire: str
    base_url: str = ""
    api_key_env: str = ""
    requires_key: bool = True
    local: bool = False
    """True when the default endpoint is on this machine.

    Used by the egress report: a local provider is not a destination, and marking one as
    though it were teaches operators to ignore the report.
    """
    aliases: tuple[str, ...] = ()
    notes: str = ""


SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        name="local",
        wire=Wire.OPENAI,
        base_url=OLLAMA_BASE_URL,
        requires_key=False,
        local=True,
        aliases=("ollama",),
        notes="Ollama's OpenAI-compatible endpoint. The default for a factory with no account.",
    ),
    ProviderSpec(
        name="llamacpp",
        wire=Wire.OPENAI,
        base_url="http://127.0.0.1:8080/v1",
        requires_key=False,
        local=True,
        notes="llama.cpp's bundled server.",
    ),
    ProviderSpec(
        name="vllm",
        wire=Wire.OPENAI,
        base_url="http://127.0.0.1:8000/v1",
        requires_key=False,
        local=True,
        notes="vLLM's OpenAI-compatible server.",
    ),
    ProviderSpec(
        name="lmstudio",
        wire=Wire.OPENAI,
        base_url="http://127.0.0.1:1234/v1",
        requires_key=False,
        local=True,
        notes="LM Studio's local server.",
    ),
    ProviderSpec(
        name="openai",
        wire=Wire.OPENAI,
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    ),
    ProviderSpec(
        name="anthropic",
        wire=Wire.ANTHROPIC,
        base_url=ANTHROPIC_BASE_URL,
        api_key_env="ANTHROPIC_API_KEY",
    ),
    ProviderSpec(
        name="openrouter",
        wire=Wire.OPENAI,
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    ),
    ProviderSpec(
        name="together",
        wire=Wire.OPENAI,
        base_url="https://api.together.xyz/v1",
        api_key_env="TOGETHER_API_KEY",
    ),
    ProviderSpec(
        name="groq",
        wire=Wire.OPENAI,
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
    ),
    ProviderSpec(
        name="openai-compatible",
        wire=Wire.OPENAI,
        api_key_env="SF_PROVIDER_API_KEY",
        requires_key=False,
        notes="Any other server speaking the same wire. Requires an explicit base URL.",
    ),
    ProviderSpec(
        name="stub",
        wire=Wire.SCRIPTED,
        requires_key=False,
        local=True,
        notes="Scripted completions. For tests and replay; never reaches a network.",
    ),
)

_BY_NAME: dict[str, ProviderSpec] = {}
for _spec in SPECS:
    _BY_NAME[_spec.name] = _spec
    for _alias in _spec.aliases:
        _BY_NAME[_alias] = _spec


class UnknownProviderError(ProviderError):
    """A tier named a provider with no adapter.

    Its own type so `sf validate` can distinguish a misconfiguration from an outage, and
    so the message can list what *is* known -- a bare "unknown provider" leaves the reader
    guessing at the spelling.
    """

    def __init__(self, name: str) -> None:
        known = ", ".join(sorted(_BY_NAME))
        super().__init__(f"unknown provider {name!r}; known providers: {known}", retryable=False)
        self.provider = name


def spec_for(name: str) -> ProviderSpec:
    """Look up a provider's known facts, raising if the name is not one."""
    try:
        return _BY_NAME[name]
    except KeyError:
        raise UnknownProviderError(name) from None


def known_providers() -> tuple[str, ...]:
    return tuple(sorted(spec.name for spec in SPECS))


@dataclass(frozen=True, slots=True)
class Resolution:
    """A provider and everything a reader needs to check the resolution was right.

    The `Provider` alone would be enough to make a call and not enough to explain one.
    `sf doctor` and `sf audit` both want the surrounding facts, and recomputing them
    from the name in two places is how the two commands come to disagree.
    """

    provider: Provider
    spec: ProviderSpec
    base_url: str
    key_present: bool
    reason: str = ""
    """Why this resolution cannot serve requests, empty when it can."""

    @property
    def usable(self) -> bool:
        return not self.reason


def resolve(
    name: str,
    *,
    base_url: str | None = None,
    env: Mapping[str, str] | None = None,
    transport: Transport | None = None,
    timeout_s: float | None = None,
    script: Any = None,
) -> Resolution:
    """Build the provider a tier names.

    `env` is injected rather than read from `os.environ` directly so a test can prove the
    credential path without mutating the process, and so `sf plan` can report what a
    *different* environment would resolve to.
    """
    spec = spec_for(name)
    environ: Mapping[str, str] = os.environ if env is None else env

    if spec.wire == Wire.SCRIPTED:
        return Resolution(
            provider=StubProvider(script or []),
            spec=spec,
            base_url="",
            key_present=True,
        )

    url = base_url or spec.base_url
    if not url:
        return Resolution(
            provider=_unusable(spec),
            spec=spec,
            base_url="",
            key_present=False,
            reason=(
                f"provider {spec.name!r} has no default endpoint; declare `baseUrl` on the tier"
            ),
        )

    key = environ.get(spec.api_key_env, "") if spec.api_key_env else ""
    if spec.requires_key and not key:
        return Resolution(
            provider=_unusable(spec),
            spec=spec,
            base_url=url,
            key_present=False,
            reason=f"{spec.api_key_env} is not set",
        )

    kwargs: dict[str, Any] = {"api_key": key or None, "name": spec.name, "transport": transport}
    if timeout_s is not None:
        kwargs["timeout_s"] = timeout_s

    provider: Provider
    if spec.wire == Wire.ANTHROPIC:
        provider = AnthropicProvider(base_url=url, **kwargs)
    else:
        provider = OpenAICompatibleProvider(base_url=url, **kwargs)

    return Resolution(provider=provider, spec=spec, base_url=url, key_present=bool(key))


def _unusable(spec: ProviderSpec) -> Provider:
    """A provider that refuses every call, naming the configuration problem.

    Returning one rather than raising keeps `resolve` total: `sf plan` and `sf doctor`
    report on every tier including the broken ones, and a resolver that raises makes the
    first misconfigured tier hide the rest.
    """
    from software_factory.providers.stub import UnavailableProvider

    return UnavailableProvider(f"provider {spec.name!r} is not configured", retryable=False)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Where a provider sends data, for the egress report."""

    provider: str
    url: str
    local: bool
    api_key_env: str = ""
    extras: dict[str, str] = field(default_factory=dict)


def endpoint_for(name: str, *, base_url: str | None = None) -> Endpoint | None:
    """The destination a provider reaches, or None when the name is unknown.

    None rather than a raise: an egress report over a definition with a typo should still
    print the destinations it *can* determine, and mark the rest indeterminate. Refusing to
    report anything because one entry is unrecognised is how a security report gets skipped.
    """
    try:
        spec = spec_for(name)
    except UnknownProviderError:
        return None
    url = base_url or spec.base_url
    if spec.wire == Wire.SCRIPTED:
        return None
    if not url:
        return None
    return Endpoint(
        provider=spec.name,
        url=url,
        local=spec.local and not base_url,
        api_key_env=spec.api_key_env,
    )
