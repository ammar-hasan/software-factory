"""The measurement of the scaffolding's effect, measured.

An instrument built to find out whether a fix worked is the instrument most likely to
agree with whoever built it. Both directions are checked: it must count a plan where
there is one, and must not count one where there is not.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from scaffold_effect import MARK, PLAN, read

PLANNED = (
    "Let me decompose the build into steps:\n\n"
    "**Plan**\n"
    "1. Pre-resolve: verify the parser payload table — check: symbols resolve.\n"
    "2. Checkpoint baseline.\n"
    "3. Write the failing test — check: fails for the right reason.\n"
)
ORIENTING = "I'll start by reading the design docs and current code to understand the change."


def transcript(path: Path, *, scaffolded: bool, turns: list[str]) -> None:
    messages = [{"role": "system", "content": "<harness>invariants</harness>"}]
    if scaffolded:
        messages.append({"role": "system", "content": f"<harness>...{MARK}...</harness>"})
    messages += [{"role": "assistant", "content": t} for t in turns]
    path.write_text(json.dumps(messages), encoding="utf-8")


def state(root: Path) -> Path:
    (root / "transcripts").mkdir(parents=True)
    return root


def test_a_numbered_plan_is_counted(tmp_path: Path) -> None:
    s = state(tmp_path / "run" / "state")
    transcript(s / "transcripts" / "wi:build:1.json", scaffolded=True, turns=[PLANNED])

    stage = read(s)[0]

    assert stage.scaffolded is True
    assert stage.steps >= PLAN
    assert stage.planned is True


def test_orienting_prose_is_not_a_plan(tmp_path: Path) -> None:
    """The failure that matters. An instrument that scores "I'll start by reading the
    code" as decomposition reports the fix worked whether or not it did."""
    s = state(tmp_path / "run" / "state")
    transcript(s / "transcripts" / "wi:build:1.json", scaffolded=False, turns=[ORIENTING])

    stage = read(s)[0]

    assert stage.scaffolded is False
    assert stage.planned is False


def test_a_plan_after_the_first_turn_still_counts(tmp_path: Path) -> None:
    """The first version of this asked only about turn 0 and scored a run that decomposed
    at turn 2 as not decomposing — undercounting the thing being measured, which is the
    direction that makes a fix look worse than it is, and the direction nobody checks."""
    s = state(tmp_path / "run" / "state")
    transcript(
        s / "transcripts" / "wi:build:1.json",
        scaffolded=True,
        turns=[ORIENTING, "The design is well-specified.", PLANNED],
    )

    stage = read(s)[0]

    assert stage.planned is True
    assert stage.at_turn == 2


def test_the_scaffolding_marker_is_the_one_the_harness_writes() -> None:
    """A marker that drifts from the prompt silently reports every run as unscaffolded."""
    from software_factory.harness.loop import _scaffolding
    from software_factory.harness.routing import Scaffold

    assert MARK in _scaffolding(frozenset(Scaffold))
