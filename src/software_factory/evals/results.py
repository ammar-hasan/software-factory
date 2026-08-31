"""Structured test results (PRD FR-10.6, docs/harness/evals.md).

A pass/fail boolean is not enough for the `regression-proven` gate: it has to know
*why* a test failed at the parent commit, because a test that fails because its import
is missing proves nothing about behaviour.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Outcome(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class FailureClass(enum.StrEnum):
    """Why a test did not pass. The distinction that makes `regression-proven` real.

    Only ``ASSERTION`` is a statement about behaviour. Everything else means the test
    body never ran, or never ran meaningfully.
    """

    ASSERTION = "assertion"
    COLLECTION = "collection"
    IMPORT = "import"
    FIXTURE = "fixture"
    TIMEOUT = "timeout"
    CRASH = "crash"
    UNKNOWN = "unknown"


#: Substrings that identify a failure as structural rather than behavioural. Ordered so
#: the most specific match wins.
_CLASSIFIERS: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
    (FailureClass.IMPORT, ("importerror", "modulenotfounderror", "cannot import name")),
    (FailureClass.COLLECTION, ("collection error", "errors during collection", "syntaxerror")),
    (FailureClass.FIXTURE, ("fixture", "error at setup", "setup failed")),
    (FailureClass.TIMEOUT, ("timeout", "timed out")),
    (FailureClass.CRASH, ("segmentation fault", "core dumped", "killed")),
    (FailureClass.ASSERTION, ("assertionerror", "assert ", "expected", "failed:")),
)


def classify_failure(message: str) -> FailureClass:
    """Classify a failure from its message.

    Structural classes are checked before ``ASSERTION`` on purpose: an import error
    whose traceback happens to contain the word "assert" is still an import error, and
    treating it as behavioural is exactly the bypass this exists to close.
    """
    lowered = message.lower()
    for failure_class, markers in _CLASSIFIERS:
        if any(marker in lowered for marker in markers):
            return failure_class
    return FailureClass.UNKNOWN


@dataclass(frozen=True, slots=True)
class TestResult:
    """One test's outcome."""

    __test__ = False  # a result, not a pytest test class

    test_id: str
    outcome: Outcome
    duration_s: float = 0.0
    message: str = ""
    failure_class: FailureClass | None = None

    def classified(self) -> FailureClass | None:
        if self.outcome is Outcome.PASSED or self.outcome is Outcome.SKIPPED:
            return None
        return self.failure_class or classify_failure(self.message)

    @property
    def is_behavioural_failure(self) -> bool:
        """True only when the test body ran and an assertion about behaviour failed."""
        return self.classified() is FailureClass.ASSERTION


@dataclass(slots=True)
class TestRun:
    """The structured result of running a suite, at one commit."""

    __test__ = False  # a result, not a pytest test class

    command: str
    commit: str
    exit_code: int
    results: list[TestResult] = field(default_factory=list)
    duration_s: float = 0.0
    truncated: bool = False

    def by_id(self, test_id: str) -> TestResult | None:
        for result in self.results:
            if result.test_id == test_id:
                return result
        return None

    @property
    def passed(self) -> bool:
        """A suite passes when it ran and nothing failed or errored.

        Exit code alone is not enough: a suite that collected nothing exits zero.
        """
        if self.exit_code != 0 or not self.results:
            return False
        return all(r.outcome in (Outcome.PASSED, Outcome.SKIPPED) for r in self.results)

    @property
    def failures(self) -> list[TestResult]:
        return [r for r in self.results if r.outcome in (Outcome.FAILED, Outcome.ERROR)]

    def as_dict(self) -> dict[str, object]:
        return {
            "command": self.command,
            "commit": self.commit,
            "exitCode": self.exit_code,
            "passed": self.passed,
            "total": len(self.results),
            "failed": len(self.failures),
            "truncated": self.truncated,
            "results": [
                {
                    "id": r.test_id,
                    "outcome": r.outcome.value,
                    "durationSeconds": r.duration_s,
                    "failureClass": (c.value if (c := r.classified()) else None),
                }
                for r in self.results
            ],
        }
