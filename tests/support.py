"""A real coordinator run with scripted model output, for tests that need one.

Shared rather than duplicated because building one costs a git repository, a scaffold and a
four-stage script, and a test that pays that cost inline tends to shrink until it is testing
the fixture.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

CALIBRATION = {"confidence": 0.8, "evidence": ["importer.py:3"], "unknowns": []}

CARRIED = {
    "decisions": ["kept the public signature of strip_bom so callers do not change"],
    "attempted": ["tried a decorator around strip_bom; it broke pickling in the worker"],
    "constraints": ["the importer runs under a 30s timeout, so no extra pass over the file"],
    "artifacts": ["branch factory/bom-headers"],
}


def _repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@localhost",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@localhost",
    }
    (repo / "importer.py").write_text("def strip_bom(text):\n    return text\n", encoding="utf-8")
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-q", "-m", "x")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)
    return repo


def run_with_discoveries(root: Path, discoveries: list[dict[str, Any]]):
    """Run one work item to handoff, with `discoveries` reported at BUILD."""
    from software_factory.definition import load_strict
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.providers import StubProvider, says
    from software_factory.scaffold import init_factory

    repo = _repo(root)
    factory = root / "factory"
    init_factory(factory, name="payments", owner="acme", repo="payments-service")
    definition = load_strict(factory)

    def output(**fields: Any) -> str:
        return json.dumps({"calibration": CALIBRATION, **CARRIED, **fields})

    provider = StubProvider(
        [
            says(output(findings="unchanged", scope="one function")),
            says(output(summary="Fixed.", claims=["done"], discoveries=discoveries)),
            says(output(verdict="accept", findings=[])),
            says(output(summary="Handed off.", branch="factory/x")),
        ]
    )
    item = WorkItem(
        id=new_id(),
        factory="payments",
        title="CSV importer mangles BOM headers",
        request="A BOM names the first column oddly.",
        source=SourceContext(provider="git-host", kind="issue", ref="acme/payments#42"),
        work_class=WorkClass.CHORE,
    )
    return local_coordinator(
        definition,
        repo=repo,
        state_dir=root / "state",
        provider=provider,
        allow_unsandboxed=True,
    ).run(item)
