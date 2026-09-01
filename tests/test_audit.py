"""One report across every factory in a workspace.

`sf workspace list` could show what factories exist. Nothing answered the question an
operator running more than one actually has: is any of them in trouble, and are they
drifting apart? The two fail differently — a broken factory is loud once somebody looks, and
drift is silent until two teams have irreconcilable conventions.

The tests are about the ways a report like this quietly misleads: hiding the broken row,
burying a failed hash chain under tidy numbers, ranking factories by age, and flagging
choices as faults until people stop reading it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.definition.workspace import scaffold_workspace
from software_factory.ledger import EntryType, Ledger
from software_factory.observability.audit import (
    MIN_RUNS_TO_COMPARE,
    Severity,
    audit,
    audit_path,
    compare,
    outliers,
    render,
)
from software_factory.scaffold import init_factory


def workspace(tmp_path: Path, names: list[str]) -> Path:
    """A real workspace with real factory definitions on disk."""
    for name in names:
        root = tmp_path / name
        root.mkdir(parents=True)
        init_factory(root, name=name, owner="acme", repo=name)
    scaffold_workspace(tmp_path, name="acme", members=names)
    return tmp_path


def run_entries(root: Path, name: str, *, count: int, blocked: int = 0) -> Ledger:
    """Runs with gate evaluations, because the comparable metrics are all rates over gates.

    Writing only RUN_STARTED/RUN_FINISHED made every rate `insufficient_data`, so the first
    outlier test compared four factories on nothing and found nothing -- a green test over
    an empty comparison.
    """
    ledger = Ledger(root / name / ".factory" / "ledger.jsonl")
    for index in range(count):
        run = f"{name}-run-{index}"
        ledger.append(
            EntryType.RUN_STARTED,
            actor="builder",
            subject=f"wi-{index}",
            payload={
                "run": run,
                "agent": "builder",
                "workItem": f"wi-{index}",
                "stage": "build",
                "purpose": "work",
            },
        )
        failing = index < blocked
        ledger.append(
            EntryType.GATE_EVALUATED,
            actor="builder",
            subject=f"wi-{index}",
            payload={
                "run": run,
                "stage": "build",
                "gate": "regression-proven",
                "outcome": "fail" if failing else "pass",
                "blocks": failing,
                "severity": "blocking" if failing else "advisory",
            },
        )
        ledger.append(
            EntryType.RUN_FINISHED,
            actor="builder",
            subject=f"wi-{index}",
            payload={"run": run, "status": "gate_failed" if failing else "completed"},
        )
    return ledger


# --------------------------------------------------------------------------------------
# The broken row is the first row
# --------------------------------------------------------------------------------------


def test_a_factory_that_does_not_load_is_reported_not_omitted(tmp_path: Path) -> None:
    """A report listing four factories and describing three hides the broken one, and the
    broken one is the reason to look."""
    root = workspace(tmp_path, ["alpha", "beta"])
    (root / "beta" / "factory.yaml").write_text("this: [is not: valid", encoding="utf-8")

    result = audit_path(root)

    broken = result.broken
    assert [f.factories for f in broken] == [("beta",)]
    assert "does not load" in broken[0].summary
    assert result.ok is False


def test_the_broken_factory_still_appears_in_the_table(tmp_path: Path) -> None:
    root = workspace(tmp_path, ["alpha", "beta"])
    (root / "beta" / "factory.yaml").write_text("not: [valid", encoding="utf-8")

    names = {f.name for f in audit_path(root).factories}

    assert names == {"alpha", "beta"}


def test_a_broken_hash_chain_is_the_headline(tmp_path: Path) -> None:
    """Every number reported for a factory is read out of its ledger. If the chain is
    broken, those numbers are not merely uncertain — they were computed from something that
    may have been edited, and a tidy run count above a broken chain is the most dangerous
    shape this report could take.
    """
    root = workspace(tmp_path, ["alpha"])
    run_entries(root, "alpha", count=3)
    path = root / "alpha" / ".factory" / "ledger.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["actor"] = "somebody-else"
    lines[1] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = audit_path(root)

    assert [f.summary for f in result.broken] == ["the ledger's hash chain does not verify"]
    assert "unverified" in result.broken[0].remediation


def test_a_healthy_ledger_is_not_reported_as_broken(tmp_path: Path) -> None:
    """`Ledger.verify()` signals success by returning and failure by raising.

    The first version of this audit did `bool(result)` on its `None` return and reported
    every healthy ledger as broken — a headline finding that fires on every factory in the
    workspace, which is indistinguishable from one that fires on none.
    """
    root = workspace(tmp_path, ["alpha"])
    run_entries(root, "alpha", count=3)

    result = audit_path(root)

    assert result.ok is True
    assert result.factories[0].ledger_verifies is True


def test_no_ledger_is_not_a_broken_ledger(tmp_path: Path) -> None:
    """A factory nobody has run yet and one whose chain is broken are opposite findings,
    and only the second is urgent."""
    root = workspace(tmp_path, ["alpha"])

    health = audit_path(root).factories[0]

    assert health.ledger_verifies is None
    assert health.runs is None


# --------------------------------------------------------------------------------------
# Overlaps and ambiguity
# --------------------------------------------------------------------------------------


def test_two_factories_claiming_one_repository_is_a_warning(tmp_path: Path) -> None:
    """Two factories opening changes on one repository will review each other's work as
    though it came from outside."""
    for name in ("alpha", "beta"):
        root = tmp_path / name
        root.mkdir(parents=True)
        init_factory(root, name=name, owner="acme", repo="shared")
    scaffold_workspace(tmp_path, name="acme", members=["alpha", "beta"])

    findings = audit_path(tmp_path).findings
    overlap = [f for f in findings if "claimed by more than one" in f.summary]

    assert overlap
    assert overlap[0].severity is Severity.WARNING
    assert set(overlap[0].factories) == {"alpha", "beta"}


# --------------------------------------------------------------------------------------
# Drift is a difference, not a fault
# --------------------------------------------------------------------------------------


def test_differing_effects_are_reported_as_divergence_not_as_broken(tmp_path: Path) -> None:
    """Factories legitimately differ. A report that flags a choice as an error trains
    people to ignore the report, and then it cannot tell them about the broken one."""
    import yaml

    root = workspace(tmp_path, ["alpha", "beta"])
    document = yaml.safe_load((root / "beta" / "factory.yaml").read_text(encoding="utf-8"))
    document["agentDefaults"]["effects"] = ["read", "write", "exec", "external"]
    (root / "beta" / "factory.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    result = audit_path(root)
    divergences = [f for f in result.findings if f.severity is Severity.DIVERGENCE]

    assert divergences
    assert result.ok is True, "a difference in configuration is not a broken factory"
    assert "not a fault" in divergences[0].remediation.lower()


def test_identical_factories_produce_no_divergence(tmp_path: Path) -> None:
    result = audit_path(workspace(tmp_path, ["alpha", "beta"]))

    assert [f for f in result.findings if f.severity is Severity.DIVERGENCE] == []


def test_one_factory_alone_cannot_diverge(tmp_path: Path) -> None:
    """There is nothing to diverge from, and a workspace of one that reports drift is
    reporting its own defaults back at itself."""
    result = audit_path(workspace(tmp_path, ["alpha"]))

    assert [f for f in result.findings if f.severity is Severity.DIVERGENCE] == []


# --------------------------------------------------------------------------------------
# Comparison, and refusing to compare
# --------------------------------------------------------------------------------------


def test_factories_with_too_little_history_are_named_not_averaged_over(
    tmp_path: Path,
) -> None:
    """A workspace average over the three that answered, presented as the workspace's
    number, is a claim about five."""
    root = workspace(tmp_path, ["alpha", "beta"])
    run_entries(root, "alpha", count=MIN_RUNS_TO_COMPARE + 2)
    run_entries(root, "beta", count=2)

    result = audit_path(root)

    assert result.quiet == ("beta",)


def test_a_quiet_factory_is_excluded_from_the_comparison(tmp_path: Path) -> None:
    """A gate pass rate over two runs is a statement about two runs."""
    root = workspace(tmp_path, ["alpha", "beta"])
    run_entries(root, "alpha", count=MIN_RUNS_TO_COMPARE + 2)
    run_entries(root, "beta", count=1)

    rows = compare(audit_path(root))

    for entries in rows.values():
        assert "beta" not in {name for name, _ in entries}


def test_only_rates_are_compared_never_totals(tmp_path: Path) -> None:
    """One factory with six months of history and one three days old are not comparable by
    volume, and a leaderboard by run count ranks them by age."""
    root = workspace(tmp_path, ["alpha", "beta"])
    run_entries(root, "alpha", count=50)
    run_entries(root, "beta", count=6)

    compared = set(compare(audit_path(root)))

    assert "runs" not in compared
    assert "changes_opened" not in compared


def test_an_unavailable_measure_stays_in_the_comparison(tmp_path: Path) -> None:
    """Filtering produces a comparison over whoever happened to have the integration,
    presented as a comparison across the workspace."""
    root = workspace(tmp_path, ["alpha", "beta"])
    run_entries(root, "alpha", count=6)
    run_entries(root, "beta", count=6)

    rows = compare(audit_path(root))

    assert rows, "nothing was comparable at all"
    for entries in rows.values():
        assert {name for name, _ in entries} == {"alpha", "beta"}


def test_no_outlier_is_reported_from_two_factories(tmp_path: Path) -> None:
    """With two, "an outlier" is just "the other one"."""
    root = workspace(tmp_path, ["alpha", "beta"])
    run_entries(root, "alpha", count=8, blocked=8)
    run_entries(root, "beta", count=8)

    assert outliers(audit_path(root)) == []


def test_an_outlier_is_reported_once_there_are_enough_to_compare(tmp_path: Path) -> None:
    root = workspace(tmp_path, ["alpha", "beta", "gamma", "delta"])
    run_entries(root, "alpha", count=8)
    run_entries(root, "beta", count=8)
    run_entries(root, "gamma", count=8)
    # Every run blocked: the one factory behaving differently from the rest.
    run_entries(root, "delta", count=8, blocked=8)

    found = outliers(audit_path(root))

    assert any("delta" in finding.factories for finding in found), [
        (f.factories, f.summary) for f in found
    ]


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------


def test_broken_findings_are_rendered_first(tmp_path: Path) -> None:
    import yaml

    root = workspace(tmp_path, ["alpha", "beta", "gamma"])
    (root / "gamma" / "factory.yaml").write_text("not: [valid", encoding="utf-8")
    document = yaml.safe_load((root / "beta" / "factory.yaml").read_text(encoding="utf-8"))
    document["agentDefaults"]["effects"] = ["read"]
    (root / "beta" / "factory.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    text = render(audit_path(root))

    assert text.index("broken:") < text.index("divergence:")


def test_a_clean_workspace_says_so(tmp_path: Path) -> None:
    assert "nothing to report" in render(audit_path(workspace(tmp_path, ["alpha"])))


def test_the_audit_is_serialisable(tmp_path: Path) -> None:
    result = audit_path(workspace(tmp_path, ["alpha"]))

    assert json.loads(json.dumps(result.as_dict()))["root"] == str(tmp_path)


def test_a_workspace_with_no_factories_is_not_an_error(tmp_path: Path) -> None:
    """The document schema requires at least one member, so this cannot be built on disk --
    but a caller can hold a `Workspace` with none, and an audit that raised on it would
    fail on exactly the empty case an operator is most likely to try first."""
    from software_factory.definition.workspace import Workspace, WorkspaceDocument

    result = audit(
        Workspace(
            root=tmp_path,
            document=WorkspaceDocument.model_construct(schema_version="1", name="acme"),
        )
    )

    assert result.ok is True
    assert result.factories == ()


@pytest.mark.parametrize("severity", list(Severity))
def test_every_severity_renders(severity: Severity) -> None:
    """A severity nothing knows how to render disappears from the report entirely, which is
    the one failure mode a findings list must not have."""
    from software_factory.observability.audit import Audit, Finding

    text = render(
        Audit(
            root="/w",
            findings=(Finding(severity=severity, factories=("a",), summary="something"),),
        )
    )

    assert "something" in text


def test_the_cli_names_the_factory_each_finding_is_about(tmp_path: Path) -> None:
    """Rich parses `[search]` as markup and swallows it.

    Every finding printed with an empty pair of brackets and no indication of which factory
    it concerned — which is the one thing a cross-factory report exists to say, and the bug
    is invisible in any test that only checks the summary text.
    """
    from typer.testing import CliRunner

    from software_factory.cli import app

    root = workspace(tmp_path, ["search", "payments"])
    (root / "search" / "factory.yaml").write_text("not: [valid", encoding="utf-8")

    result = CliRunner().invoke(app, ["workspace", "audit", "--root", str(root)])

    assert "search" in result.output
    assert result.exit_code != 0


def test_the_cli_exits_zero_on_divergence_alone(tmp_path: Path) -> None:
    """Divergence never fails the command. A report that treats a choice as an error trains
    people to stop reading it, and then it cannot tell them about the broken one."""
    import yaml
    from typer.testing import CliRunner

    from software_factory.cli import app

    root = workspace(tmp_path, ["alpha", "beta"])
    document = yaml.safe_load((root / "beta" / "factory.yaml").read_text(encoding="utf-8"))
    document["agentDefaults"]["effects"] = ["read", "write", "exec", "external"]
    (root / "beta" / "factory.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    result = CliRunner().invoke(app, ["workspace", "audit", "--root", str(root)])

    assert result.exit_code == 0, result.output
