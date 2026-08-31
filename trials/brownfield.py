"""Brownfield: a repository with history, a real test suite, and a real defect.

The greenfield trial asks whether the factory is honest about what it *cannot* check. This
one asks the opposite and harder question: with everything present, does the keystone gate
actually stop the thing it exists to stop?

`regression-proven` (FR-13.3, FR-13.3a) is the gate the whole assurance argument rests on. A
defect fix must carry a test that fails at the parent commit *for the right reason* -- not an
import error, not a collection failure, but the defect itself. It is also the gate a model
can most easily appear to satisfy: writing a test that passes both before and after is
trivial and looks identical in a diff.

So the trial runs the same defect twice.

* **Attempt one** fixes the bug and adds no test. The gate must block, and the blocker must
  say what would clear it.
* **Attempt two** fixes the bug and adds a test that genuinely fails on the old code. The
  gate must pass -- and it must pass because a suite really ran at the parent commit and
  really failed there, not because evidence was asserted.

The second half is what makes this worth doing. Until now `_gate_context` supplied
`has_test_command=False` and `build_ok=True` as constants, so the gate had never once
compared a real run at the tip against a real one at the parent. It blocked attempt one
correctly, for the weaker reason that no evidence existed at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from software_factory.orchestrator import WorkClass
from trials.harness import (
    TrialReport,
    build_factory,
    calibration,
    collect_gates,
    coordinator_for,
    git,
    scripted,
    work_item,
    write,
)

# The defect: `split_csv` keeps a UTF-8 BOM on the first header, so the first column is
# named "﻿id" and every lookup by "id" misses. Real, small, and exactly the shape that
# passes review when nobody demonstrates it.
BUGGY = '''\
    def split_csv(text):
        """Split a CSV header line into column names."""
        return [cell.strip() for cell in text.split(",")]
'''

FIXED = '''\
    def split_csv(text):
        """Split a CSV header line into column names."""
        return [cell.strip().lstrip("\\ufeff") for cell in text.split(",")]
'''

EXISTING_TESTS = """\
    from importer import split_csv


    def test_plain_header_splits():
        assert split_csv("id,name,total") == ["id", "name", "total"]


    def test_whitespace_is_trimmed():
        assert split_csv(" id , name ") == ["id", "name"]
"""

REGRESSION_TEST = """\
    from importer import split_csv


    def test_a_byte_order_mark_is_not_part_of_the_first_column_name():
        assert split_csv("\\ufeffid,name") == ["id", "name"]
"""

REQUEST = """\
Uploading a UTF-8 CSV saved by a spreadsheet names the first column oddly: every lookup by
`id` misses, because the header carries a byte-order mark. `split_csv` should not treat it as
part of the name.
"""


def prepare(root: Path) -> Path:
    """A repository with two commits, a passing suite, and the defect present."""
    repo = root / "importer"
    repo.mkdir(parents=True)
    write(repo / "pyproject.toml", '[project]\nname = "importer"\nversion = "0.1.0"\n')
    write(repo / "importer.py", BUGGY)
    write(repo / "test_importer.py", EXISTING_TESTS)
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "the importer, with the defect present")
    write(repo / "README.md", "# importer\n")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "add a readme")
    return repo


def _defect_item(ref: str) -> Any:
    return work_item(
        title="CSV importer keeps the BOM on the first column name",
        request=REQUEST,
        work_class=WorkClass.DEFECT,
        ref=ref,
    )


def _script(*, with_test: bool) -> Any:
    """The model's output. The `repo.write` calls are what actually change the workspace."""
    build_claims = ["`split_csv('\\ufeffid,name')` returns `['id', 'name']`."]
    if with_test:
        build_claims.append("A new test fails on the previous commit and passes on this one.")
    return scripted(
        {
            "findings": "split_csv does not strip the BOM, so the first column is named oddly.",
            "scope": "one function in importer.py",
            "calibration": calibration(0.85, ("importer.py:3",)),
        },
        {
            "summary": "Stripped the byte-order mark from the first column name.",
            "claims": build_claims,
            "calibration": calibration(0.85, ("importer.py:3",)),
            "decisions": [
                "stripped on every cell rather than only the first; it is a no-op elsewhere"
            ],
        },
        {"verdict": "accept", "findings": [], "calibration": calibration(0.8, ("importer.py:3",))},
        {
            "summary": "Handed off.",
            "branch": "factory/bom-headers",
            "calibration": calibration(0.8, ("importer.py:3",)),
        },
    )


def _apply(repo: Path, *, with_test: bool) -> None:
    """Make the change the scripted model claims to have made.

    Applied directly rather than through a tool call because the stub model emits no tool
    calls: the trial is about what the *gates* do with a real diff, and a change that only
    exists in the model's prose would test nothing.
    """
    write(repo / "importer.py", FIXED)
    if with_test:
        write(repo / "test_bom.py", REGRESSION_TEST)


def run(root: Path) -> TrialReport:
    report = TrialReport(
        name="Brownfield — the keystone gate against a real defect",
        question=(
            "With a real suite and a real defect, does `regression-proven` block a fix "
            "nobody demonstrated, and pass one that a real run at the parent commit "
            "actually fails?"
        ),
    )

    # --- attempt one: the fix, no test -------------------------------------------
    repo = prepare(root / "without-test")
    definition = build_factory(
        root / "without-test" / "factory", name="importer", owner="trial", repo="importer"
    )
    coordinator = coordinator_for(
        definition, repo, root / "without-test" / "state", _script(with_test=False)
    )
    workspace_root = _run_with_edit(coordinator, _defect_item("trial/importer#1"), with_test=False)
    first = workspace_root

    # --- attempt two: the fix, with a test that fails on the old code -------------
    repo2 = prepare(root / "with-test")
    definition2 = build_factory(
        root / "with-test" / "factory", name="importer", owner="trial", repo="importer"
    )
    coordinator2 = coordinator_for(
        definition2, repo2, root / "with-test" / "state", _script(with_test=True)
    )
    second = _run_with_edit(coordinator2, _defect_item("trial/importer#2"), with_test=True)

    report.stages = [s.stage.value for s in second.stages]
    report.gates = collect_gates(first) + collect_gates(second)
    report.final_stage = second.item.stage.value
    report.blocker = second.item.blocker.value if second.item.blocker else ""
    report.blocker_action = second.item.blocker_action

    def gate_of(outcome: Any, name: str) -> Any:
        for stage in outcome.stages:
            for result in stage.gates.results:
                if result.gate == name:
                    return result
        return None

    blocked_without = gate_of(first, "regression-proven")
    passed_with = gate_of(second, "regression-proven")
    tests_without = gate_of(first, "tests-pass")

    report.require(
        blocked_without is not None and blocked_without.blocks,
        "A defect fix with no regression test is blocked by `regression-proven` at BUILD, "
        f"and the blocker says what would clear it: {first.item.blocker_action!r}.",
    )
    report.require(
        tests_without is not None and tests_without.outcome.value == "pass",
        "The repository's own suite really ran: `tests-pass` reports a measured result "
        "rather than 'unenforceable', which it did for every run before this trial.",
    )
    report.require(
        passed_with is not None and not passed_with.blocks,
        "The same fix with a test that fails at the parent commit passes the gate.",
    )
    report.require(
        second.item.stage.value == "HANDOFF",
        "The demonstrated fix reaches HANDOFF.",
    )

    report.unproven = [
        "Whether a model would write that regression test unprompted. The output here is "
        "scripted, so this shows the gate is enforceable, not that it is satisfiable by an "
        "agent working alone.",
        "The parent-commit run is a second full suite execution. On a repository whose "
        "suite takes ten minutes this doubles the cost of every defect fix, and nothing "
        "here measures that.",
    ]
    return report


def _run_with_edit(coordinator: Any, item: Any, *, with_test: bool) -> Any:
    """Run the work item, applying the change the model claims to make.

    The workspace is a copy of the repository, so the edit has to land there rather than in
    the source -- which is also the point: the gates see a real diff against a real parent.
    """
    original = coordinator.workspaces.create

    def create_and_edit(**kwargs: Any) -> Any:
        workspace = original(**kwargs)
        _apply(workspace.root, with_test=with_test)
        return workspace

    coordinator.workspaces.create = create_and_edit  # type: ignore[method-assign]
    try:
        return coordinator.run(item)
    finally:
        coordinator.workspaces.create = original  # type: ignore[method-assign]
