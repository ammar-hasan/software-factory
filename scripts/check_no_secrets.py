#!/usr/bin/env python3
"""Fail if credential-shaped strings appear outside the code that looks for them.

The factory refuses to write secret-shaped content into memory, diffs, and evidence. This
check applies the same rule to the repository itself, so a real credential pasted into a
test fixture or a docstring is caught before it is published.

The detectors and their tests legitimately *contain* these shapes, so they are excluded
by path rather than by pattern -- excluding by pattern would weaken the detectors.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files whose job is to recognise or exercise these shapes.
ALLOWED = {
    Path("scripts/check_no_secrets.py"),
    Path("src/software_factory/memory/admission.py"),
    Path("src/software_factory/definition/validate.py"),
    Path("tests/test_memory.py"),
    Path("tests/test_validate.py"),
    Path("tests/test_evals.py"),
}

# Tooling caches echo test names and fixture content back at us, so they would report
# every deliberate fixture as a finding.
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".research",
    "htmlcov",
    "dist",
    "build",
    ".eggs",
}

PATTERNS: dict[str, re.Pattern[str]] = {
    "openai-style key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "github fine-grained pat": re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def main() -> int:
    findings: list[str] = []

    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in relative.parts):
            continue
        if relative in ALLOWED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                line = text[: match.start()].count("\n") + 1
                findings.append(f"{relative}:{line}: {label}")

    if findings:
        print("credential-shaped content found:", file=sys.stderr)
        for finding in findings:
            print(f"  {finding}", file=sys.stderr)
        print(
            "\nIf this is a real credential, rotate it now -- it is in git history.\n"
            "If it is a fixture, move it into one of the files that legitimately holds "
            "these shapes, or shorten it below the detector threshold.",
            file=sys.stderr,
        )
        return 1

    print("no credential-shaped content outside the detectors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
