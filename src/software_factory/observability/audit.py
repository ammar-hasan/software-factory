"""One report across every factory in a workspace (PRD FR-1.4, FR-15, V-31).

`sf workspace list` could show what factories exist. Nothing could answer the question an
operator running more than one actually has: *is any of them in trouble, and are they
drifting apart?* Both halves matter and they fail differently -- a broken factory is loud
once somebody looks, and drift is silent until two teams have irreconcilable conventions.

Five decisions, each a way this report could quietly mislead:

**A factory that failed to load is the first row, not a missing one.** A report listing four
factories and describing three is a report that hides the broken one, and the broken one is
the reason to look.

**A ledger that does not verify is the headline.** Every other number in this report is read
out of that ledger, so if its hash chain is broken, the numbers below it are not merely
uncertain -- they are computed from something that may have been edited. Reporting a tidy
run count above a broken chain is the most dangerous shape this report could take.

**Factories are not ranked by run count.** One with six months of history and one three days
old are not comparable that way, and a leaderboard by volume ranks them by age. Rates are
compared; totals are shown per factory and never as a workspace-wide sum, because a sum
across factories where some measures are unavailable is wrong by an unknown amount.

**Drift is reported as a difference, not as a fault.** Two factories legitimately use
different tiers; that is a choice, and a report that flags it as an error trains people to
ignore the report. Only differences with a safety consequence -- effects granted, credential
strategy, gate coverage -- are raised as findings.

**`insufficient_data` survives to the top.** A workspace where two of five factories have
too little history reports that, rather than averaging over the three that answered and
presenting the result as the workspace's number.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from software_factory.definition.workspace import LoadedFactory, Workspace
from software_factory.observability.metrics import Availability, Measure


class Severity(enum.StrEnum):
    """How much a finding should interrupt somebody.

    `DIVERGENCE` is deliberately not a severity ordering below `WARNING`: it is a different
    *kind* of statement. A factory using a different tier is not a lesser problem than a
    broken ledger, it is not a problem at all until somebody decides it is one.
    """

    BROKEN = "broken"
    WARNING = "warning"
    DIVERGENCE = "divergence"


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth telling an operator, and what to do about it."""

    severity: Severity
    factories: tuple[str, ...]
    summary: str
    remediation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "factories": list(self.factories),
            "summary": self.summary,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class FactoryHealth:
    """One factory's state, with every number carrying its own availability."""

    name: str
    path: str
    loaded: bool
    ledger_verifies: bool | None
    """`None` when there is no ledger to verify. Not `False`: a factory nobody has run yet
    and a factory whose chain is broken are opposite findings, and the second is urgent."""
    error: str = ""
    repositories: tuple[str, ...] = ()
    runs: int | None = None
    measures: tuple[Measure, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.loaded and self.ledger_verifies is not False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "loaded": self.loaded,
            "ledgerVerifies": self.ledger_verifies,
            "error": self.error,
            "repositories": list(self.repositories),
            "runs": self.runs,
            "measures": [m.as_dict() for m in self.measures],
        }


@dataclass(frozen=True, slots=True)
class Audit:
    """What is true across a workspace."""

    root: str
    factories: tuple[FactoryHealth, ...] = ()
    findings: tuple[Finding, ...] = ()
    quiet: tuple[str, ...] = field(default_factory=tuple)
    """Factories with too little history to compare. Named rather than dropped: a workspace
    average over the three that answered, presented as the workspace's number, is a claim
    about five."""

    @property
    def broken(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.severity is Severity.BROKEN)

    @property
    def ok(self) -> bool:
        return not self.broken

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "ok": self.ok,
            "factories": [f.as_dict() for f in self.factories],
            "findings": [f.as_dict() for f in self.findings],
            "quiet": list(self.quiet),
        }


#: Below this many runs, a factory's rates are not worth comparing against another's. A
#: gate pass rate over two runs is a statement about two runs.
MIN_RUNS_TO_COMPARE = 5

#: Metrics compared across factories. Rates only: totals scale with how long a factory has
#: existed, so comparing them ranks factories by age.
COMPARED = ("gate_pass_rate", "escalation_rate", "rework_rate", "autonomy")


def audit(workspace: Workspace, *, window: Any = None) -> Audit:
    """Read every factory in a workspace and report what is true across them."""
    healths = [_health(factory, window=window) for factory in workspace.factories]
    findings: list[Finding] = []

    for health in healths:
        if not health.loaded:
            findings.append(
                Finding(
                    severity=Severity.BROKEN,
                    factories=(health.name,),
                    summary=f"the definition does not load: {health.error}",
                    remediation=f"Run `sf validate --root {health.path}` to see where.",
                )
            )
        elif health.ledger_verifies is False:
            # Above every number, because every number was read out of it.
            findings.append(
                Finding(
                    severity=Severity.BROKEN,
                    factories=(health.name,),
                    summary="the ledger's hash chain does not verify",
                    remediation=(
                        "Every figure reported for this factory was computed from that "
                        "ledger, so treat them as unverified. `sf ledger verify` names the "
                        "first entry that breaks the chain."
                    ),
                )
            )

    for repository, claimants in workspace.overlaps().items():
        findings.append(
            Finding(
                severity=Severity.WARNING,
                factories=claimants,
                summary=f"{repository} is claimed by more than one factory",
                remediation=(
                    "Two factories opening changes on one repository will review each "
                    "other's work as though it came from outside. Give it one owner, or "
                    "scope them to different paths."
                ),
            )
        )

    for name, count in workspace.duplicate_names().items():
        findings.append(
            Finding(
                severity=Severity.WARNING,
                factories=(name,) * count,
                summary=f"{count} factories share the name {name!r}",
                remediation="Every command that takes a factory name is ambiguous here.",
            )
        )

    findings.extend(_divergence(workspace))

    quiet = tuple(
        health.name
        for health in healths
        if health.loaded and (health.runs or 0) < MIN_RUNS_TO_COMPARE
    )
    return Audit(
        root=str(workspace.root),
        factories=tuple(healths),
        findings=tuple(findings),
        quiet=quiet,
    )


def _health(factory: LoadedFactory, *, window: Any = None) -> FactoryHealth:
    from software_factory.ledger.log import Ledger
    from software_factory.observability import metrics

    if not factory.loaded:
        return FactoryHealth(
            name=factory.name,
            path=str(factory.path),
            loaded=False,
            ledger_verifies=None,
            error=factory.error,
        )

    path = factory.ledger_path
    if not path.is_file():
        return FactoryHealth(
            name=factory.name,
            path=str(factory.path),
            loaded=True,
            ledger_verifies=None,
            repositories=factory.repositories,
            runs=None,
        )

    ledger = Ledger(path)
    verifies = _verifies(ledger)
    report = metrics.compute(list(ledger.read()), window=window)
    return FactoryHealth(
        name=factory.name,
        path=str(factory.path),
        loaded=True,
        ledger_verifies=verifies,
        repositories=factory.repositories,
        runs=report.runs.total,
        measures=tuple(report.measures),
    )


def _verifies(ledger: Any) -> bool:
    """Whether the chain holds.

    `Ledger.verify()` signals success by *returning*, and failure by raising with the first
    divergent entry named. So returning normally is the pass, and there is no value to
    inspect: the first version of this did `bool(result)` on a `None` return and reported
    every healthy ledger as broken -- an audit whose headline finding fires on every factory
    in the workspace, which is indistinguishable from one that fires on none.

    A verification that raises is a failed verification, never an unknown one. Treating an
    exception as "no ledger yet" would file a torn or truncated ledger as untouched.
    """
    try:
        ledger.verify()
    except Exception:
        return False
    return True


#: Definition differences with a safety consequence. Everything else -- tiers, budgets,
#: agent counts -- is a choice, and a report that flags choices as faults trains people to
#: stop reading it.
def _divergence(workspace: Workspace) -> list[Finding]:
    """Where factories differ in ways that change what an agent may do.

    Compared across the workspace rather than against a policy, because there is no policy
    to compare against: the question is whether one factory has quietly been given something
    the others were not.
    """
    findings: list[Finding] = []
    loaded = workspace.loaded
    if len(loaded) < 2:
        return findings

    by_effects: dict[frozenset[str], list[str]] = {}
    by_strategy: dict[str, list[str]] = {}
    for factory in loaded:
        definition = factory.definition
        assert definition is not None
        defaults = definition.factory.agent_defaults
        effects = frozenset(
            str(getattr(effect, "value", effect)) for effect in (defaults.effects or ())
        )
        by_effects.setdefault(effects, []).append(factory.name)
        by_strategy.setdefault(
            str(getattr(definition.factory.credential_strategy, "value", "")), []
        ).append(factory.name)

    if len(by_effects) > 1:
        widest = max(by_effects, key=len)
        outliers = sorted(by_effects[widest])
        others = sorted(
            name for effects, names in by_effects.items() if effects != widest for name in names
        )
        findings.append(
            Finding(
                severity=Severity.DIVERGENCE,
                factories=tuple(outliers + others),
                summary=(
                    f"{', '.join(outliers)} grant "
                    f"{', '.join(sorted(widest)) or 'no declared effects'}; "
                    f"{', '.join(others)} grant less"
                ),
                remediation=(
                    "Not a fault -- factories legitimately differ. Worth a look only if the "
                    "wider grant was not a decision somebody made on purpose."
                ),
            )
        )

    if len(by_strategy) > 1:
        findings.append(
            Finding(
                severity=Severity.DIVERGENCE,
                factories=tuple(sorted(name for names in by_strategy.values() for name in names)),
                summary=(
                    "credential strategies differ: "
                    + "; ".join(
                        f"{strategy or 'unset'} in {', '.join(sorted(names))}"
                        for strategy, names in sorted(by_strategy.items())
                    )
                ),
                remediation=(
                    "Whose authorization a run's repository actions carry differs between "
                    "these factories. That is a real difference in what a change can do."
                ),
            )
        )
    return findings


def compare(audit_result: Audit) -> dict[str, list[tuple[str, Measure]]]:
    """The comparable metrics, per metric, across factories that have enough history.

    Keyed by metric rather than by factory, because the question is "who is the outlier on
    escalation rate", and a per-factory shape makes the reader transpose it by hand.

    A factory whose measure is unavailable appears in the list carrying its unavailability,
    rather than being filtered out. Filtering produces a comparison over whoever happened to
    have the integration, presented as a comparison across the workspace.
    """
    rows: dict[str, list[tuple[str, Measure]]] = {}
    for health in audit_result.factories:
        if not health.loaded or (health.runs or 0) < MIN_RUNS_TO_COMPARE:
            continue
        for measure in health.measures:
            if measure.name in COMPARED:
                rows.setdefault(measure.name, []).append((health.name, measure))
    return {
        name: sorted(entries, key=lambda pair: pair[0]) for name, entries in sorted(rows.items())
    }


def outliers(audit_result: Audit, *, spread: float = 2.0) -> list[Finding]:
    """Factories whose rate sits far from the rest of the workspace.

    `spread` is a multiple of the median absolute deviation rather than of the standard
    deviation: with three or four factories, one extreme value moves a standard deviation
    enough to hide itself, which is exactly the case this is for.

    Nothing is reported from fewer than three comparable factories. With two, "an outlier"
    is just "the other one".
    """
    findings: list[Finding] = []
    for name, entries in compare(audit_result).items():
        usable = [
            (factory, measure)
            for factory, measure in entries
            if measure.availability is Availability.AVAILABLE and measure.value is not None
        ]
        if len(usable) < 3:
            continue
        values = sorted(float(m.value or 0.0) for _, m in usable)
        middle = values[len(values) // 2]
        deviations = sorted(abs(value - middle) for value in values)
        mad = deviations[len(deviations) // 2]
        # A zero deviation means most factories agree exactly, which is the case an outlier
        # detector is most obviously for -- three factories at 100% and one at 0%. Skipping
        # it (because `distance > 2 * 0` is unsatisfiable in the same breath as being
        # trivially true) is how a detector ends up silent on its clearest signal. With a
        # zero spread, any difference from the median is the finding.
        for factory, measure in usable:
            distance = abs(float(measure.value or 0.0) - middle)
            if distance > spread * mad if mad else distance > 0:
                findings.append(
                    Finding(
                        severity=Severity.DIVERGENCE,
                        factories=(factory,),
                        summary=(
                            f"{name} is {measure.value:g}{' ' + measure.unit if measure.unit else ''} "
                            f"against a workspace median of {middle:g}"
                        ),
                        remediation=(
                            "Worth understanding before copying this factory's settings, or "
                            "before changing it to match the others."
                        ),
                    )
                )
    return findings


def render(audit_result: Audit) -> str:
    """The audit as plain text, broken findings first."""
    lines = [f"workspace {audit_result.root}", ""]
    for severity in (Severity.BROKEN, Severity.WARNING, Severity.DIVERGENCE):
        found = [f for f in audit_result.findings if f.severity is severity]
        if not found:
            continue
        lines.append(f"{severity.value}:")
        for finding in found:
            lines.append(f"  [{', '.join(finding.factories)}] {finding.summary}")
            if finding.remediation:
                lines.append(f"      {finding.remediation}")
        lines.append("")
    if audit_result.quiet:
        lines.append(
            f"too little history to compare: {', '.join(audit_result.quiet)} "
            f"(under {MIN_RUNS_TO_COMPARE} runs)"
        )
    if not audit_result.findings:
        lines.append("nothing to report")
    return "\n".join(lines).rstrip() + "\n"


def audit_path(root: Path, *, window: Any = None) -> Audit:
    """Load a workspace from disk and audit it."""
    from software_factory.definition.workspace import load_workspace

    return audit(load_workspace(root), window=window)
