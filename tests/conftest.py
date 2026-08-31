"""Shared fixtures: builders that write a real definition tree to a tmp directory.

Tests exercise the loader against files rather than against in-memory dicts, because
the loader's job is precisely to turn files into models -- stubbing the filesystem
would test the wrong half.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

FACTORY_YAML = """\
schemaVersion: v1alpha1
name: payments
description: Works the payments service backlog
handle: payments
repositories:
  - owner: acme
    name: payments-service
ladder:
  tiers:
    - name: local-small
      provider: local
      model: small-local
      contextWindow: 32000
      workingSetCeiling: 24000
      local: true
      capabilities: [code, tools]
    - name: mid
      provider: hosted
      model: mid-hosted
      contextWindow: 200000
      workingSetCeiling: 120000
      capabilities: [code, tools, reasoning]
  defaultTier: local-small
  ceilingTier: mid
  scaffoldAtOrBelow: local-small
agentDefaults:
  tier: local-small
  runner: linux
"""

RUNNER_YAML = """\
description: Linux build runner
platform:
  os: linux
  arch: x86_64
  image: ubuntu:24.04@sha256:0000000000000000000000000000000000000000000000000000000000000000
instanceShape:
  vcpus: 4
  memoryGb: 8
setupCommands:
  - python -m pip install -e .
network: allowlist
networkAllowlist:
  - pypi.org
"""


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def agent(role: str, *, body: str = "Do the work well.", **frontmatter: object) -> str:
    lines = [f"role: {role}"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n\n" + body + "\n"


@pytest.fixture
def factory_root(tmp_path: Path) -> Path:
    """A minimal but complete, valid definition tree.

    Minimal now includes a specialist. FR-2.1 asked for "at least one agent", which a
    conductor satisfies alone -- and a conductor with nobody to route to can accept work and
    do none of it, so the requirement was weaker than every real factory including the
    scaffold. This fixture was the one tree in the suite that took the requirement literally.
    """
    root = tmp_path / "factory"
    write(root / "factory.yaml", FACTORY_YAML)
    write(root / "runners" / "linux.yaml", RUNNER_YAML)
    write(
        root / "agents" / "conductor" / "agent.md",
        agent("CONDUCTOR", body="Route each work item from intake to human handoff."),
    )
    write(
        root / "agents" / "builder" / "agent.md",
        agent("BUILDER", body="Make the smallest change that satisfies the work item."),
    )
    return root
