"""Shared machinery for the trials: real repositories, real runs, honest reports."""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from software_factory.definition import load_strict
from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
from software_factory.orchestrator.coordinator import Coordinator, local_coordinator
from software_factory.providers import StubProvider, says
from software_factory.scaffold import init_factory

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "trial",
    "GIT_AUTHOR_EMAIL": "trial@localhost",
    "GIT_COMMITTER_NAME": "trial",
    "GIT_COMMITTER_EMAIL": "trial@localhost",
}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env=GIT_ENV
    )
    return result.stdout


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


@dataclass(slots=True)
class GateOutcome:
    """What one gate did, for the report."""

    stage: str
    gate: str
    outcome: str
    blocked: bool
    detail: str = ""

    def render(self) -> str:
        mark = {"pass": "pass", "fail": "FAIL", "skip": "skip", "error": "ERROR"}.get(
            self.outcome, self.outcome
        )
        suffix = f" — {self.detail}" if self.detail else ""
        return f"    {self.stage:<8} {self.gate:<22} {mark}{suffix}"


@dataclass(slots=True)
class TrialReport:
    """What a trial established, and what it could not.

    `unproven` is not a caveats section anybody can skip. It is the difference between this
    and a demo: a trial that reported only what worked would be evidence for whatever the
    reader already believed.
    """

    name: str
    question: str
    stages: list[str] = field(default_factory=list)
    gates: list[GateOutcome] = field(default_factory=list)
    final_stage: str = ""
    blocker: str = ""
    blocker_action: str = ""
    established: list[str] = field(default_factory=list)
    """What the run actually demonstrated.

    Every entry here must be *derived from the outcome*, never asserted alongside it. The
    first draft of this file asserted "a work item reached HANDOFF with a real diff" as a
    constant, and the first run blocked at DESIGN and printed it anyway -- a trial report
    claiming what it had not established, which is the failure this whole codebase keeps
    finding, reproduced in the tool built to look for it. `require` is the guard.
    """

    unproven: list[str] = field(default_factory=list)
    surprises: list[str] = field(default_factory=list)
    """Things the trial found that were not what it was looking for. The most valuable
    output a trial has, and the one a pass/fail summary discards."""

    @property
    def blocked_gates(self) -> list[GateOutcome]:
        return [gate for gate in self.gates if gate.blocked]

    @property
    def reached_handoff(self) -> bool:
        return self.final_stage == "HANDOFF"

    def require(self, condition: bool, claim: str) -> None:
        """Record `claim` as established, or as a surprise when it did not hold.

        A claim that fails is not dropped. "We expected this and it did not happen" is the
        most informative thing a trial produces, and a report that silently omitted its
        failed expectations would be a report shaped by what happened to work.
        """
        if condition:
            self.established.append(claim)
        else:
            self.surprises.append(f"Expected but did not observe: {claim}")

    def render(self) -> str:
        lines = [
            f"## {self.name}",
            "",
            f"**Question.** {self.question}",
            "",
            f"Stages run: {' → '.join(self.stages) or 'none'}",
            f"Came to rest at: {self.final_stage}"
            + (f" (blocked: {self.blocker})" if self.blocker else ""),
        ]
        if self.blocker_action:
            lines.append(f"What would clear it: {self.blocker_action}")
        lines += ["", "Gates:"]
        lines += [gate.render() for gate in self.gates] or ["    (none ran)"]
        for title, items in (
            ("What this establishes", self.established),
            ("What it does not", self.unproven),
            ("Surprises", self.surprises),
        ):
            if not items:
                continue
            lines += ["", f"**{title}.**"]
            lines += [f"- {item}" for item in items]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "question": self.question,
            "stages": self.stages,
            "finalStage": self.final_stage,
            "blocker": self.blocker,
            "gates": [
                {"stage": g.stage, "gate": g.gate, "outcome": g.outcome, "blocked": g.blocked}
                for g in self.gates
            ],
            "established": self.established,
            "unproven": self.unproven,
            "surprises": self.surprises,
        }


def scripted(*outputs: dict[str, Any]) -> StubProvider:
    """A provider that returns these stage outputs in order.

    Strict by construction: `StubProvider` raises past the end of its script rather than
    returning something plausible, so a trial that ran more stages than it scripted fails
    loudly instead of silently exercising a different path.
    """
    return StubProvider([says(json.dumps(body)) for body in outputs])


def calibration(confidence: float = 0.8, evidence: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"confidence": confidence, "evidence": list(evidence), "unknowns": []}


def build_factory(root: Path, *, name: str, owner: str, repo: str) -> Any:
    init_factory(root, name=name, owner=owner, repo=repo)
    return load_strict(root)


def work_item(*, title: str, request: str, work_class: WorkClass, ref: str) -> WorkItem:
    return WorkItem(
        id=new_id(),
        factory="trial",
        title=title,
        request=request,
        source=SourceContext(provider="git-host", kind="issue", ref=ref),
        work_class=work_class,
    )


def coordinator_for(
    definition: Any, repo: Path, state: Path, provider: StubProvider
) -> Coordinator:
    return local_coordinator(
        definition, repo=repo, state_dir=state, provider=provider, allow_unsandboxed=True
    )


def collect_gates(outcome: Any) -> list[GateOutcome]:
    return [
        GateOutcome(
            stage=stage.stage.value,
            gate=result.gate,
            outcome=result.outcome.value,
            blocked=result.blocks,
            detail=result.detail or (result.findings[0].observed if result.findings else ""),
        )
        for stage in outcome.stages
        for result in stage.gates.results
    ]
