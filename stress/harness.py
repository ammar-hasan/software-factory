"""Shared machinery for the stress scenarios."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Finding:
    """One thing the scenario checked, and what it saw."""

    claim: str
    ok: bool
    detail: str = ""

    def render(self) -> str:
        return f"    {'ok  ' if self.ok else 'FAIL'} {self.claim}" + (
            f" — {self.detail}" if self.detail else ""
        )


@dataclass(slots=True)
class StressReport:
    """What one scenario did, and what surprised it.

    `require` records rather than raises, for the same reason the trial harness does: a
    scenario that stops at its first surprise reports one problem when it could have
    reported five, and the second one is often what explains the first.
    """

    name: str
    description: str
    findings: list[Finding] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""

    def require(self, ok: bool, claim: str, detail: str = "") -> bool:
        self.findings.append(Finding(claim=claim, ok=bool(ok), detail=detail))
        return bool(ok)

    def note(self, key: str, value: Any) -> None:
        self.facts[key] = value

    @property
    def surprises(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    @property
    def clean(self) -> bool:
        return not self.surprises and not self.error

    def render(self) -> str:
        lines = [f"{self.name}  ({self.seconds:.1f}s)", f"  {self.description}"]
        lines.extend(f.render() for f in self.findings)
        if self.facts:
            lines.append(
                "    facts: " + ", ".join(f"{k}={v}" for k, v in sorted(self.facts.items()))
            )
        if self.error:
            lines.append(f"    ERROR {self.error}")
        return "\n".join(lines)


def timed(report: StressReport, work: Any) -> StressReport:
    """Run a scenario body, timing it and catching what escapes.

    An escaping exception is a *result*, not a crash of the runner: "this scenario raised"
    is the most interesting thing a stress run can discover, and losing the other scenarios
    to it would hide the rest.
    """
    started = time.monotonic()
    try:
        work(report)
    except Exception as exc:
        report.error = f"{type(exc).__name__}: {exc}"
    report.seconds = time.monotonic() - started
    return report
