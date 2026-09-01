"""Turning a raw recording into something worth sending to a person (PRD FR-22.7, V-5).

A run's recording is a real-time capture of everything that happened, which for a nine-minute
build is nine minutes a reviewer will not watch. The reference workflow ends with an artefact
somebody can actually look at: trimmed to the part that matters, indexed so a reviewer can
jump to the failure, and with anything sensitive that appeared on screen removed.

Post-production is where evidence stops being an audit trail and becomes a review aid, which
is exactly where it is most tempting to make the evidence more persuasive than it is. So
every operation here is arranged around one rule: **an edit is always visible.**

**A trim is declared, and the original digest survives.** A recording trimmed to the part
that works, presented as the whole run, lets a reviewer watch a change succeed and approve
it -- when the cut removed the failure. Every `Cut` is recorded with what it removed and why,
the edited artefact carries a new digest, and it names the original it came from.

**An edited recording never claims the original's digest.** The digest is what makes an
evidence item checkable; reusing it after changing the bytes breaks the one property the
evidence chain has.

**A truncated recording stays truncated.** Post-production cannot restore what was never
captured, and a trim that removed the truncated tail would turn a half-recording into a
tidy short one -- the exact substitution `Recording.__post_init__` refuses at capture time.

**Chapters are derived, never invented.** A marker is placed from a ledger entry that
actually happened at that moment. A chapter list somebody wrote by hand is an index to a
recording nobody checked against.

**Redaction that cannot be verified is refused.** A redaction that silently fails publishes
the secret to everyone the artefact is shared with, which is worse than not offering
redaction at all.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from software_factory.errors import ErrorCode, FactoryError
from software_factory.evals.evidence import EvidenceClass, EvidenceItem
from software_factory.evals.recording import Recording, RecordingKind
from software_factory.memory.records import utc_now


class PostProductionError(FactoryError):
    """An edit this factory will not make, or will not present as evidence."""

    code = ErrorCode.INVALID_REQUEST


class CutReason(enum.StrEnum):
    """Why a span was removed. Enumerated so a reviewer can tell what was taken out.

    `SILENCE` and `SETUP` are cosmetic; `SENSITIVE` means something was on screen that must
    not be shared. They are separated because a reviewer who sees "three cuts" needs to know
    whether the recording is shorter or *different*.
    """

    SILENCE = "silence"
    SETUP = "setup"
    SENSITIVE = "sensitive"


@dataclass(frozen=True, slots=True)
class Cut:
    """One removed span."""

    start: timedelta
    end: timedelta
    reason: CutReason
    note: str = ""

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise PostProductionError(
                f"a cut from {self.start} to {self.end} removes nothing",
                remediation="Give an end after the start.",
            )
        if self.reason is CutReason.SENSITIVE and not self.note.strip():
            raise PostProductionError(
                "a sensitive cut must say what it removed",
                remediation=(
                    "Describe it without reproducing it -- 'the deploy token was echoed' "
                    "rather than the token. A cut nobody can characterise is one nobody "
                    "can confirm was necessary or sufficient."
                ),
            )

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.total_seconds(),
            "end": self.end.total_seconds(),
            "reason": self.reason.value,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Chapter:
    """A marker a reviewer can jump to, derived from something that happened.

    `source` names the ledger entry it came from. A chapter with no source is an index to a
    recording nobody checked against, and the whole value of jumping to "the gate failed" is
    that the gate did in fact fail there.
    """

    at: timedelta
    title: str
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise PostProductionError(
                f"chapter {self.title!r} names no source event",
                remediation=(
                    "Derive chapters from ledger entries. A hand-written marker points at a "
                    "moment nobody verified anything happened at."
                ),
            )

    def as_dict(self) -> dict[str, Any]:
        return {"at": self.at.total_seconds(), "title": self.title, "source": self.source}


@dataclass(frozen=True, slots=True)
class Edited:
    """A recording after post-production, and everything done to it."""

    id: str
    source_id: str
    source_digest: str
    kind: RecordingKind
    location: str
    duration: timedelta
    digest: str
    cuts: tuple[Cut, ...] = ()
    chapters: tuple[Chapter, ...] = ()
    truncated: bool = False
    truncated_reason: str = ""
    captured_at: Any = field(default_factory=utc_now)

    @property
    def edited(self) -> bool:
        return bool(self.cuts)

    @property
    def removed(self) -> timedelta:
        return sum((cut.duration for cut in self.cuts), timedelta())

    def describe(self) -> str:
        """What a reviewer is told before they press play.

        Leads with what was removed rather than with the runtime. A reviewer who learns
        afterwards that ninety seconds were cut has already formed a view.
        """
        seconds = int(self.duration.total_seconds())
        parts = [f"{self.kind.value} recording, {seconds}s"]
        if self.cuts:
            by_reason: dict[str, int] = {}
            for cut in self.cuts:
                by_reason[cut.reason.value] = by_reason.get(cut.reason.value, 0) + 1
            shape = ", ".join(f"{count} {reason}" for reason, count in sorted(by_reason.items()))
            parts.append(
                f"EDITED: {int(self.removed.total_seconds())}s removed in "
                f"{len(self.cuts)} cut(s) ({shape})"
            )
        if self.truncated:
            parts.append(f"TRUNCATED: {self.truncated_reason}")
        if self.chapters:
            parts.append(f"{len(self.chapters)} chapter(s)")
        return ". ".join(parts)

    def as_evidence(self) -> EvidenceItem:
        """Attach as evidence, carrying every flag that changes how it should be read.

        `redacted` is true whenever a sensitive cut was made, so the evidence bundle says so
        without a reader having to open the cut list. A redacted artefact that presents as
        pristine is the one shape of this that causes harm.
        """
        return EvidenceItem(
            id=self.id,
            evidence_class=(
                EvidenceClass.TERMINAL_RECORDING
                if self.kind is RecordingKind.TERMINAL
                else EvidenceClass.SCREEN_RECORDING
            ),
            digest=self.digest,
            location=self.location,
            captured_at=self.captured_at,
            redacted=any(cut.reason is CutReason.SENSITIVE for cut in self.cuts),
            truncated=self.truncated,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sourceId": self.source_id,
            "sourceDigest": self.source_digest,
            "kind": self.kind.value,
            "location": self.location,
            "durationSeconds": self.duration.total_seconds(),
            "digest": self.digest,
            "removedSeconds": self.removed.total_seconds(),
            "cuts": [cut.as_dict() for cut in self.cuts],
            "chapters": [chapter.as_dict() for chapter in self.chapters],
            "truncated": self.truncated,
            "truncatedReason": self.truncated_reason,
        }


def edit(
    recording: Recording,
    *,
    cuts: tuple[Cut, ...] | list[Cut] = (),
    chapters: tuple[Chapter, ...] | list[Chapter] = (),
    location: str = "",
) -> Edited:
    """Produce a reviewable artefact from a raw recording.

    Refuses more than it does, because every refusal here is an artefact that would have
    misled a reviewer who trusted it.
    """
    ordered = sorted(cuts, key=lambda cut: cut.start)
    for first, second in pairwise(ordered):
        if second.start < first.end:
            raise PostProductionError(
                f"cuts {first.start}-{first.end} and {second.start}-{second.end} overlap",
                remediation=(
                    "Merge them. Overlapping cuts make the removed duration wrong, and the "
                    "removed duration is the number a reviewer uses to judge the edit."
                ),
            )
    for cut in ordered:
        if cut.end > recording.duration:
            raise PostProductionError(
                f"a cut ends at {cut.end} in a {recording.duration} recording",
                remediation=(
                    "A cut past the end silently removes nothing while appearing in the "
                    "edit list, which overstates what was taken out."
                ),
            )
    removed = sum((cut.duration for cut in ordered), timedelta())
    if removed >= recording.duration:
        raise PostProductionError(
            "the cuts remove the whole recording",
            remediation=(
                "An empty artefact presented as evidence is worse than no evidence: it "
                "renders, it plays, and it shows nothing."
            ),
        )
    for chapter in chapters:
        if chapter.at > recording.duration:
            raise PostProductionError(
                f"chapter {chapter.title!r} is at {chapter.at}, past the end",
                remediation="Derive chapter times from events inside the recording's span.",
            )

    # A new digest over the source digest and the edit list. Deriving it rather than reusing
    # the original's is the point: the bytes changed, and an evidence item whose digest no
    # longer matches its content is a chain that verifies something nobody is looking at.
    digest = hashlib.sha256(
        "|".join(
            [
                recording.digest,
                *(f"{c.start}-{c.end}-{c.reason.value}" for c in ordered),
            ]
        ).encode("utf-8")
    ).hexdigest()

    return Edited(
        id=f"{recording.id}-edited",
        source_id=recording.id,
        source_digest=recording.digest,
        kind=recording.kind,
        location=location or f"{recording.location}.edited",
        duration=recording.duration - removed,
        digest=digest,
        cuts=tuple(ordered),
        chapters=tuple(sorted(chapters, key=lambda chapter: chapter.at)),
        # Carried, never cleared. Post-production cannot restore what was never captured,
        # and a trim that removed the truncated tail would turn a half-recording into a tidy
        # short one -- exactly the substitution the capture path refuses to make.
        truncated=recording.truncated,
        truncated_reason=recording.truncated_reason,
        captured_at=recording.captured_at,
    )


#: Ledger entry types worth a chapter marker, and what to call them. A reviewer jumps to
#: where something was decided or refused, not to where a file was read.
CHAPTER_EVENTS: dict[str, str] = {
    "gate.evaluated": "gate",
    "run.violation": "contract violation",
    "run.escalation": "escalation",
    "run.checkpoint": "human checkpoint",
    "run.repair": "repair attempt",
    "work_item.blocked": "blocked",
}


def chapters_from(entries: Any, *, started_at: datetime, duration: timedelta) -> list[Chapter]:
    """Derive chapter markers from what the ledger says happened during the recording.

    Entries outside the recording's span are dropped rather than clamped to its edges. A
    marker at 0:00 for something that happened before the camera started points a reviewer
    at a moment the recording does not contain.

    Timestamps are parsed rather than subtracted directly. `LedgerEntry.ts` is an ISO string,
    and the first version of this subtracted it from a `datetime` inside a `try/except
    TypeError: continue` -- so every entry raised, every entry was skipped, and the function
    returned an empty list for every input while looking like it worked. The narrow catch
    around a wide operation is what made a total failure indistinguishable from a recording
    where nothing happened.
    """
    found: list[Chapter] = []
    for entry in entries:
        title = CHAPTER_EVENTS.get(str(getattr(entry.type, "value", entry.type)))
        if title is None:
            continue
        at = _timestamp(getattr(entry, "ts", None))
        if at is None:
            continue
        offset = at - started_at
        if offset < timedelta() or offset > duration:
            continue
        detail = str(entry.payload.get("gate") or entry.payload.get("reason") or "")
        found.append(
            Chapter(
                at=offset,
                title=f"{title}: {detail}" if detail else title,
                source=f"seq:{entry.seq}",
            )
        )
    return sorted(found, key=lambda chapter: chapter.at)


def _timestamp(raw: Any) -> datetime | None:
    """One ledger timestamp as an aware `datetime`, or `None` if it cannot be read.

    `None` rather than a default: an entry whose time is unreadable has no place on a
    timeline, and putting it at zero would be a marker pointing at the wrong moment.
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
