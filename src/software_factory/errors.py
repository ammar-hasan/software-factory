"""Typed error catalogue (PRD FR-21.5).

Every failure the factory can surface has a stable code, a human message, and a
remediation hint. Callers switch on ``code``; humans read ``message`` and do what
``remediation`` says. Nothing in this module formats for a terminal -- presentation
belongs to the CLI.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path


class ErrorCode(enum.StrEnum):
    """Stable error codes. Values are part of the API contract; never renumber."""

    AUTHENTICATION_REQUIRED = "authentication_required"
    NOT_AUTHORIZED = "not_authorized"
    INVALID_REQUEST = "invalid_request"
    RESOURCE_NOT_FOUND = "resource_not_found"
    CONFLICT = "conflict"
    BUDGET_EXCEEDED = "budget_exceeded"
    INSUFFICIENT_RESOURCES = "insufficient_resources"
    ENVIRONMENT_SETUP_FAILED = "environment_setup_failed"
    AGENT_PROCESS_FAILED = "agent_process_failed"
    INTEGRATION_NOT_CONFIGURED = "integration_not_configured"
    INTEGRATION_DISABLED = "integration_disabled"
    EXTERNAL_AUTHENTICATION_REQUIRED = "external_authentication_required"
    FEATURE_NOT_AVAILABLE = "feature_not_available"
    INFRASTRUCTURE_TIMEOUT = "infrastructure_timeout"
    OPERATION_NOT_SUPPORTED = "operation_not_supported"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    CONTENT_POLICY_VIOLATION = "content_policy_violation"
    DEFINITION_INVALID = "definition_invalid"
    SCHEMA_VERSION_UNSUPPORTED = "schema_version_unsupported"
    LEDGER_CORRUPT = "ledger_corrupt"
    CONTRACT_VIOLATION = "contract_violation"
    INTERNAL_ERROR = "internal_error"


class FactoryError(Exception):
    """Base class for every error the factory raises deliberately.

    ``remediation`` is required rather than optional: an error a user cannot act on
    is an error that will be reported to us instead of fixed by them (NFR-4.2).
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(self, message: str, remediation: str, *, detail: object = None) -> None:
        super().__init__(message)
        self.message = message
        self.remediation = remediation
        self.detail = detail

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "message": self.message,
            "remediation": self.remediation,
            "detail": self.detail,
        }


class DefinitionError(FactoryError):
    """The factory definition could not be loaded or is not internally consistent."""

    code = ErrorCode.DEFINITION_INVALID


class SchemaVersionError(FactoryError):
    """``schemaVersion`` names a version this build does not implement (FR-1.6)."""

    code = ErrorCode.SCHEMA_VERSION_UNSUPPORTED


class LedgerError(FactoryError):
    """The ledger is unreadable, or its hash chain does not verify (FR-15.1)."""

    code = ErrorCode.LEDGER_CORRUPT


class ContractViolationError(FactoryError):
    """A run attempted something outside its blast-radius contract (FR-12.5)."""

    code = ErrorCode.CONTRACT_VIOLATION


class BudgetExceededError(FactoryError):
    """A declared budget bound was reached (FR-3.11)."""

    code = ErrorCode.BUDGET_EXCEEDED


class Severity(enum.StrEnum):
    """Validation severity. ``ERROR`` blocks a load; ``WARNING`` never does."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One problem found in a definition tree.

    ``path``/``line`` locate it, ``key`` names the offending field, and ``accepted``
    lists what would have been valid -- the three things FR-2.4 requires so a reader
    never has to guess.
    """

    severity: Severity
    code: str
    message: str
    path: Path | None = None
    line: int | None = None
    key: str | None = None
    accepted: tuple[str, ...] = ()
    remediation: str = ""

    def location(self) -> str:
        if self.path is None:
            return "<definition>"
        if self.line is None:
            return str(self.path)
        return f"{self.path}:{self.line}"

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "path": str(self.path) if self.path else None,
            "line": self.line,
            "key": self.key,
            "accepted": list(self.accepted),
            "remediation": self.remediation,
        }


@dataclass(slots=True)
class ValidationReport:
    """The outcome of validating a definition tree.

    A report is truthy when it is clean, so ``if report:`` reads as "did this pass".
    """

    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)

    def extend(self, issues: list[ValidationIssue]) -> None:
        self.issues.extend(issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]

    @property
    def ok(self) -> bool:
        """True when nothing blocks a load. Warnings do not block (FR-2.3)."""
        return not self.errors

    def __bool__(self) -> bool:
        return self.ok

    def raise_if_failed(self) -> None:
        if self.ok:
            return
        first = self.errors[0]
        raise DefinitionError(
            f"{len(self.errors)} validation error(s); first at {first.location()}: {first.message}",
            remediation=first.remediation or "Fix the reported errors and re-run `sf validate`.",
            detail=[i.as_dict() for i in self.errors],
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "issues": [i.as_dict() for i in self.issues],
        }
