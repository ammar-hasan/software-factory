"""Loading a definition tree: atomicity, cross-file structure, and error locations."""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.definition import load, load_strict
from software_factory.definition.models import AgentRole, SkillStatus
from software_factory.errors import DefinitionError

from .conftest import agent, write

SKILL = """\
---
name: run-repo-validation
description: Run the repository's own lint, typecheck and test commands before calling a change done.
version: 1
status: active
owners: [platform]
reviewBy: "2027-01-01"
evals: [validation-suite]
---

Run `ruff check`, then `mypy`, then `pytest`. Attach the structured results.
"""


def test_loads_a_minimal_factory(factory_root: Path) -> None:
    definition, report = load(factory_root)

    assert report.ok, report.as_dict()
    assert definition.factory.name == "payments"
    assert definition.factory.handle == "payments"
    assert set(definition.agents) == {"conductor"}
    assert definition.conductor() is not None
    assert definition.runners["linux"].definition.instance_shape.vcpus == 4


def test_missing_factory_file_is_actionable(tmp_path: Path) -> None:
    with pytest.raises(DefinitionError, match=r"no factory\.yaml"):
        load(tmp_path)


def test_unsupported_schema_version_lists_what_is_accepted(factory_root: Path) -> None:
    text = (factory_root / "factory.yaml").read_text(encoding="utf-8")
    write(factory_root / "factory.yaml", text.replace("v1alpha1", "v9"))

    with pytest.raises(DefinitionError):
        load(factory_root)


def test_schema_version_error_cites_line_and_accepted_values(factory_root: Path) -> None:
    text = (factory_root / "factory.yaml").read_text(encoding="utf-8")
    write(factory_root / "factory.yaml", text.replace("v1alpha1", "v9"))

    try:
        load(factory_root)
    except DefinitionError as exc:
        issues = exc.detail
        assert isinstance(issues, list)
        unsupported = [i for i in issues if i["code"] == "schema.unsupported"]
        assert unsupported, issues
        assert unsupported[0]["line"] == 1
        assert "v1alpha1" in unsupported[0]["accepted"]
    else:  # pragma: no cover
        pytest.fail("expected a DefinitionError")


def test_execution_keys_are_lifted_from_flat_frontmatter(factory_root: Path) -> None:
    """Authors write `tier:` flat; inheritance needs it nested. The loader bridges them."""
    write(
        factory_root / "agents" / "builder" / "agent.md",
        agent("BUILDER", tier="mid", runner="linux", body="Make the change."),
    )

    definition, report = load(factory_root)

    assert report.ok, report.as_dict()
    assert definition.agents["builder"].definition.execution.tier == "mid"
    assert definition.agents["builder"].definition.execution.runner == "linux"


def test_agent_directory_without_agent_md_is_an_error(factory_root: Path) -> None:
    (factory_root / "agents" / "ghost").mkdir(parents=True)

    _, report = load(factory_root)

    assert not report.ok
    assert any(i.code == "agent.missing_file" for i in report.errors)


def test_factory_wide_and_agent_scoped_skills_are_kept_apart(factory_root: Path) -> None:
    write(factory_root / "skills" / "run-repo-validation" / "SKILL.md", SKILL)
    write(
        factory_root / "agents" / "critic" / "agent.md",
        agent("CRITIC", tier="mid", body="Review independently."),
    )
    write(
        factory_root / "agents" / "critic" / "skills" / "security-checklist" / "SKILL.md",
        SKILL.replace("run-repo-validation", "security-checklist").replace(
            "Run the repository's own lint, typecheck and test commands before calling a change done.",
            "Apply the security review checklist to authentication and authorization changes.",
        ),
    )

    definition, report = load(factory_root)

    assert report.ok, report.as_dict()
    assert set(definition.skills) == {"run-repo-validation"}
    assert definition.skills["run-repo-validation"].scope == "factory"
    critic_skills = {s.name for s in definition.agents["critic"].skills}
    assert critic_skills == {"security-checklist"}
    assert definition.agents["critic"].skills[0].scope == "agent:critic"
    assert {s.name for s in definition.skills_for("critic")} == {
        "run-repo-validation",
        "security-checklist",
    }


def test_skill_name_must_match_its_directory(factory_root: Path) -> None:
    write(factory_root / "skills" / "wrong-dir" / "SKILL.md", SKILL)

    _, report = load(factory_root)

    assert any(i.code == "skill.name_mismatch" for i in report.errors)


def test_scorer_without_a_rubric_is_rejected(factory_root: Path) -> None:
    write(
        factory_root / "scorers" / "tests-run" / "scorer.md",
        """\
        ---
        name: tests-run
        agents: [conductor]
        labels:
          - {value: ran, score: 1}
          - {value: skipped, score: 0}
        passingScore: 1
        judge: {type: oz, model: judge-model}
        ---
        """,
    )

    _, report = load(factory_root)

    assert any(i.code == "scorer.empty_rubric" for i in report.errors)


def test_load_strict_raises_on_a_broken_tree(factory_root: Path) -> None:
    """A definition either loads completely or not at all (FR-2.3)."""
    write(factory_root / "agents" / "second" / "agent.md", agent("CONDUCTOR"))

    with pytest.raises(DefinitionError, match="validation error"):
        load_strict(factory_root)


def test_skill_defaults_to_draft(factory_root: Path) -> None:
    write(
        factory_root / "skills" / "draft-skill" / "SKILL.md",
        """\
        ---
        name: draft-skill
        description: Investigate flaky test failures by correlating reruns across identical commits.
        ---

        Body.
        """,
    )

    definition, report = load(factory_root)

    assert report.ok, report.as_dict()
    assert definition.skills["draft-skill"].definition.status is SkillStatus.DRAFT


def test_conductor_is_discoverable_by_role(factory_root: Path) -> None:
    definition, _ = load(factory_root)
    conductor = definition.conductor()
    assert conductor is not None
    assert conductor.definition.role is AgentRole.CONDUCTOR
