"""The skill registry: selection, metrics, and the five lifecycle operations.

Implements PRD FR-7 and docs/harness/skills.md.

Every skill library becomes a junk drawer unless removal is as easy as addition. So this
module is built around one measurement -- **selection quality** -- and one rule: a skill
that cannot show evidence does not advance.

The operations (promote, evolve, merge, split, sunset) all return *proposals*. Nothing
here mutates a definition: skills live in files, and files change through review.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from software_factory.definition.models import AgentRole, SkillStatus, Stage
from software_factory.memory.similarity import jaccard, jaccard_of, tokens
from software_factory.surfaces import surfaces_overlap

DEFAULT_OFFER_SIZE = 7
"""How many skills an agent is offered per run.

Bounded deliberately: past a small number, additional options degrade selection rather
than improving it. When a library outgrows this, the answer is sharper descriptions and
splits, not a bigger offer (skills.md K-17).
"""

MIN_TRIALS = 20
MIN_LIFT = 0.10
MIN_PRECISION = 0.6
COLLISION_THRESHOLD = 0.75
OVERLAP_THRESHOLD = 0.7
BODY_SIMILARITY_THRESHOLD = 0.6
UNUSED_RUN_THRESHOLD = 200
FAILING_WINDOW_THRESHOLD = 3
MAX_REVISIONS_PER_WINDOW = 3


class Operation(enum.StrEnum):
    PROMOTE = "promote"
    EVOLVE = "evolve"
    MERGE = "merge"
    SPLIT = "split"
    SUNSET = "sunset"
    REVISE_DESCRIPTION = "revise-description"


@dataclass(slots=True)
class SkillMetrics:
    """Observed behaviour of one skill over a window.

    ``helped`` is outcome-linked: it moves only when the skill was loaded *and* cited in
    a run that then passed its gates. Self-reported usefulness never touches it.
    """

    offered: int = 0
    loaded: int = 0
    helped: int = 0
    hindered: int = 0
    missed: int = 0
    eligible_runs: int = 0
    revisions_in_window: int = 0
    eval_pass_rate: float = 0.0
    baseline_pass_rate: float = 0.0
    failing_windows: int = 0
    orphaned_anchors: bool = False

    @property
    def precision(self) -> float:
        """Of the times it was loaded, how often it helped."""
        return self.helped / self.loaded if self.loaded else 0.0

    @property
    def recall(self) -> float:
        """Of the times it should have been loaded, how often it was.

        ``missed`` is detected retrospectively from failed runs whose failure signature
        matches the skill's declared scope. It is an estimate with a stated derivation,
        not a measurement -- there is no oracle for "should have been selected".
        """
        denominator = self.helped + self.missed
        return self.helped / denominator if denominator else 0.0

    @property
    def lift(self) -> float:
        return self.eval_pass_rate - self.baseline_pass_rate


@dataclass(slots=True)
class SkillRecord:
    """A skill as the registry sees it: its declaration plus its observed behaviour."""

    name: str
    description: str
    body: str
    status: SkillStatus
    version: int = 1
    roles: tuple[AgentRole, ...] = ()
    stages: tuple[Stage, ...] = ()
    surfaces: tuple[str, ...] = ()
    owners: tuple[str, ...] = ()
    evals: tuple[str, ...] = ()
    superseded_by: str | None = None
    sample_fraction: float = 0.25
    metrics: SkillMetrics = field(default_factory=SkillMetrics)
    eval_results_by_class: dict[str, float] = field(default_factory=dict)

    @property
    def selectable(self) -> bool:
        """Draft skills load only when named; retired ones never load."""
        return self.status in (SkillStatus.TRIAL, SkillStatus.ACTIVE, SkillStatus.DEPRECATED)


@dataclass(frozen=True, slots=True)
class Proposal:
    """A proposed lifecycle change. Always reviewed; never applied by the registry."""

    operation: Operation
    skills: tuple[str, ...]
    rationale: str
    evidence: tuple[str, ...] = ()
    successor: str | None = None
    children: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation.value,
            "skills": list(self.skills),
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "successor": self.successor,
            "children": list(self.children),
        }


@dataclass(frozen=True, slots=True)
class Refusal:
    code: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class Offer:
    """What an agent was actually shown, and why."""

    offered: tuple[str, ...]
    scores: dict[str, float]
    excluded: dict[str, str]


class SkillRegistry:
    """Holds skill records and answers questions about selection and lifecycle."""

    def __init__(self, records: list[SkillRecord] | None = None) -> None:
        self._records: dict[str, SkillRecord] = {r.name: r for r in (records or [])}
        self._description_tokens: dict[str, frozenset[str]] = {}
        self._description_keys: dict[str, tuple[str, str]] = {}
        self._collisions: dict[str, float] | None = None
        self._collisions_for: int | None = None

    def add(self, record: SkillRecord) -> None:
        self._records[record.name] = record
        self._description_tokens[record.name] = frozenset(tokens(record.description))
        self._description_keys[record.name] = (record.name, record.description)
        # The collision matrix is a property of the whole registry, so any change to any
        # skill invalidates all of it. Recomputed lazily on the next read.
        self._collisions = None

    def get(self, name: str) -> SkillRecord | None:
        return self._records.get(name)

    def all(self) -> list[SkillRecord]:
        return list(self._records.values())

    # ------------------------------------------------------------------- selection

    def offer(
        self,
        *,
        role: AgentRole,
        stage: Stage,
        surfaces: set[str],
        task: str,
        limit: int = DEFAULT_OFFER_SIZE,
        sampled: frozenset[str] = frozenset(),
    ) -> Offer:
        """Rank and bound what an agent is shown.

        Ordering is by score, never by name or filesystem order -- alphabetical ordering
        is a silent bias that hands the top of the list to whoever named their skill
        `a-something`.
        """
        scores: dict[str, float] = {}
        excluded: dict[str, str] = {}

        for record in self._records.values():
            if not record.selectable:
                excluded[record.name] = f"status is {record.status.value}"
                continue
            if record.status is SkillStatus.TRIAL and record.name not in sampled:
                excluded[record.name] = "trial skill not sampled for this run"
                continue
            if record.roles and role not in record.roles:
                excluded[record.name] = f"not applicable to role {role.value}"
                continue
            if record.stages and stage not in record.stages:
                excluded[record.name] = f"not applicable to stage {stage.value}"
                continue
            if record.surfaces and not _surface_match(record.surfaces, surfaces):
                excluded[record.name] = "no surface overlap"
                continue
            scores[record.name] = self._score(record, task, surfaces)

        ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        return Offer(
            offered=tuple(name for name, _ in ranked[:limit]),
            scores=scores,
            excluded=excluded,
        )

    def _score(self, record: SkillRecord, task: str, surfaces: set[str]) -> float:
        applicability = jaccard(record.description, task)
        if record.surfaces and _surface_match(record.surfaces, surfaces):
            applicability += 0.2
        collision = self.collision(record.name)
        deprecated_penalty = 0.3 if record.status is SkillStatus.DEPRECATED else 0.0
        return (
            0.5 * applicability
            + 0.4 * record.metrics.precision
            - 0.2 * collision
            - deprecated_penalty
        )

    def collision(self, name: str) -> float:
        """Highest description similarity to any sibling that could be offered alongside.

        Read from a matrix computed once per registry version. `offer` calls `_score` per
        candidate and `_score` called this, which scanned the whole registry re-tokenizing
        two descriptions each time: for a 500-skill library, 250 000 Jaccard computations
        on every run, to return the seven names `DEFAULT_OFFER_SIZE` asks for.
        """
        if name not in self._records:
            return 0.0
        # `SkillRecord` is mutable, so a caller holding one can change its description or
        # status without passing through `add`. The signature is O(n) per call, against the
        # O(n^2) Jaccards it guards -- cheap enough to check every time rather than trust
        # that nobody did.
        signature = self._collision_signature()
        if self._collisions is None or self._collisions_for != signature:
            self._collisions = self._compute_collisions()
            self._collisions_for = signature
        return self._collisions.get(name, 0.0)

    def _collision_signature(self) -> int:
        return hash(
            tuple(
                sorted(
                    (record.name, record.status.value, record.description)
                    for record in self._records.values()
                )
            )
        )

    def _compute_collisions(self) -> dict[str, float]:
        """The whole collision matrix in one pass, tokenizing each description once."""
        live = [r for r in self._records.values() if r.status is not SkillStatus.RETIRED]
        scores = dict.fromkeys(self._records, 0.0)
        for index, left in enumerate(live):
            left_tokens = self._tokens_for(left)
            for right in live[index + 1 :]:
                overlap = jaccard_of(left_tokens, self._tokens_for(right))
                if overlap > scores[left.name]:
                    scores[left.name] = overlap
                if overlap > scores[right.name]:
                    scores[right.name] = overlap
        return scores

    def _tokens_for(self, record: SkillRecord) -> frozenset[str]:
        """Description tokens, cached. Records added before the cache existed fall back."""
        key = (record.name, record.description)
        cached = self._description_tokens.get(record.name)
        if cached is None or self._description_keys.get(record.name) != key:
            cached = frozenset(tokens(record.description))
            self._description_tokens[record.name] = cached
            self._description_keys[record.name] = key
        return cached

    # ------------------------------------------------------------------- lifecycle

    def propose_promotion(self, name: str) -> Proposal | Refusal:
        """`trial -> active` requires measured lift, not plausibility."""
        record = self._records.get(name)
        if record is None:
            return Refusal("skill.unknown", f"no skill named {name!r}", "Check the name.")
        if record.status is not SkillStatus.TRIAL:
            return Refusal(
                "skill.wrong_status",
                f"{name} is {record.status.value}, not trial",
                "Only a trial skill can be promoted to active.",
            )
        metrics = record.metrics
        if metrics.eligible_runs < MIN_TRIALS:
            return Refusal(
                "skill.insufficient_trials",
                f"{name} has {metrics.eligible_runs} eligible runs, needs {MIN_TRIALS}",
                "Leave it in trial until enough runs have been observed.",
            )
        if metrics.lift < MIN_LIFT:
            return Refusal(
                "skill.no_lift",
                f"{name} shows {metrics.lift:+.0%} against baseline, needs {MIN_LIFT:+.0%}",
                (
                    "A skill that does not measurably improve outcomes should not be adopted. "
                    "Revise it, or retire it."
                ),
            )
        if metrics.precision < MIN_PRECISION:
            return Refusal(
                "skill.low_precision",
                f"{name} helps in {metrics.precision:.0%} of loads, needs {MIN_PRECISION:.0%}",
                (
                    "It is being selected when it does not apply. Sharpen the description "
                    "before changing the body."
                ),
            )
        return Proposal(
            operation=Operation.PROMOTE,
            skills=(name,),
            rationale=(
                f"{name} lifted its eval pass rate by {metrics.lift:+.0%} over "
                f"{metrics.eligible_runs} runs, at {metrics.precision:.0%} precision"
            ),
            evidence=record.evals,
        )

    def check_revision(
        self, name: str, *, before: float, after: float, benchmark_delta: float = 0.0
    ) -> Proposal | Refusal:
        """A revision that regresses its own eval set is rejected, not merely flagged."""
        record = self._records.get(name)
        if record is None:
            return Refusal("skill.unknown", f"no skill named {name!r}", "Check the name.")
        if after < before:
            return Refusal(
                "skill.self_regression",
                f"{name} would fall from {before:.0%} to {after:.0%} on its own evals",
                "Fix the revision, or withdraw it. A revision must not lose ground.",
            )
        if benchmark_delta < -0.02:
            return Refusal(
                "skill.benchmark_regression",
                f"{name} regresses the standing benchmark by {benchmark_delta:.0%}",
                "The change helps this skill and hurts the factory. Reconsider it.",
            )
        if record.metrics.revisions_in_window >= MAX_REVISIONS_PER_WINDOW:
            return Refusal(
                "skill.revision_churn",
                (
                    f"{name} has been revised {record.metrics.revisions_in_window} times this "
                    "window; it is probably two skills or the wrong abstraction"
                ),
                "Consider splitting it instead of revising it again.",
            )
        return Proposal(
            operation=Operation.EVOLVE,
            skills=(name,),
            rationale=f"{name} improves from {before:.0%} to {after:.0%} on its eval set",
            evidence=record.evals,
        )

    def propose_merges(self) -> list[Proposal]:
        """Two skills whose scope and body both overlap heavily should be one skill."""
        proposals: list[Proposal] = []
        candidates = [
            r for r in self._records.values() if r.status in (SkillStatus.TRIAL, SkillStatus.ACTIVE)
        ]
        # Bodies are far larger than descriptions, so re-tokenizing both sides of every
        # pair was the most expensive loop in the module.
        body_tokens = {record.name: frozenset(tokens(record.body)) for record in candidates}
        for index, left in enumerate(candidates):
            for right in candidates[index + 1 :]:
                scope_overlap = _scope_overlap(left, right)
                body_overlap = jaccard_of(body_tokens[left.name], body_tokens[right.name])
                if scope_overlap < OVERLAP_THRESHOLD or body_overlap < BODY_SIMILARITY_THRESHOLD:
                    continue
                proposals.append(
                    Proposal(
                        operation=Operation.MERGE,
                        skills=(left.name, right.name),
                        rationale=(
                            f"{left.name} and {right.name} overlap {scope_overlap:.0%} in scope "
                            f"and {body_overlap:.0%} in body; they compete at selection time"
                        ),
                        evidence=tuple(sorted({*left.evals, *right.evals})),
                        successor=f"{left.name}-and-{right.name}",
                    )
                )
        return proposals

    def propose_splits(self) -> list[Proposal]:
        """A skill passing one task class and failing another is two skills."""
        proposals: list[Proposal] = []
        for record in self._records.values():
            if record.status not in (SkillStatus.TRIAL, SkillStatus.ACTIVE):
                continue
            results = record.eval_results_by_class
            if len(results) < 2:
                continue
            best_class, best = max(results.items(), key=lambda pair: pair[1])
            worst_class, worst = min(results.items(), key=lambda pair: pair[1])
            if best < 0.8 or worst > 0.4:
                continue
            proposals.append(
                Proposal(
                    operation=Operation.SPLIT,
                    skills=(record.name,),
                    rationale=(
                        f"{record.name} passes {best_class} at {best:.0%} and fails "
                        f"{worst_class} at {worst:.0%}; one description cannot serve both"
                    ),
                    evidence=record.evals,
                    children=(f"{record.name}-{best_class}", f"{record.name}-{worst_class}"),
                )
            )
        return proposals

    def check_split(self, name: str, *, child_collisions: dict[str, float]) -> Proposal | Refusal:
        """Splitting must improve selection, not merely narrow scope (skills.md K-10)."""
        record = self._records.get(name)
        if record is None:
            return Refusal("skill.unknown", f"no skill named {name!r}", "Check the name.")
        parent_collision = self.collision(name)
        worst_child = max(child_collisions.values(), default=0.0)
        if worst_child >= parent_collision:
            return Refusal(
                "skill.split_does_not_help",
                (
                    f"the children collide at {worst_child:.0%}, no better than {name} at "
                    f"{parent_collision:.0%}; the problem is the description, not the breadth"
                ),
                "Sharpen the descriptions instead of splitting.",
            )
        return Proposal(
            operation=Operation.SPLIT,
            skills=(name,),
            rationale=(
                f"splitting {name} reduces worst-case collision from {parent_collision:.0%} "
                f"to {worst_child:.0%}"
            ),
            evidence=record.evals,
            children=tuple(sorted(child_collisions)),
        )

    def propose_sunsets(self) -> list[Proposal]:
        """Batched, never trickled: pruning should be one deliberate act (skills.md K-13)."""
        proposals: list[Proposal] = []
        for record in self._records.values():
            if record.status in (SkillStatus.RETIRED, SkillStatus.DRAFT):
                continue
            reason = self._sunset_reason(record)
            if reason is None:
                continue
            proposals.append(
                Proposal(
                    operation=Operation.SUNSET,
                    skills=(record.name,),
                    rationale=reason,
                    evidence=record.evals,
                )
            )
        return proposals

    @staticmethod
    def _sunset_reason(record: SkillRecord) -> str | None:
        metrics = record.metrics
        if metrics.eligible_runs >= UNUSED_RUN_THRESHOLD and metrics.loaded == 0:
            return (
                f"never selected across {metrics.eligible_runs} eligible runs; it is not "
                "earning its place in the selection budget"
            )
        if metrics.failing_windows >= FAILING_WINDOW_THRESHOLD:
            return f"eval set failing for {metrics.failing_windows} consecutive windows"
        if metrics.orphaned_anchors:
            return "every anchor it references is orphaned; the code it describes is gone"
        if metrics.loaded and metrics.hindered > metrics.helped:
            return (
                f"hindered {metrics.hindered} runs against {metrics.helped} helped; it is "
                "costing more than it returns"
            )
        return None

    def description_problems(self, name: str) -> list[Refusal]:
        """Check a description against the discoverability rules (skills.md §4.1).

        Selection happens on the description, so a skill with a perfect body and a vague
        description is a broken skill -- and this is what says which of the two is wrong.
        """
        record = self._records.get(name)
        if record is None:
            return [Refusal("skill.unknown", f"no skill named {name!r}", "Check the name.")]

        problems: list[Refusal] = []
        text = record.description.lower()

        if not any(marker in text for marker in ("use ", "when ", "before ", "after ", "for ")):
            problems.append(
                Refusal(
                    "description.no_trigger",
                    f"{name} does not say when it applies",
                    "State the condition under which an agent should reach for it.",
                )
            )
        if not any(marker in text for marker in ("not ", "never ", "except", "rather than")):
            problems.append(
                Refusal(
                    "description.no_boundary",
                    f"{name} does not say what it is not for",
                    "State what it does not cover, so it is not selected for adjacent work.",
                )
            )
        collision = self.collision(name)
        if collision >= COLLISION_THRESHOLD:
            problems.append(
                Refusal(
                    "description.collides",
                    f"{name} is {collision:.0%} similar to a sibling description",
                    "Sharpen both, or merge the skills.",
                )
            )
        if record.metrics.loaded and record.metrics.precision < MIN_PRECISION:
            problems.append(
                Refusal(
                    "description.imprecise",
                    (
                        f"{name} helps in only {record.metrics.precision:.0%} of loads; it is "
                        "being selected when it does not apply"
                    ),
                    (
                        "Revise the description before the body. Fixing the body of a skill "
                        "that is never correctly selected changes nothing."
                    ),
                )
            )
        return problems


def _surface_match(declared: tuple[str, ...], actual: set[str]) -> bool:
    """One rule, shared with the spec's own path matching. See `software_factory.surfaces`."""
    return surfaces_overlap(declared, actual)


def _scope_overlap(left: SkillRecord, right: SkillRecord) -> float:
    """Jaccard over the union of a skill's declared applicability dimensions."""

    def scope(record: SkillRecord) -> set[str]:
        return {
            *(f"role:{r.value}" for r in record.roles),
            *(f"stage:{s.value}" for s in record.stages),
            *(f"surface:{s}" for s in record.surfaces),
        }

    a, b = scope(left), scope(right)
    if not a and not b:
        return 1.0  # both unscoped: they apply everywhere, so they fully overlap
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
