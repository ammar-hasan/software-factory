"""The CLI surface: exit codes, JSON output, and the reference scaffold.

Exit codes are part of the contract (0 ok, 1 checked-thing-failed, 2 could-not-run), so
they are asserted explicitly rather than incidentally.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from software_factory.cli import EXIT_UNUSABLE, app
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
    """Every check reports a verdict, a detail, and -- when failing -- a remediation.

    The previous assertion was that the check *names* were present. `requires-python` is
    `>=3.11`, so the python check cannot be false in an environment that can run this at
    all: a check that cannot fail proves nothing about the reporting around it (T12).
    """
    result = runner.invoke(app, ["doctor", "--json"])
    checks = payload(result.output)["checks"]

    assert {"python", "git"} <= {c["check"] for c in checks}
    for check in checks:
        assert isinstance(check["ok"], bool)
        assert check["detail"], f"{check['check']} reports no detail"
        if not check["ok"]:
            assert check["remediation"], f"{check['check']} fails with no remediation"


def test_doctor_fails_and_says_why_when_a_required_tool_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The half that matters: `doctor` exists to report a broken environment, and nothing
    tested that it reports one."""
    import shutil

    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "git" else real(name))

    result = runner.invoke(app, ["doctor", "--json"])
    body = payload(result.output)

    assert body["ok"] is False
    git_check = next(c for c in body["checks"] if c["check"] == "git")
    assert git_check["ok"] is False
    assert "Install git" in git_check["remediation"]


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
    assert set(gates["evidence-complete"]) == {"HANDOFF", "REVIEW", "VERIFY"}
    # HANDOFF is where the work becomes externally visible, so the two gates about what
    # leaves the machine run there whatever they said earlier.
    assert set(gates["secret-clean"]) >= {"BUILD", "HANDOFF"}


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


def test_work_dry_run_says_why_the_path_is_that_one(scaffold: Path) -> None:
    """The README promises this prints the stages "and why", and it printed the stages.

    Why is the one thing a dry run exists to explain: the work class chose the shape, and a
    reader looking at `TRIAGE → BUILD → REVIEW → HANDOFF` cannot tell whether DESIGN was
    skipped by policy or lost by a bug.
    """
    for request, expect in (
        ("Add semicolon delimiter support", "DESIGN is planned"),
        ("The importer crashes on BOM headers", "DESIGN is skipped"),
    ):
        result = runner.invoke(
            app,
            ["work", request, "--factory", str(scaffold), "--dry-run", "--json"],
        )

        assert result.exit_code == 0
        reason = payload(result.output)["reason"]
        assert expect in reason, reason


def test_planning_a_path_needs_no_runtime(scaffold: Path) -> None:
    """A pure function of the work item, reachable as one.

    It was a method, so the dry run — whose whole job is to answer this without running
    anything — built a `Coordinator` through `__new__` to reach it, dodging the constructor
    of a class that owns a ledger, a workspace factory and a provider. A planning question
    answered by an uninitialised object is one edit away from one that needs a provider.
    """
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import planned_path

    def plan(work_class: WorkClass, request: str):
        return planned_path(
            WorkItem(
                id=new_id(),
                factory="f",
                title="t",
                request=request,
                source=SourceContext(provider="cli", kind="test", ref="t"),
                work_class=work_class,
            )
        )

    feature = plan(WorkClass.FEATURE, "add a thing")
    defect = plan(WorkClass.DEFECT, "it crashes")

    assert "DESIGN" in [s.value for s in feature.stages]
    assert "DESIGN" not in [s.value for s in defect.stages]
    # REVIEW is in every path (FR-3.3a), and so is TRIAGE — the docstring this replaced
    # claimed TRIAGE was skipped for a well-described request, and no branch ever did.
    for path in (feature, defect, plan(WorkClass.DEFECT, "it crashes. " + "detail. " * 60)):
        stages = [s.value for s in path.stages]
        assert stages[0] == "TRIAGE" and stages[-1] == "HANDOFF"
        assert "REVIEW" in stages
        assert path.reason, "a planned path with no reason is the defect this fixed"


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


def test_principals_reports_who_may_decide_what(tmp_path: Path) -> None:
    """The security answer to "who can approve, override, widen, or stop?" -- from the
    definition, because that is the only place a grant is reviewable (FR-25.2)."""
    runner.invoke(app, ["init", str(tmp_path), "--name", "ref", "--owner", "amaya"])

    result = runner.invoke(app, ["principals", str(tmp_path), "--json"])
    body = payload(result.output)

    by_id = {p["id"]: p for p in body["principals"]}
    assert "approve_spec" in by_id["amaya"]["capabilities"]
    assert by_id["conductor"]["kind"] == "agent"
    assert "approve_spec" not in by_id["conductor"]["capabilities"]


def test_principals_names_the_capabilities_nobody_holds(tmp_path: Path) -> None:
    """A checkpoint answered by a capability nobody holds parks its work item and never
    clears, so an operator needs to see the hole before they hit it."""
    runner.invoke(app, ["init", str(tmp_path), "--name", "ref", "--owner", "amaya"])

    body = payload(runner.invoke(app, ["principals", str(tmp_path), "--json"]).output)

    assert "erase_data" in body["unheldCapabilities"]


def test_spend_attributes_cost_by_cause_agent_and_stage(tmp_path: Path) -> None:
    """Per-run budgets bound one agent. "Every run was within its limit" is the sentence
    that precedes every surprise invoice (FR-26.1, FR-26.5)."""
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    for agent, cause, units in (
        ("builder", "primary", 30),
        ("builder", "retry", 8),
        ("critic", "scoring", 4),
    ):
        ledger.append(
            EntryType.MODEL_CALLED,
            actor=agent,
            subject="wi-1",
            payload={
                "costUnits": units,
                "agent": agent,
                "stage": "BUILD",
                "cause": cause,
                "workItem": "wi-1",
            },
        )

    body = payload(
        runner.invoke(app, ["spend", str(ledger.path), "--limit", "50", "--json"]).output
    )["spend"]

    assert body["spent"] == 42.0
    assert body["state"] == "warning"
    assert body["byCause"] == {"primary": 30.0, "retry": 8.0, "scoring": 4.0}
    assert body["overheadFraction"] == pytest.approx(12 / 42, abs=1e-3)


def test_spend_on_a_factory_that_has_not_run_says_so(tmp_path: Path) -> None:
    """Zero is an answer, and "no attributed spend" is a different answer from "zero spent
    on everything" -- the second would read as a factory running for free."""
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="conductor", subject="wi-1")

    body = payload(runner.invoke(app, ["spend", str(ledger.path), "--json"]).output)["spend"]

    assert body["spent"] == 0
    assert body["byCause"] == {}


def test_govern_classes_reports_contents_and_retention() -> None:
    """A retention policy that does not say what is in the thing being retained is a number
    with no argument behind it (FR-27.1)."""
    body = payload(runner.invoke(app, ["govern", "classes", "--json"]).output)

    by_class = {c["class"]: c for c in body["classes"]}
    assert by_class["ledger"]["erasableBySubject"] is False
    assert "personal_data" in by_class["transcript"]["contains"]
    assert all(c["rationale"] for c in body["classes"])


def test_govern_seal_and_verify_round_trip(tmp_path: Path) -> None:
    """Bounded growth is otherwise a claim with no mechanism (NFR-3.2, FR-27.2)."""
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    for index in range(25):
        ledger.append(EntryType.RUN_STARTED, actor="worker", subject=f"run-{index}")

    sealed = payload(
        runner.invoke(app, ["govern", "seal", str(ledger.path), "--size", "10", "--json"]).output
    )
    assert [s["index"] for s in sealed["sealed"]] == [0, 1]
    assert sealed["sealedThrough"] == 20

    verified = payload(runner.invoke(app, ["govern", "verify", str(ledger.path), "--json"]).output)
    assert verified["segments"] == 2


def test_govern_seal_on_a_short_ledger_says_so(tmp_path: Path) -> None:
    """A partial segment would have to be re-sealed as it grew, and a seal that changes is
    not a seal."""
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="worker", subject="run-0")

    body = payload(
        runner.invoke(app, ["govern", "seal", str(ledger.path), "--size", "10", "--json"]).output
    )

    assert body["sealed"] == []
    assert body["sealedThrough"] == 0


def test_metrics_reports_unavailable_rather_than_zero(tmp_path: Path) -> None:
    """FR-15.5. "changes merged: 0" reads as a factory that merges nothing; "unavailable --
    no git-host adapter" reads as a factory nobody has told about its git host."""
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.RUN_STARTED, actor="builder", subject="r1", payload={"agent": "builder"}
    )

    body = payload(runner.invoke(app, ["metrics", str(ledger.path), "--json"]).output)["metrics"]

    merged = next(m for m in body["measures"] if m["name"] == "changes_merged")
    assert merged["value"] is None
    assert merged["availability"] == "unavailable"
    assert "git-host" in merged["reason"]


def test_metrics_changes_the_reason_when_the_integration_exists(tmp_path: Path) -> None:
    """Configuring the adapter must not delete the row, and must change what it says.

    Without the adapter the answer is "fix your configuration". With it and nothing yet
    observed the answer is "wait" -- and neither is zero, because a factory that reported
    zero merged changes would be claiming an outcome it has never looked at.
    """
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="r1", payload={})

    without = next(
        m
        for m in payload(runner.invoke(app, ["metrics", str(ledger.path), "--json"]).output)[
            "metrics"
        ]["measures"]
        if m["name"] == "changes_merged"
    )
    assert without["availability"] == "unavailable"
    assert "no git-host adapter is configured" in without["reason"]

    body = payload(
        runner.invoke(
            app, ["metrics", str(ledger.path), "--integration", "git-host", "--json"]
        ).output
    )["metrics"]

    merged = next(m for m in body["measures"] if m["name"] == "changes_merged")
    assert merged["availability"] == "insufficient_data"
    assert merged["value"] is None


def test_metrics_separates_work_from_measurement(tmp_path: Path) -> None:
    """A rising run count with flat output can be measurement activity rather than work."""
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(EntryType.RUN_STARTED, actor="builder", subject="r1", payload={})
    ledger.append(
        EntryType.RUN_STARTED, actor="critic", subject="r2", payload={"purpose": "benchmark"}
    )

    body = payload(runner.invoke(app, ["metrics", str(ledger.path), "--json"]).output)["metrics"]

    assert body["runs"]["work"] == 1
    assert body["runs"]["benchmark"] == 1
    assert body["runs"]["measurementShare"] == 0.5


def test_dash_on_a_missing_ledger_says_so(tmp_path: Path) -> None:
    result = runner.invoke(app, ["dash", str(tmp_path / "absent.jsonl")])

    assert result.exit_code != 0
    assert "no ledger" in result.output


def test_audit_egress_reports_the_scaffold_as_offline_capable(tmp_path: Path) -> None:
    """PR-2: local is the reference implementation, not a degraded mode. A scaffold that
    reached the network on day one would make that false out of the box."""
    runner.invoke(app, ["init", str(tmp_path), "--name", "ref", "--owner", "amaya"])

    body = payload(runner.invoke(app, ["audit", str(tmp_path), "--egress", "--json"]).output)

    assert body["egress"]["offlineCapable"] is True
    assert body["egress"]["destinations"] == []


def test_audit_egress_reports_what_it_cannot_determine(tmp_path: Path) -> None:
    """FR-20.6. An egress report that silently omits what it cannot see is worse than none,
    because it reads as a complete list."""
    runner.invoke(app, ["init", str(tmp_path), "--name", "ref", "--owner", "amaya"])
    path = tmp_path / "runners" / "default.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "setupCommands: []", "setupCommands:\n  - pip install -e ."
        ),
        encoding="utf-8",
    )

    body = payload(runner.invoke(app, ["audit", str(tmp_path), "--egress", "--json"]).output)

    assert body["egress"]["offlineCapable"] is False
    assert any(d["certainty"] == "indeterminate" for d in body["egress"]["destinations"])


def test_the_generated_reference_matches_the_definitions() -> None:
    """NFR-4.3, FR-30.4. Documentation written alongside the code drifts, and the drift is
    invisible: a doc describing a renamed gate reads perfectly and sends every reader to a
    name that does not exist. Generating it from the definitions makes the drift a test
    failure instead."""
    import subprocess
    import sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "generate_reference.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_every_gate_explains_what_it_establishes() -> None:
    """A gate whose reference entry is blank is a gate a reader has to read the source to
    understand -- and the generator surfacing the blank is how that gets noticed."""
    from software_factory.evals.gates import BASELINE_GATES

    for name, gate in BASELINE_GATES.items():
        assert gate.__doc__ and gate.__doc__.strip(), f"{name} has no docstring"


def test_intake_starts_work_for_a_matching_event(tmp_path: Path) -> None:
    """FR-18.10: every capability reachable through an integration must also be reachable
    through `sf`, so a fully local factory loses convenience and nothing else."""
    runner.invoke(
        app, ["init", str(tmp_path), "--name", "ref", "--owner", "amaya", "--repo", "service"]
    )

    body = payload(
        runner.invoke(
            app,
            [
                "intake",
                str(tmp_path),
                "--provider",
                "git-host",
                "--event",
                "issue_labeled",
                "--author",
                "amaya",
                "-a",
                "repos=amaya/service",
                "-a",
                "labels=factory-ready",
                "--json",
            ],
        ).output
    )

    assert [o["kind"] for o in body["outcomes"]] == ["started"]
    assert body["outcomes"][0]["agent"] == "conductor"


def test_intake_refuses_an_unmapped_author_by_default(tmp_path: Path) -> None:
    """FR-18.6. An automation accepting anyone is a choice an operator makes deliberately,
    not one they get by not thinking about it."""
    runner.invoke(
        app, ["init", str(tmp_path), "--name", "ref", "--owner", "amaya", "--repo", "service"]
    )

    body = payload(
        runner.invoke(
            app,
            [
                "intake",
                str(tmp_path),
                "--provider",
                "git-host",
                "--event",
                "issue_labeled",
                "--author",
                "stranger",
                "-a",
                "repos=amaya/service",
                "-a",
                "labels=factory-ready",
                "--json",
            ],
        ).output
    )

    assert body["outcomes"][0]["code"] == "intake.unknown_author"
    assert "principals" in body["outcomes"][0]["remediation"]


def test_intake_ignores_an_event_nothing_matches(tmp_path: Path) -> None:
    """Most events are not for this factory, and treating them as errors makes every
    unrelated push an incident."""
    runner.invoke(app, ["init", str(tmp_path), "--name", "ref", "--owner", "amaya"])

    body = payload(
        runner.invoke(
            app,
            ["intake", str(tmp_path), "--provider", "git-host", "--event", "push", "--json"],
        ).output
    )

    assert body["outcomes"][0]["kind"] == "ignored"


def test_serve_publishes_the_tool_surface_with_its_guidance(tmp_path: Path) -> None:
    """FR-19.9: a calling agent picks up the correct workflow without an operator explaining
    it. A schema says what is accepted; guidance says what to do with it."""
    runner.invoke(app, ["init", str(tmp_path), "--name", "ref", "--owner", "amaya"])

    body = payload(runner.invoke(app, ["serve", str(tmp_path), "--json"]).output)

    by_name = {t["name"]: t for t in body["tools"]}
    assert "never touches your files" in by_name["factory.pick_up"]["guidance"]
    assert by_name["factory.hand_back"]["external"] is True


def test_improve_clusters_repeated_gate_failures(tmp_path: Path) -> None:
    """Diagnosing one failure costs a run; diagnosing forty instances of one failure costs
    forty runs and produces one answer (FR-14.2)."""
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    for index in range(4):
        ledger.append(
            EntryType.GATE_EVALUATED,
            actor="builder",
            subject=f"wi-{index}",
            payload={
                "gate": "tests-pass",
                "outcome": "fail",
                "stage": "BUILD",
                "workItem": f"wi-{index}",
            },
        )

    body = payload(runner.invoke(app, ["improve", str(ledger.path), "--json"]).output)

    assert body["failures"] == 4
    assert len(body["clusters"]) == 1
    assert body["clusters"][0]["size"] == 4


def test_improve_says_nothing_repeats_rather_than_reporting_none(tmp_path: Path) -> None:
    """A one-off has no pattern to generalise from, and a proposal drawn from one instance
    is a proposal fitted to one instance -- but the failure still happened, and reporting
    zero failures would say otherwise."""
    from software_factory.ledger import EntryType, Ledger

    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        EntryType.GATE_EVALUATED,
        actor="builder",
        subject="wi-1",
        payload={"gate": "tests-pass", "outcome": "fail", "stage": "BUILD"},
    )

    body = payload(runner.invoke(app, ["improve", str(ledger.path), "--json"]).output)

    assert body["failures"] == 1
    assert body["clusters"] == []


# --------------------------------------------------------------------- sf providers


def test_providers_resolves_the_scaffold_with_no_account(scaffold: Path) -> None:
    """PR-2 made checkable: the reference path must work with nothing configured.

    A scaffold whose tiers cannot resolve would make the local-first claim false out of
    the box, and the only way to discover it would be to start a run.
    """
    result = runner.invoke(app, ["providers", str(scaffold), "--json"])

    assert result.exit_code == 0, result.output
    body = payload(result.output)
    assert body["ok"] is True
    assert [t["tier"] for t in body["tiers"]] == ["local-small", "mid"]
    assert all(t["local"] and t["usable"] for t in body["tiers"])


def test_providers_works_offline_by_default(scaffold: Path, monkeypatch) -> None:
    """A diagnostic that needs the network to report a network problem is not one.

    Without `--probe` nothing may be contacted, so the command still answers on a machine
    with no model running -- which is exactly the machine whose operator is asking.
    """
    import software_factory.providers.transport as transport

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("providers must not open a socket without --probe")

    monkeypatch.setattr(transport.UrllibTransport, "post_json", refuse)
    result = runner.invoke(app, ["providers", str(scaffold), "--json"])
    assert result.exit_code == 0, result.output


def test_providers_names_the_missing_variable(tmp_path: Path, monkeypatch) -> None:
    """ "Authentication failed" sends the reader to the wrong place; a variable name does not."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    root = tmp_path / "f"
    root.mkdir()
    runner.invoke(app, ["init", str(root), "--name", "p", "--owner", "a", "--repo", "s"])
    factory = root / "factory.yaml"
    factory.write_text(
        factory.read_text(encoding="utf-8").replace("provider: local", "provider: anthropic"),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["providers", str(root), "--json"])

    assert result.exit_code != 0
    body = payload(result.output)
    assert body["ok"] is False
    assert "ANTHROPIC_API_KEY" in body["tiers"][0]["reason"]


def test_providers_flags_a_tier_that_lies_about_being_local(tmp_path: Path, monkeypatch) -> None:
    """A tier marked local that resolves to a hosted endpoint understates egress."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    root = tmp_path / "f"
    root.mkdir()
    runner.invoke(app, ["init", str(root), "--name", "p", "--owner", "a", "--repo", "s"])
    factory = root / "factory.yaml"
    factory.write_text(
        factory.read_text(encoding="utf-8").replace("provider: local", "provider: anthropic"),
        encoding="utf-8",
    )

    body = payload(runner.invoke(app, ["providers", str(root), "--json"]).output)

    assert body["mismatchedLocality"] == ["local-small", "mid"]
    assert body["ok"] is False


def test_providers_reports_an_unknown_name_without_failing_the_others(tmp_path: Path) -> None:
    """One typo must not hide every other tier: a resolver that raises reports one line."""
    root = tmp_path / "f"
    root.mkdir()
    runner.invoke(app, ["init", str(root), "--name", "p", "--owner", "a", "--repo", "s"])
    factory = root / "factory.yaml"
    factory.write_text(
        factory.read_text(encoding="utf-8").replace(
            "provider: local\n      model: local-model\n      contextWindow: 32000",
            "provider: nonesuch\n      model: local-model\n      contextWindow: 32000",
            1,
        ),
        encoding="utf-8",
    )

    body = payload(runner.invoke(app, ["providers", str(root), "--json"]).output)

    assert len(body["tiers"]) == 2
    assert body["tiers"][0]["usable"] is False
    assert body["tiers"][1]["usable"] is True


# ------------------------------------------------------------------- sf checkpoints


def ledger_with_checkpoint(root: Path) -> Path:
    """A ledger carrying one open checkpoint, as the coordinator would write it."""
    from software_factory.identity import Capability
    from software_factory.identity.checkpoints import CheckpointKind
    from software_factory.ledger import EntryType, Ledger

    path = root / "ledger.jsonl"
    Ledger(path).append(
        EntryType.CHECKPOINT_OPENED,
        actor="coordinator",
        subject="wi-1:widen",
        payload={
            "kind": CheckpointKind.BLAST_RADIUS_WIDENING.value,
            "workItem": "wi-1",
            "question": "widen the radius to touch the migrations directory?",
            "capability": Capability.WIDEN_BLAST_RADIUS.value,
            "origin": "cli",
        },
    )
    return path


def test_checkpoints_list_shows_what_is_waiting_on_a_person(scaffold: Path) -> None:
    """`checkpoints.py` told users to run `sf checkpoints`. The command did not exist, and
    nothing anywhere opened a checkpoint -- the whole mechanism was reachable from nothing.
    """
    path = ledger_with_checkpoint(scaffold)

    body = payload(
        runner.invoke(
            app, ["checkpoints", "list", str(path), "--factory", str(scaffold), "--json"]
        ).output
    )

    assert body["open"] == 1
    assert body["checkpoints"][0]["workItem"] == "wi-1"
    assert body["checkpoints"][0]["routableTo"], "a checkpoint nobody can clear is a dead end"


def test_resolving_a_checkpoint_records_who_decided_and_why(scaffold: Path) -> None:
    path = ledger_with_checkpoint(scaffold)
    who = payload(runner.invoke(app, ["principals", str(scaffold), "--json"]).output)
    approver = next(p["id"] for p in who["principals"] if "widen_blast_radius" in p["capabilities"])

    result = runner.invoke(
        app,
        [
            "checkpoints",
            "resolve",
            str(path),
            "wi-1:widen",
            "--as",
            approver,
            "--answer",
            "yes: the migration is the change, and it is reviewed",
            "--factory",
            str(scaffold),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    decision = payload(result.output)["decision"]
    assert decision["principal"] == approver
    assert "reviewed" in decision["rationale"]

    after = payload(
        runner.invoke(
            app, ["checkpoints", "list", str(path), "--factory", str(scaffold), "--json"]
        ).output
    )
    assert after["open"] == 0


def test_a_principal_without_the_capability_cannot_resolve(scaffold: Path) -> None:
    """The answer `sf principals` prints has to be the answer that binds."""
    path = ledger_with_checkpoint(scaffold)

    result = runner.invoke(
        app,
        [
            "checkpoints",
            "resolve",
            str(path),
            "wi-1:widen",
            "--as",
            "nobody-at-all",
            "--answer",
            "sure",
            "--factory",
            str(scaffold),
            "--json",
        ],
    )

    assert result.exit_code != 0
    assert payload(result.output)["code"] == "identity.unknown_principal"


# ------------------------------------------------------------- sf govern sweep/erase


def test_govern_sweep_is_a_dry_run_unless_told_otherwise(scaffold: Path) -> None:
    """A retention report that asserts deletions it did not make is shaped to be shown to
    an auditor, so it must not be a positive claim nothing established."""
    path = ledger_with_checkpoint(scaffold)

    body = payload(runner.invoke(app, ["govern", "sweep", str(path), "--json"]).output)

    assert body["sweep"]["dryRun"] is True
    assert body["sweep"]["acted"] is False
    assert body["sweep"]["examined"] >= 1


def test_govern_erase_says_what_it_cannot_erase(scaffold: Path) -> None:
    """The ledger holds references and decisions, never bodies. A subject is entitled to
    know that, not to be told everything is gone."""
    path = ledger_with_checkpoint(scaffold)

    body = payload(
        runner.invoke(
            app, ["govern", "erase", str(path), "coordinator", "--by", "human:dpo", "--json"]
        ).output
    )

    assert body["erasure"]["examined"] >= 1
    assert body["erasure"]["unerasable"], "the ledger is unerasable by design and must say so"
    assert body["erasure"]["complete"] is False, "a dry run has not completed anything"


def test_every_command_group_is_reachable_as_a_module() -> None:
    """`sf x` and `python -m software_factory.cli x` must offer the same commands.

    They came apart once and the failure was silent in every direction that usually catches
    things. `sf worker` had tests, had a generated reference page, and answered "no such
    command" to `python -m` — because the `__main__` guard sat mid-file, so running the
    module as a script called `app()` and exited before the groups defined below it were
    registered. The console script imports the module first and never reaches the guard,
    so the two entry points disagreed about which commands existed.

    Asserted through a real subprocess rather than by inspecting `app`: importing the module
    is precisely the path that *worked*, and a test that imports proves nothing about the
    one that did not.
    """
    import subprocess
    import sys

    from software_factory.cli import app

    groups = sorted(group.name for group in app.registered_groups if group.name)
    assert groups, "no command groups registered at all"

    result = subprocess.run(
        [sys.executable, "-m", "software_factory.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "COLUMNS": "200", "NO_COLOR": "1"},
    )

    missing = [name for name in groups if name not in result.stdout]
    assert missing == [], f"invisible to `python -m`: {missing}"


def test_no_command_name_is_claimed_twice() -> None:
    """A group and a command may not share a name, and nothing in typer objects if they do.

    `sf plan` already meant "show the resolved configuration". Adding an orchestration group
    under the same name shadowed it: typer resolved the group, the command stopped
    answering, and the only complaint came from two unrelated tests that happened to call
    it. A CLI that silently loses a documented command to a name collision will lose the
    next one the same way.
    """
    from software_factory.cli import app

    groups = [group.name for group in app.registered_groups if group.name]
    commands = [command.name for command in app.registered_commands if command.name]

    collisions = sorted(set(groups) & set(commands))
    assert collisions == [], f"these names are both a group and a command: {collisions}"

    for names, kind in ((groups, "group"), (commands, "command")):
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert duplicates == [], f"duplicate {kind} name(s): {duplicates}"


def test_no_command_answers_a_missing_factory_with_a_traceback(tmp_path: Path) -> None:
    """`_fail` says "never a traceback: the user did nothing wrong", and one command did.

    `sf worker route` pointed at a directory with no `factory.yaml` — which is what the
    README's own example did, since it was the one worker command written without `--root`
    — answered a typo with twenty lines of Python internals. Fifty-one call sites kept the
    promise by catching individually, which is a promise that lapses on the next command
    somebody adds, so it is now kept by the group.

    Swept rather than spot-checked, for the same reason.
    """
    empty = tmp_path / "nowhere"
    empty.mkdir()
    commands = [
        ["worker", "route", "--root", str(empty), "--requires", "gpu"],
        ["worker", "list", "--root", str(empty)],
        ["plan", "--root", str(empty)],
        ["validate", "--root", str(empty)],
        ["audit", "--root", str(empty)],
        ["providers", "--root", str(empty)],
    ]

    leaked = []
    for argv in commands:
        result = runner.invoke(app, argv)
        if result.exception is not None and not isinstance(result.exception, SystemExit):
            leaked.append(f"{' '.join(argv)}: {type(result.exception).__name__}")
        if "Traceback" in result.output:
            leaked.append(f"{' '.join(argv)}: printed a traceback")

    assert leaked == [], leaked


def test_the_message_survives_the_group_handler(tmp_path: Path) -> None:
    """Caught is not the same as reported: the error's own remediation has to come out."""
    empty = tmp_path / "nowhere"
    empty.mkdir()

    result = runner.invoke(app, ["worker", "route", "--root", str(empty), "--requires", "gpu"])

    assert result.exit_code == EXIT_UNUSABLE
    assert "no factory.yaml" in result.output
    assert "Run `sf init` here" in result.output
