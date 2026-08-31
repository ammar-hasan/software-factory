"""The CLI surface: exit codes, JSON output, and the reference scaffold.

Exit codes are part of the contract (0 ok, 1 checked-thing-failed, 2 could-not-run), so
they are asserted explicitly rather than incidentally.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from software_factory.cli import app
from software_factory.ledger import EntryType, Ledger

runner = CliRunner()


@pytest.fixture
def scaffold(tmp_path: Path) -> Path:
    root = tmp_path / "factory"
    root.mkdir()
    result = runner.invoke(
        app, ["init", str(root), "--name", "payments", "--owner", "acme", "--repo", "svc"]
    )
    assert result.exit_code == 0, result.output
    return root


def payload(output: str) -> dict:
    return json.loads(output)


def test_version_prints_and_exits_clean() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip()


def test_init_writes_a_definition_that_validates(scaffold: Path) -> None:
    assert (scaffold / "factory.yaml").is_file()
    assert (scaffold / "agents" / "conductor" / "agent.md").is_file()

    result = runner.invoke(app, ["validate", str(scaffold), "--json"])

    assert result.exit_code == 0
    assert payload(result.output)["ok"] is True


def test_the_reference_scaffold_lints_clean(scaffold: Path) -> None:
    """A scaffold that emits warnings teaches every new user that warnings are normal."""
    result = runner.invoke(app, ["lint", str(scaffold), "--json"])

    assert result.exit_code == 0
    body = payload(result.output)
    assert body["ok"] is True
    assert body["validation"]["warnings"] == 0, body["validation"]["issues"]


def test_init_does_not_overwrite_without_force(scaffold: Path) -> None:
    (scaffold / "factory.yaml").write_text("# edited by hand\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(scaffold), "--json"])

    body = payload(result.output)
    assert any("factory.yaml" in p for p in body["skipped"])
    assert (scaffold / "factory.yaml").read_text(encoding="utf-8") == "# edited by hand\n"


def test_init_force_overwrites(scaffold: Path) -> None:
    (scaffold / "factory.yaml").write_text("# edited by hand\n", encoding="utf-8")

    result = runner.invoke(app, ["init", str(scaffold), "--force", "--json"])

    assert result.exit_code == 0
    assert "schemaVersion" in (scaffold / "factory.yaml").read_text(encoding="utf-8")


def test_validate_on_a_missing_definition_exits_unusable(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path), "--json"])

    assert result.exit_code == 2
    body = payload(result.output)
    assert body["ok"] is False
    assert body["error"]["remediation"]


def test_validate_reports_failure_with_exit_one(scaffold: Path) -> None:
    """A broken definition is a checked thing that failed, not a command that could not run."""
    (scaffold / "agents" / "second").mkdir()
    (scaffold / "agents" / "second" / "agent.md").write_text(
        "---\nrole: CONDUCTOR\n---\n\nAlso a conductor.\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["validate", str(scaffold), "--json"])

    assert result.exit_code == 1
    codes = {i["code"] for i in payload(result.output)["validation"]["issues"]}
    assert "factory.multiple_conductors" in codes


def test_plan_resolves_inheritance(scaffold: Path) -> None:
    result = runner.invoke(app, ["plan", str(scaffold), "--json"])

    assert result.exit_code == 0
    agents = payload(result.output)["agents"]
    assert agents["builder"]["execution"]["tier"] == "local-small"
    assert agents["critic"]["execution"]["tier"] == "mid"
    assert agents["conductor"]["execution"]["runner"] == "default"


def test_plan_explain_names_the_source_of_each_value(scaffold: Path) -> None:
    result = runner.invoke(app, ["plan", str(scaffold), "--explain", "--json"])

    origins = {
        o["field"]: o["source"] for o in payload(result.output)["agents"]["critic"]["origins"]
    }
    assert origins["tier"] == "agent"
    assert origins["runner"] == "factory"


def test_audit_reports_no_egress_for_the_default_factory(scaffold: Path) -> None:
    """The scaffold denies network by default; audit must show that, not assume it."""
    result = runner.invoke(app, ["audit", str(scaffold), "--json"])

    assert result.exit_code == 0
    body = payload(result.output)
    assert body["egress"] == []
    assert all(row["network"] == "none" for row in body["agents"])


def test_audit_flags_setup_commands_as_statically_unverifiable(scaffold: Path) -> None:
    """Setup commands can reach the network; audit must not claim otherwise."""
    runner_path = scaffold / "runners" / "default.yaml"
    runner_path.write_text(
        runner_path.read_text(encoding="utf-8").replace(
            "setupCommands: []", "setupCommands:\n  - pip install -e ."
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["audit", str(scaffold), "--json"])

    assert payload(result.output)["unverifiedEgress"]


def test_audit_surfaces_declared_egress(scaffold: Path) -> None:
    runner_path = scaffold / "runners" / "default.yaml"
    runner_path.write_text(
        runner_path.read_text(encoding="utf-8").replace(
            "network: none", "network: allowlist\nnetworkAllowlist:\n  - pypi.org"
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["audit", str(scaffold), "--json"])

    assert payload(result.output)["egress"] == ["pypi.org"]


def test_schema_lists_kinds_then_exports_one() -> None:
    listing = runner.invoke(app, ["schema"])
    assert listing.exit_code == 0
    assert "factory" in listing.output

    exported = runner.invoke(app, ["schema", "factory"])
    assert exported.exit_code == 0
    body = payload(exported.output)
    assert body["$schema"].startswith("https://json-schema.org/")
    assert body["x-semanticRules"], "schema must name the rules it cannot express"


def test_schema_rejects_an_unknown_kind() -> None:
    result = runner.invoke(app, ["schema", "nonsense"])

    assert result.exit_code == 2


def test_ledger_verify_accepts_a_good_chain(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for index in range(5):
        ledger.append(EntryType.RUN_STARTED, actor="builder", subject=f"run-{index}")

    result = runner.invoke(app, ["ledger", "verify", str(ledger.path), "--json"])

    assert result.exit_code == 0
    assert payload(result.output)["entries"] == 5


def test_ledger_verify_reports_a_broken_chain(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="run-1")
    ledger.append(EntryType.RUN_FINISHED, actor="builder", subject="run-1")
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    ledger.path.write_text(lines[0] + "\n", encoding="utf-8")
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="run-2")
    # Re-append after truncation is fine; corrupt an entry to force a real break.
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["actor"] = "impostor"
    lines[0] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
    ledger.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = runner.invoke(app, ["ledger", "verify", str(ledger.path), "--json"])

    assert result.exit_code == 1
    assert payload(result.output)["ok"] is False


def test_ledger_tail_returns_the_most_recent_entries(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for index in range(10):
        ledger.append(EntryType.TOOL_CALLED, actor="builder", subject=f"run-{index}")

    result = runner.invoke(app, ["ledger", "tail", str(ledger.path), "-n", "3", "--json"])

    entries = payload(result.output)["entries"]
    assert [e["seq"] for e in entries] == [8, 9, 10]


def test_doctor_reports_environment_checks() -> None:
    result = runner.invoke(app, ["doctor", "--json"])

    checks = {c["check"] for c in payload(result.output)["checks"]}
    assert {"python", "git"} <= checks


# --------------------------------------------------------- knowledge subsystem commands


def test_stages_marks_the_non_skippable_stage() -> None:
    result = runner.invoke(app, ["stages", "--json"])

    assert result.exit_code == 0
    body = payload(result.output)
    assert "REVIEW" in body["nonSkippable"]
    assert body["transitions"]["COMPLETE"] == []


def test_stages_shows_blocked_reachable_from_working_stages() -> None:
    """Work can stall anywhere, so every non-terminal stage must be able to park."""
    body = payload(runner.invoke(app, ["stages", "--json"]).output)

    for stage in ("TRIAGE", "DESIGN", "BUILD", "REVIEW", "VERIFY"):
        assert "BLOCKED" in body["transitions"][stage]


def test_gates_lists_every_baseline_gate_with_its_stages() -> None:
    result = runner.invoke(app, ["gates", "--json"])

    assert result.exit_code == 0
    gates = payload(result.output)["gates"]
    assert gates["regression-proven"] == ["BUILD"]
    assert set(gates["evidence-complete"]) == {"REVIEW", "VERIFY"}


def test_memory_stats_reports_lane_counts(tmp_path: Path) -> None:
    from software_factory.memory import (
        Candidate,
        Kind,
        MemoryStore,
        Scope,
        Source,
        SourceKind,
        admit,
    )

    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.load()
    admit(
        Candidate(
            kind=Kind.FACT,
            scope=Scope.REPOSITORY,
            scope_ref="acme/payments",
            content="The importer parses headers before rows.",
            provenance=(Source(kind=SourceKind.RUN, ref="run-1"),),
        ),
        store,
    )

    result = runner.invoke(app, ["memory", "stats", str(path), "--json"])

    assert result.exit_code == 0
    stats = payload(result.output)["stats"]
    assert stats["candidate"] == 1
    assert stats["total"] == 1


def test_memory_why_reports_not_found_with_exit_one(tmp_path: Path) -> None:
    path = tmp_path / "memory.jsonl"
    path.write_text("", encoding="utf-8")

    result = runner.invoke(app, ["memory", "why", str(path), "mem_missing", "--json"])

    assert result.exit_code == 1
    assert payload(result.output)["ok"] is False


def test_memory_why_walks_the_provenance_tree(tmp_path: Path) -> None:
    from software_factory.memory import (
        Candidate,
        Kind,
        Memory,
        MemoryStore,
        Scope,
        Source,
        SourceKind,
        admit,
    )

    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.load()

    def write(content: str, parents: tuple[str, ...] = ()) -> Memory:
        outcome = admit(
            Candidate(
                kind=Kind.FACT,
                scope=Scope.REPOSITORY,
                scope_ref="acme/payments",
                content=content,
                provenance=(Source(kind=SourceKind.RUN, ref=f"run-{content[:5]}"),),
                parents=parents,
            ),
            store,
        )
        assert isinstance(outcome, Memory), outcome
        return outcome

    root = write("Deploys are gated on the staging smoke suite.")
    child = write("Hotfixes therefore wait for staging to finish.", (root.id,))

    result = runner.invoke(app, ["memory", "why", str(path), child.id, "--json"])

    assert result.exit_code == 0
    tree = payload(result.output)["provenance"]
    assert tree["parents"][0]["id"] == root.id


def test_memory_blast_reports_downstream_impact(tmp_path: Path) -> None:
    from software_factory.memory import (
        Candidate,
        Kind,
        Memory,
        MemoryStore,
        Scope,
        Source,
        SourceKind,
        admit,
    )

    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.load()
    root = admit(
        Candidate(
            kind=Kind.FACT,
            scope=Scope.REPOSITORY,
            scope_ref="acme/payments",
            content="Deploys are gated on the staging smoke suite.",
            provenance=(Source(kind=SourceKind.RUN, ref="run-1"),),
        ),
        store,
    )
    assert isinstance(root, Memory)

    result = runner.invoke(app, ["memory", "blast", str(path), root.id, "--json"])

    assert result.exit_code == 0
    assert payload(result.output)["impact"]["total"] == 0


def test_memory_policy_defaults_to_a_dry_run(tmp_path: Path) -> None:
    """A pass that rewrites lanes should never be the default of an inspection command."""
    from software_factory.memory import (
        Candidate,
        Kind,
        MemoryStore,
        Scope,
        Source,
        SourceKind,
        admit,
    )

    path = tmp_path / "memory.jsonl"
    store = MemoryStore(path)
    store.load()
    for content in (
        "Retries are enabled for the payments webhook.",
        "Retries are disabled for the payments webhook.",
    ):
        admit(
            Candidate(
                kind=Kind.FACT,
                scope=Scope.REPOSITORY,
                scope_ref="acme/payments",
                content=content,
                provenance=(Source(kind=SourceKind.RUN, ref=f"run-{content[:6]}"),),
            ),
            store,
        )

    dry = runner.invoke(app, ["memory", "policy", str(path), "--json"])
    assert payload(dry.output)["dryRun"] is True

    reloaded = MemoryStore(path)
    reloaded.load()
    assert not any(m.quarantined for m in reloaded.all())

    applied = runner.invoke(app, ["memory", "policy", str(path), "--apply", "--json"])
    assert applied.exit_code == 0
    assert payload(applied.output)["report"]["quarantined"]


# --------------------------------------------------------------------------- sf work


def test_work_dry_run_plans_stages_without_executing(scaffold: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "Add semicolon delimiter support",
            "--factory",
            str(scaffold),
            "--dry-run",
            "--json",
        ],
    )

    assert result.exit_code == 0
    body = payload(result.output)
    assert body["plannedStages"][0] == "TRIAGE"
    assert "REVIEW" in body["plannedStages"]
    assert "nothing was executed" in body["note"]


def test_work_dry_run_classifies_the_request(scaffold: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "The importer crashes on BOM headers",
            "--factory",
            str(scaffold),
            "--dry-run",
            "--json",
        ],
    )

    assert payload(result.output)["workItem"]["workClass"] == "defect"


def test_work_honours_an_explicit_work_class(scaffold: Path) -> None:
    result = runner.invoke(
        app,
        [
            "work",
            "Tidy the docstrings",
            "--factory",
            str(scaffold),
            "--class",
            "chore",
            "--dry-run",
            "--json",
        ],
    )

    assert payload(result.output)["workItem"]["workClass"] == "chore"


def test_work_without_a_provider_refuses_rather_than_pretending(
    scaffold: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A factory that cannot reach inference must say so, not produce unverified output."""
    monkeypatch.delenv("SF_PROVIDER_ENDPOINT", raising=False)

    result = runner.invoke(app, ["work", "do something", "--factory", str(scaffold), "--json"])

    assert result.exit_code == 2
    body = payload(result.output)
    assert body["ok"] is False
    assert "--dry-run" in body["error"]["remediation"]


def test_work_on_a_missing_factory_is_actionable(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["work", "do something", "--factory", str(tmp_path), "--dry-run", "--json"]
    )

    assert result.exit_code == 2
    assert payload(result.output)["error"]["remediation"]


# ---------------------------------------------------------------------- sf spec induct


def test_spec_induct_proposes_units_from_a_codebase(tmp_path: Path) -> None:
    (tmp_path / "importer.py").write_text(
        '"""Importer."""\n\n\ndef strip_bom(text):\n    """Strip a BOM."""\n    return text\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["spec", "induct", str(tmp_path), "--json"])

    assert result.exit_code == 0
    induction = payload(result.output)["induction"]
    assert induction["proposed"] >= 1
    assert all(unit["status"] == "draft" for unit in induction["units"])


def test_spec_induct_writes_nothing(tmp_path: Path) -> None:
    source = tmp_path / "importer.py"
    source.write_text("def compute(a, b):\n    return a + b\n", encoding="utf-8")
    before = sorted(p.name for p in tmp_path.iterdir())

    runner.invoke(app, ["spec", "induct", str(tmp_path), "--json"])

    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_spec_induct_on_an_empty_directory_is_not_an_error(tmp_path: Path) -> None:
    result = runner.invoke(app, ["spec", "induct", str(tmp_path), "--json"])

    assert result.exit_code == 0
    assert payload(result.output)["induction"]["proposed"] == 0
