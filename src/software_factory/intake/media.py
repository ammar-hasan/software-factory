"""Media as a research input (V26).

A specification arrives as a recording more often than anyone plans for: a demo somebody
screen-captured, a support call where the actual requirement was stated out loud, a
walkthrough of the bug nobody could reproduce from the ticket. The factory could read
tickets, chat and repositories, and had no way to take any of that.

Accepting it is easy. Accepting it *safely* is the module, because a transcript is the most
dangerous input a factory can be handed:

**It is untrusted, always, and the trust class is not negotiable.** A transcript is words
somebody said, transcribed by a model that guesses, in a file anybody could have edited. It
is `UNTRUSTED` at the same level as a comment on a public issue -- and the memory store
already refuses untrusted provenance in canon, so the one thing this module must not do is
launder it into something stronger on the way in.

**A transcript is quoted, never obeyed.** "Now delete the old migrations" in a recording is
a sentence a person said, not an instruction to this factory. Everything extracted here is
wrapped as quoted material, and a claim whose only support is a transcript can never satisfy
a gate on its own.

**Confidence from the transcriber is carried, not discarded.** Speech recognition reports
how sure it was, and a passage at 40% confidence is a guess about what somebody said. A
requirement derived from a guess about words is two guesses deep, and the reader has to be
able to see that.

**A timestamp is provenance.** "At 14:32 they said the export is wrong" can be checked
against the recording; "the export is wrong" cannot. Every extracted passage keeps its
offset, which is what makes the source a source rather than an assertion.
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from software_factory.errors import ErrorCode, FactoryError
from software_factory.spec.units import TrustClass

#: Below this, a passage is reported but never offered as the basis of a requirement.
#: Speech recognition below about two-thirds confidence is reliably wrong on exactly the
#: words that matter -- names, numbers and negations.
USABLE_CONFIDENCE = 0.65

#: Longest passage kept whole. A wall of transcript in a pack is a pack that has spent its
#: budget on somebody clearing their throat.
MAX_PASSAGE_CHARS = 600


class MediaError(FactoryError):
    """A media source this factory will not read."""

    code = ErrorCode.INVALID_REQUEST


class MediaKind(enum.StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    SCREEN_SHARE = "screen-share"


@dataclass(frozen=True, slots=True)
class Passage:
    """One span of transcript, with everything needed to check it against the source."""

    at: timedelta
    text: str
    speaker: str = ""
    confidence: float = 1.0
    truncated: bool = False

    @property
    def usable(self) -> bool:
        """Whether this passage may support a requirement, as opposed to merely appearing.

        A property rather than a filter applied at load, so an unusable passage is still
        *visible*. Dropping low-confidence speech means a reader never learns that the one
        moment they care about was unintelligible.
        """
        return self.confidence >= USABLE_CONFIDENCE

    def quote(self) -> str:
        """The passage as it must appear in a pack: attributed, timed, and marked untrusted.

        `untrusted="true"` is the same marking the task text carries, because a transcript
        has exactly the same standing: words from outside the definition, which an agent may
        read and may not treat as an instruction.
        """
        who = f" speaker={self.speaker!r}" if self.speaker else ""
        mark = "" if self.usable else ' confidence="low"'
        return (
            f'<quote untrusted="true" at="{int(self.at.total_seconds())}s"{who}{mark}>'
            f"{self.text}</quote>"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.total_seconds(),
            "text": self.text,
            "speaker": self.speaker,
            "confidence": self.confidence,
            "usable": self.usable,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class MediaSource:
    """A recording offered as research, and what could be read out of it."""

    id: str
    kind: MediaKind
    ref: str
    digest: str
    duration: timedelta
    passages: tuple[Passage, ...] = ()
    trust: TrustClass = TrustClass.UNTRUSTED

    def __post_init__(self) -> None:
        if self.trust is not TrustClass.UNTRUSTED:
            # The one refusal that matters. A caller that could mark a transcript
            # `OPERATOR` could walk it into canon, and everything else here would be
            # decoration around an open door.
            raise MediaError(
                f"a media source cannot be {self.trust.value}",
                remediation=(
                    "Transcripts are untrusted: words somebody said, transcribed by a model "
                    "that guesses, in a file anybody could edit. To raise a claim's trust, "
                    "verify it against something checkable and cite that instead."
                ),
            )

    @property
    def usable_passages(self) -> tuple[Passage, ...]:
        return tuple(passage for passage in self.passages if passage.usable)

    @property
    def unintelligible(self) -> tuple[Passage, ...]:
        return tuple(passage for passage in self.passages if not passage.usable)

    def search(self, pattern: str) -> tuple[Passage, ...]:
        """Passages matching a pattern, low-confidence ones included.

        Included deliberately: somebody searching for "export" needs to know the word
        appears at 14:32 in a passage the transcriber was unsure about, because that is
        precisely the moment to go and listen.
        """
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise MediaError(
                f"{pattern!r} is not a valid search",
                remediation="Use a literal string or a valid regular expression.",
            ) from exc
        return tuple(passage for passage in self.passages if regex.search(passage.text))

    def render(self, *, limit: int = 20) -> str:
        """What a pack shows: quoted passages, and an honest note about what was left out."""
        shown = self.usable_passages[:limit]
        lines = [
            f'<media kind="{self.kind.value}" ref="{self.ref}" '
            f'duration="{int(self.duration.total_seconds())}s" untrusted="true">'
        ]
        lines.extend(f"  {passage.quote()}" for passage in shown)
        remaining = len(self.usable_passages) - len(shown)
        if remaining > 0:
            lines.append(f"  <!-- {remaining} further passage(s) not shown -->")
        if self.unintelligible:
            # Stated rather than silently dropped. A reader who is not told that four
            # passages were unintelligible reads the transcript as complete.
            lines.append(
                f"  <!-- {len(self.unintelligible)} passage(s) below "
                f"{USABLE_CONFIDENCE:.0%} transcription confidence, not shown -->"
            )
        lines.append("</media>")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "ref": self.ref,
            "digest": self.digest,
            "durationSeconds": self.duration.total_seconds(),
            "trust": self.trust.value,
            "passages": [passage.as_dict() for passage in self.passages],
            "unintelligible": len(self.unintelligible),
        }


#: `00:01:23.450 --> 00:01:27.000` and the `1` cue-number lines that precede SRT entries.
_TIME = re.compile(
    r"^(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{3})\s*-->\s*\d{2}:\d{2}:\d{2}"
)
_SPEAKER = re.compile(r"^(?:<v\s+(?P<v>[^>]+)>|(?P<plain>[A-Z][\w .'-]{0,40}):)\s*")
_CONFIDENCE = re.compile(r"\[\s*confidence[:=]\s*(?P<value>[01](?:\.\d+)?)\s*\]", re.IGNORECASE)


def parse_transcript(text: str, *, ref: str, kind: MediaKind = MediaKind.VIDEO) -> MediaSource:
    """Read a WebVTT or SRT transcript.

    Both formats, because a caller who has one and not the other is not going to convert it,
    and the difference between them is a comma. A cue with no timestamp is skipped rather
    than given offset zero: a passage that claims to be at the start of the recording when
    nobody knows where it is, is provenance that points at the wrong place.
    """
    passages: list[Passage] = []
    current: timedelta | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is None or not buffer:
            return
        raw = " ".join(buffer).strip()
        if not raw:
            return
        confidence = 1.0
        match = _CONFIDENCE.search(raw)
        if match:
            confidence = float(match.group("value"))
            raw = _CONFIDENCE.sub("", raw).strip()
        speaker = ""
        speaker_match = _SPEAKER.match(raw)
        if speaker_match:
            speaker = (speaker_match.group("v") or speaker_match.group("plain") or "").strip()
            raw = raw[speaker_match.end() :].strip()
        truncated = len(raw) > MAX_PASSAGE_CHARS
        passages.append(
            Passage(
                at=current,
                text=raw[:MAX_PASSAGE_CHARS],
                speaker=speaker,
                confidence=confidence,
                truncated=truncated,
            )
        )

    for line in text.splitlines():
        stripped = line.strip()
        timing = _TIME.match(stripped)
        if timing:
            flush()
            buffer = []
            current = timedelta(
                hours=int(timing.group("h")),
                minutes=int(timing.group("m")),
                seconds=int(timing.group("s")),
                milliseconds=int(timing.group("ms")),
            )
            continue
        if not stripped:
            flush()
            buffer = []
            current = None
            continue
        if stripped.isdigit() or stripped.upper().startswith("WEBVTT"):
            continue
        if current is not None:
            buffer.append(stripped)
    flush()

    if not passages:
        raise MediaError(
            f"{ref} holds no timed passages",
            remediation=(
                "Expected WebVTT or SRT with `00:00:00.000 --> ...` cues. An untimed "
                "transcript cannot be checked against the recording, so nothing in it "
                "could be cited as a source."
            ),
        )

    return MediaSource(
        id=hashlib.sha256(ref.encode("utf-8")).hexdigest()[:12],
        kind=kind,
        ref=ref,
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        duration=max(passage.at for passage in passages),
        passages=tuple(passages),
    )
