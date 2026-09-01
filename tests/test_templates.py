"""Spec templates derived from the repository they will be used in.

Induction already read a repository and proposed units. What it could not do was change
*shape*: every unit came out with the same sections and the same idea of what could be
anchored. One shape for every repository means the shape fits none of them, and the cost
lands on whoever has to work around the form before they can say anything true.

The tests here are mostly about restraint: what the deriver refuses to conclude, and what it
declines to read.
"""

from __future__ import annotations

from pathlib import Path

from software_factory.spec.templates import Support, derive


def repo(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def python_files(count: int, *, body: str = "def f():\n    return 1\n") -> dict[str, str]:
    return {f"src/module_{index}.py": body for index in range(count)}


# --------------------------------------------------------------------------------------
# What the repository can actually support
# --------------------------------------------------------------------------------------


def test_a_repository_with_no_tests_marks_the_test_section_unenforceable(tmp_path: Path) -> None:
    """The value that makes this module worth having.

    `required` makes every unit fail a check nobody can satisfy; omitting the section hides
    a real gap. Naming it is the only option that leaves an operator something to do.
    """
    template = derive(repo(tmp_path, python_files(5)))

    verifies = next(s for s in template.sections if s.name == "Verifies")
    assert verifies.support is Support.UNENFORCEABLE
    assert "no test files" in verifies.evidence


def test_an_unenforceable_section_is_shown_not_dropped(tmp_path: Path) -> None:
    """An agent not told the repository has no tests will keep proposing test anchors and
    keep being refused, and neither it nor the reader will learn why."""
    rendered = derive(repo(tmp_path, python_files(5))).render()

    assert "Verifies" in rendered
    assert "not available here" in rendered


def test_a_repository_with_tests_requires_the_anchor(tmp_path: Path) -> None:
    template = derive(
        repo(tmp_path, {**python_files(5), "tests/test_module.py": "def test_x():\n    pass\n"})
    )

    verifies = next(s for s in template.sections if s.name == "Verifies")
    assert verifies.support is Support.REQUIRED


def test_every_section_names_the_evidence_that_produced_it(tmp_path: Path) -> None:
    """A section with no evidence is a fixed template with extra steps, and the whole claim
    of this module is that the shape was derived."""
    template = derive(repo(tmp_path, python_files(5)))

    assert all(section.evidence.strip() for section in template.sections)


# --------------------------------------------------------------------------------------
# Ambiguity is reported, not resolved
# --------------------------------------------------------------------------------------


def test_a_repository_with_no_dominant_language_gets_the_generic_template(
    tmp_path: Path,
) -> None:
    """A repository that is 40% one thing and 30% another is not either of them.

    A template confidently tailored to the wrong half is worse than a generic one — the
    generic one at least does not mislead.
    """
    mixed = {
        **{f"a{i}.py": "x = 1\n" for i in range(3)},
        **{f"b{i}.ts": "const x = 1;\n" for i in range(3)},
        **{f"c{i}.go": "package main\n" for i in range(3)},
        **{f"d{i}.rs": "fn main() {}\n" for i in range(3)},
        **{f"e{i}.rb": "x = 1\n" for i in range(3)},
        **{f"f{i}.java": "class A {}\n" for i in range(3)},
    }
    template = derive(repo(tmp_path, mixed))

    assert template.generic is True
    assert "no language accounts for enough" in template.notes[0]


def test_the_generic_template_says_it_is_generic(tmp_path: Path) -> None:
    """Guessing produces a template that reads as authoritative and is wrong. This reads as
    generic and is merely unhelpful, which is recoverable."""
    rendered = derive(repo(tmp_path, {"README.md": "hello"})).render()

    assert "Generic template" in rendered


def test_one_stray_file_does_not_make_a_repository_polyglot(tmp_path: Path) -> None:
    """A repository is not a Rust repository because of one build script."""
    template = derive(repo(tmp_path, {**python_files(20), "build.rs": "fn main() {}\n"}))

    assert template.languages == ("python",)


def test_an_empty_directory_is_generic_and_says_why(tmp_path: Path) -> None:
    template = derive(tmp_path)

    assert template.generic is True
    assert "no readable source files" in template.notes[0]


# --------------------------------------------------------------------------------------
# What it declines to read
# --------------------------------------------------------------------------------------


def test_dot_directories_are_not_part_of_the_repository(tmp_path: Path) -> None:
    """Found the hard way, on this repository.

    Anything under a dot-directory is generally ignored by git — caches, virtualenvs,
    scratch notes, downloaded reference material. A template derived from those describes a
    working directory rather than a repository, and the vocabulary it mines is whatever was
    last read rather than the code.
    """
    root = repo(
        tmp_path,
        {
            **python_files(5, body="def compute_ledger_digest():\n    return 1\n"),
            ".scratch/notes.py": "zzzdistinctive = 'zzzdistinctive'\n" * 200,
        },
    )

    template = derive(root)

    assert not any("zzzdistinctive" in word for word in template.vocabulary)


def test_vendored_code_is_not_this_repositorys_conventions(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            **python_files(5, body="def ledger_digest():\n    return 1\n"),
            "node_modules/pkg/index.js": "const vendored = 1;\n" * 200,
        },
    )

    assert "vendored" not in derive(root).vocabulary


def test_assets_do_not_feed_the_vocabulary(tmp_path: Path) -> None:
    """Mining this repository's own checked-in SVG screenshots taught it `clip` and
    `terminal`, from `clipPath` elements — the vocabulary of the image format, offered to
    agents as the language of the codebase."""
    root = repo(
        tmp_path,
        {
            **python_files(5, body="def reconcile_ledger():\n    return 1\n"),
            "docs/shot.svg": '<svg><clipPath id="clip"/></svg>\n' * 200,
        },
    )

    assert "clip" not in derive(root).vocabulary


def test_the_vocabulary_is_this_repositorys_words_not_the_languages(tmp_path: Path) -> None:
    """Frequency alone produces `self`, `data`, `result` — the language of programming
    rather than the language of this repository."""
    body = "def handler(self, data, result):\n    reconciliation = data\n    return result\n"
    template = derive(repo(tmp_path, python_files(6, body=body)))

    assert "reconciliation" in template.vocabulary
    assert "self" not in template.vocabulary
    assert "data" not in template.vocabulary


# --------------------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------------------


def test_a_web_service_gets_criteria_about_requests(tmp_path: Path) -> None:
    """A status-code example in a data pipeline teaches the wrong thing more effectively
    than no example at all."""
    root = repo(
        tmp_path,
        {**python_files(5), "pyproject.toml": 'dependencies = ["fastapi>=0.1"]\n'},
    )

    assert any("401" in example for example in derive(root).criterion_examples)


def test_a_data_pipeline_gets_criteria_about_rows(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {**python_files(5), "pyproject.toml": 'dependencies = ["polars", "dbt-core"]\n'},
    )

    assert any("row" in example for example in derive(root).criterion_examples)


def test_shape_comes_from_declared_dependencies_not_file_names(tmp_path: Path) -> None:
    """A repository with `api.py` may be anything. A repository that depends on a web
    framework is serving something."""
    root = repo(
        tmp_path,
        {**python_files(5), "src/api.py": "x = 1\n", "pyproject.toml": 'deps = ["typer"]\n'},
    )

    template = derive(root)

    assert "cli" in template.notes[0]


def test_a_repository_with_no_framework_is_a_library(tmp_path: Path) -> None:
    root = repo(tmp_path, {**python_files(5), "pyproject.toml": "[project]\nname='x'\n"})

    assert "library" in derive(root).notes[0]


def test_a_web_service_with_a_cli_entry_point_is_a_web_service(tmp_path: Path) -> None:
    """The marker order is load-bearing: first match wins, and what a repository serves
    matters more than how it is started."""
    root = repo(
        tmp_path,
        {
            **python_files(5),
            "src/cli.py": "x = 1\n",
            "pyproject.toml": 'deps = ["fastapi", "typer"]\n',
        },
    )

    assert "http" in derive(root).notes[0]


# --------------------------------------------------------------------------------------
# Against this repository
# --------------------------------------------------------------------------------------


def test_it_recognises_the_repository_it_lives_in() -> None:
    """The end-to-end check that the deriver does something real: run it here, where the
    answer is known — a Python command-line tool with tests and type annotations."""
    template = derive(Path(__file__).resolve().parent.parent)

    assert template.generic is False
    assert template.languages == ("python",)
    assert "cli" in template.notes[0]
    assert next(s for s in template.sections if s.name == "Verifies").support is Support.REQUIRED
    assert next(s for s in template.sections if s.name == "Types").support is Support.REQUIRED
    assert "factory" in template.vocabulary
