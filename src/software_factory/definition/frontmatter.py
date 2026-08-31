"""Markdown-with-YAML-frontmatter parsing, with line numbers preserved.

Agents, automations, scorers, and skills are all "frontmatter plus a prompt body".
Validation errors must cite a line in the *original* file (FR-2.4), so this module
tracks the offset of the frontmatter block rather than handing YAML a bare string
and losing the mapping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from software_factory.errors import DefinitionError

_FENCE = re.compile(r"^---[ \t]*$")


@dataclass(frozen=True, slots=True)
class Document:
    """A parsed frontmatter document.

    ``frontmatter_start_line`` is the 1-based line of the opening fence, so a YAML
    error reported at relative line *n* maps to ``frontmatter_start_line + n``.
    """

    path: Path
    frontmatter: dict[str, Any]
    body: str
    frontmatter_start_line: int
    body_start_line: int
    source_lines: tuple[str, ...] = ()
    """The document's own lines, as parsed.

    `line_of` used to re-read `self.path` on every call, and `_record_pydantic` calls it
    once per validation error -- so a file with twenty errors was read twenty times. Worse,
    it ignored the `text=` argument `parse` accepts, so `parse(path, text=...)` for
    in-memory content reported lines from a different file, or from none.
    """

    def line_of(self, key: str) -> int | None:
        """Best-effort source line for a top-level frontmatter key.

        Used to point validation errors at the offending field. Returns ``None`` for
        keys that are nested or absent -- callers fall back to the file's first line.
        """
        if key not in self.frontmatter:
            return None
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*:")
        for offset, line in enumerate(self.source_lines):
            if offset < self.frontmatter_start_line:
                continue
            if offset >= self.body_start_line - 1:
                break
            if pattern.match(line):
                return offset + 1
        return None


def parse(path: Path, text: str | None = None) -> Document:
    """Parse ``path`` into frontmatter and body.

    A document with no frontmatter fence is valid and yields an empty mapping; that
    keeps skills and prompts usable before anyone has configured them. A document
    with an *unterminated* fence is an error, because the alternative is silently
    treating the whole file as YAML.
    """
    raw = text if text is not None else _read(path)
    lines = raw.splitlines()

    if not lines or not _FENCE.match(lines[0]):
        return Document(
            path=path,
            frontmatter={},
            body=raw,
            frontmatter_start_line=0,
            body_start_line=1,
            source_lines=tuple(lines),
        )

    closing: int | None = None
    for index in range(1, len(lines)):
        if _FENCE.match(lines[index]):
            closing = index
            break

    if closing is None:
        raise DefinitionError(
            f"{path}: frontmatter block opened on line 1 but never closed",
            remediation="Add a closing `---` line after the frontmatter.",
        )

    block = "\n".join(lines[1:closing])
    try:
        loaded = yaml.safe_load(block) if block.strip() else {}
    except yaml.YAMLError as exc:
        raise DefinitionError(
            f"{path}: frontmatter is not valid YAML: {exc}",
            remediation="Fix the YAML syntax in the frontmatter block.",
        ) from exc

    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise DefinitionError(
            f"{path}: frontmatter must be a mapping, got {type(loaded).__name__}",
            remediation="Frontmatter must be `key: value` pairs, not a list or scalar.",
        )

    return Document(
        path=path,
        frontmatter=loaded,
        body="\n".join(lines[closing + 1 :]).strip("\n"),
        frontmatter_start_line=1,
        body_start_line=closing + 2,
        source_lines=tuple(lines),
    )


def parse_yaml_file(path: Path, text: str | None = None) -> dict[str, Any]:
    """Parse a plain YAML document that must be a mapping."""
    raw = text if text is not None else _read(path)
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise DefinitionError(
            f"{path}: not valid YAML: {exc}",
            remediation="Fix the YAML syntax.",
        ) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise DefinitionError(
            f"{path}: expected a mapping at the document root, got {type(loaded).__name__}",
            remediation="The document root must be `key: value` pairs.",
        )
    return loaded


def yaml_line_of(path: Path, key: str) -> int | None:
    """Best-effort source line of a top-level key in a plain YAML file."""
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:  # pragma: no cover
        return None
    for offset, line in enumerate(lines):
        if pattern.match(line):
            return offset + 1
    return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DefinitionError(
            f"{path}: file not found",
            remediation="Create the file, or remove the reference to it.",
        ) from exc
    except UnicodeDecodeError as exc:
        raise DefinitionError(
            f"{path}: not valid UTF-8",
            remediation="Definition files must be UTF-8 encoded.",
        ) from exc
