"""Structured test results (PRD FR-10.6, docs/harness/evals.md).

A pass/fail boolean is not enough for the `regression-proven` gate: it has to know
*why* a test failed at the parent commit, because a test that fails because its import
is missing proves nothing about behaviour.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field


class Outcome(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class FailureClass(enum.StrEnum):
    """Why a test did not pass. The distinction that makes `regression-proven` real.

    Only ``ASSERTION`` is a statement about behaviour. ``EXISTENCE`` is carved out of it
    because an assertion that a name exists is not a behavioural claim: a test whose whole
    content is ``assert hasattr(mod, "new_fn")`` fails at the parent commit with a genuine
    ``AssertionError`` and proves only that the name did not exist -- the same bypass an
    import error gives, one keystroke away.
    """

    ASSERTION = "assertion"
    EXISTENCE = "existence"
    COLLECTION = "collection"
    IMPORT = "import"
    FIXTURE = "fixture"
    TIMEOUT = "timeout"
    CRASH = "crash"
    UNKNOWN = "unknown"


#: Structural failures, matched on the exception *type* at the start of a line rather than
#: as bare substrings. Substring matching misread real assertion output constantly:
#: `assert config.timeout == 30` contains "timeout", `assert proc.killed is False`
#: contains "killed", and a fixture named in a traceback contains "fixture" -- each of
#: which rejected a genuine regression test.
_EXCEPTION_LINE = re.compile(
    r"^\s*(?:E\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_.]*Error|[A-Za-z_][A-Za-z0-9_.]*Exception)\b",
    re.MULTILINE,
)

_STRUCTURAL_EXCEPTIONS: dict[str, FailureClass] = {
    "ImportError": FailureClass.IMPORT,
    "ModuleNotFoundError": FailureClass.IMPORT,
    "SyntaxError": FailureClass.COLLECTION,
    "IndentationError": FailureClass.COLLECTION,
    "CollectionError": FailureClass.COLLECTION,
    "TimeoutError": FailureClass.TIMEOUT,
    "Failed": FailureClass.UNKNOWN,
}

#: Phrasings a test runner uses for its own structural problems, as whole phrases.
_STRUCTURAL_PHRASES: tuple[tuple[FailureClass, tuple[str, ...]], ...] = (
    (FailureClass.IMPORT, ("cannot import name", "no module named")),
    (
        FailureClass.COLLECTION,
        ("error during collection", "errors during collection", "error collecting"),
    ),
    (FailureClass.FIXTURE, ("error at setup of", "fixture ", "not found in", "errors at setup")),
    (FailureClass.TIMEOUT, ("timed out after", "test exceeded", "timeout >")),
    (FailureClass.CRASH, ("segmentation fault", "core dumped", "worker crashed")),
)

#: An assertion whose subject is only a name's presence. Not a behavioural claim.
_EXISTENCE_ASSERTION = re.compile(
    r"assert\s+(?:"
    r"hasattr\s*\("
    r"|not\s+hasattr\s*\("
    r"|['\"]?\w+['\"]?\s+in\s+dir\s*\("
    r"|callable\s*\("
    r"|\w+(?:\.\w+)*\s+is\s+not\s+None\s*$"
    r")",
    re.IGNORECASE,
)


def classify_failure(message: str) -> FailureClass:
    """Classify a failure from its message.

    Order: structural exception type, then structural phrase, then existence assertion,
    then behavioural assertion. Structural classes win because a test that never ran its
    body proves nothing about behaviour -- but they are matched precisely, so a genuine
    assertion that merely *mentions* a timeout or a fixture is not misread as one.
    """
    if not message.strip():
        return FailureClass.UNKNOWN

    for match in _EXCEPTION_LINE.finditer(message):
        name = match.group("name").rsplit(".", 1)[-1]
        structural = _STRUCTURAL_EXCEPTIONS.get(name)
        if structural is not None and structural is not FailureClass.UNKNOWN:
            return structural
        if name == "AssertionError":
            break

    lowered = message.lower()
    for failure_class, phrases in _STRUCTURAL_PHRASES:
        if any(phrase in lowered for phrase in phrases):
            return failure_class

    if _EXISTENCE_ASSERTION.search(message):
        return FailureClass.EXISTENCE

    if "assertionerror" in lowered or re.search(r"^\s*(?:E\s+)?assert\s", message, re.MULTILINE):
        return FailureClass.ASSERTION

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
