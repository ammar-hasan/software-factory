"""Living Spec units and their agreement state (PRD FR-5, docs/harness/living-spec.md).

The spec is only useful if it can *block* a change, and it can only earn that authority
if drift is detected mechanically. So every anchor here is content-addressed: agreement
is a digest comparison and a test outcome, never a model's opinion.
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

UNIT_ID = r"^[A-Z][A-Z0-9]{1,7}-[0-9]{1,6}$"
UnitId = Annotated[str, StringConstraints(pattern=UNIT_ID)]
"""Spec unit ids look like ``PAY-101``.

Deliberately short and human-quotable, because these ids end up in review comments and
commit messages. They are immutable and never reused, including after retirement, so
that an old ledger entry keeps its meaning forever (living-spec.md S-1).
"""


class UnitStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    RETIRED = "retired"


class Agreement(enum.StrEnum):
    """The five states of the spec/code/test triangle (living-spec.md §2)."""

    AGREED = "agreed"
    UNVERIFIED = "unverified"
    DRIFTED = "drifted"
    CONTRADICTED = "contradicted"
    ORPHANED = "orphaned"

    @property
    def blocks_build(self) -> bool:
        """Only a contradiction blocks. Drift is a re-anchor proposal, not a stop."""
        return self is Agreement.CONTRADICTED


class TrustClass(enum.StrEnum):
    """Where a claim came from, and therefore how far it may travel (PRD FR-6.4b).

    Ordered weakest-last so ``min`` over an iterable gives the derived trust directly.
    """

    VERIFIED = "verified"
    OPERATOR = "operator"
    INTERNAL = "internal"
    UNTRUSTED = "untrusted"


_TRUST_ORDER = {
    TrustClass.VERIFIED: 0,
    TrustClass.OPERATOR: 1,
    TrustClass.INTERNAL: 2,
    TrustClass.UNTRUSTED: 3,
}


def derived_trust(*classes: TrustClass) -> TrustClass:
    """Trust is monotone downward: a derived object is only as trusted as its weakest input."""
    if not classes:
        return TrustClass.INTERNAL
    return max(classes, key=lambda c: _TRUST_ORDER[c])


def digest_text(text: str) -> str:
    """Digest of an anchored range, normalised so formatting is not a behaviour change.

    Whitespace runs collapse and blank lines vanish before hashing. Without this, every
    reformat marks every anchor drifted and the signal is worthless within a week
    (adversarial finding AR-20). Indentation-significant languages are unaffected in
    practice because a reindent that changes semantics also changes tokens.
    """
    normalised = re.sub(r"[ \t]+", " ", text)
    normalised = "\n".join(line.strip() for line in normalised.splitlines() if line.strip())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


class CodeAnchor(BaseModel):
    """A content-addressed pointer into code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    symbol: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    digest: str | None = None

    @model_validator(mode="after")
    def _range_is_ordered(self) -> Self:
        if self.start_line and self.end_line and self.end_line < self.start_line:
            raise ValueError("end_line must not precede start_line")
        return self

    def locator(self) -> str:
        if self.symbol:
            return f"{self.path}:{self.symbol}"
        if self.start_line:
            return f"{self.path}:{self.start_line}-{self.end_line or self.start_line}"
        return self.path


class TestAnchor(BaseModel):
    """A pointer to the test that verifies a criterion."""

    __test__ = False  # not a pytest test class, despite the name

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    test_id: str | None = None

    def locator(self) -> str:
        return f"{self.path}::{self.test_id}" if self.test_id else self.path


class Criterion(BaseModel):
    """One individually checkable acceptance criterion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    verified_by: tuple[TestAnchor, ...] = ()
    observed_failing: bool = False
    """True once a test for this criterion has been seen failing without the change.

    A test that has never failed is unproven, not proven -- this flag is what the
    `criterion-observed-failing` gate reads.
    """


class Constraint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    kind: str
    statement: str = Field(min_length=1)


class SpecUnit(BaseModel):
    """One unit of intended behaviour."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    id: UnitId
    title: str = Field(min_length=1)
    status: UnitStatus = UnitStatus.DRAFT
    intent: str = Field(min_length=1)
    acceptance: tuple[Criterion, ...] = ()
    constraints: tuple[Constraint, ...] = ()
    implements: tuple[CodeAnchor, ...] = ()
    verifies: tuple[TestAnchor, ...] = ()
    supersedes: tuple[UnitId, ...] = ()
    provenance: tuple[str, ...] = ()
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    trust: TrustClass = TrustClass.OPERATOR
    owners: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _criterion_ids_unique(self) -> Self:
        ids = [c.id for c in self.acceptance]
        if len(ids) != len(set(ids)):
            raise ValueError("acceptance criterion ids must be unique within a unit")
        return self

    @model_validator(mode="after")
    def _active_units_are_anchored(self) -> Self:
        """An active unit with no code anchor can never be checked, so it cannot gate."""
        if self.status is UnitStatus.ACTIVE and not self.implements:
            raise ValueError(
                "an active unit must declare at least one `implements` anchor; "
                "leave it `draft` until it points at code"
            )
        return self

    def surfaces(self) -> set[str]:
        return {anchor.path for anchor in self.implements}

    def intersects(self, paths: set[str]) -> bool:
        """True when this unit governs any of ``paths``.

        Prefix matching, so an anchor on a package covers files inside it.
        """
        for anchor in self.implements:
            for path in paths:
                if path == anchor.path or path.startswith(anchor.path.rstrip("/") + "/"):
                    return True
        return False


VAGUE = re.compile(
    r"^\W*(?:should|must|will)?\s*(?:be\s+)?"
    r"(?:fast|quick|slow|good|nice|clean|simple|robust|reliable|scalable|performant|"
    r"user[- ]friendly|intuitive|maintainable|secure)\W*$",
    re.IGNORECASE,
)


def criterion_is_checkable(statement: str) -> bool:
    """Reject criteria no test could distinguish from their negation (living-spec.md S-3).

    A cheap syntactic screen, not a proof: it catches the common case ("should be fast")
    and leaves the judgement calls to review. Being cheap is the point -- it runs on
    every load.
    """
    return not VAGUE.match(statement.strip())


@dataclass(frozen=True, slots=True)
class AgreementResult:
    """Why a unit is in the state it is in.

    ``reason`` is written for a human reading a blocked build, so it names the anchor
    rather than restating the state.
    """

    unit_id: str
    state: Agreement
    reason: str
    drifted_anchors: tuple[str, ...] = ()
    failing_tests: tuple[str, ...] = ()
    conflicting_units: tuple[str, ...] = ()

    @property
    def blocks_build(self) -> bool:
        return self.state.blocks_build


@dataclass(slots=True)
class SpecStore:
    """The loaded set of spec units, keyed by id."""

    units: dict[str, SpecUnit] = field(default_factory=dict)
    root: Path | None = None

    def add(self, unit: SpecUnit) -> None:
        if unit.id in self.units:
            raise ValueError(f"duplicate spec unit id {unit.id!r}")
        self.units[unit.id] = unit

    def active(self) -> list[SpecUnit]:
        return [u for u in self.units.values() if u.status is UnitStatus.ACTIVE]

    def slice_for(self, paths: set[str]) -> list[SpecUnit]:
        """Units governing ``paths``, ranked most-anchored first.

        Ranking by anchor overlap puts the most specific unit first, which is the one a
        reader most needs when the budget truncates the list.
        """
        matching = [u for u in self.active() if u.intersects(paths)]
        return sorted(matching, key=lambda u: (-len(u.implements), u.id))
