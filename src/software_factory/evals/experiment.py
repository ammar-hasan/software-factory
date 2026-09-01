"""The central-bet experiment (PRD §11.2), and the routing it justifies.

§1.1 claims that a modest model inside an excellent harness beats a frontier model inside a
poor one. That is a hypothesis, and §11.2 is its protocol -- written, in the PRD's own words,
"to be able to fail". Everything in this module exists to keep that true, because the
failure mode of an in-house benchmark is not that it produces a wrong number. It is that it
produces a flattering one, and that no part of the system was ever capable of producing any
other.

So the machinery here is mostly refusals:

**A registration locks at the first trial.** Pre-registration that can be edited afterwards
is a results section written in advance. The document is hashed when the first trial is
recorded; an edit after that is refused, and an amendment is a separate, dated record --
so a reader sees not just the final protocol but that it changed, and when.

**Unequal budgets are refused, not noted.** The PRD is explicit: "any difference in attempts
is a confound, not a treatment". A benchmark that lets the treatment have more attempts and
mentions it in a footnote has measured attempts.

**Repetitions are not samples.** Ten runs of one task are one task's worth of evidence.
Everything aggregates per task before it compares, and the comparison is a paired
permutation test over tasks -- distribution-free, and honest about the unit of analysis.

**An underpowered corpus yields `INSUFFICIENT_DATA`, never a verdict.** This is the
availability discipline the rest of the factory uses, in the one place where the temptation
to round up is strongest. A corpus of twelve tasks cannot detect a ten-point effect, and a
report that says "no significant difference" from twelve tasks is reporting the corpus size
as though it were a finding.

**Any failed primary falsifies. No exemptions.** AC-1 through AC-5 are conjunctive. In
particular AC-4 failing for a subsystem means that subsystem *must be removed* -- and the
verdict says so by name, because "retained for plausibility" is how a harness accumulates
parts that never earned their place.

**Nothing here reports a result the factory has not measured.** A fresh registration with no
trials evaluates to `INSUFFICIENT_DATA` with the reason "no trials recorded", which is the
honest state of this experiment today.
"""

from __future__ import annotations

import enum
import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from software_factory.errors import ErrorCode, FactoryError
from software_factory.memory.records import utc_now

#: How many permutations the paired test draws. Enough that the smallest resolvable
#: p-value (1/10001) sits well below any Holm-corrected threshold five comparisons deep.
PERMUTATIONS = 10_000

#: Fixed so a report is reproducible. A benchmark whose p-values move between runs of the
#: same data cannot be checked by anybody, which is the property that matters most here.
PERMUTATION_SEED = 20260901


class ExperimentError(FactoryError):
    """A protocol violation this factory will not record."""

    code = ErrorCode.DEFINITION_INVALID


class Verdict(enum.StrEnum):
    """What the experiment concluded.

    `INSUFFICIENT_DATA` is not a third kind of failure. It says the experiment cannot speak,
    which is a different thing from speaking against the hypothesis -- and collapsing them
    would let an underpowered run be reported either as vindication or as refutation
    depending on who was writing the summary.
    """

    SUPPORTED = "supported"
    FALSIFIED = "falsified"
    INSUFFICIENT_DATA = "insufficient_data"


class Condition(enum.StrEnum):
    """The five conditions of §11.2.

    `A` is a *competent* baseline, which is the load-bearing word. A benchmark against a
    strawman measures the strawman.
    """

    A_BASELINE_LARGE = "A"
    B_BASELINE_SMALL = "B"
    C_FACTORY_SMALL = "C"
    D_FACTORY_LARGE = "D"
    E_ABLATION = "E"


#: The subsystems AC-4 ablates. Named here rather than free text so a failing ablation
#: points at something the codebase actually contains.
ABLATABLE = ("awareness", "gates", "skills", "memory", "scaffolding")


@dataclass(frozen=True, slots=True)
class Task:
    """One corpus task, and what is known about it before any condition runs.

    `difficulty` and `contamination_suspect` are fixed *before* conditions are assigned.
    Stratifying after the fact is how a corpus quietly becomes the set of tasks the
    treatment happened to do well on.
    """

    id: str
    repository: str
    task_class: str
    difficulty: float
    parent_commit: str
    held_out: bool = False
    contamination_suspect: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "repository": self.repository,
            "taskClass": self.task_class,
            "difficulty": self.difficulty,
            "parentCommit": self.parent_commit,
            "heldOut": self.held_out,
            "contaminationSuspect": self.contamination_suspect,
        }


@dataclass(frozen=True, slots=True)
class Attempt:
    """One condition's attempt at one task."""

    task_id: str
    condition: Condition
    passed: bool
    cost: float = 0.0
    review_minutes: float = 0.0
    stated_confidence: float | None = None
    snapshot_isolated: bool = True
    ablation: str = ""
    """Which subsystem was removed, for condition E. Empty for every other condition."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task_id,
            "condition": self.condition.value,
            "passed": self.passed,
            "cost": self.cost,
            "reviewMinutes": self.review_minutes,
            "statedConfidence": self.stated_confidence,
            "snapshotIsolated": self.snapshot_isolated,
            "ablation": self.ablation,
        }


@dataclass(frozen=True, slots=True)
class Registration:
    """The protocol, fixed before any trial runs.

    Every field here is something a results section could otherwise be quietly written
    around: which effect counts as meaningful, how many tasks are needed to see it, how many
    attempts each condition gets, and what result would falsify the claim.
    """

    hypothesis: str
    minimum_effect: float
    """Pass-rate difference below which a result is noise, in points (0.10 = 10 points)."""
    alpha: float = 0.05
    required_tasks: int = 120
    required_repositories: int = 5
    required_task_classes: int = 4
    attempt_budget: int = 1
    repair_budget: int = 1
    registered_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not 0 < self.minimum_effect < 1:
            raise ExperimentError(
                f"a minimum effect of {self.minimum_effect} is not a pass-rate difference",
                remediation="Give it as a fraction, e.g. 0.10 for ten percentage points.",
            )
        if not self.hypothesis.strip():
            raise ExperimentError(
                "a registration needs a hypothesis",
                remediation=(
                    "State what would be true if the bet is right. A registration with no "
                    "hypothesis cannot be falsified by anything."
                ),
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis,
            "minimumEffect": self.minimum_effect,
            "alpha": self.alpha,
            "requiredTasks": self.required_tasks,
            "requiredRepositories": self.required_repositories,
            "requiredTaskClasses": self.required_task_classes,
            "attemptBudget": self.attempt_budget,
            "repairBudget": self.repair_budget,
            "registeredAt": self.registered_at.isoformat(),
        }

    def digest(self) -> str:
        """A stable hash of the protocol, so an edit after the first trial is detectable."""
        payload = dict(self.as_dict())
        payload.pop("registeredAt")
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Amendment:
    """A protocol change made after the first trial, recorded rather than applied silently."""

    at: datetime
    reason: str
    previous_digest: str
    new_digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "reason": self.reason,
            "previousDigest": self.previous_digest,
            "newDigest": self.new_digest,
        }


@dataclass(frozen=True, slots=True)
class Criterion:
    """One acceptance criterion's result."""

    id: str
    statement: str
    primary: bool
    met: bool | None
    """`None` when it could not be evaluated. Never coerced to `False`: "we could not tell"
    and "it failed" force different next actions, and only one of them is a finding."""
    detail: str
    p_value: float | None = None
    adjusted_alpha: float | None = None
    effect: float | None = None
    effect_reached: bool | None = None
    """Whether the observed effect met the registered minimum, ignoring significance.

    Separate from `met` because the two failures mean different things. An ablation with a
    large effect that misses Holm-corrected significance says "not established on this
    corpus"; one with no effect at all says "this subsystem did nothing". Only the second
    justifies removal, and AC-4's consequence is mandatory removal -- so conflating them
    would delete working subsystems on underpowered evidence.
    """

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "primary": self.primary,
            "met": self.met,
            "detail": self.detail,
            "pValue": self.p_value,
            "adjustedAlpha": self.adjusted_alpha,
            "effect": self.effect,
            "effectReached": self.effect_reached,
        }


@dataclass(frozen=True, slots=True)
class Result:
    """What the experiment concluded, and everything needed to check it."""

    verdict: Verdict
    reason: str
    criteria: tuple[Criterion, ...] = ()
    must_remove: tuple[str, ...] = ()
    """Subsystems whose ablation did not reduce the pass rate.

    AC-4's consequence, spelled out. The PRD says a subsystem that fails its ablation "must
    be removed, not retained for plausibility", and a report that merely marks the criterion
    failed leaves the removal to somebody's discretion later.
    """
    contamination_suspect: int = 0
    excluded: tuple[str, ...] = ()
    tasks: int = 0
    registration_digest: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "criteria": [c.as_dict() for c in self.criteria],
            "mustRemove": list(self.must_remove),
            "contaminationSuspect": self.contamination_suspect,
            "excluded": list(self.excluded),
            "tasks": self.tasks,
            "registrationDigest": self.registration_digest,
        }


@dataclass
class Experiment:
    """A registration, a corpus, and the attempts recorded against them."""

    registration: Registration
    tasks: tuple[Task, ...] = ()
    attempts: tuple[Attempt, ...] = ()
    amendments: tuple[Amendment, ...] = ()
    locked_digest: str = ""
    """The registration's digest at the moment the first trial was recorded."""

    # ------------------------------------------------------------------ recording

    def record(self, attempts: list[Attempt] | tuple[Attempt, ...]) -> None:
        """Add attempts, locking the protocol on the first one.

        Refuses an attempt against a task the corpus does not contain. A corpus that grows
        as results arrive is a corpus selected by its results, which is the failure this
        whole protocol is arranged against.
        """
        known = {task.id for task in self.tasks}
        unknown = sorted({a.task_id for a in attempts} - known)
        if unknown:
            raise ExperimentError(
                f"attempts against task(s) not in the corpus: {', '.join(unknown)}",
                remediation=(
                    "Add them to the corpus before running, or drop them. A corpus that "
                    "grows as results arrive is a corpus selected by its results."
                ),
            )
        if not self.locked_digest:
            self.locked_digest = self.registration.digest()
        elif self.locked_digest != self.registration.digest():
            raise ExperimentError(
                "the registration changed after the first trial",
                remediation=(
                    "Record it as an amendment instead. A protocol edited after results "
                    "start arriving is a results section written in advance."
                ),
            )
        self.attempts = (*self.attempts, *attempts)

    def amend(self, reason: str, registration: Registration) -> None:
        """Change the protocol after trials have started, visibly.

        Allowed, because a protocol that cannot be corrected gets worked around instead. The
        cost is that the change is dated, reasoned and reported alongside the result.
        """
        if not reason.strip():
            raise ExperimentError(
                "an amendment needs a reason",
                remediation="Say what changed and why. An undated silent edit is the thing "
                "this whole mechanism exists to prevent.",
            )
        previous = self.locked_digest or self.registration.digest()
        self.amendments = (
            *self.amendments,
            Amendment(
                at=utc_now(),
                reason=reason.strip(),
                previous_digest=previous,
                new_digest=registration.digest(),
            ),
        )
        self.registration = registration
        self.locked_digest = registration.digest()

    # ------------------------------------------------------------------ analysis

    def evaluate(self) -> Result:
        """Judge the experiment against its own registration.

        The order matters. Power and corpus adequacy are checked *first*, so an underpowered
        run cannot reach the criteria at all -- there is no path through this function that
        reports a verdict from a corpus too small to produce one.
        """
        primary_analysis = [a for a in self.attempts if a.snapshot_isolated]
        excluded: list[str] = []
        dropped = len(self.attempts) - len(primary_analysis)
        if dropped:
            # §11.2: "any condition that cannot be snapshot-isolated is excluded from the
            # primary analysis". Excluded loudly, because without isolation the precedent
            # sections replay the known resolution and the experiment measures retrieval.
            excluded.append(f"{dropped} attempt(s) were not snapshot-isolated")

        blocked = self._adequacy(primary_analysis)
        if blocked is not None:
            return Result(
                verdict=Verdict.INSUFFICIENT_DATA,
                reason=blocked,
                contamination_suspect=sum(1 for t in self.tasks if t.contamination_suspect),
                excluded=tuple(excluded),
                tasks=len(self.tasks),
                registration_digest=self.locked_digest or self.registration.digest(),
            )

        rates = self._per_task_rates(primary_analysis)
        primaries = [
            self._ac1(rates),
            self._ac2(primary_analysis),
            self._ac3(rates),
            *self._ac4(primary_analysis, rates),
            self._ac5(primary_analysis),
        ]
        _holm(
            primaries,
            alpha=self.registration.alpha,
            minimum_effect=self.registration.minimum_effect,
        )
        secondaries = [self._ac6(primary_analysis), self._ac7(primary_analysis)]
        criteria = tuple(primaries + secondaries)

        # Falsification is decisive; support is conjunctive. Checked in this order because
        # the reverse lets a harness that failed every task hide behind an uncomputable
        # criterion: with C passing nothing, cost per passing task has no value, and the
        # first version of this reported INSUFFICIENT_DATA for a result that had already
        # failed AC-1 outright. A criterion nobody could evaluate is not a reason to
        # withhold a failure that another criterion established.
        failed_early = [c for c in primaries if c.met is False]
        unevaluable = [c for c in primaries if c.met is None]
        if unevaluable and not failed_early:
            return Result(
                verdict=Verdict.INSUFFICIENT_DATA,
                reason=(
                    "primary criteria could not be evaluated: "
                    + ", ".join(f"{c.id} ({c.detail})" for c in unevaluable)
                ),
                criteria=criteria,
                contamination_suspect=sum(1 for t in self.tasks if t.contamination_suspect),
                excluded=tuple(excluded),
                tasks=len(self.tasks),
                registration_digest=self.locked_digest or self.registration.digest(),
            )

        failed = [c for c in primaries if c.met is False]
        # Only ablations whose *effect* was below the registered minimum. An ablation that
        # showed a large effect but missed Holm-corrected significance failed AC-4 -- so the
        # experiment is still falsified -- but it has not shown the subsystem is useless,
        # and AC-4's consequence is mandatory removal. Naming it here would delete a working
        # subsystem on an underpowered corpus.
        must_remove = tuple(
            c.id.removeprefix("AC-4:")
            for c in failed
            if c.id.startswith("AC-4:") and c.effect_reached is False
        )
        return Result(
            verdict=Verdict.FALSIFIED if failed else Verdict.SUPPORTED,
            reason=(
                "every primary criterion held"
                if not failed
                else "failed: " + ", ".join(c.id for c in failed)
            ),
            criteria=criteria,
            must_remove=must_remove,
            contamination_suspect=sum(1 for t in self.tasks if t.contamination_suspect),
            excluded=tuple(excluded),
            tasks=len(self.tasks),
            registration_digest=self.locked_digest or self.registration.digest(),
        )

    # ------------------------------------------------------------------ adequacy

    def _adequacy(self, attempts: list[Attempt]) -> str | None:
        """Why the experiment cannot speak yet, or `None` if it can."""
        if not attempts:
            return "no trials recorded"
        scored = {task.id for task in self.tasks if not task.held_out}
        if len(scored) < self.registration.required_tasks:
            return (
                f"{len(scored)} scoreable task(s); the registration requires "
                f"{self.registration.required_tasks} to detect a "
                f"{self.registration.minimum_effect:.0%} effect"
            )
        repositories = {task.repository for task in self.tasks}
        if len(repositories) < self.registration.required_repositories:
            return (
                f"{len(repositories)} repositor(ies); the registration requires "
                f"{self.registration.required_repositories}"
            )
        classes = {task.task_class for task in self.tasks}
        if len(classes) < self.registration.required_task_classes:
            return (
                f"{len(classes)} task class(es); the registration requires "
                f"{self.registration.required_task_classes}"
            )
        budgets = self._budgets(attempts)
        if len(set(budgets.values())) > 1:
            # A confound, not a treatment. Refused rather than footnoted.
            shape = ", ".join(f"{c}={n}" for c, n in sorted(budgets.items()))
            return f"conditions had unequal attempt budgets ({shape})"
        return None

    def _budgets(self, attempts: list[Attempt]) -> dict[str, int]:
        """Attempts per task, per analysis arm. Equal across arms or the run is void.

        Keyed on the *analysis* key, not the raw condition. Every ablation is condition `E`,
        so keying on the condition made five ablation arms look like five repeated attempts
        at one -- and any experiment with more than one ablation was voided as having
        unequal budgets before it could produce a single criterion. Each ablation is its own
        arm, and an arm given fewer attempts than C would make its subsystem look
        indispensable for reasons of budget rather than of contribution.
        """
        counts: dict[tuple[str, str], int] = {}
        for attempt in attempts:
            key = (_key(attempt), attempt.task_id)
            counts[key] = counts.get(key, 0) + 1
        per_condition: dict[str, int] = {}
        for (condition, _), count in counts.items():
            per_condition[condition] = max(per_condition.get(condition, 0), count)
        return per_condition

    # ------------------------------------------------------------------ criteria

    def _per_task_rates(self, attempts: list[Attempt]) -> dict[str, dict[str, float]]:
        """Pass rate per condition per task.

        The aggregation that makes repetitions honest. Ten runs of one task are one task's
        worth of evidence, and comparing raw trial counts treats them as ten.
        """
        buckets: dict[str, dict[str, list[bool]]] = {}
        for attempt in attempts:
            key = _key(attempt)
            buckets.setdefault(key, {}).setdefault(attempt.task_id, []).append(attempt.passed)
        return {
            condition: {task: sum(v) / len(v) for task, v in tasks.items()}
            for condition, tasks in buckets.items()
        }

    def _compare(
        self,
        rates: dict[str, dict[str, float]],
        treatment: str,
        control: str,
        criterion_id: str,
        statement: str,
    ) -> Criterion:
        left = rates.get(treatment, {})
        right = rates.get(control, {})
        shared = sorted(set(left) & set(right))
        if not shared:
            return Criterion(
                id=criterion_id,
                statement=statement,
                primary=True,
                met=None,
                detail=f"no task was attempted by both {treatment} and {control}",
            )
        differences = [left[task] - right[task] for task in shared]
        effect = mean(differences)
        p = _paired_permutation(differences)
        return Criterion(
            id=criterion_id,
            statement=statement,
            primary=True,
            met=None,  # Holm decides, once every primary's p-value is known.
            detail=f"{effect:+.1%} over {len(shared)} shared task(s)",
            p_value=p,
            effect=effect,
        )

    def _ac1(self, rates: dict[str, dict[str, float]]) -> Criterion:
        return self._compare(
            rates, "C", "A", "AC-1", "C's pass rate exceeds A's by the registered effect"
        )

    def _ac3(self, rates: dict[str, dict[str, float]]) -> Criterion:
        return self._compare(
            rates,
            "C",
            "B",
            "AC-3",
            "C's pass rate exceeds B's -- the harness, not the tier, is doing the work",
        )

    def _ac2(self, attempts: list[Attempt]) -> Criterion:
        """Cost per *passing* task, which is the only cost comparison that is not gameable.

        Cost per attempt rewards a condition that fails quickly and cheaply. The PRD says
        "fully-loaded", so this counts every attempt's cost against the tasks that passed.
        """
        c_cost, c_passes = _cost_and_passes(attempts, "C")
        a_cost, a_passes = _cost_and_passes(attempts, "A")
        if not a_passes:
            # Genuinely undefined: with no baseline passes there is nothing to be below.
            return Criterion(
                id="AC-2",
                statement="C's fully-loaded cost per passing task is below A's",
                primary=True,
                met=None,
                detail="A passed nothing, so there is no baseline cost per passing task",
            )
        if not c_passes:
            # Not undefined -- unbounded. A harness that spends and never passes has an
            # infinite cost per passing task, and calling that "could not be evaluated"
            # would be the single most flattering reading available.
            return Criterion(
                id="AC-2",
                statement="C's fully-loaded cost per passing task is below A's",
                primary=True,
                met=False,
                detail=f"C passed nothing while spending {c_cost:.2f}",
            )
        c_rate = c_cost / c_passes
        a_rate = a_cost / a_passes
        return Criterion(
            id="AC-2",
            statement="C's fully-loaded cost per passing task is below A's",
            primary=True,
            met=c_rate < a_rate,
            detail=f"C {c_rate:.2f} vs A {a_rate:.2f} per passing task",
            effect=a_rate - c_rate,
        )

    def _ac4(self, attempts: list[Attempt], rates: dict[str, dict[str, float]]) -> list[Criterion]:
        """One criterion per ablated subsystem, because they fail independently.

        A single AC-4 would let one subsystem earning its place cover for four that do not.
        Each ablation that fails names the subsystem, and the result carries it in
        `must_remove`.
        """
        ablated = sorted({a.ablation for a in attempts if a.ablation})
        if not ablated:
            return [
                Criterion(
                    id="AC-4",
                    statement="each ablation reduces C's pass rate by the registered effect",
                    primary=True,
                    met=None,
                    detail="no ablation attempts were recorded",
                )
            ]
        unknown = sorted(set(ablated) - set(ABLATABLE))
        if unknown:
            raise ExperimentError(
                f"ablation of unknown subsystem(s): {', '.join(unknown)}",
                remediation=f"Ablate one of: {', '.join(ABLATABLE)}.",
            )
        return [
            self._compare(
                rates,
                "C",
                f"E:{subsystem}",
                f"AC-4:{subsystem}",
                f"removing {subsystem} reduces C's pass rate by the registered effect",
            )
            for subsystem in ablated
        ]

    def _ac5(self, attempts: list[Attempt]) -> Criterion:
        """Calibration error: the gap between stated confidence and what happened.

        Failing this means the calibration machinery produces confident wrongness, which the
        PRD calls worse than no calibration at all -- so it is a primary, not a nicety.
        """
        c_error = _calibration_error(attempts, "C")
        a_error = _calibration_error(attempts, "A")
        if c_error is None or a_error is None:
            return Criterion(
                id="AC-5",
                statement="C's calibration error is no worse than A's",
                primary=True,
                met=None,
                detail="attempts did not carry stated confidence",
            )
        return Criterion(
            id="AC-5",
            statement="C's calibration error is no worse than A's",
            primary=True,
            met=c_error <= a_error,
            detail=f"C {c_error:.3f} vs A {a_error:.3f} mean absolute error",
            effect=a_error - c_error,
        )

    def _ac6(self, attempts: list[Attempt]) -> Criterion:
        held_out = {task.id for task in self.tasks if task.held_out}
        sealed = [a for a in attempts if a.task_id in held_out]
        if not sealed:
            return Criterion(
                id="AC-6",
                statement="C's advantage holds on the sealed held-out third",
                primary=False,
                met=None,
                detail="the held-out set has not been opened",
            )
        rates = self._per_task_rates(sealed)
        compared = self._compare(rates, "C", "A", "AC-6", "C's advantage holds on the held-out set")
        return Criterion(
            id="AC-6",
            statement="C's advantage holds on the sealed held-out third",
            primary=False,
            met=(compared.effect or 0.0) >= self.registration.minimum_effect,
            detail=compared.detail,
            effect=compared.effect,
        )

    def _ac7(self, attempts: list[Attempt]) -> Criterion:
        c_review = [a.review_minutes for a in attempts if _key(a) == "C" and a.passed]
        a_review = [a.review_minutes for a in attempts if _key(a) == "A" and a.passed]
        if not c_review or not a_review or not any(c_review + a_review):
            return Criterion(
                id="AC-7",
                statement="C's human review cost per accepted change is no worse than A's",
                primary=False,
                met=None,
                detail="review time was not recorded",
            )
        return Criterion(
            id="AC-7",
            statement="C's human review cost per accepted change is no worse than A's",
            primary=False,
            met=mean(c_review) <= mean(a_review),
            detail=f"C {mean(c_review):.1f} min vs A {mean(a_review):.1f} min per accepted change",
        )


# --------------------------------------------------------------------------- statistics


def _holm(criteria: list[Criterion], *, alpha: float, minimum_effect: float) -> None:
    """Decide the p-value criteria together, Holm-corrected, in place.

    Five comparisons at alpha 0.05 have a one-in-four chance of producing a spurious
    "significant" result, and this experiment exists precisely to resist a flattering one.
    Criteria already decided without a p-value (AC-2, AC-5) are left alone.

    A criterion needs *both* significance and an effect at least the registered size. A
    statistically detectable one-point improvement is not the claim the project made, and
    with a large enough corpus every difference becomes significant.

    `minimum_effect` is passed in rather than read from a module default. The first version
    of this function took it from a global that nothing set, so a registration declaring a
    twenty-point threshold was silently judged against ten -- a control that existed and was
    not wired in, which is a defect this codebase has found in itself three times now.
    """
    tested = [c for c in criteria if c.p_value is not None]
    ordered = sorted(tested, key=lambda c: c.p_value or 1.0)
    count = len(ordered)
    for index, criterion in enumerate(ordered):
        adjusted = alpha / (count - index)
        reached = (criterion.effect or 0.0) >= minimum_effect
        significant = (criterion.p_value or 1.0) <= adjusted
        replacement = Criterion(
            id=criterion.id,
            statement=criterion.statement,
            primary=criterion.primary,
            met=significant and reached,
            detail=(
                criterion.detail
                if significant or not reached
                else f"{criterion.detail}; not significant at {adjusted:.4f}"
            ),
            p_value=criterion.p_value,
            adjusted_alpha=adjusted,
            effect=criterion.effect,
            effect_reached=reached,
        )
        criteria[criteria.index(criterion)] = replacement


def _paired_permutation(differences: list[float]) -> float:
    """Two-sided p-value for "the mean paired difference is zero", by sign-flipping.

    Distribution-free and paired, which matches the design: each task is compared against
    itself under two conditions, and nothing here assumes the differences are normal. A
    t-test would assume it, and pass rates over a stratified corpus are not.
    """
    if not differences:
        return 1.0
    observed = abs(mean(differences))
    if observed == 0:
        return 1.0
    rng = random.Random(PERMUTATION_SEED)
    extreme = 0
    for _ in range(PERMUTATIONS):
        flipped = [d if rng.random() < 0.5 else -d for d in differences]
        if abs(mean(flipped)) >= observed:
            extreme += 1
    # Add-one, so a p-value is never exactly zero: no finite number of permutations can
    # establish that a difference is impossible under the null.
    return (extreme + 1) / (PERMUTATIONS + 1)


def _key(attempt: Attempt) -> str:
    """A condition's analysis key: `E` splits by which subsystem was removed."""
    if attempt.condition is Condition.E_ABLATION and attempt.ablation:
        return f"E:{attempt.ablation}"
    return attempt.condition.value


def _cost_and_passes(attempts: list[Attempt], condition: str) -> tuple[float, int]:
    group = [a for a in attempts if _key(a) == condition]
    return sum(a.cost for a in group), sum(1 for a in group if a.passed)


def _calibration_error(attempts: list[Attempt], condition: str) -> float | None:
    scored = [a for a in attempts if _key(a) == condition and a.stated_confidence is not None]
    if not scored:
        return None
    return mean(abs((a.stated_confidence or 0.0) - (1.0 if a.passed else 0.0)) for a in scored)


# ------------------------------------------------------------------------- persistence


def load(path: Path) -> Experiment:
    """Read an experiment from disk, refusing a file that cannot be trusted."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentError(
            f"{path} is not a readable experiment",
            remediation="Re-register it, or restore the file from version control.",
        ) from exc
    registration = Registration(
        hypothesis=str(raw["registration"]["hypothesis"]),
        minimum_effect=float(raw["registration"]["minimumEffect"]),
        alpha=float(raw["registration"].get("alpha", 0.05)),
        required_tasks=int(raw["registration"].get("requiredTasks", 120)),
        required_repositories=int(raw["registration"].get("requiredRepositories", 5)),
        required_task_classes=int(raw["registration"].get("requiredTaskClasses", 4)),
        attempt_budget=int(raw["registration"].get("attemptBudget", 1)),
        repair_budget=int(raw["registration"].get("repairBudget", 1)),
        registered_at=datetime.fromisoformat(raw["registration"]["registeredAt"]),
    )
    return Experiment(
        registration=registration,
        tasks=tuple(
            Task(
                id=str(t["id"]),
                repository=str(t["repository"]),
                task_class=str(t["taskClass"]),
                difficulty=float(t["difficulty"]),
                parent_commit=str(t["parentCommit"]),
                held_out=bool(t.get("heldOut", False)),
                contamination_suspect=bool(t.get("contaminationSuspect", False)),
            )
            for t in raw.get("tasks", [])
        ),
        attempts=tuple(
            Attempt(
                task_id=str(a["task"]),
                condition=Condition(a["condition"]),
                passed=bool(a["passed"]),
                cost=float(a.get("cost", 0.0)),
                review_minutes=float(a.get("reviewMinutes", 0.0)),
                stated_confidence=(
                    None if a.get("statedConfidence") is None else float(a["statedConfidence"])
                ),
                snapshot_isolated=bool(a.get("snapshotIsolated", True)),
                ablation=str(a.get("ablation", "")),
            )
            for a in raw.get("attempts", [])
        ),
        amendments=tuple(
            Amendment(
                at=datetime.fromisoformat(m["at"]),
                reason=str(m["reason"]),
                previous_digest=str(m["previousDigest"]),
                new_digest=str(m["newDigest"]),
            )
            for m in raw.get("amendments", [])
        ),
        locked_digest=str(raw.get("lockedDigest", "")),
    )


def save(experiment: Experiment, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "registration": experiment.registration.as_dict(),
                "lockedDigest": experiment.locked_digest,
                "tasks": [t.as_dict() for t in experiment.tasks],
                "attempts": [a.as_dict() for a in experiment.attempts],
                "amendments": [m.as_dict() for m in experiment.amendments],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------- routing proposals


@dataclass(frozen=True, slots=True)
class RoutingProposal:
    """A tier recommendation for one task class, and the evidence behind it.

    A proposal rather than a change. The experiment measures; an operator decides. A
    benchmark that silently rewrote the ladder would make the next benchmark a comparison
    against a configuration nobody chose.
    """

    task_class: str
    tier: str
    reason: str
    tasks: int
    confidence: str
    """`available` or `insufficient_data`. Never a number pretending to be a probability."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "taskClass": self.task_class,
            "tier": self.tier,
            "reason": self.reason,
            "tasks": self.tasks,
            "confidence": self.confidence,
        }


#: Below this many tasks in a class, a per-class recommendation is noise. The whole-corpus
#: requirement in the registration governs the *experiment*; this governs each slice of it,
#: because a corpus of 120 split five ways is five corpora of 24.
MIN_TASKS_PER_CLASS = 10


def routing_proposals(experiment: Experiment) -> list[RoutingProposal]:
    """What the measured results say about which tier each task class should use.

    Per task class, because that is the granularity a ladder can act on and the granularity
    at which the answer actually differs: a small model that handles defect fixes well may
    be hopeless at a refactor spanning nine files.

    Every class reports something. A class with too few tasks reports `insufficient_data`
    and keeps its current tier, rather than being left out of the list -- a missing row
    reads as "no opinion", and an operator scanning for classes to move would skip it.
    """
    isolated = [a for a in experiment.attempts if a.snapshot_isolated]
    by_class: dict[str, list[Task]] = {}
    for task in experiment.tasks:
        if not task.held_out:
            by_class.setdefault(task.task_class, []).append(task)

    proposals: list[RoutingProposal] = []
    for task_class, tasks in sorted(by_class.items()):
        ids = {task.id for task in tasks}
        relevant = [a for a in isolated if a.task_id in ids]
        small = _rate(relevant, "C")
        large = _rate(relevant, "D")
        if len(tasks) < MIN_TASKS_PER_CLASS or small is None or large is None:
            proposals.append(
                RoutingProposal(
                    task_class=task_class,
                    tier="unchanged",
                    reason=(
                        f"{len(tasks)} task(s) and "
                        f"{'both' if small is not None and large is not None else 'not both'} "
                        f"tiers measured; {MIN_TASKS_PER_CLASS} tasks on both are needed"
                    ),
                    tasks=len(tasks),
                    confidence="insufficient_data",
                )
            )
            continue
        margin = large - small
        if margin >= experiment.registration.minimum_effect:
            proposals.append(
                RoutingProposal(
                    task_class=task_class,
                    tier="large",
                    reason=f"the large tier passes {margin:+.0%} more of this class",
                    tasks=len(tasks),
                    confidence="available",
                )
            )
        else:
            # The default direction, and the project's whole point: absent evidence that the
            # larger tier earns its cost on this class, the smaller one runs it.
            proposals.append(
                RoutingProposal(
                    task_class=task_class,
                    tier="small",
                    reason=(
                        f"the large tier adds only {margin:+.0%}, under the registered "
                        f"{experiment.registration.minimum_effect:.0%}"
                    ),
                    tasks=len(tasks),
                    confidence="available",
                )
            )
    return proposals


def _rate(attempts: list[Attempt], condition: str) -> float | None:
    """Pass rate for one arm, aggregated per task first. `None` when the arm did not run."""
    per_task: dict[str, list[bool]] = {}
    for attempt in attempts:
        if _key(attempt) == condition:
            per_task.setdefault(attempt.task_id, []).append(attempt.passed)
    if not per_task:
        return None
    return mean(sum(v) / len(v) for v in per_task.values())
