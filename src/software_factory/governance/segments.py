"""Ledger segmentation: sealing a prefix so it can be archived (PRD FR-27.2, FR-15.10a).

NFR-3.2 promises bounded growth. Without segmentation that is a claim with no mechanism: an
append-only log grows forever, and `verify()` walking it from entry one gets slower every
day until nobody runs it.

A **segment** is a sealed prefix of the ledger. Sealing records the range, the digest of the
last entry in it, and a segment digest over the whole range. The next segment carries the
previous segment's digest, so the chain continues *across* the boundary -- which is the
property that makes an archived prefix verifiable without being present:

    segment 0: entries 1..1000, ends at hash H1000, digest D0
    segment 1: entries 1001..2000, prev_segment_digest = D0, ends at H2000, digest D1

Verifying segment 1 needs D0 and not the thousand entries behind it. So a factory can
archive segment 0 to cold storage and still prove that segment 1 was not rewritten -- which
is the whole reason the ledger is hash-chained in the first place.

What this does *not* do is delete anything. Sealing is a claim about a range; archiving is a
separate act an operator takes, and the manifest is what makes it safe to take.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise
from pathlib import Path

from software_factory.errors import FactoryError
from software_factory.ledger.entry import GENESIS, EntryType, LedgerEntry
from software_factory.ledger.log import Ledger
from software_factory.memory.records import utc_now


class SegmentError(FactoryError):
    """A segment could not be sealed, or does not verify."""


#: How many entries a segment holds by default.
#:
#: Large enough that sealing is rare, small enough that one segment is a reasonable unit to
#: archive and re-verify. A factory writing thousands of entries a day seals roughly weekly.
DEFAULT_SEGMENT_SIZE = 10_000


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """What a verification actually established, segment by segment.

    A bare "ok" from a check that could only inspect half the manifest is the failure this
    project keeps finding: a green result that implies more than it means. An archived
    prefix is the normal case here -- its entries are legitimately absent -- so the report
    separates the segments checked against real entries from those checked on the chain
    alone.
    """

    checked_against_entries: list[int] = field(default_factory=list)
    chain_only: list[int] = field(default_factory=list)
    """Segments whose entries were not present. Verified as links, not as content."""

    @property
    def fully_verified(self) -> bool:
        return not self.chain_only

    def as_dict(self) -> dict[str, object]:
        return {
            "checkedAgainstEntries": sorted(self.checked_against_entries),
            "chainOnly": sorted(self.chain_only),
            "fullyVerified": self.fully_verified,
        }


@dataclass(frozen=True, slots=True)
class Segment:
    """A sealed prefix of the ledger.

    Everything needed to verify the segment *after* its entries are gone: where it starts
    and ends, what the last entry hashed to, what the previous segment digested to, and the
    digest over the whole thing.
    """

    index: int
    first_seq: int
    last_seq: int
    last_hash: str
    prev_segment_digest: str
    entry_count: int
    sealed_at: datetime = field(default_factory=utc_now)
    archived_to: str = ""

    @property
    def digest(self) -> str:
        """This segment's digest, over everything that identifies it.

        Includes ``prev_segment_digest``, which is what chains segments: changing an earlier
        segment changes every later segment's digest, exactly as changing an earlier entry
        changes every later entry's hash.
        """
        material = json.dumps(
            {
                "index": self.index,
                "firstSeq": self.first_seq,
                "lastSeq": self.last_seq,
                "lastHash": self.last_hash,
                "prevSegmentDigest": self.prev_segment_digest,
                "entryCount": self.entry_count,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "firstSeq": self.first_seq,
            "lastSeq": self.last_seq,
            "lastHash": self.last_hash,
            "prevSegmentDigest": self.prev_segment_digest,
            "entryCount": self.entry_count,
            "sealedAt": self.sealed_at.isoformat(),
            "digest": self.digest,
            "archivedTo": self.archived_to,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> Segment:
        return cls(
            index=int(str(raw["index"])),
            first_seq=int(str(raw["firstSeq"])),
            last_seq=int(str(raw["lastSeq"])),
            last_hash=str(raw["lastHash"]),
            prev_segment_digest=str(raw["prevSegmentDigest"]),
            entry_count=int(str(raw["entryCount"])),
            sealed_at=datetime.fromisoformat(str(raw["sealedAt"])),
            archived_to=str(raw.get("archivedTo", "")),
        )


@dataclass(slots=True)
class Manifest:
    """The sealed segments of one ledger, in order.

    Stored beside the ledger rather than inside it. Inside would mean a manifest entry
    changing the hash of the range it describes, which is a definition that cannot be
    satisfied.
    """

    path: Path
    segments: list[Segment] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> Manifest:
        manifest = cls(path=path)
        if not path.exists():
            return manifest
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                manifest.segments.append(Segment.from_dict(json.loads(line)))
        manifest.segments.sort(key=lambda s: s.index)
        return manifest

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = "\n".join(
            json.dumps(segment.as_dict(), sort_keys=True, separators=(",", ":"))
            for segment in self.segments
        )
        self.path.write_text(body + ("\n" if body else ""), encoding="utf-8")

    @property
    def sealed_through(self) -> int:
        """The highest sequence number covered by a sealed segment. 0 when none are."""
        return self.segments[-1].last_seq if self.segments else 0

    @property
    def tip_digest(self) -> str:
        """The digest the next segment chains to. GENESIS when nothing is sealed yet."""
        return self.segments[-1].digest if self.segments else GENESIS

    def verify(self) -> None:
        """Check the segment chain, which is verifiable without any entry being present.

        This is the point of the whole module: an archived prefix stays provable.
        """
        expected_first = 1
        previous_digest = GENESIS
        for expected_index, segment in enumerate(self.segments):
            if segment.index != expected_index:
                raise SegmentError(
                    f"segment index {segment.index} where {expected_index} was expected",
                    remediation="A segment is missing, or the manifest was reordered.",
                )
            if segment.first_seq != expected_first:
                raise SegmentError(
                    f"segment {segment.index} starts at {segment.first_seq}, "
                    f"leaving a gap after {expected_first - 1}",
                    remediation=(
                        "Segments must cover the ledger without gaps, or an archived range "
                        "cannot be shown to be the range it claims."
                    ),
                )
            if segment.prev_segment_digest != previous_digest:
                raise SegmentError(
                    f"segment {segment.index} chains to {segment.prev_segment_digest[:12]}, "
                    f"but the previous segment digests to {previous_digest[:12]}",
                    remediation=(
                        "An earlier segment was altered after sealing. Restore the manifest "
                        "from backup; the chain is what makes an archived prefix provable."
                    ),
                )
            expected_first = segment.last_seq + 1
            previous_digest = segment.digest

    def verify_against(self, ledger: Ledger) -> VerificationReport:
        """Verify the chain *and* check each segment against the entries it seals.

        `verify()` alone establishes that the manifest is internally consistent, which is
        a weaker claim than it reads as: the entries it describes could have been rewritten
        underneath it and nothing would notice. This is the check the module docstring
        promises -- and it is separate because an archived prefix genuinely cannot be
        checked this way, and pretending otherwise would make the strong check impossible
        to run at all.

        Two comparisons per segment whose entries are present: the last entry's hash
        against `last_hash`, and the entry count against `entry_count`. Plus the anchor:
        every segment writes its digest into the ledger when sealed, so the final segment
        -- which no later segment chains to -- is committed to by something outside the
        manifest. Without that anchor the tip is rewritable at will.
        """
        self.verify()
        report = VerificationReport()
        by_seq = {entry.seq: entry for entry in ledger.read()}
        anchors = _anchors_in(ledger)

        for segment in self.segments:
            anchor = anchors.get(segment.index)
            if anchor is not None and anchor != segment.digest:
                raise SegmentError(
                    f"segment {segment.index} digests to {segment.digest[:12]}, but its "
                    f"ledger anchor records {anchor[:12]}",
                    remediation=(
                        "The manifest was altered after sealing. The ledger anchor is the "
                        "independent record; restore the manifest from backup."
                    ),
                )

            window = [
                by_seq[seq]
                for seq in range(segment.first_seq, segment.last_seq + 1)
                if seq in by_seq
            ]
            if len(window) != segment.entry_count:
                if not window:
                    report.chain_only.append(segment.index)
                    continue
                raise SegmentError(
                    f"segment {segment.index} sealed {segment.entry_count} entries but "
                    f"{len(window)} are present in {segment.first_seq}..{segment.last_seq}",
                    remediation="An entry was removed from a sealed range.",
                )

            # Each entry is re-hashed rather than trusted. Comparing only the stored tip
            # hash catches nothing: a tamperer editing an entry in the middle of the range
            # leaves every other line, the tip included, exactly as it was.
            previous = window[0].prev_hash
            for entry in window:
                if not entry.verify() or entry.prev_hash != previous:
                    raise SegmentError(
                        f"entry {entry.seq} does not match the sealed range of segment "
                        f"{segment.index}",
                        remediation=(
                            "An entry inside a sealed range was rewritten. Run `sf ledger "
                            "verify` to find the first divergence."
                        ),
                    )
                previous = entry.hash
            if previous != segment.last_hash:
                raise SegmentError(
                    f"segment {segment.index} ends at {previous[:12]}, which does not "
                    f"match the sealed hash {segment.last_hash[:12]}",
                    remediation="A sealed range was rewritten. Restore from backup.",
                )
            report.checked_against_entries.append(segment.index)

        missing_anchor = [
            s.index
            for s in self.segments
            if s.index not in anchors and s.index in report.checked_against_entries
        ]
        if missing_anchor:
            raise SegmentError(
                f"segment(s) {missing_anchor} have no anchor entry in the ledger",
                remediation=(
                    "A segment sealed by this version writes a `segment.sealed` entry. A "
                    "manifest with no anchor cannot be shown to be the one that was sealed."
                ),
            )
        return report


def _anchors_in(ledger: Ledger) -> dict[int, str]:
    """Segment digests recorded in the ledger at seal time, by segment index.

    This is what makes the tip tamper-evident. The manifest chains each segment to the one
    before it, so every segment *except the last* is committed to by a successor -- and the
    last one is the one an attacker would rewrite.
    """
    anchors: dict[int, str] = {}
    for entry in ledger.read():
        if entry.type is not EntryType.SEGMENT_SEALED:
            continue
        index = entry.payload.get("index")
        digest = entry.payload.get("digest")
        if isinstance(index, int) and isinstance(digest, str):
            anchors[index] = digest
    return anchors


def seal(
    ledger: Ledger,
    manifest: Manifest,
    *,
    size: int = DEFAULT_SEGMENT_SIZE,
    now: datetime | None = None,
) -> list[Segment]:
    """Seal every complete segment the ledger has accumulated. Returns the new ones.

    Only *complete* segments are sealed. A partial one would have to be re-sealed as it
    grew, and a seal that changes is not a seal.

    The ledger is verified first. Sealing a tampered range does not merely fail to detect
    it -- it launders it, because the segment then records the tampered hash as the sealed
    truth and every later comparison is against the forgery.
    """
    try:
        ledger.verify()
    except FactoryError as exc:
        raise SegmentError(
            f"the ledger does not verify, so nothing may be sealed: {exc}",
            remediation=(
                "Run `sf ledger verify` and restore from backup. Sealing a broken chain "
                "records the tampered hash as the sealed truth."
            ),
        ) from exc

    entries = [entry for entry in ledger.read() if entry.seq > manifest.sealed_through]
    if len(entries) < size:
        return []

    sealed: list[Segment] = []
    index = len(manifest.segments)
    previous_digest = manifest.tip_digest

    for start in range(0, len(entries) - size + 1, size):
        window = entries[start : start + size]
        segment = Segment(
            index=index,
            first_seq=window[0].seq,
            last_seq=window[-1].seq,
            last_hash=window[-1].hash,
            prev_segment_digest=previous_digest,
            entry_count=len(window),
            sealed_at=now or utc_now(),
        )
        _check_contiguous(window)
        manifest.segments.append(segment)
        sealed.append(segment)
        previous_digest = segment.digest
        index += 1

    if sealed:
        manifest.save()
        for segment in sealed:
            # The anchor goes in after the manifest is durable: an anchor for a segment the
            # manifest does not contain would report tampering on the next verification,
            # which is a worse failure than a missing anchor.
            ledger.append(
                EntryType.SEGMENT_SEALED,
                actor="governance",
                subject=f"segment-{segment.index}",
                payload={
                    "index": segment.index,
                    "digest": segment.digest,
                    "firstSeq": segment.first_seq,
                    "lastSeq": segment.last_seq,
                    "lastHash": segment.last_hash,
                },
            )
    return sealed


def _check_contiguous(window: list[LedgerEntry]) -> None:
    """A segment must cover an unbroken range, or its digest describes nothing definite."""
    for previous, entry in pairwise(window):
        if entry.seq != previous.seq + 1:
            raise SegmentError(
                f"ledger jumps from {previous.seq} to {entry.seq}; a segment cannot seal a "
                "range with a hole in it",
                remediation="Run `sf ledger verify` to find the first divergence.",
            )
