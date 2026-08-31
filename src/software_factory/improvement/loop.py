"""The self-improvement loop, and the four things that stop it eating itself (PRD FR-14).

The loop is *cluster → diagnose → propose → validate → submit*, and only the first and last
steps are mechanical: diagnosis and proposal are runs, and validation is the eval suite.
What lives here is everything around them, because a loop that proposes changes to the
factory that judges its own proposals has three failure modes that are not obvious and one
that is:

* **Thrash** (FR-14.6). Without a cooling period the loop proposes a change, sees the next
  failure, proposes another change to the same target, and the reviewer sees a stream. Also:
  a proposal already rejected must not return without *new* evidence, or "no" costs the
  reviewer the same effort every week.
* **Rubric drift** (FR-14.7a.3). A scorer whose pass rate rises while its outcome partner
  stays flat has probably learned to be easier to satisfy. That is not detectable in any one
  proposal -- it is a trend -- and the answer is to suspend the scorer from driving the loop
  until a human re-validates it, rather than to keep taking its word.
* **Ineffectiveness** (FR-14.7a.4). If adopted proposals do not move outcomes over a window,
  the loop is a cost with no return, and the default is off rather than left running. A loop
  that cannot show it works is a defect and has to show as one.
* **Writing** (FR-14.3b). The obvious one: proposing is not writing. Everything here returns
  a proposal. Nothing applies one, and there is no code path from this module to a
  definition taking effect.

FR-14.1: the loop is opt-in per scorer. Enabling it on a scorer authorises investigating
*that* scorer's failures -- not the factory in general.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from software_factory.evals.scorers import ImprovementProposal, ProposalVerdict, Scorer
from software_factory.identity import Capability, Decision
from software_factory.improvement.clustering import Cluster
from software_factory.memory.records import utc_now

#: How long the loop must leave a target alone after proposing a change to it (FR-14.6).
#:
#: Long enough that the previous proposal has been reviewed and its effect observed. A
#: shorter period proposes against a factory that has not yet changed, which is proposing
#: against stale evidence.
DEFAULT_COOLING = timedelta(days=7)

#: How many self-improvement proposals may be open at once, factory-wide.
#:
#: A reviewer's attention is the scarce resource here, not compute. Twenty open proposals is
#: not twice as much improvement as ten; it is a queue nobody works through.
DEFAULT_MAX_OPEN = 5

#: A scorer's pass rate may rise this much more than its outcome partner before the gap is
#: reported as suspected capture.
DRIFT_TOLERANCE = 0.10

#: Adopted proposals must move outcomes by at least this much over the effectiveness window,
#: or the loop switches itself off.
MIN_LOOP_EFFECT = 0.01


class ProposalStatus(enum.StrEnum):
    OPEN = "open"
    ADOPTED = "adopted"
    REJECTED = "rejected"
    REVERTED = "reverted"


@dataclass(frozen=True, slots=True)
class ProposalRecord:
    """One proposal and what became of it. The unit of improvement telemetry (FR-14.8)."""

    id: str
    target: str
    scorer: str
    signature: str
    status: ProposalStatus
    opened_at: datetime = field(default_factory=utc_now)
    settled_at: datetime | None = None
    evidence: tuple[str, ...] = ()
    """The runs and scorer results that motivated it (FR-14.4). A proposal a reviewer cannot
    trace to its evidence is a proposal they have to take on faith."""

    outcome_effect: float | None = None
    """Measured movement in the outcome partner after adoption. ``None`` until measured --
    which is different from zero, and conflating them would let an unmeasured loop report
    as an ineffective one, or worse, an ineffective one as unmeasured."""


@dataclass(frozen=True, slots=True)
class Refused:
    """Why the loop declined to propose. Always actionable, never a bare skip."""

    code: str
    message: str
    remediation: str


@dataclass(frozen=True, slots=True)
class DriftFinding:
    """A scorer whose pass rate has outrun its outcome partner (FR-14.7a.3)."""

    scorer: str
    outcome_partner: str
    scorer_delta: float
    outcome_delta: float

    @property
    def gap(self) -> float:
        return self.scorer_delta - self.outcome_delta

    def describe(self) -> str:
        return (
            f"{self.scorer} improved {self.scorer_delta:+.1%} while {self.outcome_partner} "
            f"moved {self.outcome_delta:+.1%}; a gap of {self.gap:+.1%} usually means the "
            "scorer got easier to satisfy rather than the work getting better"
        )


@dataclass(slots=True)
class LoopState:
    """Everything the loop needs to know about its own history.

    Held explicitly rather than recomputed from the ledger on each call: the anti-thrash
    rules are about *this loop's* past behaviour, and a loop that forgets its own proposals
    between invocations has no anti-thrash at all.
    """

    records: list[ProposalRecord] = field(default_factory=list)
    suspended_scorers: dict[str, str] = field(default_factory=dict)
    """``scorer -> why``. Suspension needs a reason, because clearing it is a human
    judgement and a human cannot judge an unexplained flag."""

    enabled: bool = True
    disabled_reason: str = ""

    def open_proposals(self) -> list[ProposalRecord]:
        return [r for r in self.records if r.status is ProposalStatus.OPEN]

    def last_for_target(self, target: str) -> ProposalRecord | None:
        matching = [r for r in self.records if r.target == target]
        return max(matching, key=lambda r: r.opened_at) if matching else None

    def rejected_signatures(self) -> dict[str, ProposalRecord]:
        """The most recent rejection per failure signature."""
        rejected: dict[str, ProposalRecord] = {}
        for record in self.records:
            if record.status is not ProposalStatus.REJECTED:
                continue
            existing = rejected.get(record.signature)
            if existing is None or record.opened_at > existing.opened_at:
                rejected[record.signature] = record
        return rejected


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Proposals opened, adopted, rejected, reverted, and whether any of it worked."""

    opened: int
    adopted: int
    rejected: int
    reverted: int
    measured: int
    mean_outcome_effect: float | None

    @property
    def adoption_rate(self) -> float:
        settled = self.adopted + self.rejected
        return self.adopted / settled if settled else 0.0

    @property
    def revert_rate(self) -> float:
        return self.reverted / self.adopted if self.adopted else 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "opened": self.opened,
            "adopted": self.adopted,
            "rejected": self.rejected,
            "reverted": self.reverted,
            "measured": self.measured,
            "adoptionRate": round(self.adoption_rate, 4),
            "revertRate": round(self.revert_rate, 4),
            "meanOutcomeEffect": (
                None if self.mean_outcome_effect is None else round(self.mean_outcome_effect, 4)
            ),
        }


def telemetry(state: LoopState) -> Telemetry:
    """FR-14.8. A loop whose adopted proposals do not move outcomes is a defect, and this is
    where it shows as one."""
    by_status = dict.fromkeys(ProposalStatus, 0)
    effects: list[float] = []
    for record in state.records:
        by_status[record.status] += 1
        if record.status is ProposalStatus.ADOPTED and record.outcome_effect is not None:
            effects.append(record.outcome_effect)
    return Telemetry(
        opened=by_status[ProposalStatus.OPEN],
        adopted=by_status[ProposalStatus.ADOPTED],
        rejected=by_status[ProposalStatus.REJECTED],
        reverted=by_status[ProposalStatus.REVERTED],
        measured=len(effects),
        mean_outcome_effect=(sum(effects) / len(effects) if effects else None),
    )


def detect_drift(
    scorer: Scorer,
    *,
    scorer_delta: float,
    outcome_delta: float,
    tolerance: float = DRIFT_TOLERANCE,
) -> DriftFinding | None:
    """Report a scorer whose pass rate has outrun the outcome it is supposed to track.

    Only an improving scorer can drift in the sense that matters. A scorer getting *worse*
    while its outcome holds is a different signal -- the scorer may have got stricter, which
    is not capture -- and reporting it here would bury the case this exists for.
    """
    if not scorer.outcome_partner:
        return None
    if scorer_delta <= 0:
        return None
    if scorer_delta - outcome_delta < tolerance:
        return None
    return DriftFinding(
        scorer=scorer.name,
        outcome_partner=scorer.outcome_partner,
        scorer_delta=scorer_delta,
        outcome_delta=outcome_delta,
    )


def check_effectiveness(
    state: LoopState, *, minimum: float = MIN_LOOP_EFFECT, min_measured: int = 3
) -> str | None:
    """Why the loop should switch itself off, or ``None``.

    ``min_measured`` exists because "adopted proposals have not moved outcomes" is only
    meaningful once enough of them have been measured. Disabling a loop on one measurement
    would disable it for noise; never disabling it at all is FR-14.7a.4's failure.
    """
    numbers = telemetry(state)
    if numbers.measured < min_measured:
        return None
    if numbers.mean_outcome_effect is None:
        return None
    if numbers.mean_outcome_effect < minimum:
        return (
            f"{numbers.measured} adopted proposals moved outcomes by "
            f"{numbers.mean_outcome_effect:+.1%} on average, below the {minimum:+.1%} the "
            "loop must earn to be worth running"
        )
    return None


def may_propose(
    state: LoopState,
    cluster: Cluster,
    *,
    target: str,
    scorer: Scorer | None = None,
    new_evidence: bool = False,
    cooling: timedelta = DEFAULT_COOLING,
    max_open: int = DEFAULT_MAX_OPEN,
    now: datetime | None = None,
) -> Refused | None:
    """``None`` when the loop may propose against this cluster, otherwise why not.

    Checked in order of how absolute the refusal is: a disabled loop refuses everything, a
    suspended scorer refuses its own failures, and the rest are about this specific target.
    """
    now = now or utc_now()

    if not state.enabled:
        return Refused(
            "loop.disabled",
            f"the improvement loop is disabled: {state.disabled_reason}",
            (
                "Re-enable it deliberately once the reason is addressed. A loop that cannot "
                "show it improves outcomes is a cost, and the default is off."
            ),
        )

    if scorer is not None:
        suspended = state.suspended_scorers.get(scorer.name)
        if suspended is not None:
            return Refused(
                "loop.scorer_suspended",
                f"scorer {scorer.name!r} is suspended from driving improvement: {suspended}",
                (
                    "A human must re-validate it against a fresh labelled sample. Until "
                    "then its failures are not evidence of anything to fix."
                ),
            )
        allowed, reason = scorer.may_drive_improvement()
        if not allowed:
            return Refused(
                "loop.scorer_untrusted",
                f"scorer {scorer.name!r} may not drive change: {reason}",
                (
                    "Label a sample and declare an outcome partner. Without one there is no "
                    "way to tell an improvement from a scorer that got easier to satisfy."
                ),
            )

    open_now = state.open_proposals()
    if len(open_now) >= max_open:
        return Refused(
            "loop.too_many_open",
            f"{len(open_now)} self-improvement proposals are already open, at the cap of {max_open}",
            (
                "A reviewer's attention is the scarce resource, not compute. Work through "
                "the queue before adding to it."
            ),
        )

    previous = state.last_for_target(target)
    if previous is not None and now - previous.opened_at < cooling:
        remaining = cooling - (now - previous.opened_at)
        return Refused(
            "loop.cooling",
            f"{target!r} was proposed against {now - previous.opened_at} ago, inside its "
            f"{cooling} cooling period",
            (
                f"Wait {remaining} for the previous change to be reviewed and its effect "
                "observed. Proposing sooner proposes against evidence the last change has "
                "not had time to alter."
            ),
        )

    rejected = state.rejected_signatures().get(cluster.signature)
    if rejected is not None and not new_evidence:
        return Refused(
            "loop.already_rejected",
            f"a proposal for this failure signature was rejected on "
            f"{rejected.opened_at.date().isoformat()}",
            (
                "Bring new evidence -- failures the rejected proposal did not cover, or a "
                "different diagnosis. Re-proposing the same case costs the reviewer the "
                "same effort and reaches the same answer."
            ),
        )

    return None


def submit(
    state: LoopState,
    proposal: ImprovementProposal,
    verdict: ProposalVerdict,
    *,
    proposal_id: str,
    scorer_name: str,
    signature: str,
    now: datetime | None = None,
) -> ProposalRecord:
    """Record a validated proposal as open, awaiting human review.

    Open, never adopted (FR-14.5). Nothing here applies a change: the record says a human
    has something to look at, and `identity.duties.approve` is where adoption happens.
    """
    if not verdict.accepted:
        return _record(
            state,
            proposal,
            proposal_id=proposal_id,
            scorer_name=scorer_name,
            signature=signature,
            status=ProposalStatus.REJECTED,
            now=now,
        )
    return _record(
        state,
        proposal,
        proposal_id=proposal_id,
        scorer_name=scorer_name,
        signature=signature,
        status=ProposalStatus.OPEN,
        now=now,
    )


def settle(
    state: LoopState,
    proposal_id: str,
    status: ProposalStatus,
    *,
    decision: Decision,
    outcome_effect: float | None = None,
    now: datetime | None = None,
) -> ProposalRecord | Refused:
    """Move a proposal out of ``open``, optionally recording what adopting it achieved.

    `decision` is required, and it is not decoration. This used to rewrite a record's status
    to whatever the caller passed, with no authority and no evidence -- so a REJECTED record
    could be moved back to OPEN and would vanish from `rejected_signatures()`, taking
    FR-14.6's anti-thrash rule with it. Every other state change in this codebase carries the
    identity that made it; this one now does too.

    The capability is `adopt_definition_change` because that is what settling a proposal
    decides: whether a change the factory proposed about itself goes forward.
    """
    if decision.capability is not Capability.ADOPT_DEFINITION_CHANGE:
        return Refused(
            "loop.wrong_capability",
            f"{decision.principal_id!r} exercised {decision.capability.value}, which does "
            "not authorise settling an improvement proposal",
            "Settle it with `adopt_definition_change`, or leave it open.",
        )
    if decision.subject != proposal_id:
        return Refused(
            "loop.wrong_subject",
            f"the decision names {decision.subject or 'nothing'!r}, not {proposal_id!r}",
            "A decision settles one proposal. Authorise this one by name.",
        )

    for index, record in enumerate(state.records):
        if record.id != proposal_id:
            continue
        reopening = (
            record.status is ProposalStatus.REJECTED and status is not ProposalStatus.REJECTED
        )
        # Reopening a rejection is the move that erases the anti-thrash record, so it is the
        # one that has to be visible. Allowed, but never silent.
        if reopening and not decision.evidence_shown:
            return Refused(
                "loop.reopen_without_evidence",
                f"{proposal_id!r} was rejected; reopening it needs the new evidence "
                "that makes the answer different",
                (
                    "Cite the failures the rejected proposal did not cover, or a "
                    "different diagnosis. Reopening with none re-asks a question that "
                    "was already answered."
                ),
            )
        settled = ProposalRecord(
            id=record.id,
            target=record.target,
            scorer=record.scorer,
            signature=record.signature,
            status=status,
            opened_at=record.opened_at,
            settled_at=now or utc_now(),
            evidence=record.evidence,
            outcome_effect=outcome_effect,
        )
        state.records[index] = settled
        return settled
    return Refused(
        "loop.unknown_proposal",
        f"no proposal {proposal_id!r} is on record",
        "Check the id; `sf improve` lists the open proposals.",
    )


def _record(
    state: LoopState,
    proposal: ImprovementProposal,
    *,
    proposal_id: str,
    scorer_name: str,
    signature: str,
    status: ProposalStatus,
    now: datetime | None,
) -> ProposalRecord:
    record = ProposalRecord(
        id=proposal_id,
        target=proposal.target,
        scorer=scorer_name,
        signature=signature,
        status=status,
        opened_at=now or utc_now(),
        settled_at=None if status is ProposalStatus.OPEN else (now or utc_now()),
        evidence=proposal.regressions_addressed,
    )
    state.records.append(record)
    return record


def suspend_for_drift(state: LoopState, finding: DriftFinding) -> None:
    """Stop a drifting scorer driving the loop until a human re-validates it."""
    state.suspended_scorers[finding.scorer] = finding.describe()


def disable(state: LoopState, reason: str) -> None:
    state.enabled = False
    state.disabled_reason = reason
