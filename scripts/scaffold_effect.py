#!/usr/bin/env python3
"""Did the small-tier scaffolding change what the model actually did?

`scaffolds_for` was correct, tested, and called by nothing until this repository's ninth
"control that exists and is not wired in" was found. Wiring it in is worth nothing if the
prompt reaches the model and the behaviour does not change, so this counts the behaviour.

    python scripts/scaffold_effect.py <state-dir> [<state-dir> ...]

Reads the transcripts a run left behind and asks one question per stage: did the agent
produce a numbered plan of three or more steps? That is what `decompose` asks for and it is
countable without reading, which matters -- reading transcripts for evidence of a thing you
just built is how you find it whether or not it is there.

**This is an observation, not a trial.** Runs differ in more than their scaffolding, the
sample is small, and nothing here is randomised or pre-registered. `sf experiment` exists
for claims that have to survive being wrong; this exists to notice whether there is
anything there to test.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

#: The section the harness composes at a scaffolded tier.
MARK = "how work is done here"

#: A numbered step, in the shapes a model actually writes them.
STEP = re.compile(r"^\s*(?:\d+\.|\*\*?\d+[.)])\s+", re.M)

#: What counts as a plan. Two steps is a sentence with a comma in it.
PLAN = 3


@dataclass(frozen=True, slots=True)
class Stage:
    run: str
    stage: str
    scaffolded: bool
    steps: int
    at_turn: int | None

    @property
    def planned(self) -> bool:
        return self.steps >= PLAN


def read(state: Path) -> list[Stage]:
    stages = []
    for path in sorted((state / "transcripts").glob("*.json")):
        messages = json.loads(path.read_text(encoding="utf-8"))
        scaffolded = any(
            MARK in m.get("content", "") for m in messages if m.get("role") == "system"
        )
        turns = [
            m["content"]
            for m in messages
            if m.get("role") == "assistant" and m.get("content", "").strip()
        ]
        # Every turn, not the first. The first version of this asked only about turn 0 and
        # scored a run that decomposed at turn 2 as not decomposing -- undercounting the
        # thing being measured, which is the direction that makes a fix look worse than it
        # is, and the direction nobody checks.
        counts = [len(STEP.findall(turn)) for turn in turns]
        stages.append(
            Stage(
                run=state.parent.name,
                stage=path.stem,
                scaffolded=scaffolded,
                steps=max(counts, default=0),
                at_turn=next((i for i, n in enumerate(counts) if n >= PLAN), None),
            )
        )
    return stages


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("states", nargs="+", type=Path, help="Factory state directories.")
    parser.add_argument("--json", dest="as_json", action="store_true")
    args = parser.parse_args()

    stages: list[Stage] = []
    for state in args.states:
        if not (state / "transcripts").is_dir():
            print(f"no transcripts under {state}", file=sys.stderr)
            continue
        stages.extend(read(state))

    if not stages:
        print("no transcripts found", file=sys.stderr)
        return 1

    groups = {flag: [s for s in stages if s.scaffolded is flag] for flag in (True, False)}
    counted: dict[str, dict[str, int]] = {
        "scaffolded": {
            "stages": len(groups[True]),
            "planned": sum(1 for s in groups[True] if s.planned),
        },
        "unscaffolded": {
            "stages": len(groups[False]),
            "planned": sum(1 for s in groups[False] if s.planned),
        },
    }

    if args.as_json:
        print(
            json.dumps(
                {"stages": len(stages), "summary": counted, "detail": [asdict(s) for s in stages]},
                indent=2,
            )
        )
        return 0

    print(f"{'run':10} {'scaf':6} {'steps':6} {'turn':5} stage")
    for s in stages:
        print(f"{s.run:10} {str(s.scaffolded)[:5]:6} {s.steps:<6} {s.at_turn!s:5} {s.stage}")
    for label, key in (("scaffolded", "scaffolded"), ("not scaffolded", "unscaffolded")):
        row = counted[key]
        if row["stages"]:
            print(
                f"\n{label}: {row['planned']}/{row['stages']} stages produced a plan of {PLAN}+ steps"
            )
    print("\nAn observation, not a trial: the runs differ in more than their scaffolding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
