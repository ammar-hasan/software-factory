"""Matching a declared surface pattern against a concrete path.

One rule, in one place, because two copies of it had already drifted: skills used
``pattern.rstrip("/*")``, which strips trailing ``/`` and ``*`` characters and nothing
else, so ``src/**`` matched and ``*.py`` silently did not. An author whose skill declared
``surfaces: ["*.py"]`` saw it excluded with the reason "no surface overlap", which reads
like a correct decision. ``SpecUnit.intersects`` carried the prefix half of the same rule
with none of the glob half.
"""

from __future__ import annotations

import fnmatch

__all__ = ["surface_match", "surfaces_overlap"]


def surface_match(pattern: str, path: str) -> bool:
    """Whether one declared surface pattern covers one concrete path.

    The accepted syntax, which nothing stated before:

    * an exact path -- ``src/importers/csv.py``
    * a directory -- ``src/importers``, ``src/importers/`` or ``src/importers/**``,
      covering everything beneath it
    * a glob -- ``*.py``, ``src/*/models.py``

    ``*`` crosses ``/`` here, so ``src/*`` also covers ``src/a/b.py``. That is looser than
    a git pathspec, deliberately: a surface declaration decides whether a skill is worth
    *offering*, and the cost of being too narrow is an author whose skill is never shown
    with one line of feedback to work from.
    """
    if pattern == path:
        return True

    prefix = pattern.rstrip("/")
    while prefix.endswith("/**"):
        prefix = prefix[: -len("/**")]
    if prefix and not _is_glob(prefix) and (path == prefix or path.startswith(prefix + "/")):
        return True

    return fnmatch.fnmatch(path, pattern)


def surfaces_overlap(declared: tuple[str, ...], actual: set[str]) -> bool:
    """Whether any declared pattern covers any concrete path."""
    return any(surface_match(pattern, path) for pattern in declared for path in actual)


def _is_glob(text: str) -> bool:
    return any(character in text for character in "*?[")
