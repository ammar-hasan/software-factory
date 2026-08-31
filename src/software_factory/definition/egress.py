"""Every outbound destination a definition could reach (PRD FR-20.6).

FR-20.6 has two halves and the second is the hard one: "a local factory makes no network
call the operator did not configure" is a promise, and "`sf audit --egress` enumerates every
possible outbound destination from the definition" is what makes it checkable.

Enumerable *from the definition* is the constraint. Anything discoverable by reading the
files is enumerated; anything that is not is reported as **not statically determinable**,
with what would have to be inspected to find out. Both halves matter equally -- an egress
report that silently omits what it cannot see is worse than none, because it reads as a
complete list.

Four sources, in decreasing order of how confidently they can be enumerated:

1. **Runner allowlists.** Declared hosts. Fully enumerable.
2. **Model endpoints.** A tier names a provider; a non-local provider reaches it. Enumerable
   as a provider, not always as a host, and it says which.
3. **MCP servers.** A declared server has an address or a command. An address is a
   destination; a command is a program whose egress is that program's business.
4. **Setup commands.** Arbitrary shell. Not enumerable at all, and reported as such: a
   `pip install` reaches an index whose host is in a config file this cannot read.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from software_factory.definition.loader import Definition
from software_factory.definition.models import NetworkPolicy


class Certainty(enum.StrEnum):
    """How confident the report is that this destination is reachable."""

    DECLARED = "declared"
    """Named in the definition. This is reachable and the operator wrote it down."""

    IMPLIED = "implied"
    """Follows from a declaration without being named: a hosted model tier reaches its
    provider, whatever host that turns out to be."""

    INDETERMINATE = "indeterminate"
    """Something in the definition can reach the network and what it reaches cannot be
    read from the files. The honest answer, and the one an egress report must not omit."""


@dataclass(frozen=True, slots=True)
class Destination:
    """One place a run could send bytes, and why the report believes so."""

    target: str
    certainty: Certainty
    source: str
    """What in the definition put it here -- a runner name, an agent, an MCP server. An
    operator reading an unexpected destination needs to know where to go and change it."""

    detail: str = ""

    def render(self) -> str:
        mark = {
            Certainty.DECLARED: "",
            Certainty.IMPLIED: " (implied)",
            Certainty.INDETERMINATE: " (not determinable)",
        }[self.certainty]
        detail = f" — {self.detail}" if self.detail else ""
        return f"{self.target}{mark}  [{self.source}]{detail}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "certainty": self.certainty.value,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class EgressReport:
    """Everything a definition could reach, and everything it could not be shown not to."""

    destinations: tuple[Destination, ...] = ()

    @property
    def offline_capable(self) -> bool:
        """True when nothing in the definition can reach the network.

        Requires the *absence* of indeterminate sources, not merely the absence of declared
        ones: a setup command that might install a package is exactly the thing that makes
        "this factory is offline" false while leaving the destination list empty.
        """
        return not self.destinations

    def by_certainty(self, certainty: Certainty) -> tuple[Destination, ...]:
        return tuple(d for d in self.destinations if d.certainty is certainty)

    def as_dict(self) -> dict[str, Any]:
        return {
            "offlineCapable": self.offline_capable,
            "destinations": [d.as_dict() for d in self.destinations],
            "note": (
                "Destinations marked `indeterminate` are places the definition can reach "
                "whose address cannot be read from the files. An egress report that omitted "
                "them would read as a complete list."
            ),
        }


def enumerate_egress(definition: Definition) -> EgressReport:
    """Every outbound destination reachable from this definition."""
    found: list[Destination] = []
    found += _from_runners(definition)
    found += _from_model_tiers(definition)
    found += _from_mcp_servers(definition)
    found += _from_integrations(definition)
    return EgressReport(destinations=tuple(sorted(found, key=lambda d: (d.source, d.target))))


def _from_runners(definition: Definition) -> list[Destination]:
    found: list[Destination] = []
    for runner in definition.runners.values():
        # Checked *before* the network policy, and outside it. Setup commands run when the
        # runner is provisioned, which is before the run's network policy applies to
        # anything -- so `network: none` does not constrain them, and letting it suppress
        # them here would report a factory as offline-capable while its runner build pulls
        # from a package index.
        if runner.definition.setup_commands:
            found.append(
                Destination(
                    target="unknown",
                    certainty=Certainty.INDETERMINATE,
                    source=f"runner {runner.name!r}",
                    detail=(
                        f"{len(runner.definition.setup_commands)} setup command(s) run "
                        "arbitrary shell at provisioning time, before the run's network "
                        "policy applies; a package install reaches an index whose host is "
                        "in a config file this cannot read. Inspect them directly."
                    ),
                )
            )

        policy = runner.definition.network
        if policy is NetworkPolicy.NONE:
            continue
        if policy is NetworkPolicy.OPEN:
            found.append(
                Destination(
                    target="*",
                    certainty=Certainty.DECLARED,
                    source=f"runner {runner.name!r}",
                    detail="`network: open` reaches anything the machine can reach",
                )
            )
        found += [
            Destination(
                target=host,
                certainty=Certainty.DECLARED,
                source=f"runner {runner.name!r}",
            )
            for host in runner.definition.network_allowlist
        ]
    return found


def _from_model_tiers(definition: Definition) -> list[Destination]:
    """Where each tier's inference actually goes.

    The provider *registry* knows each provider's endpoint, so a tier naming `anthropic`
    resolves to a host rather than to a shrug. That upgrade matters: an operator asked to
    approve egress cannot approve "anthropic (model endpoint)", and a report full of
    unresolvable entries is one that gets waved through.

    Three cases, and the distinctions are the point:

    * A **local** provider on its default loopback endpoint is not egress. Listing it as a
      destination trains operators to ignore the report.
    * A **known** provider is `DECLARED` with its address.
    * An **unknown** provider name is `INDETERMINATE`, not implied. We cannot say where it
      goes, and saying "somewhere, probably" would be inventing the thing this module
      exists to avoid.

    A tier that declares `local: true` but names a provider the registry knows to be hosted
    is reported as a destination anyway, on the provider's evidence rather than the tier's
    claim -- the flag is an assertion by the author and the registry is a fact about the
    endpoint.
    """
    from software_factory.providers.registry import UnknownProviderError, endpoint_for, spec_for

    ladder = definition.factory.ladder
    if ladder is None:
        return []

    found: list[Destination] = []
    for tier in ladder.tiers:
        try:
            spec = spec_for(tier.provider)
        except UnknownProviderError:
            found.append(
                Destination(
                    target=f"{tier.provider} (unrecognised model provider)",
                    certainty=Certainty.INDETERMINATE,
                    source=f"tier {tier.name!r}",
                    detail=(
                        "no adapter is registered for this provider, so its endpoint "
                        "cannot be determined from the definition"
                    ),
                )
            )
            continue

        endpoint = endpoint_for(tier.provider)
        if endpoint is None:
            # The scripted provider. It never opens a socket, so it is not a destination.
            continue
        if endpoint.local:
            continue

        detail = "a hosted tier reaches its provider's endpoint"
        if tier.local:
            detail += (
                f"; the tier declares `local: true`, but {spec.name!r} is a hosted "
                "provider and the endpoint is what decides"
            )
        found.append(
            Destination(
                target=endpoint.url,
                certainty=Certainty.DECLARED,
                source=f"tier {tier.name!r}",
                detail=detail,
            )
        )
    return found


def _from_mcp_servers(definition: Definition) -> list[Destination]:
    found: list[Destination] = []
    servers: dict[str, Any] = dict(definition.factory.mcp_servers)
    for agent in definition.agents.values():
        servers.update(agent.definition.execution.mcp_servers or {})

    for name, server in sorted(servers.items()):
        command = getattr(server, "command", None)
        server_id = getattr(server, "id", None)
        if command:
            found.append(
                Destination(
                    target="unknown",
                    certainty=Certainty.INDETERMINATE,
                    source=f"mcp {name!r}",
                    detail=(
                        f"runs `{command}` locally; whatever that program reaches is its "
                        "own business and not readable from here"
                    ),
                )
            )
        elif server_id:
            # Referenced by id, so its address lives in the operator's tool-server registry
            # rather than in this definition. Reporting it as indeterminate rather than
            # dropping it is the point: a server whose destination is elsewhere is still a
            # destination, and an omission here would read as "this reaches nothing".
            found.append(
                Destination(
                    target=f"tool server {server_id!r}",
                    certainty=Certainty.INDETERMINATE,
                    source=f"mcp {name!r}",
                    detail=(
                        "referenced by id; its address is in the tool-server registry, not "
                        "in this definition. Check the registry to resolve it."
                    ),
                )
            )
    return found


def _from_integrations(definition: Definition) -> list[Destination]:
    """A declared integration reaches its provider by definition."""
    return [
        Destination(
            target=f"{integration.provider} (integration)",
            certainty=Certainty.IMPLIED,
            source="factory integrations",
            detail="an integration polls or receives from its provider",
        )
        for integration in definition.factory.integrations
    ]
