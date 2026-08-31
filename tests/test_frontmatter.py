"""Frontmatter parsing: the mapping from bytes on disk to a model plus a line number."""

from __future__ import annotations

from pathlib import Path

import pytest

from software_factory.definition import frontmatter as fm
from software_factory.errors import DefinitionError


def test_parses_frontmatter_and_body(tmp_path: Path) -> None:
    path = tmp_path / "agent.md"
    path.write_text("---\nrole: BUILDER\nconcurrency: 2\n---\n\nBody text.\n", encoding="utf-8")

    doc = fm.parse(path)

    assert doc.frontmatter == {"role": "BUILDER", "concurrency": 2}
    assert doc.body == "Body text."
    assert doc.body_start_line == 5


def test_body_only_document_is_valid(tmp_path: Path) -> None:
    """A file with no fence is a prompt with no configuration, not an error."""
    path = tmp_path / "agent.md"
    path.write_text("Just a prompt.\n", encoding="utf-8")

    doc = fm.parse(path)

    assert doc.frontmatter == {}
    assert doc.body == "Just a prompt.\n"


def test_unterminated_fence_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "agent.md"
    path.write_text("---\nrole: BUILDER\n", encoding="utf-8")

    with pytest.raises(DefinitionError, match="never closed"):
        fm.parse(path)


def test_non_mapping_frontmatter_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "agent.md"
    path.write_text("---\n- one\n- two\n---\nbody\n", encoding="utf-8")

    with pytest.raises(DefinitionError, match="must be a mapping"):
        fm.parse(path)


def test_line_of_locates_a_top_level_key(tmp_path: Path) -> None:
    """Validation messages cite a line, so the parser has to know where keys live."""
    path = tmp_path / "agent.md"
    path.write_text("---\nrole: BUILDER\nconcurrency: 2\n---\nbody\n", encoding="utf-8")

    doc = fm.parse(path)

    assert doc.line_of("role") == 2
    assert doc.line_of("concurrency") == 3
    assert doc.line_of("absent") is None


def test_invalid_yaml_reports_the_file(tmp_path: Path) -> None:
    path = tmp_path / "agent.md"
    path.write_text("---\nrole: [unclosed\n---\nbody\n", encoding="utf-8")

    with pytest.raises(DefinitionError, match="not valid YAML"):
        fm.parse(path)


def test_missing_file_names_the_path(tmp_path: Path) -> None:
    with pytest.raises(DefinitionError, match="file not found"):
        fm.parse(tmp_path / "nope.md")


def test_yaml_file_root_must_be_a_mapping(tmp_path: Path) -> None:
    path = tmp_path / "runner.yaml"
    path.write_text("- a\n- b\n", encoding="utf-8")

    with pytest.raises(DefinitionError, match="expected a mapping"):
        fm.parse_yaml_file(path)
