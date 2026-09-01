"""The central-bet experiment (PRD §11.2).

The project's premise is that a modest model in an excellent harness beats a frontier model
in a poor one. §11.2 is the protocol for testing that, and the PRD says it is written "to be
able to fail".

The failure mode of an in-house benchmark is not that it produces a wrong number. It is that
it produces a flattering one and no part of the system was ever capable of producing any
other. So most of what is tested here is the machinery's ability to say no: to an edited
registration, to unequal budgets, to a corpus too small to see the effect it claims to
measure, and — the one that matters most — to the experiment's own hypothesis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.errors import FactoryError
from software_factory.evals.experiment import (
    Attempt,
    Condition,
    Experiment,
    Registration,
    Task,
    Verdict,
    load,
    save,
)

#: Twenty tasks, not six. Six is enough to satisfy an adequacy check and not enough for a
#: Holm-corrected paired test to resolve anything: a perfect twenty-point separation over
#: six tasks has p around 0.03, which fails at every corrected threshold. Testing the
#: analysis on a corpus too small for it would have measured the corpus.
CORPUS_SIZE = 20

REGISTRATION = Registration(
    hypothesis="A small model in this harness beats a large model in a stock one.",
    minimum_effect=0.10,
    required_tasks=CORPUS_SIZE,
    required_repositories=2,
    required_task_classes=2,
)


def corpus(count: int = CORPUS_SIZE, *, held_out: int = 0) -> tuple[Task, ...]:
    """A corpus that satisfies the registration above, so tests can exercise the analysis
    rather than tripping the adequacy checks every time."""
    classes = ("defect", "feature", "refactor", "test")
    return tuple(
        Task(
            id=f"t{index}",
            repository=f"repo-{index % 2}",
            task_class=classes[index % 4],
            difficulty=float(index),
            parent_commit=f"c{index}",
            held_out=index >= count - held_out and held_out > 0,
        )
        for index in range(count)
    )


def attempts(
    condition: Condition,
    passes: list[bool],
    *,
    cost: float = 1.0,
    confidence: float | None = None,
    ablation: str = "",
    isolated: bool = True,
    offset: int = 0,
) -> list[Attempt]:
    return [
        Attempt(
            task_id=f"t{index + offset}",
            condition=condition,
            passed=passed,
            cost=cost,
            stated_confidence=confidence,
            ablation=ablation,
            snapshot_isolated=isolated,
        )
        for index, passed in enumerate(passes)
    ]


def experiment(*groups: list[Attempt], tasks: tuple[Task, ...] | None = None) -> Experiment:
    exp = Experiment(registration=REGISTRATION, tasks=tasks or corpus())
    for group in groups:
        exp.record(group)
    return exp


# --------------------------------------------------------------------------------------
# The honest state today
# --------------------------------------------------------------------------------------


def test_an_experiment_with_no_trials_reports_insufficient_data() -> None:
    """The state of this experiment as of today, and the answer the factory must give.

    Not "unsupported", not "pending", and certainly not a default pass. `INSUFFICIENT_DATA`
    says the experiment cannot speak — which is a different thing from speaking against the
    hypothesis, and collapsing them lets an unrun experiment be reported either way
    depending on who writes the summary.
    """
    result = Experiment(registration=REGISTRATION, tasks=corpus()).evaluate()

    assert result.verdict is Verdict.INSUFFICIENT_DATA
    assert result.reason == "no trials recorded"


def test_a_corpus_too_small_to_see_the_effect_cannot_produce_a_verdict() -> None:
    """A report that says "no significant difference" from twelve tasks is reporting the
    corpus size as though it were a finding."""
    small = Registration(hypothesis="h", minimum_effect=0.10, required_tasks=120)
    exp = Experiment(registration=small, tasks=corpus(CORPUS_SIZE))
    exp.record(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE))

    result = exp.evaluate()

    assert result.verdict is Verdict.INSUFFICIENT_DATA
    assert "120" in result.reason


def test_too_few_repositories_cannot_produce_a_verdict() -> None:
    """One repository measures one codebase's conventions, not a harness."""
    single = tuple(
        Task(id=f"t{i}", repository="only", task_class="defect", difficulty=1.0, parent_commit="c")
        for i in range(CORPUS_SIZE)
    )
    exp = Experiment(registration=REGISTRATION, tasks=single)
    exp.record(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE))

    assert "repositor" in exp.evaluate().reason


def test_unequal_attempt_budgets_void_the_run() -> None:
    """The PRD is explicit: any difference in attempts is a confound, not a treatment.

    Refused rather than footnoted. A benchmark that lets the treatment have more attempts
    and mentions it in the notes has measured attempts.
    """
    exp = experiment(
        attempts(Condition.A_BASELINE_LARGE, [True] * CORPUS_SIZE),
        attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE),
        attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE),  # a second attempt at every task
    )

    result = exp.evaluate()

    assert result.verdict is Verdict.INSUFFICIENT_DATA
    assert "unequal attempt budgets" in result.reason


# --------------------------------------------------------------------------------------
# The registration locks
# --------------------------------------------------------------------------------------


def test_the_registration_locks_at_the_first_trial() -> None:
    """Pre-registration that can be edited afterwards is a results section written early."""
    exp = experiment(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE))
    exp.registration = Registration(hypothesis="something easier", minimum_effect=0.01)

    with pytest.raises(FactoryError) as caught:
        exp.record(attempts(Condition.A_BASELINE_LARGE, [False] * CORPUS_SIZE))

    assert "after the first trial" in str(caught.value)


def test_an_amendment_records_what_changed_and_when() -> None:
    """Allowed, because a protocol that cannot be corrected gets worked around instead.

    The cost is that the change is dated, reasoned, and reported alongside the result.
    """
    exp = experiment(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE))
    before = exp.locked_digest

    exp.amend(
        "the oracle accepted a wrong change", Registration(hypothesis="h", minimum_effect=0.2)
    )

    assert len(exp.amendments) == 1
    assert exp.amendments[0].previous_digest == before
    assert exp.amendments[0].new_digest == exp.locked_digest
    assert "oracle" in exp.amendments[0].reason


def test_an_unexplained_amendment_is_refused() -> None:
    exp = experiment(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE))

    with pytest.raises(FactoryError):
        exp.amend("  ", Registration(hypothesis="h", minimum_effect=0.2))


def test_a_corpus_cannot_grow_as_results_arrive() -> None:
    """A corpus selected by its results is the failure this whole protocol resists."""
    exp = Experiment(registration=REGISTRATION, tasks=corpus(CORPUS_SIZE))

    with pytest.raises(FactoryError) as caught:
        exp.record([Attempt(task_id="t99", condition=Condition.C_FACTORY_SMALL, passed=True)])

    assert "t99" in str(caught.value)


def test_a_registration_with_no_hypothesis_is_refused() -> None:
    """It cannot be falsified by anything, which is the point of writing one."""
    with pytest.raises(FactoryError):
        Registration(hypothesis="   ", minimum_effect=0.1)


def test_a_nonsensical_minimum_effect_is_refused() -> None:
    with pytest.raises(FactoryError):
        Registration(hypothesis="h", minimum_effect=10)


# --------------------------------------------------------------------------------------
# It can falsify its own hypothesis
# --------------------------------------------------------------------------------------


def _full(c_passes: list[bool], a_passes: list[bool], b_passes: list[bool]) -> Experiment:
    """Every condition, with ablations that all earn their place and matched confidence.

    Built so the *only* thing under test in each falsification case is the pass pattern
    supplied. A helper that left AC-2 or AC-5 unevaluable would return INSUFFICIENT_DATA and
    look like a falsification test that worked.
    """
    tasks = corpus(CORPUS_SIZE)
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record(attempts(Condition.A_BASELINE_LARGE, a_passes, cost=10.0, confidence=0.5))
    exp.record(attempts(Condition.B_BASELINE_SMALL, b_passes, cost=1.0, confidence=0.5))
    exp.record(attempts(Condition.C_FACTORY_SMALL, c_passes, cost=1.0, confidence=0.5))
    for subsystem in ("awareness", "gates", "skills", "memory", "scaffolding"):
        exp.record(
            attempts(
                Condition.E_ABLATION,
                [False] * CORPUS_SIZE,
                cost=1.0,
                confidence=0.5,
                ablation=subsystem,
            )
        )
    return exp


def test_the_experiment_can_falsify_the_project_premise() -> None:
    """The single most important test in this repository.

    If a harness that does worse than its baseline still comes out `SUPPORTED`, then every
    other number this factory produces about itself is decoration. The premise has to be
    refutable by its own machinery, on data that refutes it.
    """
    exp = _full(
        c_passes=[False] * CORPUS_SIZE,
        a_passes=[True] * CORPUS_SIZE,
        b_passes=[True] * CORPUS_SIZE,
    )

    result = exp.evaluate()

    assert result.verdict is Verdict.FALSIFIED
    assert "AC-1" in result.reason


def test_a_harness_that_only_matches_its_baseline_is_falsified() -> None:
    """Not "inconclusive". The claim was that the harness wins; parity refutes it."""
    result = _full(
        c_passes=[True] * 10 + [False] * 10,
        a_passes=[True] * 10 + [False] * 10,
        b_passes=[True] * 10 + [False] * 10,
    ).evaluate()

    assert result.verdict is Verdict.FALSIFIED


def test_ac3_failing_names_the_tier_not_the_harness() -> None:
    """The PRD: "AC-3 failing means the tier, not the harness, explains the result -- the
    project's premise is wrong." So C beating A while failing to beat B must falsify."""
    result = _full(
        c_passes=[True] * CORPUS_SIZE,
        a_passes=[False] * CORPUS_SIZE,
        b_passes=[True] * CORPUS_SIZE,
    ).evaluate()

    assert result.verdict is Verdict.FALSIFIED
    assert "AC-3" in result.reason


def test_a_costlier_harness_is_falsified_by_ac2() -> None:
    """AC-2 failing means the harness is a cost multiplier, and the local-first case
    weakens accordingly."""
    tasks = corpus(CORPUS_SIZE)
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record(attempts(Condition.A_BASELINE_LARGE, [True] * CORPUS_SIZE, cost=1.0, confidence=0.5))
    exp.record(
        attempts(Condition.B_BASELINE_SMALL, [False] * CORPUS_SIZE, cost=1.0, confidence=0.5)
    )
    exp.record(
        attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE, cost=100.0, confidence=0.5)
    )
    for subsystem in ("awareness", "gates"):
        exp.record(
            attempts(
                Condition.E_ABLATION,
                [False] * CORPUS_SIZE,
                cost=1.0,
                confidence=0.5,
                ablation=subsystem,
            )
        )

    result = exp.evaluate()

    ac2 = next(c for c in result.criteria if c.id == "AC-2")
    assert ac2.met is False
    assert result.verdict is Verdict.FALSIFIED


def test_confident_wrongness_is_falsified_by_ac5() -> None:
    """ "AC-5 failing means the calibration machinery produces confident wrongness, which is
    worse than no calibration at all." So it is a primary, not a nicety."""
    tasks = corpus(CORPUS_SIZE)
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record(
        attempts(Condition.A_BASELINE_LARGE, [False] * CORPUS_SIZE, cost=1.0, confidence=0.1)
    )
    exp.record(
        attempts(Condition.B_BASELINE_SMALL, [False] * CORPUS_SIZE, cost=1.0, confidence=0.5)
    )
    # C fails everything while claiming near-certainty: the worst possible calibration.
    exp.record(
        attempts(Condition.C_FACTORY_SMALL, [False] * CORPUS_SIZE, cost=1.0, confidence=0.99)
    )
    exp.record(
        attempts(
            Condition.E_ABLATION, [False] * CORPUS_SIZE, cost=1.0, confidence=0.5, ablation="gates"
        )
    )

    result = exp.evaluate()

    ac5 = next(c for c in result.criteria if c.id == "AC-5")
    assert ac5.met is False


# --------------------------------------------------------------------------------------
# AC-4: an ablation that does not hurt names a subsystem for removal
# --------------------------------------------------------------------------------------


def test_a_subsystem_that_does_not_earn_its_place_is_named_for_removal() -> None:
    """The PRD says such a subsystem "must be removed, not retained for plausibility".

    A report that only marks the criterion failed leaves the removal to somebody's
    discretion later, which is exactly how a harness accumulates parts nobody can justify.
    """
    tasks = corpus(CORPUS_SIZE)
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record(
        attempts(Condition.A_BASELINE_LARGE, [False] * CORPUS_SIZE, cost=10.0, confidence=0.5)
    )
    exp.record(
        attempts(Condition.B_BASELINE_SMALL, [False] * CORPUS_SIZE, cost=1.0, confidence=0.5)
    )
    exp.record(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE, cost=1.0, confidence=0.5))
    # Removing `gates` costs nothing: the subsystem is not doing any work.
    exp.record(
        attempts(
            Condition.E_ABLATION, [True] * CORPUS_SIZE, cost=1.0, confidence=0.5, ablation="gates"
        )
    )
    # Removing `memory` hurts: it earns its place.
    exp.record(
        attempts(
            Condition.E_ABLATION, [False] * CORPUS_SIZE, cost=1.0, confidence=0.5, ablation="memory"
        )
    )

    result = exp.evaluate()

    assert result.verdict is Verdict.FALSIFIED
    assert result.must_remove == ("gates",)


def test_each_ablation_is_judged_separately() -> None:
    """A single AC-4 would let one subsystem earning its place cover for four that do not."""
    tasks = corpus(CORPUS_SIZE)
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE, cost=1.0, confidence=0.5))
    for subsystem in ("gates", "memory", "skills"):
        exp.record(
            attempts(
                Condition.E_ABLATION,
                [False] * CORPUS_SIZE,
                cost=1.0,
                confidence=0.5,
                ablation=subsystem,
            )
        )

    ids = {c.id for c in exp.evaluate().criteria}

    assert {"AC-4:gates", "AC-4:memory", "AC-4:skills"} <= ids


def test_ablating_something_that_is_not_a_subsystem_is_refused() -> None:
    """So a failing ablation points at something the codebase actually contains."""
    exp = experiment(
        attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE),
        attempts(Condition.E_ABLATION, [False] * CORPUS_SIZE, ablation="vibes"),
    )

    with pytest.raises(FactoryError) as caught:
        exp.evaluate()

    assert "vibes" in str(caught.value)


def test_no_ablations_means_ac4_cannot_be_evaluated() -> None:
    """Not "met". An ablation nobody ran is not evidence that every subsystem earns its
    place — which is the most convenient possible misreading."""
    result = experiment(
        attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE),
        attempts(Condition.A_BASELINE_LARGE, [False] * CORPUS_SIZE),
    ).evaluate()

    assert result.verdict is Verdict.INSUFFICIENT_DATA
    assert "AC-4" in result.reason


# --------------------------------------------------------------------------------------
# Statistics that do not flatter
# --------------------------------------------------------------------------------------


def test_repetitions_on_one_task_are_not_independent_samples() -> None:
    """Ten runs of one task are one task's worth of evidence.

    Aggregating per task before comparing is what stops a corpus of six tasks run ten times
    from looking like sixty independent observations.
    """
    tasks = corpus(CORPUS_SIZE)
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    # Every arm gets three attempts, the ablation included. Repeating only the arms under
    # comparison would itself be the unequal budget the protocol refuses.
    for _ in range(3):
        exp.record(
            attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE, cost=1.0, confidence=0.5)
        )
        exp.record(
            attempts(Condition.A_BASELINE_LARGE, [False] * CORPUS_SIZE, cost=9.0, confidence=0.5)
        )
        exp.record(
            attempts(
                Condition.E_ABLATION,
                [False] * CORPUS_SIZE,
                cost=1.0,
                confidence=0.5,
                ablation="gates",
            )
        )

    result = exp.evaluate()

    # The unit of analysis is the task, so the effect is a per-task rate difference of 1.0,
    # not something inflated by the repetition count.
    ac1 = next(c for c in result.criteria if c.id == "AC-1")
    assert ac1.effect == pytest.approx(1.0)


def test_a_significant_but_tiny_effect_does_not_count() -> None:
    """A statistically detectable one-point improvement is not the claim the project made,
    and with a large enough corpus every difference becomes significant."""
    tasks = tuple(
        Task(
            id=f"t{i}",
            repository=f"r{i % 3}",
            task_class=("defect", "feature", "refactor", "test")[i % 4],
            difficulty=1.0,
            parent_commit="c",
        )
        for i in range(100)
    )
    registration = Registration(
        hypothesis="h",
        minimum_effect=0.10,
        required_tasks=100,
        required_repositories=3,
        required_task_classes=4,
    )
    exp = Experiment(registration=registration, tasks=tasks)
    # C beats A on exactly two tasks out of a hundred: consistent, and meaningless.
    c = [True] * 2 + [False] * 98
    a = [False] * 100
    exp.record([Attempt(f"t{i}", Condition.C_FACTORY_SMALL, p, 1.0) for i, p in enumerate(c)])
    exp.record([Attempt(f"t{i}", Condition.A_BASELINE_LARGE, p, 1.0) for i, p in enumerate(a)])

    ac1 = next(c for c in exp.evaluate().criteria if c.id == "AC-1")

    assert ac1.effect == pytest.approx(0.02)
    assert ac1.met is False


def test_the_registered_minimum_effect_is_the_one_that_is_used() -> None:
    """The Holm correction read a module default that nothing set, so a registration
    declaring a twenty-point threshold was silently judged against ten — a control that
    existed and was not wired in."""
    tasks = corpus(CORPUS_SIZE)
    strict = Registration(
        hypothesis="h",
        minimum_effect=0.90,
        required_tasks=6,
        required_repositories=2,
        required_task_classes=2,
    )
    exp = Experiment(registration=strict, tasks=tasks)
    # A half-point improvement: comfortably over ten points, nowhere near ninety.
    exp.record(
        [
            Attempt(f"t{i}", Condition.C_FACTORY_SMALL, i < CORPUS_SIZE // 2, 1.0)
            for i in range(CORPUS_SIZE)
        ]
    )
    exp.record(
        [Attempt(f"t{i}", Condition.A_BASELINE_LARGE, False, 1.0) for i in range(CORPUS_SIZE)]
    )

    ac1 = next(c for c in exp.evaluate().criteria if c.id == "AC-1")

    assert ac1.effect == pytest.approx(0.5)
    assert ac1.met is False, "a 50-point effect met a 90-point threshold"


def test_a_p_value_is_never_exactly_zero() -> None:
    """No finite number of permutations establishes that a difference is impossible."""
    result = _full(
        c_passes=[True] * CORPUS_SIZE,
        a_passes=[False] * CORPUS_SIZE,
        b_passes=[False] * CORPUS_SIZE,
    ).evaluate()

    for criterion in result.criteria:
        if criterion.p_value is not None:
            assert criterion.p_value > 0


def test_the_same_data_produces_the_same_p_values() -> None:
    """A benchmark whose p-values move between runs of the same data cannot be checked."""
    first = _full([True] * CORPUS_SIZE, [False] * CORPUS_SIZE, [False] * CORPUS_SIZE).evaluate()
    second = _full([True] * CORPUS_SIZE, [False] * CORPUS_SIZE, [False] * CORPUS_SIZE).evaluate()

    assert [c.p_value for c in first.criteria] == [c.p_value for c in second.criteria]


# --------------------------------------------------------------------------------------
# Isolation and contamination
# --------------------------------------------------------------------------------------


def test_attempts_without_snapshot_isolation_are_excluded_and_said_so() -> None:
    """Without isolation the precedent sections replay the known resolution, and the
    experiment measures retrieval rather than capability."""
    exp = Experiment(registration=REGISTRATION, tasks=corpus(CORPUS_SIZE))
    exp.record(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE, isolated=False))

    result = exp.evaluate()

    assert any("snapshot-isolated" in note for note in result.excluded)
    assert result.verdict is Verdict.INSUFFICIENT_DATA


def test_contamination_suspect_tasks_are_counted_separately() -> None:
    """Reported rather than dropped: dropping them silently is a corpus decision made after
    seeing which tasks the treatment did well on."""
    tasks = (
        *corpus(CORPUS_SIZE - 1),
        Task(
            id=f"t{CORPUS_SIZE - 1}",
            repository="repo-1",
            task_class="defect",
            difficulty=1.0,
            parent_commit="c",
            contamination_suspect=True,
        ),
    )
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE))

    assert exp.evaluate().contamination_suspect == 1


def test_the_held_out_third_is_not_used_for_the_primary_analysis() -> None:
    """Sealed means sealed: a held-out task counted toward the corpus requirement would let
    the tuning set be padded with the very tasks it is not allowed to see."""
    exp = Experiment(registration=REGISTRATION, tasks=corpus(CORPUS_SIZE, held_out=10))
    exp.record(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE))

    result = exp.evaluate()

    assert result.verdict is Verdict.INSUFFICIENT_DATA
    assert "scoreable" in result.reason


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------


def test_an_experiment_survives_a_round_trip(tmp_path: Path) -> None:
    exp = _full([True] * CORPUS_SIZE, [False] * CORPUS_SIZE, [False] * CORPUS_SIZE)
    exp.amend("the oracle was weak on t3", REGISTRATION)
    path = tmp_path / "experiment.json"

    save(exp, path)
    loaded = load(path)

    assert loaded.registration.hypothesis == exp.registration.hypothesis
    assert len(loaded.attempts) == len(exp.attempts)
    assert len(loaded.amendments) == 1
    assert loaded.evaluate().verdict is exp.evaluate().verdict


def test_a_corrupt_experiment_file_is_refused_not_treated_as_empty(tmp_path: Path) -> None:
    """An empty experiment reports INSUFFICIENT_DATA, which would read as "not run yet"
    rather than "the results file is damaged"."""
    path = tmp_path / "experiment.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(FactoryError):
        load(path)


def test_the_saved_file_carries_the_locked_digest(tmp_path: Path) -> None:
    """So a reader can check that the published protocol is the one the trials ran under."""
    exp = experiment(attempts(Condition.C_FACTORY_SMALL, [True] * CORPUS_SIZE))
    path = tmp_path / "experiment.json"
    save(exp, path)

    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["lockedDigest"] == exp.registration.digest()


def test_an_underpowered_ablation_is_not_named_for_removal() -> None:
    """AC-4's consequence is mandatory removal, so the list has to be conservative.

    An ablation showing a large effect that misses Holm-corrected significance has failed
    AC-4 — the experiment is still falsified — but it has not shown the subsystem is
    useless. Naming it would delete working subsystems on an underpowered corpus, and the
    first version of this did exactly that: `memory` was demonstrably load-bearing and got
    listed for removal because six tasks cannot resolve a corrected p-value.
    """
    tasks = corpus(6)
    small = Registration(
        hypothesis="h",
        minimum_effect=0.10,
        required_tasks=6,
        required_repositories=2,
        required_task_classes=2,
    )
    exp = Experiment(registration=small, tasks=tasks)
    c = [True, True, True, True, False, False]
    # Removing memory costs one task in six: a 17-point effect, comfortably over the
    # registered 10, and resting on a single task -- which no amount of sign-flipping can
    # distinguish from chance. Effect reached, significance not, subsystem not condemned.
    ablated = [True, True, True, False, False, False]
    exp.record(attempts(Condition.C_FACTORY_SMALL, c, cost=1.0, confidence=0.5))
    exp.record(attempts(Condition.A_BASELINE_LARGE, [False] * 6, cost=9.0, confidence=0.5))
    exp.record(attempts(Condition.E_ABLATION, ablated, cost=1.0, confidence=0.5, ablation="memory"))

    result = exp.evaluate()
    criterion = next(c for c in result.criteria if c.id == "AC-4:memory")

    assert criterion.met is False, "one task cannot reach corrected significance"
    assert criterion.effect_reached is True, "the effect itself cleared the threshold"
    assert "memory" not in result.must_remove


def test_a_harness_that_never_passes_fails_the_cost_criterion() -> None:
    """Unbounded, not undefined. A harness that spends and never passes has an infinite cost
    per passing task, and reporting that as "could not be evaluated" is the single most
    flattering reading available."""
    tasks = corpus()
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record(attempts(Condition.A_BASELINE_LARGE, [True] * CORPUS_SIZE, cost=1.0))
    exp.record(attempts(Condition.C_FACTORY_SMALL, [False] * CORPUS_SIZE, cost=50.0))

    ac2 = next(c for c in exp.evaluate().criteria if c.id == "AC-2")

    assert ac2.met is False


def test_a_failure_is_not_withheld_because_another_criterion_was_uncomputable() -> None:
    """Falsification is decisive; support is conjunctive.

    With C passing nothing, several criteria have no value. The first version checked
    unevaluable criteria first and reported INSUFFICIENT_DATA for a result that had already
    failed AC-1 outright — a harness that failed every task, hiding behind an uncomputable
    cost ratio.
    """
    result = _full(
        c_passes=[False] * CORPUS_SIZE,
        a_passes=[True] * CORPUS_SIZE,
        b_passes=[True] * CORPUS_SIZE,
    ).evaluate()

    assert result.verdict is Verdict.FALSIFIED


# --------------------------------------------------------------------------------------
# Routing proposals: what the measurement is actually for
# --------------------------------------------------------------------------------------


def _by_class(proposals):
    return {p.task_class: p for p in proposals}


def test_a_class_the_small_tier_handles_stays_on_the_small_tier() -> None:
    """The default direction, and the project's whole point: absent evidence that the larger
    tier earns its cost on this class, the smaller one runs it."""
    from software_factory.evals.experiment import routing_proposals

    tasks = tuple(
        Task(
            id=f"t{i}",
            repository=f"r{i % 2}",
            task_class="defect",
            difficulty=1.0,
            parent_commit="c",
        )
        for i in range(12)
    )
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record([Attempt(f"t{i}", Condition.C_FACTORY_SMALL, True) for i in range(12)])
    exp.record([Attempt(f"t{i}", Condition.D_FACTORY_LARGE, True) for i in range(12)])

    proposal = _by_class(routing_proposals(exp))["defect"]

    assert proposal.tier == "small"
    assert proposal.confidence == "available"


def test_a_class_the_small_tier_cannot_handle_is_moved_up() -> None:
    from software_factory.evals.experiment import routing_proposals

    tasks = tuple(
        Task(
            id=f"t{i}",
            repository=f"r{i % 2}",
            task_class="refactor",
            difficulty=1.0,
            parent_commit="c",
        )
        for i in range(12)
    )
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record([Attempt(f"t{i}", Condition.C_FACTORY_SMALL, False) for i in range(12)])
    exp.record([Attempt(f"t{i}", Condition.D_FACTORY_LARGE, True) for i in range(12)])

    proposal = _by_class(routing_proposals(exp))["refactor"]

    assert proposal.tier == "large"
    assert "+100%" in proposal.reason


def test_a_thin_class_reports_insufficient_data_rather_than_vanishing() -> None:
    """A missing row reads as "no opinion", and an operator scanning for classes to move
    would skip it. A corpus of 120 split five ways is five corpora of 24."""
    from software_factory.evals.experiment import routing_proposals

    tasks = tuple(
        Task(id=f"t{i}", repository="r", task_class="chore", difficulty=1.0, parent_commit="c")
        for i in range(3)
    )
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record([Attempt(f"t{i}", Condition.C_FACTORY_SMALL, True) for i in range(3)])
    exp.record([Attempt(f"t{i}", Condition.D_FACTORY_LARGE, True) for i in range(3)])

    proposal = _by_class(routing_proposals(exp))["chore"]

    assert proposal.confidence == "insufficient_data"
    assert proposal.tier == "unchanged"


def test_a_class_with_only_one_tier_measured_proposes_nothing() -> None:
    """Half a comparison is not a comparison, and "the small tier passed everything" says
    nothing about whether the large one would have done better."""
    from software_factory.evals.experiment import routing_proposals

    tasks = tuple(
        Task(
            id=f"t{i}",
            repository=f"r{i % 2}",
            task_class="defect",
            difficulty=1.0,
            parent_commit="c",
        )
        for i in range(12)
    )
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record([Attempt(f"t{i}", Condition.C_FACTORY_SMALL, True) for i in range(12)])

    proposal = _by_class(routing_proposals(exp))["defect"]

    assert proposal.confidence == "insufficient_data"


def test_held_out_tasks_do_not_drive_routing() -> None:
    """Sealed means sealed. Tuning the ladder on the held-out third is exactly the tuning
    the held-out third exists to be protected from."""
    from software_factory.evals.experiment import routing_proposals

    tasks = tuple(
        Task(
            id=f"t{i}",
            repository=f"r{i % 2}",
            task_class="defect",
            difficulty=1.0,
            parent_commit="c",
            held_out=True,
        )
        for i in range(12)
    )
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record([Attempt(f"t{i}", Condition.C_FACTORY_SMALL, False) for i in range(12)])
    exp.record([Attempt(f"t{i}", Condition.D_FACTORY_LARGE, True) for i in range(12)])

    assert routing_proposals(exp) == []


def test_a_proposal_is_a_proposal_and_not_a_change() -> None:
    """A benchmark that silently rewrote the ladder would make the next benchmark a
    comparison against a configuration nobody chose."""
    from software_factory.evals.experiment import RoutingProposal, routing_proposals

    tasks = tuple(
        Task(
            id=f"t{i}",
            repository=f"r{i % 2}",
            task_class="defect",
            difficulty=1.0,
            parent_commit="c",
        )
        for i in range(12)
    )
    exp = Experiment(registration=REGISTRATION, tasks=tasks)
    exp.record([Attempt(f"t{i}", Condition.C_FACTORY_SMALL, False) for i in range(12)])
    exp.record([Attempt(f"t{i}", Condition.D_FACTORY_LARGE, True) for i in range(12)])

    proposals = routing_proposals(exp)

    assert all(isinstance(p, RoutingProposal) for p in proposals)
    assert exp.registration.minimum_effect == REGISTRATION.minimum_effect


# --------------------------------------------------------------------------------------
# This repository's own experiment
# --------------------------------------------------------------------------------------


def test_this_repositorys_central_bet_is_not_claimed_as_proven() -> None:
    """The registered experiment for *this* factory, checked on every test run.

    The project's README, PRD and commit messages all rest on the claim that a modest model
    in a good harness beats a frontier model in a poor one. No trial has been run. This test
    exists so that stays visible: if the shipped experiment ever reports `supported`, it is
    because trials were recorded and the criteria held — not because somebody edited a
    document. And if it ever reports `falsified`, the PRD requires that be published rather
    than quietly reverted.
    """
    from software_factory.evals.experiment import load

    path = Path(__file__).resolve().parent.parent / "docs" / "experiment.json"
    assert path.exists(), "the registered experiment is missing from the repository"

    experiment = load(path)
    result = experiment.evaluate()

    assert experiment.registration.required_tasks >= 120, "PRD 11.2 requires at least 120 tasks"
    assert experiment.registration.minimum_effect >= 0.10
    assert result.verdict is not Verdict.SUPPORTED or experiment.attempts, (
        "the central bet reports as supported with no trials recorded"
    )
    if not experiment.attempts:
        assert result.verdict is Verdict.INSUFFICIENT_DATA
        assert result.reason == "no trials recorded"
