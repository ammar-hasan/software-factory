"""Terminal and screen recordings as evidence, and what happens when they fail (PRD FR-22).

FR-22.3's shape is the whole design: screen and browser verification is **optional but
first-class**, and where unavailable the gate "degrades to requiring an explicit statement
that visual evidence is absent (PR-9) -- never to silence."

That sentence rules out the two obvious implementations. It rules out making recording
mandatory, because a factory on a headless runner would then be unable to finish a
user-facing change. And it rules out skipping the requirement when recording is
unavailable, because then a change that *should* have visual evidence and has none looks
exactly like one that never needed any.

So the third option: the absence is itself an artifact. A `NotRecorded` carries why, and the
evidence bundle holds it in place of the recording. A reviewer reading the bundle sees
"no visual evidence: no display server on this runner" rather than a gap they have to
notice.

FR-22.7 is the same principle one level down: a truncated or failed recording is reported as
truncated, with retry guidance -- never presented as successful evidence. A half-recording
that renders as a recording is worse than none, because a reviewer watches it, sees the
change work up to the truncation, and approves.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from software_factory.digests import digest_parts
from software_factory.evals.evidence import EvidenceClass, EvidenceItem
from software_factory.memory.records import utc_now


class RecordingKind(enum.StrEnum):
    TERMINAL = "terminal"
    """A command session. Cheap, always available, and the default for anything that is not
    user-facing."""

    BROWSER = "browser"
    SCREEN = "screen"


class Unavailable(enum.StrEnum):
    """Why a recording could not be made. Each maps to different advice.

    Enumerated rather than free text because a reviewer seeing "recording unavailable" for
    the fortieth time needs to know whether it is the same cause -- and because
    `sf doctor` can only tell an operator how to fix a cause it can name.
    """

    NO_DISPLAY = "no_display"
    NO_BROWSER = "no_browser"
    NOT_ENABLED = "not_enabled"
    UNSUPPORTED_EXECUTOR = "unsupported_executor"
    CAPTURE_FAILED = "capture_failed"


REMEDIATION: dict[Unavailable, str] = {
    Unavailable.NO_DISPLAY: (
        "This runner has no display server. Use the container executor with a virtual "
        "display, or accept that visual evidence is unavailable here and record that."
    ),
    Unavailable.NO_BROWSER: (
        "No browser is installed on this runner. Install one in the runner image if visual "
        "verification matters for this repository's work."
    ),
    Unavailable.NOT_ENABLED: (
        "Screen recording is not enabled for this factory. Enable it in `policy/` if "
        "user-facing changes here should carry visual evidence."
    ),
    Unavailable.UNSUPPORTED_EXECUTOR: (
        "This executor cannot capture a screen session. The container executor can, with a "
        "virtual display configured."
    ),
    Unavailable.CAPTURE_FAILED: (
        "The capture started and did not complete. Re-run the verification step; if it "
        "fails repeatedly the recorder itself is the problem, not the change."
    ),
}


@dataclass(frozen=True, slots=True)
class Recording:
    """A completed recording, ready to attach as evidence."""

    id: str
    kind: RecordingKind
    location: str
    duration: timedelta
    digest: str
    captured_at: datetime = field(default_factory=utc_now)
    truncated: bool = False
    truncated_reason: str = ""

    def __post_init__(self) -> None:
        if self.truncated and not self.truncated_reason.strip():
            raise ValueError(
                "a truncated recording must say why; FR-22.7 requires retry guidance, and "
                "a truncation with no stated cause reads as a complete recording that "
                "happens to end early"
            )

    def as_evidence(self) -> EvidenceItem:
        """Attach as an evidence item, carrying the truncation flag.

        `EvidenceItem.truncated` is a field rather than a property precisely so this cannot
        be lost in translation: a half-recording rendered as a recording is worse than none,
        because a reviewer watches it, sees the change work up to the cut, and approves.
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
            truncated=self.truncated,
        )

    def describe(self) -> str:
        seconds = int(self.duration.total_seconds())
        if self.truncated:
            return (
                f"{self.kind.value} recording, {seconds}s, TRUNCATED: {self.truncated_reason}. "
                f"{REMEDIATION[Unavailable.CAPTURE_FAILED]}"
            )
        return f"{self.kind.value} recording, {seconds}s"


@dataclass(frozen=True, slots=True)
class NotRecorded:
    """The absence of a recording, as an artifact.

    This is the type that makes FR-22.3's "never to silence" real. Without it, "we did not
    record" and "there was nothing to record" produce the same evidence bundle -- an empty
    one -- and a reviewer cannot tell a change that skipped visual verification from one
    that did not need it.
    """

    kind: RecordingKind
    reason: Unavailable
    detail: str = ""
    at: datetime = field(default_factory=utc_now)

    @property
    def remediation(self) -> str:
        return REMEDIATION[self.reason]

    def as_evidence(self) -> EvidenceItem:
        """An evidence item whose content is the statement of absence.

        Digested over the reason so two absences for the same cause are the same artifact,
        and an absence that changes cause is a different one.
        """
        return EvidenceItem(
            id=f"absent-{self.kind.value}-{digest_parts(self.reason.value, self.detail, length=8)}",
            evidence_class=EvidenceClass.ARTIFACT,
            digest=digest_parts(self.reason.value, self.detail),
            location=f"absent://{self.kind.value}/{self.reason.value}",
            captured_at=self.at,
        )

    def describe(self) -> str:
        detail = f" ({self.detail})" if self.detail else ""
        return (
            f"No {self.kind.value} recording{detail}: {self.reason.value.replace('_', ' ')}. "
            f"{self.remediation}"
        )


Capture = Recording | NotRecorded


@dataclass(frozen=True, slots=True)
class RecordingPolicy:
    """When a work class is expected to carry visual evidence.

    Expected, not required. The difference is FR-22.3: an expectation that cannot be met is
    recorded as unmet, and a requirement that cannot be met blocks a change for a reason
    that has nothing to do with the change.
    """

    visual_for_work_classes: frozenset[str] = frozenset({"feature", "defect"})
    """User-facing work. A chore that renames a variable needs no screenshot."""

    terminal_always: bool = True
    """Terminal recording is cheap and available everywhere, so it is the floor."""

    enabled: bool = True

    def expects_visual(self, work_class: str, *, user_facing: bool | None) -> bool:
        """Whether visual evidence is expected for this work.

        `None` means nobody has said, and it is treated as "expect it" for the classes that
        usually need it. That is the conservative direction: the cost of expecting evidence
        for a change that turned out to be invisible is a statement in the bundle saying
        none was captured; the cost of the other default is a user-facing change reviewed
        with no picture of what it looks like.
        """
        if not self.enabled:
            return False
        if user_facing is False:
            return False
        return work_class in self.visual_for_work_classes


def visual_evidence_statement(captures: list[Capture]) -> str:
    """The explicit statement FR-22.3 requires when visual evidence is absent.

    Returned as text for the evidence bundle and the change description, because "explicit"
    means a reviewer reads it -- a flag in a JSON payload nobody renders is not explicit.
    """
    visual = [
        capture
        for capture in captures
        if _kind(capture) in (RecordingKind.BROWSER, RecordingKind.SCREEN)
    ]
    if not visual:
        return (
            "No visual evidence was attempted for this change. If it alters what a person "
            "sees, that is a gap a reviewer should push back on."
        )

    absent = [c for c in visual if isinstance(c, NotRecorded)]
    truncated = [c for c in visual if isinstance(c, Recording) and c.truncated]
    if absent:
        return "Visual evidence is absent. " + " ".join(c.describe() for c in absent)
    if truncated:
        return "Visual evidence is incomplete. " + " ".join(c.describe() for c in truncated)
    return f"{len(visual)} visual recording(s) attached."


def _kind(capture: Capture) -> RecordingKind:
    return capture.kind
