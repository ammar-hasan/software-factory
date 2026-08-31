"""`sf init` -- write a complete, valid factory definition (PRD FR-20.1, FR-30.3).

The tree this writes must load cleanly and lint cleanly, every time. A scaffold that
emits warnings teaches every new user that warnings are normal, so the CI reference test
asserts a clean lint, not merely a clean load.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from software_factory.scaffold import templates

DEFAULT_IMAGE = (
    "ubuntu:24.04@sha256:0000000000000000000000000000000000000000000000000000000000000000"
)
"""Placeholder digest.

Pinning by digest is required (FR-17.9), and a scaffold cannot know the digest the
operator wants. Emitting a syntactically-pinned placeholder means `sf validate` passes
while the operator still sees an obviously-fake digest to replace, which is better than
emitting an unpinned tag that quietly normalises unpinned images.
"""


@dataclass(slots=True)
class InitResult:
    """What `sf init` created."""

    root: Path
    created: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)

    @property
    def wrote_anything(self) -> bool:
        return bool(self.created)


def init_factory(
    root: Path,
    *,
    name: str = "factory",
    owner: str = "your-org",
    repo: str = "your-repo",
    local_model: str = "local-model",
    image: str = DEFAULT_IMAGE,
    force: bool = False,
) -> InitResult:
    """Write a reference factory definition under ``root``.

    Existing files are never overwritten unless ``force``; the result reports what was
    skipped so a re-run on a partly-initialised directory is safe and legible.
    """
    result = InitResult(root=root)
    review_by = (dt.date.today() + dt.timedelta(days=365)).isoformat()

    files: dict[str, str] = {
        "factory.yaml": templates.FACTORY_YAML.format(
            name=name, owner=owner, repo=repo, local_model=local_model
        ),
        "runners/default.yaml": templates.RUNNER_YAML.format(image=image),
        "agents/conductor/agent.md": templates.CONDUCTOR,
        "agents/scout/agent.md": templates.SCOUT,
        "agents/architect/agent.md": templates.ARCHITECT,
        "agents/builder/agent.md": templates.BUILDER,
        "agents/critic/agent.md": templates.CRITIC,
        "skills/repository-validation/SKILL.md": templates.VALIDATION_SKILL.format(
            owner=owner, review_by=review_by
        ),
        "scorers/tests-actually-run/scorer.md": templates.SCORER.format(judge_model="judge-model"),
        "automations/labeled-issue/automation.md": templates.AUTOMATION.format(
            owner=owner, repo=repo
        ),
        "policy/stages.yaml": templates.STAGES_POLICY,
        "policy/gates.yaml": templates.GATES_POLICY,
        "policy/budgets.yaml": templates.BUDGETS_POLICY,
        "policy/memory.yaml": templates.MEMORY_POLICY,
        ".gitignore": templates.GITIGNORE,
        "README.md": templates.README.format(name=name),
    }

    for relative, content in files.items():
        path = root / relative
        if path.exists() and not force:
            result.skipped.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        result.created.append(path)

    return result
