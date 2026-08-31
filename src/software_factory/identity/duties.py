"""Separation of duties: who may approve what someone else proposed (PRD FR-25.3).

One rule and one exception. The rule: the principal who proposes a change may not be its
sole approver. The exception is that there isn't one -- a self-referential change needs two
approvers from a group other than the proposer's, because the failure mode there is not
carelessness but capture, and one careful approver does not detect capture.

This is separate from :mod:`.principals` because holding a capability and being *eligible*
to exercise it on a particular subject are different questions, and conflating them is how a
capability check ends up approving the approver's own work.
"""

from __future__ import annotations

from dataclasses import dataclass

from software_factory.identity.principals import (
    Capability,
    Decision,
    Directory,
    Refused,
)

#: Approvals required for a change to the factory's own definition (FR-14.3a).
DEFINITION_APPROVERS = 1

#: Approvals required for a change to a scorer, gate, or eval -- the things that decide
#: whether the factory's work is good (FR-14.7, FR-25.3). Two, from a group other than the
#: proposer's, because a proposal that quietly widens what counts as success reads
#: perfectly well to one approver who shares the proposer's assumptions.
SELF_REFERENTIAL_APPROVERS = 2


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """A change awaiting approval, and who proposed it."""

    subject: str
    proposer_id: str
    self_referential: bool = False
    definition_change: bool = False

    @property
    def required_approvals(self) -> int:
        if self.self_referential:
            return SELF_REFERENTIAL_APPROVERS
        return DEFINITION_APPROVERS

    @property
    def capability(self) -> Capability:
        return (
            Capability.APPROVE_SELF_REFERENTIAL_CHANGE
            if self.self_referential
            else Capability.ADOPT_DEFINITION_CHANGE
        )


@dataclass(frozen=True, slots=True)
class ApprovalState:
    """Approvals gathered so far, and whether they are enough."""

    request: ApprovalRequest
    approvals: tuple[Decision, ...] = ()

    @property
    def satisfied(self) -> bool:
        return len(self.approvals) >= self.request.required_approvals

    @property
    def outstanding(self) -> int:
        return max(0, self.request.required_approvals - len(self.approvals))

    def render(self) -> str:
        who = ", ".join(d.principal_id for d in self.approvals) or "nobody yet"
        return (
            f"{self.request.subject}: {len(self.approvals)}/"
            f"{self.request.required_approvals} approvals ({who})"
        )


def approve(
    directory: Directory,
    state: ApprovalState,
    *,
    principal_id: str,
    rationale: str,
    evidence_shown: tuple[str, ...] = (),
    channel: str = "cli",
) -> ApprovalState | Refused:
    """Record one approval against a request, or say why it does not count.

    Four ways it does not count, in the order they are checked -- cheapest and most
    absolute first:

    1. The proposer is approving their own change. FR-25.3, and the reason the whole module
       exists.
    2. The same principal is approving twice. Two approvals from one person is one approval
       written down twice, and a count that can be inflated that way measures nothing.
    3. A self-referential change is being approved by someone in the proposer's group. One
       approver who shares the proposer's assumptions is not independent review of a change
       to what counts as success.
    4. The principal does not hold the capability, is unknown, or gave no reason --
       :func:`Directory.authorise` answers those.
    """
    request = state.request

    if principal_id == request.proposer_id:
        return Refused(
            "duties.self_approval",
            f"{principal_id!r} proposed {request.subject!r} and cannot be its approver",
            (
                "Another principal holding "
                f"{request.capability.value} must approve it. Proposing and approving the "
                "same change is one person's judgement recorded twice."
            ),
        )

    if any(existing.principal_id == principal_id for existing in state.approvals):
        return Refused(
            "duties.duplicate_approval",
            f"{principal_id!r} has already approved {request.subject!r}",
            "A second approval must come from a different principal.",
        )

    decision = directory.authorise(
        principal_id,
        request.capability,
        subject=request.subject,
        rationale=rationale,
        evidence_shown=evidence_shown,
        channel=channel,
    )
    if isinstance(decision, Refused):
        return decision

    if request.self_referential:
        conflict = _shares_group(directory, principal_id, request.proposer_id)
        if conflict is not None:
            return Refused(
                "duties.same_group",
                (
                    f"{principal_id!r} and the proposer {request.proposer_id!r} are both in "
                    f"{conflict!r}; a self-referential change needs an approver from "
                    "outside the proposer's group"
                ),
                (
                    "Ask a principal in a different group. A change to what counts as "
                    "success reads correctly to anyone who shares the assumption behind it."
                ),
            )

    return ApprovalState(request=request, approvals=(*state.approvals, decision))


def _shares_group(directory: Directory, left_id: str, right_id: str) -> str | None:
    """The first group both principals belong to, or ``None``.

    Ungrouped principals do not conflict: a factory that has not declared groups has not
    expressed an opinion about independence, and inventing one would block every approval
    on a definition that never asked for it.
    """
    left, right = directory.get(left_id), directory.get(right_id)
    if left is None or right is None:
        return None
    shared = sorted(left.groups & right.groups)
    return shared[0] if shared else None
