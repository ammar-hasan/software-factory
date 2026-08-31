"""What each persisted class may contain, and how long it is kept (PRD FR-27.1, FR-15.10).

A retention policy that does not say *what is in the thing being retained* is a number with
no argument behind it. So classification comes first: each class declares whether it can
hold repository content, personal data, or credentials, and the retention default follows
from that rather than from taste.

Two rules fall straight out of the classification and are enforced rather than advised:

* A class that can hold **credentials** has no retention default worth arguing about --
  it is redacted at capture (FR-17.3) and the classification records that the redaction is
  the control, not the retention.
* A class that can hold **personal data** must be erasable by subject (FR-27.4). A class
  that cannot be erased may not be classified as holding personal data, because the
  classification would be a promise the storage cannot keep.

The ledger is the deliberate exception, and it is stated rather than hidden: it holds
*references* and never bodies, which is exactly what makes erasure-by-reference (FR-15.10b)
possible in an append-only design.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import timedelta


class Sensitivity(enum.StrEnum):
    """What a persisted class can contain. Ordered least to most constrained."""

    METADATA = "metadata"
    REPOSITORY_CONTENT = "repository_content"
    PERSONAL_DATA = "personal_data"
    CREDENTIALS = "credentials"


class DataClass(enum.StrEnum):
    """Every class this factory persists. Adding storage means adding a member here."""

    LEDGER = "ledger"
    TRANSCRIPT = "transcript"
    AWARENESS_PACK = "awareness_pack"
    EVIDENCE = "evidence"
    RECORDING = "recording"
    MEMORY = "memory"
    SPEC = "spec"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class Classification:
    """One class's contents, retention, and how it is erased."""

    data_class: DataClass
    contains: frozenset[Sensitivity]
    retention: timedelta | None
    """``None`` means "kept until erased by reference", not "kept forever unexamined"."""

    erasable_by_subject: bool
    rationale: str
    redacted_at_capture: bool = False

    def __post_init__(self) -> None:
        if Sensitivity.PERSONAL_DATA in self.contains and not self.erasable_by_subject:
            raise ValueError(
                f"{self.data_class.value} is classified as holding personal data but cannot "
                "be erased by subject; the classification would be a promise the storage "
                "cannot keep"
            )
        if Sensitivity.CREDENTIALS in self.contains and not self.redacted_at_capture:
            raise ValueError(
                f"{self.data_class.value} is classified as able to hold credentials and is "
                "not redacted at capture; retention is not a control for credentials, "
                "redaction is (FR-17.3)"
            )

    def expires_at_age(self, age: timedelta) -> bool:
        return self.retention is not None and age >= self.retention

    def as_dict(self) -> dict[str, object]:
        return {
            "class": self.data_class.value,
            "contains": sorted(s.value for s in self.contains),
            "retention": None if self.retention is None else int(self.retention.total_seconds()),
            "erasableBySubject": self.erasable_by_subject,
            "redactedAtCapture": self.redacted_at_capture,
            "rationale": self.rationale,
        }


#: The factory's default classification. An operator overrides retention in `policy/`; the
#: *contents* are a property of the data and not theirs to declare away.
DEFAULT_CLASSIFICATION: dict[DataClass, Classification] = {
    DataClass.LEDGER: Classification(
        data_class=DataClass.LEDGER,
        contains=frozenset({Sensitivity.METADATA}),
        retention=None,
        erasable_by_subject=False,
        rationale=(
            "References and decisions, never bodies. That is what makes "
            "erasure-by-reference possible: the record that a thing existed and was erased "
            "survives, and the thing does not. A ledger holding bodies could not be both "
            "append-only and erasable."
        ),
    ),
    DataClass.TRANSCRIPT: Classification(
        data_class=DataClass.TRANSCRIPT,
        contains=frozenset(
            {
                Sensitivity.REPOSITORY_CONTENT,
                Sensitivity.PERSONAL_DATA,
                Sensitivity.CREDENTIALS,
            }
        ),
        retention=timedelta(days=30),
        erasable_by_subject=True,
        redacted_at_capture=True,
        rationale=(
            "Issue text and chat reach transcripts verbatim, so they carry whatever a "
            "person wrote -- their name, an incident detail, a pasted token. The shortest "
            "retention of any class because it is the class with the least reason to be "
            "read a month later."
        ),
    ),
    DataClass.AWARENESS_PACK: Classification(
        data_class=DataClass.AWARENESS_PACK,
        contains=frozenset({Sensitivity.REPOSITORY_CONTENT, Sensitivity.PERSONAL_DATA}),
        retention=timedelta(days=30),
        erasable_by_subject=True,
        rationale=(
            "A pack quotes the work item and the code, so it inherits both. Kept as long as "
            "the transcript it belongs to: a transcript without its pack cannot answer "
            "'what did the agent know when it decided that'."
        ),
    ),
    DataClass.EVIDENCE: Classification(
        data_class=DataClass.EVIDENCE,
        contains=frozenset({Sensitivity.REPOSITORY_CONTENT, Sensitivity.CREDENTIALS}),
        retention=timedelta(days=180),
        erasable_by_subject=True,
        redacted_at_capture=True,
        rationale=(
            "Test output and command transcripts, which is where a credential leaks if one "
            "does. Kept far longer than a transcript because a reviewer's approval rests on "
            "it and 'what did they see' has to remain answerable."
        ),
    ),
    DataClass.RECORDING: Classification(
        data_class=DataClass.RECORDING,
        contains=frozenset({Sensitivity.REPOSITORY_CONTENT, Sensitivity.PERSONAL_DATA}),
        retention=timedelta(days=90),
        erasable_by_subject=True,
        rationale=(
            "A screen recording captures whatever was on screen, which is not a thing the "
            "recorder chose. Large as well as sensitive, so the retention is the shortest "
            "that still lets a reviewer watch what they are approving."
        ),
    ),
    DataClass.MEMORY: Classification(
        data_class=DataClass.MEMORY,
        contains=frozenset({Sensitivity.REPOSITORY_CONTENT, Sensitivity.PERSONAL_DATA}),
        retention=None,
        erasable_by_subject=True,
        rationale=(
            "Memory has its own lifecycle -- lanes, decay, eviction (FR-6) -- so a blanket "
            "age limit would fight it. What it does not have is an exemption from erasure: "
            "`MemoryStore.erase` destroys the content and keeps the record."
        ),
    ),
    DataClass.SPEC: Classification(
        data_class=DataClass.SPEC,
        contains=frozenset({Sensitivity.REPOSITORY_CONTENT}),
        retention=None,
        erasable_by_subject=False,
        rationale=(
            "The spec is the repository's intended behaviour and lives in the repository. "
            "Retention is git's problem, not this factory's."
        ),
    ),
    DataClass.WORKSPACE: Classification(
        data_class=DataClass.WORKSPACE,
        contains=frozenset({Sensitivity.REPOSITORY_CONTENT, Sensitivity.CREDENTIALS}),
        retention=timedelta(hours=6),
        erasable_by_subject=False,
        redacted_at_capture=True,
        rationale=(
            "A checkout plus whatever a run wrote into it, including a secret an agent was "
            "granted. Hours rather than days: a workspace outlives its usefulness the "
            "moment its run ends, and `WorkspaceFactory.reclaim` enforces the same bound."
        ),
    ),
}


def classification_for(data_class: DataClass) -> Classification:
    return DEFAULT_CLASSIFICATION[data_class]


def classes_holding(sensitivity: Sensitivity) -> list[DataClass]:
    """Which classes can contain this. The question an erasure request starts from."""
    return sorted(
        (c.data_class for c in DEFAULT_CLASSIFICATION.values() if sensitivity in c.contains),
        key=lambda c: c.value,
    )
