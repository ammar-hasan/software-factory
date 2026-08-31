"""The skill registry: selection quality, and a lifecycle that can shrink.

See ``docs/harness/skills.md`` for the design and ``docs/PRD.md`` §7.7 for requirements.
"""

from software_factory.skills.registry import (
    DEFAULT_OFFER_SIZE,
    Offer,
    Operation,
    Proposal,
    Refusal,
    SkillMetrics,
    SkillRecord,
    SkillRegistry,
)

__all__ = [
    "DEFAULT_OFFER_SIZE",
    "Offer",
    "Operation",
    "Proposal",
    "Refusal",
    "SkillMetrics",
    "SkillRecord",
    "SkillRegistry",
]
