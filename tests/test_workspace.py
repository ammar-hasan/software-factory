"""Workspaces: more than one factory in one tree (PRD FR-1.5, FR-1.4).

FR-1.5 has always said multiple factories may share a definition tree via a workspace file.
There was no model, no loader, no command and no view. FR-1.4 — a P0 requirement that lint
warn when two factories in the same tree overlap on a repository — was therefore
*unimplementable* rather than unimplemented, which is the harder kind to notice: nobody
finds it by reading a command that only ever sees one factory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from software_factory.cli import EXIT_UNUSABLE, app
from software_factory.definition.workspace import (
    WORKSPACE_FILE,
    load_workspace,
    scaffold_workspace,
    summarise,
)
from software_factory.errors import DefinitionError, Severity
from software_factory.ledger import EntryType, Ledger

runner = CliRunner()


def factory(root: Path, name: str, repo: str) -> Path:
    from software_factory.scaffold import init_factory

    init_factory(root / name, name=name, owner="acme", repo=repo)
    return root / name


def workspace(tmp_path: Path, *members: tuple[str, str]) -> Path:
    for name, repo in members:
        factory(tmp_path, name, repo)
    scaffold_workspace(tmp_path, name="acme", members=[name for name, _ in members])
    return tmp_path


# ------------------------------------------------------------------------- loading


def test_a_workspace_loads_every_factory_it_lists(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "payments-service"), ("identity", "identity-service"))

    loaded = load_workspace(root)

    assert loaded.document.name == "acme"
    assert sorted(f.name for f in loaded.loaded) == ["identity", "payments"]


def test_a_member_that_fails_to_load_is_listed_with_its_reason(tmp_path: Path) -> None:
    """A workspace listing four factories and reporting three hides the broken one, and the
    broken one is the reason to look."""
    root = workspace(tmp_path, ("payments", "payments-service"))
    scaffold_workspace(root, name="acme", members=["payments", "missing"])

    loaded = load_workspace(root)

    assert len(loaded.factories) == 2
    broken = [f for f in loaded.factories if not f.loaded]
    assert len(broken) == 1
    assert broken[0].error
    assert not loaded.report.ok


def test_a_missing_workspace_file_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(DefinitionError) as caught:
        load_workspace(tmp_path)

    assert WORKSPACE_FILE in caught.value.message
    assert caught.value.remediation


def test_an_unsupported_schema_version_is_refused(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "payments-service"))
    (root / WORKSPACE_FILE).write_text(
        'schemaVersion: "99.0"\nname: acme\nfactories:\n  - path: payments\n', encoding="utf-8"
    )

    with pytest.raises(DefinitionError, match="schemaVersion"):
        load_workspace(root)


def test_an_unknown_key_in_the_workspace_file_is_an_error(tmp_path: Path) -> None:
    """A typo must be an error, not a setting that silently does nothing (FR-2.4)."""
    root = workspace(tmp_path, ("payments", "payments-service"))
    text = (root / WORKSPACE_FILE).read_text(encoding="utf-8")
    (root / WORKSPACE_FILE).write_text(text + "factoryes: []\n", encoding="utf-8")

    with pytest.raises(DefinitionError):
        load_workspace(root)


def test_the_same_factory_listed_twice_is_reported(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "payments-service"))
    scaffold_workspace(root, name="acme", members=["payments", "payments"])

    loaded = load_workspace(root)

    assert len(loaded.loaded) == 1
    assert any(i.code == "workspace.duplicate_member" for i in loaded.report.issues)


# ------------------------------------------------------------- the cross-factory rules


def test_two_factories_over_one_repository_are_warned_about(tmp_path: Path) -> None:
    """FR-1.4, and it can only be checked here.

    FR-1.3 says one factory applies one policy. Two policies over one repository means
    whichever intake matches first decides which applied.
    """
    root = workspace(tmp_path, ("payments", "shared-service"), ("identity", "shared-service"))

    loaded = load_workspace(root)

    assert loaded.overlaps() == {"acme/shared-service": ("identity", "payments")}
    overlap = [i for i in loaded.report.issues if i.code == "workspace.repository_overlap"]
    assert len(overlap) == 1
    assert overlap[0].severity is Severity.WARNING


def test_overlap_is_a_warning_so_a_migration_can_pass_through_it(tmp_path: Path) -> None:
    """Legitimate while one factory is being split out of another. A hard error would make
    the safe intermediate state of that migration impossible."""
    root = workspace(tmp_path, ("payments", "shared-service"), ("identity", "shared-service"))

    assert load_workspace(root).report.ok


def test_two_factories_with_one_name_is_an_error(tmp_path: Path) -> None:
    """A name is a factory's identity (FR-1.1); every command taking one would be ambiguous."""
    factory(tmp_path, "a", "one-service")
    factory(tmp_path, "b", "two-service")
    for directory in ("a", "b"):
        path = tmp_path / directory / "factory.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace(f"name: {directory}", "name: payments"),
            encoding="utf-8",
        )
    scaffold_workspace(tmp_path, name="acme", members=["a", "b"])

    loaded = load_workspace(tmp_path)

    assert loaded.duplicate_names() == {"payments": 2}
    assert not loaded.report.ok


def test_an_unloadable_factory_does_not_count_as_overlapping_nothing(tmp_path: Path) -> None:
    """A factory whose definition failed to parse has no knowable repositories, and
    reporting it as clean would answer a question nobody asked."""
    root = workspace(tmp_path, ("payments", "shared-service"))
    scaffold_workspace(root, name="acme", members=["payments", "missing"])

    assert load_workspace(root).overlaps() == {}


# ------------------------------------------------------------------------ summaries


def test_a_factory_with_no_ledger_reports_none_rather_than_zero(tmp_path: Path) -> None:
    """On a comparison table, an absent ledger rendered as 0 reads as a team doing nothing."""
    root = workspace(tmp_path, ("payments", "payments-service"))

    (summary,) = summarise(load_workspace(root))

    assert summary.runs is None
    assert summary.ledger_present is False


def test_run_counts_come_from_each_factorys_own_ledger(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "payments-service"), ("identity", "identity-service"))
    ledger = Ledger(root / "payments" / ".factory" / "ledger.jsonl")
    for index in range(3):
        ledger.append(EntryType.RUN_STARTED, actor="builder", subject=f"r{index}", payload={})
    ledger.append(
        EntryType.WORK_ITEM_TRANSITION, actor="conductor", subject="wi-1", payload={"to": "HANDOFF"}
    )

    by_name = {s.name: s for s in summarise(load_workspace(root))}

    assert by_name["payments"].runs == 3
    assert by_name["payments"].handoffs == 1
    assert by_name["identity"].runs is None


# ------------------------------------------------------------------ the CLI, end to end


def test_sf_workspace_list_shows_every_factory(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "payments-service"), ("identity", "identity-service"))

    result = runner.invoke(app, ["workspace", "list", str(root), "--json"])

    assert result.exit_code == 0, result.output
    names = [f["name"] for f in json.loads(result.stdout)["factories"]]
    assert sorted(names) == ["identity", "payments"]


def test_sf_workspace_validate_reports_the_overlap(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "shared-service"), ("identity", "shared-service"))

    result = runner.invoke(app, ["workspace", "validate", str(root), "--json"])

    body = json.loads(result.stdout)
    assert body["overlaps"] == {"acme/shared-service": ["identity", "payments"]}
    assert result.exit_code == 0, "a warning must not fail the command"


def test_sf_workspace_validate_fails_on_an_unloadable_member(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "payments-service"))
    scaffold_workspace(root, name="acme", members=["payments", "missing"])

    result = runner.invoke(app, ["workspace", "validate", str(root), "--json"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["ok"] is False


def test_sf_workspace_metrics_folds_each_ledger_over_one_window(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "payments-service"), ("identity", "identity-service"))
    ledger = Ledger(root / "payments" / ".factory" / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="r1", payload={})

    result = runner.invoke(app, ["workspace", "metrics", str(root), "--json"])

    assert result.exit_code == 0, result.output
    by_name = {f["factory"]: f for f in json.loads(result.stdout)["factories"]}
    assert by_name["payments"]["runs"] == 1
    assert by_name["identity"]["available"] is False


def test_sf_workspace_init_writes_a_file_that_loads(tmp_path: Path) -> None:
    factory(tmp_path, "payments", "payments-service")

    result = runner.invoke(
        app, ["workspace", "init", str(tmp_path), "--name", "acme", "--factory", "payments"]
    )

    assert result.exit_code == 0, result.output
    assert load_workspace(tmp_path).document.name == "acme"


def test_sf_workspace_init_refuses_to_overwrite(tmp_path: Path) -> None:
    root = workspace(tmp_path, ("payments", "payments-service"))

    result = runner.invoke(
        app, ["workspace", "init", str(root), "--name", "other", "--factory", "payments"]
    )

    assert result.exit_code == EXIT_UNUSABLE
    assert load_workspace(root).document.name == "acme"
