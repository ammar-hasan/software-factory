"""Data classification, retention, legal hold, erasure, and ledger segmentation (FR-27)."""

from software_factory.governance.classification import (
    DEFAULT_CLASSIFICATION,
    Classification,
    DataClass,
    Sensitivity,
    classes_holding,
    classification_for,
)
from software_factory.governance.retention import (
    Artifact,
    ErasureReport,
    HoldReason,
    LegalHold,
    Retention,
    SweepReport,
)
from software_factory.governance.segments import (
    DEFAULT_SEGMENT_SIZE,
    Manifest,
    Segment,
    SegmentError,
    seal,
)

__all__ = [
    "DEFAULT_CLASSIFICATION",
    "DEFAULT_SEGMENT_SIZE",
    "Artifact",
    "Classification",
    "DataClass",
    "ErasureReport",
    "HoldReason",
    "LegalHold",
    "Manifest",
    "Retention",
    "Segment",
    "SegmentError",
    "Sensitivity",
    "SweepReport",
    "classes_holding",
    "classification_for",
    "seal",
]
