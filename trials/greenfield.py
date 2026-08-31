"""Greenfield: the factory works a repository that has nothing in it yet.

The risks here are all about *absence*. There is no test suite for `tests-pass` to run, no
build for `build-green` to check, no history for the precedent section to retrieve, and no
spec for `spec-agreement` to compare against. Every one of those is a place a gate could
report "pass" when what it means is "there was nothing to check" -- and this project's whole
argument is that those two must not look alike.

So the question is not "can it build a library". It is: **on an empty repository, does the
factory tell the truth about what it could not verify?**
"""

from __future__ import annotations

from pathlib import Path

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

REQUEST = """\
Start a small library that parses ISO-8601 durations into seconds. Nothing exists yet: this
is the first change in an empty repository.
"""


def prepare(root: Path) -> Path:
    """An empty repository. One commit so there is a parent, and nothing else."""
    repo = root / "durations"
    repo.mkdir(parents=True)
    write(repo / "README.md", "# durations\n\nNothing here yet.\n")
    git(repo, "init", "--quiet", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "empty repository")
    return repo


def run(root: Path) -> TrialReport:
    repo = prepare(root)
    definition = build_factory(root / "factory", name="durations", owner="trial", repo="durations")

    provider = scripted(
        {
            "findings": "The repository is empty; there is no prior art to follow.",
            "scope": "one new module, no existing callers",
            "calibration": calibration(0.7, ("README.md",)),
            "constraints": ["no test runner is configured in this repository yet"],
        },
        {
            "plan": "Add `durations.py` with a single `parse_duration` function.",
            "acceptance": ["`parse_duration('PT1M30S')` returns 90"],
            "calibration": calibration(0.75, ("README.md",)),
            "decisions": ["kept it a single function; a class would be premature here"],
        },
        {
            "summary": "Added a parser for ISO-8601 durations.",
            "claims": ["`parse_duration('PT1M30S')` returns 90."],
            "calibration": calibration(0.75, ("durations.py",)),
            "artifacts": ["durations.py"],
        },
        {
            "verdict": "accept",
            "findings": [],
            "calibration": calibration(0.7, ("durations.py",)),
        },
        {
            "summary": "Handed off on branch factory/durations.",
            "branch": "factory/durations",
            "calibration": calibration(0.7, ("durations.py",)),
        },
    )

    outcome = coordinator_for(definition, repo, root / "state", provider).run(
        work_item(
            title="Parse ISO-8601 durations",
            request=REQUEST,
            work_class=WorkClass.FEATURE,
            ref="trial/durations#1",
        )
    )

    gates = collect_gates(outcome)
    unenforceable = [g for g in gates if g.outcome == "unenforceable"]

    report = TrialReport(
        name="Greenfield — an empty repository",
        question=(
            "On a repository with no tests, no build and no history, does the factory report "
            "what it could not verify, or does it report success?"
        ),
        stages=[s.stage.value for s in outcome.stages],
        gates=gates,
        final_stage=outcome.item.stage.value,
        blocker=outcome.item.blocker.value if outcome.item.blocker else "",
        blocker_action=outcome.item.blocker_action,
    )

    report.require(
        len(report.stages) >= 4,
        "The full path runs on a repository the factory has never seen: "
        f"{' → '.join(report.stages)}.",
    )
    report.require(
        bool(unenforceable),
        "At least one gate reported *unenforceable* rather than passing "
        f"({', '.join(sorted({g.gate for g in unenforceable})) or 'none did'}). This is the "
        "property the trial exists for: a repository with no validation cannot satisfy a "
        "validation gate, and saying so is different from passing.",
    )
    report.require(
        report.reached_handoff,
        "A work item reached HANDOFF with a real diff and an evidence bundle over real artifacts.",
    )
    report.unproven = [
        "Whether the change is any good. The model's output is scripted here, so this says "
        "nothing about the quality of what a real model would produce.",
        "Whether the *degraded* label travels: the gate says unenforceable, and whether a "
        "reviewer downstream actually sees it depends on a change description this trial "
        "does not produce.",
    ]
    return report
