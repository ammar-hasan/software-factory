"""Which agent runtime drives a run (PRD FR-11.1, docs/harness/HARNESS.md §8.1).

A provider answers *which model*, and swapping one changes the weights behind a completion.
A harness answers something larger: how context is assembled, which tools exist, what runs
without approval, how the sandbox is enforced, and what a finished run emits. Two harnesses
on one model are not the same engine, which is why both independence checks in
`definition.validate` compare harness *and* model rather than model alone.

`loom` -- the turn loop in `harness.loop` -- is one implementation of this contract, not the
shape of it. `HarnessSpec` has been in the schema since the first version and nothing ever
dispatched on it: a definition could pin `type: claude-code`, the loader would validate it,
`sf plan` would print it, and the run would go to the built-in loop anyway. That is this
project's recurring failure mode under its own name -- a control that existed and was not
wired in -- and this module is the wire.

Two rules shape it, both borrowed from `providers.registry` because the problems are the
same one at a different altitude.

**Resolution is by name, and an unknown name is a configuration error.** `known_harnesses()`
lets `sf validate` reject a typo with a line number, rather than letting a run reach the
dispatch and die there.

**Resolution is total.** An adapter with no runtime behind it reports that through
`available()` and ends its run as `SETUP_FAILED` with a stated reason. It does not raise
past the coordinator, and it never quietly falls back to the built-in harness: a run that
was pinned to Codex and silently served by `loom` is a measurement of the wrong thing, and
every downstream comparison of harnesses would inherit the lie.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar

from software_factory.harness.awareness import AwarenessPack
from software_factory.harness.loop import Budget, RunResult, RunStatus, TurnLoop
from software_factory.harness.routing import RoutingState
from software_factory.harness.tools import BlastRadius, Grants, ToolRegistry
from software_factory.providers.base import Provider

NATIVE_HARNESS = "loom"
"""The built-in harness's name, as the PRD glossary spells it.

`oz` resolves to it as well. The independence checks used that spelling as a stand-in for
"this agent declared no harness", and evals default to it; both are talking about this
adapter, and two names for one engine would make a judge on the built-in harness look
independent of a subject on the built-in harness.
"""


@dataclass(slots=True)
class HarnessRequest:
    """Everything an adapter needs to drive one agent through one stage.

    Deliberately the same inputs the built-in loop already takes, plus the two the ladder
    now chooses between rungs (`provider` and `runner`), so that adapting a second harness
    is a matter of translating this request into that harness's own vocabulary rather than
    of re-plumbing the coordinator.
    """

    registry: ToolRegistry
    grants: Grants
    pack: AwarenessPack
    contract: BlastRadius
    budget: Budget
    routing: RoutingState
    role_prompt: str
    task: str
    output_schema: dict[str, Any] | None = None
    provider: Provider | None = None
    """The model endpoint, when the harness uses ours.

    Optional because an external harness authenticates and calls its model itself: Claude
    Code holds its own credentials and speaks its own wire, and handing it a `Provider`
    would describe a call it is never going to make.
    """

    runner: str | None = None
    """The runner this rung selected, or None for the agent's own.

    Carried rather than acted on by the built-in harness, which runs in this process. It is
    here because an external harness has to be *told* where to execute, and because a rung
    that escalates the model without escalating the machine is only half a rung.
    """

    repair_budget: int = 3
    per_mtok_in: float = 0.0
    per_mtok_out: float = 0.0
    should_stop: Any = None
    on_tool: Any = None


class HarnessAdapter(ABC):
    """One agent runtime, behind one method.

    Narrow on purpose. Everything a harness varies -- prompt assembly, tool dispatch,
    approval rules, sandboxing -- is *inside* `run`, because those are the things the
    adapter exists to differ about. A contract that also fixed how context is assembled
    would only fit harnesses that assemble it the way this one does.
    """

    name: ClassVar[str]

    @abstractmethod
    def run(self, request: HarnessRequest) -> RunResult:
        """Drive one stage to a typed outcome. Never raises for a stated failure."""

    def available(self) -> tuple[bool, str]:
        """Whether this harness can run here, and why not when it cannot.

        Answered before a run starts, for the same reason a missing API key is: discovering
        it mid-run wastes the setup and reports the cause as whatever failed last.
        """
        return True, ""


class NativeHarness(HarnessAdapter):
    """The built-in turn loop (`harness.loop`), behind the adapter contract."""

    name: ClassVar[str] = NATIVE_HARNESS

    def run(self, request: HarnessRequest) -> RunResult:
        if request.provider is None:
            return RunResult(
                status=RunStatus.SETUP_FAILED,
                reason="the built-in harness needs a provider and was given none",
            )
        return TurnLoop(
            provider=request.provider,
            registry=request.registry,
            grants=request.grants,
            pack=request.pack,
            contract=request.contract,
            budget=request.budget,
            routing=request.routing,
            role_prompt=request.role_prompt,
            task=request.task,
            output_schema=request.output_schema,
            repair_budget=request.repair_budget,
            per_mtok_in=request.per_mtok_in,
            per_mtok_out=request.per_mtok_out,
            should_stop=request.should_stop,
            on_tool=request.on_tool,
        ).run()


class ExternalHarness(HarnessAdapter):
    """A third-party agent CLI, named and selectable but not yet driven.

    The contract, the registry entry and the definition path are real: a factory can pin
    `type: codex` on one stage today, `sf validate` accepts it, and the ladder can put a
    rung on it. What is missing is the process that runs it, and this says so rather than
    serving the run from the built-in harness -- a stage that reports Codex and ran `loom`
    corrupts every later comparison between them.
    """

    command: ClassVar[str]

    def available(self) -> tuple[bool, str]:
        return False, f"the {self.name} harness has no runtime in this build"

    def run(self, request: HarnessRequest) -> RunResult:  # noqa: ARG002 - interface conformance
        return RunResult(
            status=RunStatus.SETUP_FAILED,
            reason=(
                f"the {self.name} harness is declared but not wired: nothing in this build "
                f"runs `{self.command}`. Pin a tier or model to use the built-in harness."
            ),
        )


class ClaudeCodeHarness(ExternalHarness):
    name: ClassVar[str] = "claude-code"
    command: ClassVar[str] = "claude"


class CodexHarness(ExternalHarness):
    name: ClassVar[str] = "codex"
    command: ClassVar[str] = "codex"


_ADAPTERS: dict[str, HarnessAdapter] = {
    adapter.name: adapter for adapter in (NativeHarness(), ClaudeCodeHarness(), CodexHarness())
}

_ALIASES: dict[str, str] = {"oz": NATIVE_HARNESS}


class UnknownHarnessError(ValueError):
    """A definition named a harness with no adapter.

    Its own type, and it lists what is known: a bare "unknown harness" leaves the reader
    guessing at the spelling, which is the same reason `UnknownProviderError` exists.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            f"unknown harness {name!r}; known harnesses: {', '.join(known_harnesses())}"
        )
        self.harness = name


def known_harnesses() -> tuple[str, ...]:
    return tuple(sorted(_ADAPTERS))


def canonical_harness(name: str | None) -> str:
    """The adapter name a declaration means, with aliases and the default resolved.

    `None` is the built-in harness: an agent that declares a `model` or a `tier` and no
    `harness` is running on `loom`, and the independence checks have to see that as the
    same engine as one that names it.
    """
    if not name:
        return NATIVE_HARNESS
    return _ALIASES.get(name, name)


def resolve_harness(name: str | None) -> HarnessAdapter:
    """The adapter a definition's harness name selects."""
    canonical = canonical_harness(name)
    try:
        return _ADAPTERS[canonical]
    except KeyError:
        raise UnknownHarnessError(canonical) from None
