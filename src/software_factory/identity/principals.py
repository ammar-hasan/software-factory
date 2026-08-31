"""Principals, capabilities, and the decisions they authorise (PRD FR-25).

The PRD's own note on why this exists: the baseline said "a human" must approve, override,
widen, force-promote and emergency-stop, and never said *which* human. Every one of those
became an unchecked string parameter -- `actor="human:maintainer"` -- which an agent can
write as easily as a person can. `StageMachine.cancel` was the clearest case: it documented
itself as available to a human and checked nothing at all.

Three ideas, in order:

* A **principal** is anyone who can act: a person, an agent, an automation, or the
  coordination plane. It has a stable id, a kind, and the groups it belongs to.
* A **capability** is one authority, granted per capability rather than per person. There is
  no "admin" here on purpose: `approve_spec` and `erase_data` are different powers and a
  role that bundles them is a role nobody can reason about.
* A **decision** is an exercised capability, attributed and evidenced. FR-25.4: a decision
  without attribution is not a decision, so this module cannot express one.

Provider identities map to principals explicitly (FR-25.1). An unmapped identity may trigger
intake -- anyone can file an issue -- but may not make a decision, because the mapping is
what turns "a GitHub login" into "a person this factory recognises".
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime

from software_factory.errors import FactoryError
from software_factory.memory.records import utc_now
from software_factory.spec.units import TrustClass


class AuthorisationError(FactoryError):
    """A principal attempted something it has no capability for."""


class PrincipalKind(enum.StrEnum):
    """What sort of actor this is.

    The distinction is load-bearing rather than descriptive: several capabilities are
    reserved to `PERSON` outright, because "a human decides" is the entire content of the
    checkpoint. An agent holding `approve_spec` would make the checkpoint a formality.
    """

    PERSON = "person"
    AGENT = "agent"
    AUTOMATION = "automation"
    PLANE = "plane"


class Capability(enum.StrEnum):
    """One authority each (FR-25.2).

    Deliberately fine-grained and deliberately not hierarchical. A capability set is a list
    an operator can read and audit; a role hierarchy is one they have to simulate.
    """

    APPROVE_SPEC = "approve_spec"
    ANSWER_QUESTION = "answer_question"
    WIDEN_BLAST_RADIUS = "widen_blast_radius"
    FORCE_PROMOTE_SKILL = "force_promote_skill"
    ADOPT_DEFINITION_CHANGE = "adopt_definition_change"
    APPROVE_SELF_REFERENTIAL_CHANGE = "approve_self_referential_change"
    EMERGENCY_STOP = "emergency_stop"
    ERASE_DATA = "erase_data"
    OVERRIDE_GATE = "override_gate"
    CANCEL_WORK = "cancel_work"
    SKIP_STAGE = "skip_stage"
    STEER_RUN = "steer_run"


#: Capabilities only a person may hold, whatever the definition says.
#:
#: These are the checkpoints whose *whole content* is that a human looked. Granting one to
#: an agent does not delegate the decision; it deletes it. The definition loader refuses
#: such a grant rather than honouring it, because a factory that can configure its way out
#: of its own checkpoints has none.
PERSON_ONLY: frozenset[Capability] = frozenset(
    {
        Capability.APPROVE_SPEC,
        Capability.ADOPT_DEFINITION_CHANGE,
        Capability.APPROVE_SELF_REFERENTIAL_CHANGE,
        Capability.OVERRIDE_GATE,
        Capability.FORCE_PROMOTE_SKILL,
        Capability.ERASE_DATA,
        Capability.WIDEN_BLAST_RADIUS,
        Capability.CANCEL_WORK,
        Capability.SKIP_STAGE,
        # Both were missing, and both are checkpoints in `ANSWERED_BY`. An automation
        # holding `emergency_stop` can halt the factory on a rule nobody reviewed at the
        # moment it fires; an agent holding `answer_question` clears the checkpoint that
        # exists *because* a person needed to answer, which does not delegate the question
        # so much as delete it.
        Capability.EMERGENCY_STOP,
        Capability.ANSWER_QUESTION,
    }
)


@dataclass(frozen=True, slots=True)
class Principal:
    """One actor with a stable id.

    ``id`` is stable across renames because it ends up in the ledger, and a ledger entry
    whose actor cannot be resolved later is an entry that has lost its meaning.
    """

    id: str
    kind: PrincipalKind
    display_name: str = ""
    groups: frozenset[str] = frozenset()
    capabilities: frozenset[Capability] = frozenset()
    #: Provider identities that resolve to this principal, as ``provider:handle``.
    identities: frozenset[str] = frozenset()
    active: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("a principal needs a stable id")
        forbidden = self.capabilities & PERSON_ONLY
        if self.kind is not PrincipalKind.PERSON and forbidden:
            names = ", ".join(sorted(c.value for c in forbidden))
            raise ValueError(
                f"{self.id!r} is a {self.kind.value} and cannot hold {names}: these "
                "capabilities exist to record that a person decided, so granting one to a "
                "non-person removes the checkpoint rather than delegating it"
            )

    def holds(self, capability: Capability) -> bool:
        return self.active and capability in self.capabilities

    @property
    def trust(self) -> TrustClass:
        """How far a claim sourced from this principal may travel (FR-6.4b).

        A person's decision is `operator`; the plane's own bookkeeping is `internal`; an
        agent's or automation's output is `internal` too, because an agent reads untrusted
        text and cannot be the reason a claim is trusted.
        """
        return TrustClass.OPERATOR if self.kind is PrincipalKind.PERSON else TrustClass.INTERNAL


@dataclass(frozen=True, slots=True)
class Decision:
    """An exercised capability, attributed and evidenced (FR-25.4).

    ``evidence_shown`` is what the deciding principal actually saw at the time, not what is
    reconstructible now. An approval given against a summary is a different decision from
    one given against a diff, and only the record made at the time can tell them apart.
    """

    principal_id: str
    capability: Capability
    subject: str
    rationale: str
    at: datetime = field(default_factory=utc_now)
    evidence_shown: tuple[str, ...] = ()
    channel: str = "cli"

    def as_dict(self) -> dict[str, object]:
        return {
            "principal": self.principal_id,
            "capability": self.capability.value,
            "subject": self.subject,
            "rationale": self.rationale,
            "at": self.at.isoformat(),
            "evidenceShown": list(self.evidence_shown),
            "channel": self.channel,
        }


@dataclass(frozen=True, slots=True)
class Refused:
    """Why an authorisation did not succeed. Never a bare False."""

    code: str
    message: str
    remediation: str

    def raise_for(self) -> None:
        raise AuthorisationError(self.message, remediation=self.remediation)


class Directory:
    """The principals a factory recognises, and the questions asked of them.

    Loaded from the definition, so authority is configuration a repository review can see --
    the same argument as FR-2.1 for everything else. Nothing here mutates at run time: a
    factory that can grant itself a capability mid-run has no capability model.
    """

    def __init__(self, principals: list[Principal] | None = None) -> None:
        self._by_id: dict[str, Principal] = {}
        self._by_identity: dict[str, str] = {}
        self._sealed = False
        for principal in principals or []:
            self.add(principal)

    def add(self, principal: Principal) -> None:
        if principal.id in self._by_id:
            raise ValueError(f"duplicate principal id {principal.id!r}")
        if self._sealed:
            raise ValueError(
                "this directory is sealed; capabilities may not be granted at run time. "
                "Change the definition and reload."
            )
        self._by_id[principal.id] = principal
        for declared in principal.identities:
            # Normalised here rather than only at lookup. `add` stored identities verbatim
            # and `resolve_identity` lower-cased its key, so a directory built directly --
            # in a test, or by any caller not going through the loader -- silently lost
            # every mixed-case identity, and the collision check compared un-normalised
            # strings so `GitHub:Amaya` and `git-host:amaya` did not collide either.
            provider, separator, handle = declared.partition(":")
            identity = (
                f"{provider.strip().casefold()}:{handle.strip().casefold()}"
                if separator
                else declared.strip().casefold()
            )
            existing = self._by_identity.get(identity)
            if existing is not None and existing != principal.id:
                raise ValueError(
                    f"provider identity {declared!r} maps to both {existing!r} and "
                    f"{principal.id!r}; an ambiguous identity cannot attribute a decision"
                )
            self._by_identity[identity] = principal.id

    def get(self, principal_id: str) -> Principal | None:
        return self._by_id.get(principal_id)

    def frozen(self) -> Directory:
        """A copy that refuses further mutation.

        The class docstring says "nothing here mutates at run time", and `add` is public and
        grants capabilities. Both readings are defensible -- construction needs a way to add
        -- so the honest resolution is a method that makes the claim true where it is
        relied on, rather than a docstring asserting it of a class that does not enforce it.
        """
        copy = Directory(self.all())
        copy._sealed = True
        return copy

    def resolve_identity(self, provider: str, handle: str) -> Principal | None:
        """Map a provider identity to a principal, or ``None`` if it is unmapped.

        ``None`` is a normal answer, not an error: anyone may open an issue, and intake
        accepts work from strangers. It only stops being normal when a *decision* is
        attempted -- see :meth:`authorise`, which refuses an unknown principal by name.
        """
        # Each part folded separately. Folding the joined string only strips the outer
        # whitespace, so a handle with leading space still missed.
        key = f"{provider.strip().casefold()}:{handle.strip().casefold()}"
        principal_id = self._by_identity.get(key)
        principal = self._by_id.get(principal_id) if principal_id else None
        # Deactivating a principal revokes their intake trust too. It did not: `authorise`
        # refuses an inactive principal, but this is the check that decides whether an
        # automation requiring a known author accepts an event -- so a departed maintainer's
        # handle still started work on their say-so.
        return principal if principal is not None and principal.active else None

    def all(self) -> list[Principal]:
        return sorted(self._by_id.values(), key=lambda p: p.id)

    def holders(self, capability: Capability) -> list[Principal]:
        """Who could decide this. Used to route a checkpoint to someone who can clear it."""
        return [p for p in self.all() if p.holds(capability)]

    def authorise(
        self,
        principal_id: str,
        capability: Capability,
        *,
        subject: str,
        rationale: str,
        evidence_shown: tuple[str, ...] = (),
        channel: str = "cli",
    ) -> Decision | Refused:
        """Check one principal against one capability, returning the decision it justifies.

        The decision is *returned*, not recorded: the caller writes it to the ledger with
        the rest of the transition it belongs to, so an authorisation and the action it
        authorised cannot end up as two records that disagree.
        """
        principal = self._by_id.get(principal_id)
        if principal is None:
            return Refused(
                "identity.unknown_principal",
                f"{principal_id!r} is not a principal this factory recognises",
                (
                    "Add them to `principals/` with the capabilities they hold. An "
                    "unmapped identity may trigger intake but may not make a decision."
                ),
            )
        if not principal.active:
            return Refused(
                "identity.inactive_principal",
                f"{principal_id!r} is no longer active",
                "Reactivate the principal, or have an active holder decide instead.",
            )
        if not principal.holds(capability):
            holders = [p.id for p in self.holders(capability)]
            return Refused(
                "identity.missing_capability",
                f"{principal_id!r} does not hold {capability.value}",
                (
                    f"Grant it in the definition, or ask one of: {', '.join(holders)}."
                    if holders
                    else (
                        f"No principal holds {capability.value}. Grant it to someone in "
                        "the definition; a capability nobody holds is a checkpoint nobody "
                        "can clear."
                    )
                ),
            )
        if not subject.strip():
            return Refused(
                "identity.no_subject",
                f"{principal_id!r} exercised {capability.value} against no subject",
                (
                    "Name what is being decided -- a work item id, a delta id, a run id. "
                    "A decision with no subject is not an approval of anything; it is a "
                    "token that authorises everything, permanently."
                ),
            )
        if not rationale.strip():
            return Refused(
                "identity.no_rationale",
                f"a decision on {subject!r} was made with no stated reason",
                (
                    "State why. The record exists so a later reader can evaluate the "
                    "decision, and 'approved' alone tells them nothing."
                ),
            )
        return Decision(
            principal_id=principal.id,
            capability=capability,
            subject=subject,
            rationale=rationale,
            evidence_shown=evidence_shown,
            channel=channel,
        )
