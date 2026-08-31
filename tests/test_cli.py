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
