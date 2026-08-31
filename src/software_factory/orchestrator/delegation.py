"""Sub-agent delegation, its bounds, and the tree that makes it legible (PRD FR-34).

The gap this closes is not that delegation was missing. It is that nothing *forbade* it, and
an unspecified capability that arrives anyway arrives without a budget, without attribution,
and without a view that shows it happened. A specialist can already call tools; a specialist
that dispatches a sub-run is a small step from there, and the first time one does it the
factory would have no answer to "what did this cost" or "who actually did the work".

Three things follow, and each is a rule about spend as much as about structure.

**A child's spend counts against its parent.** Otherwise delegation is a way to exceed a work
item's budget by asking someone else to spend it -- the budget bounds a run, and a run that
can create runs bounds nothing.

**Depth and fan-out are bounded, declared, and small.** Unbounded delegation is unbounded
spend, and the failure mode is quiet: a run that looks stalled while forty descendants work.

**The tree is a view.** Spend is already attributed by agent (FR-26.5); what was missing was
the shape -- which agents served this request, in what relation, and what each cost. An
operator asking "why did this work item cost forty units" needs the tree, not a flat list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from software_factory.ledger.entry import EntryType

MAX_DELEGATION_DEPTH = 2
"""How far delegation may nest: a specialist may delegate, and its child may not.

Two rather than one because a builder asking a prover to check something is the case this
exists for. Two rather than three because the third level is where the tree stops being
something a person can hold in their head while reading a cost report -- and because every
level multiplies fan-out.
"""

MAX_FAN_OUT = 4
"""How many children one run may dispatch.

Small on purpose. A run needing five sub-agents has not decomposed its work, it has
scattered it, and the stage machine is the mechanism for decomposition that a reviewer can
see.
"""


@dataclass(frozen=True, slots=True)
class Refused:
    """Why a delegation was not permitted. Never a bare False."""

    code: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class RunNode:
    """One run in the delegation tree, with what it cost."""

    run_id: str
    agent: str
    stage: str
    parent_run_id: str = ""
    cost_units: float = 0.0
    children: tuple[RunNode, ...] = ()

    @property
    def total_cost(self) -> float:
        """This run's cost plus everything it delegated.

        The number that matters: a run whose own spend is small and whose descendants' is not
        is exactly the case a flat per-agent report renders as innocent.
        """
        return self.cost_units + sum(child.total_cost for child in self.children)

    @property
    def depth(self) -> int:
        return 1 + max((child.depth for child in self.children), default=0)

    def render(self, indent: int = 0) -> str:
        pad = "  " * indent
        own = f"{self.cost_units:.2f}"
        total = f"{self.total_cost:.2f}"
        suffix = f" (own {own}, with children {total})" if self.children else f" ({own})"
        lines = [f"{pad}{self.agent} · {self.stage} · {self.run_id}{suffix}"]
        lines += [child.render(indent + 1) for child in self.children]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run": self.run_id,
            "agent": self.agent,
            "stage": self.stage,
            "parent": self.parent_run_id,
            "costUnits": round(self.cost_units, 6),
            "totalCostUnits": round(self.total_cost, 6),
            "children": [child.as_dict() for child in self.children],
        }


@dataclass(slots=True)
class DelegationBook:
    """Which runs delegated to which, and whether another delegation is permitted."""

    parents: dict[str, str] = field(default_factory=dict)
    """child run id -> parent run id."""

    def depth_of(self, run_id: str) -> int:
        """How many delegations deep this run is. A root run is 0."""
        depth = 0
        seen: set[str] = set()
        current = run_id
        while current in self.parents and current not in seen:
            seen.add(current)
            current = self.parents[current]
            depth += 1
        return depth

    def children_of(self, run_id: str) -> list[str]:
        return sorted(child for child, parent in self.parents.items() if parent == run_id)

    def may_delegate(self, parent_run_id: str) -> Refused | None:
        """``None`` when this run may dispatch another, otherwise why not."""
        depth = self.depth_of(parent_run_id)
        if depth >= MAX_DELEGATION_DEPTH:
            return Refused(
                "delegation.too_deep",
                f"{parent_run_id!r} is already {depth} delegation(s) deep; the ceiling is "
                f"{MAX_DELEGATION_DEPTH}",
                (
                    "Return what you have and let the stage machine carry the rest. Nesting "
                    "further makes a tree nobody can hold in their head while reading a cost "
                    "report, and every level multiplies fan-out."
                ),
            )
        existing = len(self.children_of(parent_run_id))
        if existing >= MAX_FAN_OUT:
            return Refused(
                "delegation.fan_out",
                f"{parent_run_id!r} has already dispatched {existing}; the ceiling is "
                f"{MAX_FAN_OUT}",
                (
                    "A run needing more sub-agents has not decomposed its work, it has "
                    "scattered it. The stage machine is the decomposition a reviewer can see."
                ),
            )
        return None

    def record(self, *, parent_run_id: str, child_run_id: str) -> Refused | None:
        """Register a delegation, or refuse it. Returns None on success."""
        if child_run_id == parent_run_id:
            return Refused(
                "delegation.self",
                f"{child_run_id!r} cannot delegate to itself",
                "A run that is its own parent makes the depth check non-terminating.",
            )
        if child_run_id in self.parents:
            return Refused(
                "delegation.already_parented",
                f"{child_run_id!r} already has a parent",
                "A run has one parent, or its cost is attributable to two places at once.",
            )
        refusal = self.may_delegate(parent_run_id)
        if refusal is not None:
            return refusal
        self.parents[child_run_id] = parent_run_id
        return None


def tree_from(entries: Any) -> list[RunNode]:
    """Build the delegation forest from the ledger, with cost folded in (FR-34.3).

    Returns the roots. A run with no recorded parent is a root, which includes every run in a
    factory that never delegates -- so this is a flat list there, and the same view works
    either way rather than needing a caller to know which case they are in.
    """
    runs: dict[str, dict[str, Any]] = {}
    parents: dict[str, str] = {}
    cost: dict[str, float] = {}

    for entry in entries:
        payload = entry.payload
        if entry.type is EntryType.RUN_STARTED:
            run_id = str(payload.get("run", entry.subject))
            runs[run_id] = {
                "agent": str(entry.actor),
                "stage": str(payload.get("stage", "")),
            }
            parent = str(payload.get("parentRun", ""))
            if parent:
                parents[run_id] = parent
        elif entry.type is EntryType.MODEL_CALLED:
            run_id = str(payload.get("run", entry.subject))
            cost[run_id] = cost.get(run_id, 0.0) + float(payload.get("costUnits", 0.0) or 0.0)

    def build(run_id: str) -> RunNode:
        facts = runs.get(run_id, {"agent": "unknown", "stage": ""})
        return RunNode(
            run_id=run_id,
            agent=str(facts["agent"]),
            stage=str(facts["stage"]),
            parent_run_id=parents.get(run_id, ""),
            cost_units=cost.get(run_id, 0.0),
            children=tuple(
                build(child) for child in sorted(c for c, p in parents.items() if p == run_id)
            ),
        )

    roots = [run_id for run_id in sorted(runs) if run_id not in parents]
    return [build(run_id) for run_id in roots]
