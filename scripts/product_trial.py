#!/usr/bin/env python3
"""Build a real product with the factory, one work item at a time.

`live_trial.py` proves the factory can carry *one* work item through a real model. That is
the smallest honest claim, and it is not the interesting one. A factory is a thing you use
for months, and the questions that decide whether it is usable are all about the second,
fifth and twentieth change:

  * Does a small clear change stay cheap, or does every request cost the same?
  * Does an ambiguous request get refused, or silently guessed at?
  * Does a defect fix have to prove itself, or does a green suite count as proof?
  * When the factory itself introduces a bug, does the next run catch it?
  * Does a large change get decomposed, or attempted whole and abandoned?

So this builds `jsonlint` -- a real command-line JSON validator -- through a scripted
sequence of work items chosen to hit each of those. Every step names what it is testing and
what would count as the factory getting it wrong, *before* the run, so a charitable reading
afterwards is not available.

    SF_PROVIDER_API_KEY=... python scripts/product_trial.py \\
        --provider openai-compatible --base-url https://host/v1 --model some-model

Writes `docs/product-trial.md`. Not part of the test suite: it needs a live model, costs
real money, and takes as long as the model takes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The product's starting point. Deliberately a *working* skeleton rather than an empty
#: directory: a factory asked to create a repository from nothing is being asked a different
#: question, and the one worth answering is what it does to code that already exists.
SEED = {
    "jsonlint/__init__.py": '"""A small JSON validator."""\n\n__version__ = "0.1.0"\n',
    "jsonlint/core.py": (
        '"""Validate JSON text."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "\n"
        "\n"
        "def validate(text: str) -> bool:\n"
        '    """True when `text` is valid JSON."""\n'
        "    try:\n"
        "        json.loads(text)\n"
        "    except ValueError:\n"
        "        return False\n"
        "    return True\n"
    ),
    "tests/test_core.py": (
        "from jsonlint.core import validate\n"
        "\n"
        "\n"
        "def test_accepts_an_object():\n"
        "    assert validate('{\"a\": 1}')\n"
        "\n"
        "\n"
        "def test_rejects_a_trailing_comma():\n"
        "    assert not validate('{\"a\": 1,}')\n"
    ),
    "pyproject.toml": (
        "[project]\n"
        'name = "jsonlint"\n'
        'version = "0.1.0"\n'
        'requires-python = ">=3.11"\n'
        "\n"
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n'
    ),
    "README.md": "# jsonlint\n\nA small JSON validator.\n",
}


@dataclass(frozen=True, slots=True)
class Step:
    """One work item, and what it is here to find out.

    `expectation` and `failure` are written before the run and printed beside the result.
    Deciding afterwards what a run was testing is how every trial comes out a success.
    """

    name: str
    title: str
    request: str
    work_class: str
    tests: str
    """What this step is probing: size, clarity, class, or recovery."""
    expectation: str
    failure: str
    """What the factory getting this wrong would look like. The falsifier for this step."""
    seed_patch: dict[str, str] = field(default_factory=dict)
    """Files written to the repository *before* the run, for steps that need a defect to
    exist first. Committed, so a fix has a parent commit to prove itself against."""


STEPS: tuple[Step, ...] = (
    Step(
        name="small-clear",
        title="Report the version",
        request=(
            "Add a `--version` flag to the command line entry point that prints the "
            "package version from `jsonlint.__version__` and exits 0."
        ),
        work_class="feature",
        tests="a small, completely unambiguous change",
        expectation="reaches handoff cheaply, with a test",
        failure=(
            "spending as much as the largest step, or blocking on a clarification nobody "
            "needs -- a factory whose floor cost is its ceiling cost is unusable for the "
            "changes people actually make most often"
        ),
    ),
    Step(
        name="large-clear",
        title="Report where the error is",
        request=(
            "Validation currently answers only true or false. Change it to report, for "
            "invalid input, the line and column of the first syntax error and a message "
            "naming what was expected. Keep `validate` returning a bool for existing "
            "callers and add whatever new surface the richer result needs."
        ),
        work_class="feature",
        tests="a large change that touches an existing public API",
        expectation=(
            "reaches handoff with the old signature intact and new tests covering both "
            "the bool path and the located error"
        ),
        failure=(
            "breaking `validate`'s existing contract, or reaching handoff with the new "
            "surface untested -- both pass a test suite that only knows the old behaviour"
        ),
    ),
    Step(
        name="ambiguous",
        title="Make the errors better",
        request="The errors could be better. Improve them.",
        work_class="feature",
        tests="a request with no checkable acceptance criterion",
        expectation=(
            "blocked for clarification, or a change whose acceptance criteria the factory "
            "wrote down and can be judged against"
        ),
        failure=(
            "quietly guessing and reaching handoff -- an unfalsifiable change nobody asked "
            "for, which is the single most expensive thing an autonomous factory can do"
        ),
    ),
    Step(
        name="defect-fix",
        title="Duplicate keys are accepted silently",
        request=(
            "`validate` accepts an object with duplicate keys, which is legal JSON but "
            "almost always a mistake in a config file. Reject it, and say which key was "
            "duplicated."
        ),
        work_class="defect",
        tests="the regression-proven gate on a genuine defect",
        expectation=(
            "a test that fails at the parent commit for the right reason, then passes -- "
            "and no handoff without one"
        ),
        failure=(
            "reaching handoff with a test that passed before the change, which would mean "
            "the keystone gate can be satisfied by a test that proves nothing"
        ),
    ),
    Step(
        name="planted-defect",
        title="Trailing commas are accepted",
        request=(
            "`validate` now accepts a trailing comma in an object, which is not valid JSON. "
            "Fix it so trailing commas are rejected, and keep the duplicate-key behaviour."
        ),
        work_class="defect",
        tests="recovery from a bug in code the factory has already touched",
        expectation="finds the planted regression and fixes it without undoing earlier work",
        failure=(
            "fixing the trailing comma by reverting the file to its original state, which "
            "passes the new test and silently deletes every previous change"
        ),
        seed_patch={
            "jsonlint/core.py": (
                '"""Validate JSON text."""\n'
                "\n"
                "from __future__ import annotations\n"
                "\n"
                "import json\n"
                "import re\n"
                "\n"
                "\n"
                "def validate(text: str) -> bool:\n"
                '    """True when `text` is valid JSON."""\n'
                "    # A planted defect: stripping trailing commas before parsing makes\n"
                "    # invalid JSON validate successfully.\n"
                '    cleaned = re.sub(r",\\s*([}\\]])", r"\\1", text)\n'
                "    try:\n"
                "        json.loads(cleaned)\n"
                "    except ValueError:\n"
                "        return False\n"
                "    return True\n"
            )
        },
    ),
    Step(
        name="refactor",
        title="Separate parsing from reporting",
        request=(
            "The validation and the error formatting are in one function. Split them so "
            "the formatting can be tested on its own, without changing any behaviour."
        ),
        work_class="refactor",
        tests="a change that must alter structure and nothing else",
        expectation="handoff with every existing test passing and no behaviour change",
        failure="changing behaviour under cover of a refactor, which no test names",
    ),
)


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def seed_repo(root: Path) -> Path:
    repo = root / "jsonlint"
    repo.mkdir(parents=True)
    for name, body in SEED.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    git(["init", "-q", "-b", "main"], repo)
    git(["config", "user.email", "factory@example.test"], repo)
    git(["config", "user.name", "factory"], repo)
    git(["add", "-A"], repo)
    git(["commit", "-qm", "jsonlint: a working skeleton"], repo)
    return repo


@dataclass
class Result:
    step: Step
    stage: str = ""
    blocker: str = ""
    action: str = ""
    gates: list[dict[str, Any]] = field(default_factory=list)
    stages_run: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    changed: list[str] = field(default_factory=list)
    tests_pass: bool | None = None
    error: str = ""

    @property
    def reached_handoff(self) -> bool:
        return self.stage == "HANDOFF" and not self.blocker


def run_step(step: Step, *, repo: Path, factory: Path, state: Path, provider: Any) -> Result:
    from software_factory.definition import load_strict
    from software_factory.ledger import EntryType, Ledger
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator

    if step.seed_patch:
        for name, body in step.seed_patch.items():
            (repo / name).write_text(body, encoding="utf-8")
        git(["add", "-A"], repo)
        git(["commit", "-qm", f"planted: {step.title}"], repo)

    before = (
        len(list(Ledger(state / "ledger.jsonl").read())) if (state / "ledger.jsonl").exists() else 0
    )
    item = WorkItem(
        id=new_id(),
        factory="jsonlint",
        title=step.title,
        request=step.request,
        source=SourceContext(provider="cli", kind="product-trial", ref=step.name),
        work_class=WorkClass(step.work_class),
    )
    coordinator = local_coordinator(
        load_strict(factory),
        repo=repo,
        state_dir=state,
        provider=provider,
        allow_unsandboxed=True,
    )

    result = Result(step=step)
    started = time.monotonic()
    try:
        outcome = coordinator.run(item)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        result.seconds = time.monotonic() - started
        return result
    result.seconds = time.monotonic() - started

    result.stage = item.stage.value
    result.blocker = item.blocker.value if item.blocker else ""
    result.action = item.blocker_action
    result.stages_run = [s.stage.value for s in outcome.stages]
    result.changed = list(outcome.changed_paths)

    entries = list(Ledger(state / "ledger.jsonl").read())[before:]
    result.gates = [
        {
            "gate": e.payload.get("gate"),
            "outcome": e.payload.get("outcome"),
            "blocks": e.payload.get("blocks"),
        }
        for e in entries
        if e.type is EntryType.GATE_EVALUATED
    ]
    for entry in entries:
        if entry.type is EntryType.MODEL_CALLED:
            result.input_tokens += int(entry.payload.get("inputTokens", 0) or 0)
            result.output_tokens += int(entry.payload.get("outputTokens", 0) or 0)

    # The product's own tests, run against the tree the factory left behind. A factory that
    # reaches handoff on a repository whose tests fail has reached handoff on nothing, and
    # its own gates are the last place to learn that from.
    if outcome.changed_paths or result.reached_handoff:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        result.tests_pass = completed.returncode == 0
    return result


HEADER = """# Product trial: building `jsonlint`

| Field | Value |
| --- | --- |
| Generated by | `python scripts/product_trial.py` |
| Provider | {provider} |
| Model | {model} |
| Steps | {steps} |
| Reached handoff | {handoffs} |
| Total tokens | {tokens} |

`live_trial.py` proves the factory can carry *one* work item through a real model. That is
the smallest honest claim and not the interesting one. A factory is a thing you use for
months, and what decides whether it is usable is the second, fifth and twentieth change.

So this builds a real product through a sequence of work items chosen to probe the places a
factory fails: a change too small to justify the ceremony, one too large to do at once, a
request with nothing checkable in it, a defect that must prove its own fix, a bug planted in
code the factory had already touched, and a refactor that must change nothing.

**Every step's expectation and falsifier were written before the run.** Deciding afterwards
what a run was testing is how every trial comes out a success.

"""


def render(results: list[Result], *, provider: str, model: str) -> str:
    handoffs = sum(1 for r in results if r.reached_handoff)
    tokens = sum(r.input_tokens + r.output_tokens for r in results)
    body = HEADER.format(
        provider=provider,
        model=model,
        steps=len(results),
        handoffs=f"{handoffs} of {len(results)}",
        tokens=f"{tokens:,}",
    )
    body += "| Step | Probes | Reached | Tests | Tokens | Seconds |\n"
    body += "| --- | --- | --- | --- | --- | --- |\n"
    for r in results:
        tests = {True: "pass", False: "**FAIL**", None: "—"}[r.tests_pass]
        reached = r.stage or "crashed"
        if r.blocker:
            reached = f"{reached} ({r.blocker})"
        body += (
            f"| {r.step.name} | {r.step.tests} | {reached} | {tests} | "
            f"{r.input_tokens + r.output_tokens:,} | {r.seconds:.0f} |\n"
        )
    body += "\n"

    for r in results:
        body += f"## {r.step.name} — {r.step.title}\n\n"
        body += f"**Probes.** {r.step.tests}\n\n"
        body += f"**Expected.** {r.step.expectation}\n\n"
        body += f"**Would be wrong if.** {r.step.failure}\n\n"
        body += f"**Request.** {r.step.request}\n\n"
        if r.error:
            body += f"**The run raised:** `{r.error}`\n\n"
            continue
        body += f"Reached **{r.stage}** after {' → '.join(r.stages_run) or 'no stages'}.\n\n"
        if r.blocker:
            body += f"Blocked: `{r.blocker}` — {r.action}\n\n"
        if r.changed:
            body += "Changed: " + ", ".join(f"`{p}`" for p in r.changed) + "\n\n"
        if r.tests_pass is not None:
            body += f"The product's own tests: **{'pass' if r.tests_pass else 'FAIL'}**\n\n"
        blocking = [g for g in r.gates if g.get("blocks")]
        if blocking:
            body += "| Blocking gate | Outcome |\n| --- | --- |\n"
            for gate in blocking:
                body += f"| {gate['gate']} | {gate['outcome']} |\n"
            body += "\n"
        body += f"Spend: {r.input_tokens:,} in, {r.output_tokens:,} out, {r.seconds:.0f}s.\n\n"
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="openai-compatible")
    parser.add_argument("--model", required=True)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--out", default="docs/product-trial.md")
    parser.add_argument("--only", default=None, help="Run one step by name.")
    parser.add_argument("--keep", default=None, help="Keep the tree at this path.")
    args = parser.parse_args()

    import tempfile

    from software_factory.providers.registry import resolve
    from software_factory.scaffold import init_factory

    resolution = resolve(args.provider, base_url=args.base_url)
    if not resolution.usable:
        print(f"provider not usable: {resolution.reason}", file=sys.stderr)
        return 2

    root = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="sf-product-"))
    root.mkdir(parents=True, exist_ok=True)
    repo = seed_repo(root)
    factory = root / "factory"
    init_factory(factory, name="jsonlint", owner="acme", repo="jsonlint", local_model=args.model)
    state = root / "state"

    chosen = [s for s in STEPS if not args.only or s.name == args.only]
    if args.only and not chosen:
        print(
            f"unknown step {args.only!r}; known: {', '.join(s.name for s in STEPS)}",
            file=sys.stderr,
        )
        return 2

    results: list[Result] = []
    for index, step in enumerate(chosen, start=1):
        print(f"[{index}/{len(chosen)}] {step.name}: {step.title}", file=sys.stderr, flush=True)
        result = run_step(
            step, repo=repo, factory=factory, state=state, provider=resolution.provider
        )
        results.append(result)
        mark = (
            "handoff" if result.reached_handoff else (result.blocker or result.error or "stopped")
        )
        print(
            f"        → {result.stage or 'crashed'} [{mark}] "
            f"{result.input_tokens + result.output_tokens:,} tokens, {result.seconds:.0f}s",
            file=sys.stderr,
            flush=True,
        )
        # Committed between steps so the next one has a real parent commit: the
        # regression-proven gate compares against HEAD, and a tree that never commits gives
        # every step the same parent and makes every test look pre-existing.
        if result.changed:
            git(["add", "-A"], repo)
            git(["commit", "-qm", f"{step.name}: {step.title}"], repo)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(results, provider=args.provider, model=args.model), encoding="utf-8")
    print(f"\nwrote {out}", file=sys.stderr)
    print(f"tree kept at {root}", file=sys.stderr)

    summary = {
        "steps": len(results),
        "handoffs": sum(1 for r in results if r.reached_handoff),
        "testsFailing": sum(1 for r in results if r.tests_pass is False),
        "crashes": sum(1 for r in results if r.error),
    }
    print(json.dumps(summary), file=sys.stderr)
    # Non-zero only on a crash or a broken product tree. A blocked step is a *result*, and
    # often the correct one -- the ambiguous step is expected to block.
    return 1 if summary["crashes"] or summary["testsFailing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
