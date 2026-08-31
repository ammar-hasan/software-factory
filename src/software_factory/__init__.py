"""Software Factory: fleets of specialist agents that carry engineering work from
intake to a reviewable change, on a harness built for living specs, self-regulating
memory, evals, and evolving skills.

The public surface is deliberately small; everything else is reachable through the
subpackages (:mod:`software_factory.definition`, :mod:`software_factory.ledger`, ...).
"""

from software_factory.errors import (
    FactoryError,
    LedgerError,
    SchemaVersionError,
    ValidationIssue,
    ValidationReport,
)

__version__ = "0.1.0"

SCHEMA_VERSIONS = ("v1alpha1",)
"""Definition schema versions this build accepts (FR-1.6)."""

__all__ = [
    "SCHEMA_VERSIONS",
    "FactoryError",
    "LedgerError",
    "SchemaVersionError",
    "ValidationIssue",
    "ValidationReport",
    "__version__",
]
