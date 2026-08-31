"""Intake: normalised events, the adapter contract, and the pipeline (PRD FR-18)."""

from software_factory.intake.adapters import (
    Adapter,
    Deduplicator,
    Health,
    HealthReport,
    Registry,
    Reply,
)
from software_factory.intake.events import (
    FactoryEvent,
    Origin,
    Provider,
    event_identity,
    matches,
    overlapping_keys,
)
from software_factory.intake.pipeline import (
    Automation,
    Ignored,
    Outcome,
    Pipeline,
    Refused,
    Started,
)

__all__ = [
    "Adapter",
    "Automation",
    "Deduplicator",
    "FactoryEvent",
    "Health",
    "HealthReport",
    "Ignored",
    "Origin",
    "Outcome",
    "Pipeline",
    "Provider",
    "Refused",
    "Registry",
    "Reply",
    "Started",
    "event_identity",
    "matches",
    "overlapping_keys",
]
