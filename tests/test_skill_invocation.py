"""Invoking a skill, rather than waiting for the registry to select one.

Skills here were only ever *offered* to a run in progress. Being able to invoke one — "run
the triage skill over this backlog" — is a different capability, and the lifecycle
machinery already carries everything it needs: a skill declares its scope, its owners and
its evals. What was missing was an argument schema and an entry point.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from software_factory.cli import app
from software_factory.definition.models import SkillArgument
from software_factory.skills.registry import SkillArgumentError, render

runner = CliRunner()


def spec(description: str = "which file to look at", **over) -> SkillArgument:
    return SkillArgument(description=description, **over)


# ------------------------------------------------------------------- rendering


def test_arguments_are_substituted_into_the_body() -> None:
    body = render(
        "Look at {{path}} and report what you find.",
        {"path": spec()},
        {"path": "src/importers/csv.py"},
    )

    assert body == "Look at src/importers/csv.py and report what you find."


def test_an_unknown_argument_is_refused_rather_than_ignored() -> None:
    """Silently ignoring it means the caller believes they configured something they did not."""
    with pytest.raises(SkillArgumentError, match="does not accept"):
        render("Look at {{path}}.", {"path": spec()}, {"paths": "x"})


def test_a_missing_required_argument_is_refused_before_anything_is_spent() -> None:
    """Left unsubstituted, `{{path}}` reaches the model as an instruction to find a file
    literally called that."""
    with pytest.raises(SkillArgumentError, match="needs path"):
        render("Look at {{path}}.", {"path": spec()}, {})


def test_a_refusal_says_what_the_argument_is_for() -> None:
    with pytest.raises(SkillArgumentError) as caught:
        render("Look at {{path}}.", {"path": spec("the file to inspect")}, {})

    assert "the file to inspect" in caught.value.remediation


def test_a_default_makes_an_argument_optional() -> None:
    body = render(
        "Sample {{fraction}} of runs.",
        {"fraction": SkillArgument(description="how much", required=False, default="0.25")},
        {},
    )

    assert body == "Sample 0.25 of runs."


def test_a_required_argument_may_not_also_have_a_default() -> None:
    """Declaring both says two things at once, and a reader has to guess which the code
    believes."""
    with pytest.raises(ValueError, match="not required"):
        SkillArgument(description="x", required=True, default="y")


def test_braces_that_are_not_declared_arguments_are_left_alone() -> None:
    """A skill body is prose, and prose contains braces."""
    body = render(
        'Emit {{ "ok": true }} when done, for {{path}}.', {"path": spec()}, {"path": "a.py"}
    )

    assert '{{ "ok": true }}' in body
    assert "a.py" in body


def test_a_skill_with_no_arguments_renders_unchanged() -> None:
    """Most skills inform a run and take nothing. Requiring arguments would make the common
    case ceremonial."""
    assert render("Prefer small diffs.", {}, {}) == "Prefer small diffs."


# ------------------------------------------------------------------ the CLI


@pytest.fixture
def factory(tmp_path: Path) -> Path:
    from software_factory.scaffold import init_factory

    root = tmp_path / "f"
    init_factory(root, name="payments", owner="acme", repo="svc")
    directory = root / "skills" / "inspect-file"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        "name: inspect-file\n"
        "description: Inspect one file and report what it does, for triage and review work.\n"
        "arguments:\n"
        "  path:\n"
        "    description: the file to inspect\n"
        "  depth:\n"
        "    description: how deeply to look\n"
        "    required: false\n"
        "    default: shallow\n"
        "---\n\n"
        "Inspect {{path}} at {{depth}} depth and report what it does.\n",
        encoding="utf-8",
    )
    return root


def test_sf_skill_list_shows_which_skills_can_be_invoked(factory: Path) -> None:
    result = runner.invoke(app, ["skill", "list", str(factory), "--json"])

    assert result.exit_code == 0, result.output
    skills = {s["name"]: s for s in json.loads(result.stdout)["skills"]}
    assert skills["inspect-file"]["invocable"] is True
    assert skills["inspect-file"]["arguments"]["path"]["required"] is True
    assert skills["inspect-file"]["arguments"]["depth"]["default"] == "shallow"


def test_sf_skill_render_shows_the_prompt_without_running_anything(factory: Path) -> None:
    """A skill whose rendered prompt nobody can see before paying for a run is a prompt
    debugged by inference."""
    result = runner.invoke(
        app,
        ["skill", "render", "inspect-file", str(factory), "--arg", "path=importer.py", "--json"],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)["body"]
    assert "Inspect importer.py at shallow depth" in body


def test_sf_skill_render_refuses_a_missing_argument(factory: Path) -> None:
    result = runner.invoke(app, ["skill", "render", "inspect-file", str(factory)])

    assert result.exit_code == 2
    assert "path" in result.output


def test_sf_skill_render_names_the_skills_it_knows(factory: Path) -> None:
    result = runner.invoke(app, ["skill", "render", "nonesuch", str(factory)])

    assert result.exit_code == 2
    assert "inspect-file" in result.output


def test_sf_skill_run_refuses_without_a_provider_rather_than_pretending(factory: Path) -> None:
    """The refusal points at `sf skill render`, which is the part that works offline."""
    result = runner.invoke(
        app,
        ["skill", "run", "inspect-file", str(factory), "--arg", "path=a.py", "--json"],
        env={"SF_PROVIDER_ENDPOINT": ""},
    )

    assert result.exit_code == 2
    assert "skill render" in json.loads(result.stdout)["error"]["remediation"]
