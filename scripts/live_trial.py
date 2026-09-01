#!/usr/bin/env python3
"""Run a real work item through a real model endpoint, end to end.

Every trial, screenshot and end-to-end run in this repository uses a scripted stub. That is
the right default -- a suite that needs a model is a suite nobody runs -- and it means the
strongest honest claim the repository can make on its own is *the harness works*, not *the
factory works*. This script is how that second claim gets made, and it is deliberately one
command with no arguments beyond an endpoint.

It is not part of the test suite and never will be. A test that needs a live model is a test
that fails when a laptop is on a train, and the failure teaches people to skip the suite.

    # a local model, no key, nothing leaves the machine
    python scripts/live_trial.py --provider local --model qwen2.5-coder

    # any OpenAI-compatible endpoint
    OPENAI_API_KEY=... python scripts/live_trial.py --provider openai --model gpt-4o-mini

    # or the Anthropic wire
    ANTHROPIC_API_KEY=... python scripts/live_trial.py --provider anthropic --model claude-...

    # any other OpenAI-compatible host
    SF_PROVIDER_API_KEY=... python scripts/live_trial.py \\
        --provider openai-compatible --base-url https://host/v1 --model some-model

What it proves, and what it does not: a green run here means this factory drove a real model
through triage, build, review and handoff on a real git repository, with real gates, and
produced a real diff. It does not mean the change is *good* -- that is what the gates and the
scorers are for, and this prints their verdicts rather than summarising them charitably.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_FILES = {
    "importer.py": (
        "def strip_bom(text):\n"
        "    # Returns the text unchanged, which is the defect.\n"
        "    return text\n"
        "\n"
        "\n"
        "def read_headers(line):\n"
        "    return [cell.strip() for cell in strip_bom(line).split(',')]\n"
    ),
    "test_importer.py": (
        "from importer import read_headers\n"
        "\n"
        "\n"
        "def test_plain_headers():\n"
        "    assert read_headers('a,b,c') == ['a', 'b', 'c']\n"
    ),
    "README.md": "# demo\n\nA tiny CSV importer.\n",
}

REQUEST = (
    "Uploading a UTF-8 CSV that begins with a byte-order mark names the first column "
    "oddly: the BOM is carried into the first header. `strip_bom` in importer.py returns "
    "its input unchanged. Fix it, and add a test that fails before the fix."
)


def build_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    for name, body in REPO_FILES.items():
        (repo / name).write_text(body, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "trial",
        "GIT_AUTHOR_EMAIL": "trial@example.test",
        "GIT_COMMITTER_NAME": "trial",
        "GIT_COMMITTER_EMAIL": "trial@example.test",
    }
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "init")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)
    return repo


def make_provider(name: str, base_url: str | None) -> Any:
    """Resolve the provider through the registry `sf providers` reads.

    Resolved rather than constructed here, so this script cannot drift from what the
    product says it supports, and so the "why is this unusable" sentence a reader sees is
    the same sentence `sf doctor` prints.
    """
    from software_factory.providers.registry import UnknownProviderError, resolve

    try:
        resolution = resolve(name, base_url=base_url)
    except UnknownProviderError as exc:
        raise SystemExit(f"{exc}\n{exc.remediation}") from exc

    if not resolution.usable:
        raise SystemExit(
            f"the {name!r} provider cannot serve requests: {resolution.reason}\n"
            f"Set {resolution.spec.api_key_env or 'the endpoint'} and try again. Never pass "
            "a key as a flag: `ps` shows a flag to every process on the host."
        )
    return resolution.provider, resolution.base_url


def reachable(provider: Any, model: str) -> str:
    """One tiny call, before building anything.

    A run that fails on turn one after two minutes of setup teaches nothing that a
    two-second check would not have said immediately.
    """
    from software_factory.providers.base import Message, ProviderError, Role

    try:
        provider.complete(
            [Message(role=Role.USER, content="Reply with the single word: ready")],
            model=model,
            max_tokens=16,
        )
    except ProviderError as exc:
        return str(exc)
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="local", help="A provider `sf providers` knows.")
    parser.add_argument("--model", required=True, help="The model id the endpoint serves.")
    parser.add_argument("--base-url", default=None, help="Override the endpoint.")
    parser.add_argument("--json", action="store_true", help="Emit the result as JSON.")
    parser.add_argument("--keep", default=None, help="Keep the workspace at this path.")
    args = parser.parse_args()

    from software_factory.definition import load_strict
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.scaffold import init_factory

    provider, endpoint = make_provider(args.provider, args.base_url)
    # `sf init` writes a ladder whose tiers all point at the local tier's model, so the
    # single `--model` covers every stage. A factory with a real ladder names its own.
    print(f"provider   {args.provider}  →  {endpoint}", file=sys.stderr)
    print(f"model      {args.model}", file=sys.stderr)

    problem = reachable(provider, args.model)
    if problem:
        print(f"\nthe endpoint is not usable: {problem}", file=sys.stderr)
        print(
            "\nNothing was built. Start the endpoint, or point --base-url somewhere that answers.",
            file=sys.stderr,
        )
        return 2
    print("endpoint   reachable\n", file=sys.stderr)

    directory = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="sf-live-"))
    directory.mkdir(parents=True, exist_ok=True)
    repo = build_repo(directory)
    factory = directory / "factory"
    # The model goes into the *definition's* ladder, because that is where the coordinator
    # reads it from. Passing `--model` and not writing it here is how the first run of this
    # script reached the endpoint, was told "model does not exist", and reported a gate
    # failure about calibration -- the flag looked like it had been applied and had not.
    init_factory(factory, name="live", owner="acme", repo="importer", local_model=args.model)
    definition = load_strict(factory)
    state = directory / "state"

    item = WorkItem(
        id=new_id(),
        factory="live",
        title="CSV importer mangles BOM headers",
        request=REQUEST,
        source=SourceContext(provider="cli", kind="live-trial", ref="live"),
        work_class=WorkClass.DEFECT,
    )

    started = time.monotonic()
    coordinator = local_coordinator(
        definition, repo=repo, state_dir=state, provider=provider, allow_unsandboxed=True
    )
    outcome = coordinator.run(item)
    elapsed = time.monotonic() - started

    diff = subprocess.run(
        ["git", "diff", "HEAD"], cwd=repo, capture_output=True, text=True, check=False
    ).stdout

    from software_factory.ledger import EntryType, Ledger

    entries = list(Ledger(state / "ledger.jsonl").read())
    gates = [
        {"gate": e.payload.get("gate"), "outcome": e.payload.get("outcome")}
        for e in entries
        if e.type is EntryType.GATE_EVALUATED
    ]
    calls = [e for e in entries if e.type is EntryType.MODEL_CALLED]
    result = {
        "provider": args.provider,
        "endpoint": endpoint,
        "model": args.model,
        "workItem": item.id,
        "finalStage": item.stage.value,
        "blocked": item.blocker.value if item.blocker else None,
        "blockerAction": item.blocker_action,
        "seconds": round(elapsed, 1),
        "modelCalls": len(calls),
        "inputTokens": sum(int(e.payload.get("inputTokens", 0) or 0) for e in calls),
        "outputTokens": sum(int(e.payload.get("outputTokens", 0) or 0) for e in calls),
        "costUnits": round(sum(float(e.payload.get("costUnits", 0) or 0) for e in calls), 4),
        "gates": gates,
        "gatesFailed": [g for g in gates if str(g["outcome"]).lower() not in ("pass", "passed")],
        "diffLines": len(diff.splitlines()),
        "workspace": str(directory),
        "outcome": getattr(outcome, "reason", ""),
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"final stage    {result['finalStage']}")
        if result["blocked"]:
            print(f"blocked        {result['blocked']} — {result['blockerAction']}")
        print(f"model calls    {result['modelCalls']}  ({result['seconds']}s)")
        print(f"tokens         {result['inputTokens']} in / {result['outputTokens']} out")
        print(f"diff           {result['diffLines']} lines")
        for gate in gates:
            mark = "ok  " if str(gate["outcome"]).lower() in ("pass", "passed") else "FAIL"
            print(f"  {mark} {gate['gate']}")
        print(f"\nworkspace      {directory}")
        if not args.keep:
            print("               (a temporary directory; pass --keep to choose one)")

    # A blocked run is not a failed script. The factory refusing to hand off work that did
    # not satisfy its gates is the system behaving correctly, and exiting non-zero for it
    # would train somebody to stop reading the output.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
