"""Skill lifecycle: selection quality, and five operations that all require evidence.

The interesting assertions here are the refusals. Any registry can add a skill; the
question is whether it can decline to promote one and can propose removing one.
"""

from __future__ import annotations

import pytest

from software_factory.definition.models import AgentRole, SkillStatus, Stage
from software_factory.skills import (
    Operation,
    Proposal,
    Refusal,
    SkillMetrics,
    SkillRecord,
    SkillRegistry,
)

VALIDATION_DESC = (
    "Use before finishing a code change to run the repository lint, typecheck and test "
    "commands and attach structured results. Not for documentation-only changes."
)
MIGRATION_DESC = (
    "Use when planning a database schema migration to produce a reversible plan with a "
    "rollback path. Not for application-level data backfills."
)


def record(
    name: str = "repo-validation",
    *,
    description: str = VALIDATION_DESC,
    body: str = "Run the formatter, then the type checker, then the tests.",
    status: SkillStatus = SkillStatus.ACTIVE,
    metrics: SkillMetrics | None = None,
    **kwargs,
) -> SkillRecord:
    return SkillRecord(
        name=name,
        description=description,
        body=body,
        status=status,
        metrics=metrics or SkillMetrics(),
        **kwargs,
    )


def registry(*records: SkillRecord) -> SkillRegistry:
    return SkillRegistry(list(records))


# ------------------------------------------------------------------------- selection


def test_the_offer_is_bounded() -> None:
    """Past a small number, more options degrade selection rather than improving it."""
    reg = registry(
        *[
            record(
                f"skill-{index}", description=f"Use when handling case {index} of import parsing."
            )
            for index in range(20)
        ]
    )

    offer = reg.offer(
        role=AgentRole.BUILDER, stage=Stage.BUILD, surfaces=set(), task="import parsing"
    )

    assert len(offer.offered) <= 7


def test_the_offer_is_ranked_by_score_not_by_name() -> None:
    """Alphabetical ordering is a silent bias toward whoever named their skill first."""
    helpful = record(
        "zzz-relevant",
        description="Use when parsing CSV import headers to normalise their encoding.",
        metrics=SkillMetrics(loaded=10, helped=9),
    )
    unhelpful = record(
        "aaa-irrelevant",
        description="Use when rotating deployment credentials on a schedule.",
        metrics=SkillMetrics(loaded=10, helped=1),
    )

    offer = registry(helpful, unhelpful).offer(
        role=AgentRole.BUILDER, stage=Stage.BUILD, surfaces=set(), task="csv import header encoding"
    )

    assert offer.offered[0] == "zzz-relevant"


def test_retired_skills_are_never_offered() -> None:
    reg = registry(record(status=SkillStatus.RETIRED))

    offer = reg.offer(role=AgentRole.BUILDER, stage=Stage.BUILD, surfaces=set(), task="anything")

    assert offer.offered == ()
    assert "repo-validation" in offer.excluded


def test_draft_skills_are_never_offered() -> None:
    reg = registry(record(status=SkillStatus.DRAFT))

    offer = reg.offer(role=AgentRole.BUILDER, stage=Stage.BUILD, surfaces=set(), task="anything")

    assert offer.offered == ()


def test_trial_skills_are_offered_only_when_sampled() -> None:
    reg = registry(record(status=SkillStatus.TRIAL))

    unsampled = reg.offer(
        role=AgentRole.BUILDER, stage=Stage.BUILD, surfaces=set(), task="repository validation"
    )
    sampled = reg.offer(
        role=AgentRole.BUILDER,
        stage=Stage.BUILD,
        surfaces=set(),
        task="repository validation",
        sampled=frozenset({"repo-validation"}),
    )

    assert unsampled.offered == ()
    assert sampled.offered == ("repo-validation",)


def test_role_and_stage_scoping_excludes_with_a_reason() -> None:
    reg = registry(record(roles=(AgentRole.CRITIC,), stages=(Stage.REVIEW,)))

    offer = reg.offer(
        role=AgentRole.BUILDER, stage=Stage.BUILD, surfaces=set(), task="repository validation"
    )

    assert offer.offered == ()
    assert "role" in offer.excluded["repo-validation"]


def test_surface_scoping_matches_by_prefix() -> None:
    reg = registry(record(surfaces=("src/importers",)))

    matched = reg.offer(
        role=AgentRole.BUILDER,
        stage=Stage.BUILD,
        surfaces={"src/importers/csv.py"},
        task="repository validation",
    )
    unmatched = reg.offer(
        role=AgentRole.BUILDER,
        stage=Stage.BUILD,
        surfaces={"src/reports/render.py"},
        task="repository validation",
    )

    assert matched.offered == ("repo-validation",)
    assert unmatched.offered == ()


def test_deprecated_skills_are_still_offered_but_down_weighted() -> None:
    """A deprecation must not break in-flight work."""
    deprecated = record("old-validation", status=SkillStatus.DEPRECATED, superseded_by="new")
    reg = registry(deprecated)

    offer = reg.offer(
        role=AgentRole.BUILDER, stage=Stage.BUILD, surfaces=set(), task="repository validation"
    )

    assert offer.offered == ("old-validation",)
    assert offer.scores["old-validation"] < 0.5


# ------------------------------------------------------------------------- promotion


def test_promotion_requires_measured_lift() -> None:
    reg = registry(
        record(
            status=SkillStatus.TRIAL,
            metrics=SkillMetrics(
                eligible_runs=50, loaded=40, helped=30, eval_pass_rate=0.72, baseline_pass_rate=0.70
            ),
        )
    )

    result = reg.propose_promotion("repo-validation")

    assert isinstance(result, Refusal)
    assert result.code == "skill.no_lift"


def test_promotion_requires_enough_trials() -> None:
    reg = registry(
        record(
            status=SkillStatus.TRIAL,
            metrics=SkillMetrics(
                eligible_runs=3, loaded=3, helped=3, eval_pass_rate=0.9, baseline_pass_rate=0.5
            ),
        )
    )

    result = reg.propose_promotion("repo-validation")

    assert isinstance(result, Refusal)
    assert result.code == "skill.insufficient_trials"


def test_promotion_requires_precision() -> None:
    """A skill selected when it does not apply is a description problem, and says so."""
    reg = registry(
        record(
            status=SkillStatus.TRIAL,
            metrics=SkillMetrics(
                eligible_runs=50, loaded=40, helped=8, eval_pass_rate=0.9, baseline_pass_rate=0.5
            ),
        )
    )

    result = reg.propose_promotion("repo-validation")

    assert isinstance(result, Refusal)
    assert result.code == "skill.low_precision"
    assert "description" in result.remediation


def test_a_skill_with_evidence_is_proposed_for_promotion() -> None:
    reg = registry(
        record(
            status=SkillStatus.TRIAL,
            evals=("validation-suite",),
            metrics=SkillMetrics(
                eligible_runs=50, loaded=40, helped=32, eval_pass_rate=0.9, baseline_pass_rate=0.6
            ),
        )
    )

    result = reg.propose_promotion("repo-validation")

    assert isinstance(result, Proposal)
    assert result.operation is Operation.PROMOTE
    assert result.evidence == ("validation-suite",)


def test_only_trial_skills_can_be_promoted() -> None:
    reg = registry(record(status=SkillStatus.ACTIVE))

    result = reg.propose_promotion("repo-validation")

    assert isinstance(result, Refusal)
    assert result.code == "skill.wrong_status"


# -------------------------------------------------------------------------- evolution


def test_a_revision_that_regresses_its_own_evals_is_rejected() -> None:
    reg = registry(record())

    result = reg.check_revision("repo-validation", before=0.9, after=0.7)

    assert isinstance(result, Refusal)
    assert result.code == "skill.self_regression"


def test_a_revision_that_regresses_the_standing_benchmark_is_rejected() -> None:
    """A change that helps one skill and hurts the factory is not an improvement."""
    reg = registry(record())

    result = reg.check_revision("repo-validation", before=0.7, after=0.8, benchmark_delta=-0.05)

    assert isinstance(result, Refusal)
    assert result.code == "skill.benchmark_regression"


def test_revision_churn_is_flagged_as_a_probable_split() -> None:
    reg = registry(record(metrics=SkillMetrics(revisions_in_window=3)))

    result = reg.check_revision("repo-validation", before=0.7, after=0.8)

    assert isinstance(result, Refusal)
    assert result.code == "skill.revision_churn"


def test_an_improving_revision_is_accepted() -> None:
    reg = registry(record(evals=("validation-suite",)))

    result = reg.check_revision("repo-validation", before=0.70, after=0.85)

    assert isinstance(result, Proposal)
    assert result.operation is Operation.EVOLVE


# ------------------------------------------------------------------------------ merge


def test_overlapping_skills_produce_a_merge_proposal() -> None:
    shared_body = "Run the formatter, then the type checker, then the repository test suite."
    left = record(
        "validate-a",
        description="Use before finishing a change to run repository validation commands.",
        body=shared_body,
        roles=(AgentRole.BUILDER,),
        stages=(Stage.BUILD,),
    )
    right = record(
        "validate-b",
        description="Use before completing work to execute the repository validation suite.",
        body=shared_body,
        roles=(AgentRole.BUILDER,),
        stages=(Stage.BUILD,),
    )

    proposals = registry(left, right).propose_merges()

    assert len(proposals) == 1
    assert set(proposals[0].skills) == {"validate-a", "validate-b"}
    assert proposals[0].operation is Operation.MERGE


def test_distinct_skills_do_not_produce_a_merge_proposal() -> None:
    left = record("validate", roles=(AgentRole.BUILDER,), stages=(Stage.BUILD,))
    right = record(
        "migrate",
        description=MIGRATION_DESC,
        body="Write the forward migration, then the rollback, then verify both.",
        roles=(AgentRole.ARCHITECT,),
        stages=(Stage.DESIGN,),
    )

    assert registry(left, right).propose_merges() == []


# ------------------------------------------------------------------------------ split


def test_divergent_eval_results_produce_a_split_proposal() -> None:
    reg = registry(record(eval_results_by_class={"python": 0.9, "typescript": 0.2}))

    proposals = reg.propose_splits()

    assert len(proposals) == 1
    assert proposals[0].operation is Operation.SPLIT
    assert len(proposals[0].children) == 2


def test_uniform_eval_results_do_not_produce_a_split() -> None:
    reg = registry(record(eval_results_by_class={"python": 0.85, "typescript": 0.82}))

    assert reg.propose_splits() == []


def test_a_split_that_does_not_improve_selection_is_refused() -> None:
    """If the children collide as much as the parent, the description was the problem."""
    sibling = record("other", description=VALIDATION_DESC.replace("Use", "Apply"))
    reg = registry(record(), sibling)
    parent_collision = reg.collision("repo-validation")

    result = reg.check_split(
        "repo-validation",
        child_collisions={"child-a": parent_collision + 0.05, "child-b": 0.1},
    )

    assert isinstance(result, Refusal)
    assert result.code == "skill.split_does_not_help"


def test_a_split_that_sharpens_selection_is_proposed() -> None:
    sibling = record("other", description=VALIDATION_DESC.replace("Use", "Apply"))
    reg = registry(record(), sibling)

    result = reg.check_split("repo-validation", child_collisions={"child-a": 0.1, "child-b": 0.2})

    assert isinstance(result, Proposal)
    assert result.operation is Operation.SPLIT


# ----------------------------------------------------------------------------- sunset


def test_a_never_selected_skill_is_proposed_for_retirement() -> None:
    reg = registry(record(metrics=SkillMetrics(eligible_runs=500, loaded=0)))

    proposals = reg.propose_sunsets()

    assert len(proposals) == 1
    assert proposals[0].operation is Operation.SUNSET
    assert "never selected" in proposals[0].rationale


def test_a_persistently_failing_skill_is_proposed_for_retirement() -> None:
    reg = registry(record(metrics=SkillMetrics(failing_windows=4)))

    assert reg.propose_sunsets()


def test_a_skill_with_only_orphaned_anchors_is_proposed_for_retirement() -> None:
    reg = registry(record(metrics=SkillMetrics(orphaned_anchors=True)))

    assert reg.propose_sunsets()


def test_a_skill_that_hinders_more_than_it_helps_is_proposed_for_retirement() -> None:
    reg = registry(record(metrics=SkillMetrics(loaded=40, helped=5, hindered=20)))

    proposals = reg.propose_sunsets()

    assert proposals
    assert "costing more than it returns" in proposals[0].rationale


def test_a_healthy_skill_is_not_proposed_for_retirement() -> None:
    reg = registry(
        record(metrics=SkillMetrics(eligible_runs=100, loaded=60, helped=50, hindered=2))
    )

    assert reg.propose_sunsets() == []


def test_sunset_proposals_are_never_applied_by_the_registry() -> None:
    """Retirement is a reviewed change; the registry only ever proposes."""
    reg = registry(record(metrics=SkillMetrics(eligible_runs=500, loaded=0)))

    reg.propose_sunsets()

    assert reg.get("repo-validation").status is SkillStatus.ACTIVE


# --------------------------------------------------------------------- discoverability


def test_a_description_without_a_trigger_is_flagged() -> None:
    reg = registry(record(description="Runs things. Does not run other things."))

    codes = {p.code for p in reg.description_problems("repo-validation")}

    assert "description.no_trigger" in codes


def test_a_description_without_a_boundary_is_flagged() -> None:
    reg = registry(record(description="Use when a code change is finished to run validation."))

    codes = {p.code for p in reg.description_problems("repo-validation")}

    assert "description.no_boundary" in codes


def test_colliding_descriptions_are_flagged_on_both() -> None:
    reg = registry(record("a"), record("b"))

    codes = {p.code for p in reg.description_problems("a")}

    assert "description.collides" in codes


def test_low_precision_blames_the_description_before_the_body() -> None:
    reg = registry(record(metrics=SkillMetrics(loaded=40, helped=5)))

    problems = {p.code: p for p in reg.description_problems("repo-validation")}

    assert "description.imprecise" in problems
    assert "before the body" in problems["description.imprecise"].remediation


def test_a_good_description_passes() -> None:
    reg = registry(record(metrics=SkillMetrics(loaded=40, helped=35)))

    assert reg.description_problems("repo-validation") == []


# ---------------------------------------------------------------------------- metrics


def test_precision_and_recall_are_computed_from_outcomes() -> None:
    metrics = SkillMetrics(loaded=10, helped=6, missed=2)

    assert metrics.precision == pytest.approx(0.6)
    assert metrics.recall == pytest.approx(0.75)


def test_metrics_are_zero_rather_than_undefined_when_unused() -> None:
    metrics = SkillMetrics()

    assert metrics.precision == 0.0
    assert metrics.recall == 0.0
