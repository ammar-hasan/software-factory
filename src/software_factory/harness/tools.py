"""The typed tool registry (PRD FR-10, docs/harness/HARNESS.md §4).

Two rules do most of the work here:

* **Grants come from configuration, never from text.** An ungranted call is refused and
  recorded as a violation -- not returned as an error message the model can route
  around. This is the structural half of the injection defence: no wording in any
  prompt, file, comment, or tool description can widen what an agent may reach.
* **Results are structured.** Where a native tool emits human-oriented text, the adapter
  parses it into the declared schema, and a parse failure is a typed tool failure rather
  than prose handed to the model to interpret.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from software_factory.definition.models import Effect


class CostClass(enum.StrEnum):
    FREE = "free"
    CHEAP = "cheap"
    MODERATE = "moderate"
    EXPENSIVE = "expensive"


class FailureKind(enum.StrEnum):
    NOT_FOUND = "not_found"
    INVALID_INPUT = "invalid_input"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    TRUNCATED = "truncated"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class ToolFailure:
    """A typed failure. ``remediation`` is imperative: it states the next action."""

    kind: FailureKind
    message: str
    remediation: str
    partial: Any = None
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "kind": self.kind.value,
            "message": self.message,
            "remediation": self.remediation,
            "truncated": self.truncated,
            "partial": self.partial,
        }


@dataclass(frozen=True, slots=True)
class ToolSuccess:
    value: Any
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"ok": True, "value": self.value, "truncated": self.truncated}


ToolResult = ToolSuccess | ToolFailure
Handler = Callable[[dict[str, Any]], ToolResult]


@dataclass(frozen=True, slots=True)
class Example:
    """A worked example. Required: a tool nobody can see used is a tool nobody uses well."""

    inputs: dict[str, Any]
    output: str


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool declaration. Both schemas and one example are mandatory (HARNESS.md T-1)."""

    name: str
    description: str
    effect: Effect
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    handler: Handler
    examples: tuple[Example, ...]
    cost_class: CostClass = CostClass.CHEAP
    idempotent: bool = True
    timeout_ms: int = 30_000

    def signature(self) -> str:
        properties = self.input_schema.get("properties", {})
        required = set(self.input_schema.get("required", []))
        args = ", ".join(
            f"{name}{'' if name in required else '?'}: {spec.get('type', 'any')}"
            for name, spec in properties.items()
        )
        return f"{self.name}({args}) -> {self.output_schema.get('type', 'object')}"


@dataclass(frozen=True, slots=True)
class Violation:
    """A refused attempt. Recorded with context, because a pattern of these is a signal."""

    tool: str
    reason: str
    effect: Effect | None = None
    escalating: bool = False
    """True when the attempt targeted a grant boundary rather than a path outside scope."""


class ToolRegistryError(Exception):
    """A tool declaration is invalid. Raised at registration, never at call time."""


@dataclass(slots=True)
class Grants:
    """What one agent may do this run. Resolved before the run starts; immutable during it."""

    tools: frozenset[str] = frozenset()
    effects: frozenset[Effect] = frozenset({Effect.READ})
    external_actions: frozenset[str] = frozenset()
    allow_all_tools: bool = False
    """Only for a factory that has not narrowed its grants. Effects still apply."""

    def permits(self, tool: Tool) -> tuple[bool, str, bool]:
        """Return ``(allowed, reason, escalating)``.

        An effect-class denial is escalating: asking for ``exec`` when only ``read`` was
        granted is an attempt at a capability boundary, not a mis-typed tool name.
        """
        if not self.allow_all_tools and tool.name not in self.tools:
            return False, f"tool {tool.name!r} is not granted to this agent", False
        if tool.effect not in self.effects:
            return (
                False,
                f"effect {tool.effect.value!r} is not granted to this agent",
                True,
            )
        return True, "", False


class ToolRegistry:
    """Holds tool declarations and dispatches calls under a grant set."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self.violations: list[Violation] = []

    def register(self, tool: Tool) -> None:
        """Register a tool. Rejects declarations that cannot be presented to an agent."""
        if not tool.input_schema or not tool.output_schema:
            raise ToolRegistryError(
                f"{tool.name}: both input and output schemas are required; an agent cannot "
                "use a tool whose shape it cannot see"
            )
        if not tool.examples:
            raise ToolRegistryError(
                f"{tool.name}: at least one worked example is required; a tool nobody can "
                "see used is a tool nobody uses well"
            )
        if not tool.description.strip():
            raise ToolRegistryError(f"{tool.name}: a description is required")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def granted(self, grants: Grants) -> list[Tool]:
        """Tools this agent may call.

        Ungranted tools are not listed at all: an agent should not spend attention on
        doors that are locked (awareness.md §3.7).
        """
        return sorted(
            (tool for tool in self._tools.values() if grants.permits(tool)[0]),
            key=lambda tool: tool.name,
        )

    def call(self, name: str, arguments: dict[str, Any], *, grants: Grants) -> ToolResult:
        """Dispatch a call, enforcing grants first.

        A denial is recorded as a violation *and* returned as a typed failure. The agent
        learns it cannot do this; the operator learns it tried.
        """
        tool = self._tools.get(name)
        if tool is None:
            self.violations.append(Violation(tool=name, reason="unknown tool"))
            return ToolFailure(
                FailureKind.NOT_FOUND,
                f"no tool named {name!r}",
                "Call a tool from the toolbelt in your context.",
            )

        allowed, reason, escalating = grants.permits(tool)
        if not allowed:
            self.violations.append(
                Violation(tool=name, reason=reason, effect=tool.effect, escalating=escalating)
            )
            return ToolFailure(
                FailureKind.DENIED,
                reason,
                (
                    "This is a configuration decision, not a prompt one. Ask a human to "
                    "grant it if the work genuinely requires it."
                ),
            )

        missing = [key for key in tool.input_schema.get("required", []) if key not in arguments]
        if missing:
            return ToolFailure(
                FailureKind.INVALID_INPUT,
                f"{name}: missing required argument(s): {', '.join(missing)}",
                f"Call it as {tool.signature()}.",
            )

        try:
            return tool.handler(arguments)
        except TimeoutError:
            return ToolFailure(
                FailureKind.TIMEOUT,
                f"{name} exceeded {tool.timeout_ms}ms",
                "Narrow the request, or split it into smaller calls.",
            )
        except Exception as exc:
            return ToolFailure(
                FailureKind.INTERNAL,
                f"{name} raised {exc!r}",
                "This is a defect in the tool, not in your request. Try a different approach.",
            )

    def escalating_violations(self, *, since: int = 0) -> list[Violation]:
        """Escalating violations recorded after index ``since``.

        Runs share a registry, and the violation list is cumulative, so a loop that asked
        "were there any?" terminated every later run for the first run's violation -- and
        leaked its text into their results. Callers pass the mark they took at start.
        """
        return [v for v in self.violations[since:] if v.escalating]

    def violation_mark(self) -> int:
        """The current violation count, to be passed back as ``since``."""
        return len(self.violations)

    def render_toolbelt(self, grants: Grants) -> list[str]:
        """One line per granted tool, with its signature and an example."""
        lines = []
        for tool in self.granted(grants):
            example = tool.examples[0]
            lines.append(
                f"{tool.signature()} [{tool.effect.value}, {tool.cost_class.value}]\n"
                f"    {tool.description}\n"
                f"    e.g. {tool.name}({example.inputs}) -> {example.output}"
            )
        return lines


@dataclass(slots=True)
class BlastRadius:
    """What a run may affect, and what undo costs (PRD FR-12.1, HARNESS.md §5).

    Stated to the agent affirmatively. The purpose is to license bold approaches inside a
    safe envelope, not to intimidate -- an agent that does not know undo is free will
    choose the timid approach every time.
    """

    writable_paths: tuple[str, ...] = ()
    effects_allowed: frozenset[Effect] = frozenset({Effect.READ})
    external_actions: frozenset[str] = frozenset()
    network: str = "none"
    checkpoints: bool = True
    wall_clock_s: int = 1800
    tool_calls: int = 200

    def courage_clause(self) -> str:
        """Generated from the enforced contract, never hand-written (HARNESS.md B-8).

        Deriving it mechanically is what stops it from overstating an agent's actual
        freedom: the text can only ever describe grants that are really in force.
        """
        paths = ", ".join(self.writable_paths) or "the run workspace"
        external = ", ".join(sorted(self.external_actions)) or "none"
        undo = (
            "A checkpoint was taken before this run and at each step boundary; "
            "`checkpoint.restore` returns the workspace exactly to any of them, and doing "
            "so costs nothing and counts against no quality measure."
            if self.checkpoints
            else "No checkpoints are available this run, so prefer small, verifiable steps."
        )
        return (
            f"You may modify anything under {paths}. {undo} "
            f"Nothing you do inside this workspace is visible outside it until an external "
            f"action, and the external actions available to you this run are: {external}. "
            "Therefore: prefer the approach you believe is right over the approach that is "
            "merely safe, try the alternative you are unsure about, and record what you "
            "rejected and why."
        )


@dataclass(slots=True)
class CalibrationCriterion:
    criterion_id: str
    confidence: float
    evidence: tuple[str, ...] = ()
    basis: str = ""


@dataclass(slots=True)
class Calibration:
    """An agent's structured self-assessment (PRD FR-11.6, HARNESS.md O-4)."""

    criteria: list[CalibrationCriterion] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0
    rewritten: list[str] = field(default_factory=list)

    def normalise(self) -> Calibration:
        """Rewrite uncited confidence to zero (HARNESS.md O-5).

        Confidence without evidence is not confidence, and letting it through means a
        downstream gate treats a guess as a finding. The rewrite is recorded so the
        agent's calibration can be scored on what it *claimed*, not on what survived.
        """
        for criterion in self.criteria:
            if criterion.confidence > 0 and not criterion.evidence:
                self.rewritten.append(criterion.criterion_id)
                criterion.confidence = 0.0
        if self.criteria:
            self.overall_confidence = sum(c.confidence for c in self.criteria) / len(self.criteria)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "overallConfidence": round(self.overall_confidence, 4),
            "criteria": [
                {
                    "id": c.criterion_id,
                    "confidence": c.confidence,
                    "evidence": list(c.evidence),
                    "basis": c.basis,
                }
                for c in self.criteria
            ],
            "unknowns": list(self.unknowns),
            "assumptions": list(self.assumptions),
            "rewrittenForMissingEvidence": list(self.rewritten),
        }


def calibration_error(stated: float, observed_pass: bool) -> float:
    """How far a stated confidence was from what happened.

    Squared error, so confident-and-wrong is penalised much harder than uncertain-and-
    wrong. That asymmetry is the point: an agent that hedges everything is annoying, and
    an agent that is confidently wrong is dangerous.
    """
    return (stated - (1.0 if observed_pass else 0.0)) ** 2
