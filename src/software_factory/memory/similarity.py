"""Deterministic, dependency-free similarity (PRD FR-6.14, memory.md M-36).

Contradiction detection, duplicate merging, and diversity capping all need a notion of
"these two claims are about the same thing". An embedding model would be better at it
and would also make the fabric undeployable offline and non-deterministic across runs.
So the default is lexical and structural, embedding backends are optional adapters, and
their absence never disables the fabric.
"""

from __future__ import annotations

import re

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "then",
        "than",
        "that",
        "this",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "to",
        "of",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "it",
        "its",
        "into",
        "about",
        "over",
        "under",
        "do",
        "does",
        "did",
        "we",
        "you",
        "they",
        "he",
        "she",
        "our",
        "your",
        "their",
        "my",
        "me",
        "us",
        "them",
        "there",
        "here",
        "when",
        "while",
        "so",
        "such",
    }
)
"""Content-word filter.

Negation words (``not``, ``no``, ``never``, ``without``) are deliberately *absent*: this
module's whole job includes telling "X must happen" from "X must not happen", and a
stopword list that eats the negation makes every contradiction look like a duplicate.
"""

_WORD = re.compile(r"[a-z0-9_]+")


def tokens(text: str) -> set[str]:
    """Content words, lowercased, with stopwords and one-character noise removed."""
    return {
        word
        for word in _WORD.findall(text.lower())
        # Numbers are kept at any length: "retry after 3s" and "retry after 30s" are
        # different claims, and dropping short numeric tokens makes them identical.
        if word not in _STOPWORDS and (len(word) > 2 or word.isdigit())
    }


def jaccard(left: str, right: str) -> float:
    """Set overlap of content words, in [0, 1]."""
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(left: str, right: str) -> float:
    """How much of the smaller claim is contained in the larger.

    Better than Jaccard for consolidation, where a general memory legitimately subsumes
    a specific one and their sizes differ a lot.
    """
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


_NEGATORS = frozenset({"not", "never", "no", "without", "avoid", "cannot", "neither"})
_ANTONYMS: tuple[tuple[str, str], ...] = (
    ("always", "never"),
    ("enable", "disable"),
    ("enabled", "disabled"),
    ("allow", "forbid"),
    ("allowed", "forbidden"),
    ("include", "exclude"),
    ("required", "optional"),
    ("before", "after"),
    ("increase", "decrease"),
    ("add", "remove"),
    ("true", "false"),
    ("succeed", "fail"),
    ("succeeds", "fails"),
    ("supported", "unsupported"),
)


def negates(left: str, right: str, *, topic_threshold: float = 0.45) -> bool:
    """True when two claims are about the same thing and assert opposite things.

    Two conditions must both hold: the claims share enough topic words to be about the
    same subject, and exactly one of them carries a negation (or they carry opposite
    members of an antonym pair). Requiring *exactly one* negator is what stops "X must
    not happen" and "X must not happen" from reading as a contradiction.
    """
    if containment(left, right) < topic_threshold:
        return False

    left_tokens, right_tokens = tokens(left), tokens(right)
    left_negated = bool(left_tokens & _NEGATORS)
    right_negated = bool(right_tokens & _NEGATORS)
    if left_negated != right_negated:
        return True

    return any(
        (positive in left_tokens and negative in right_tokens)
        or (negative in left_tokens and positive in right_tokens)
        for positive, negative in _ANTONYMS
    )


def relevance(claim: str, query: str, surfaces: set[str] | None = None) -> float:
    """Rank a memory against a task, in [0, 1].

    Surface mentions are weighted heavily: a memory that names the file being changed is
    far more likely to matter than one that merely shares vocabulary with the request.
    """
    score = jaccard(claim, query)
    if surfaces:
        lowered = claim.lower()
        hits = sum(1 for surface in surfaces if surface.lower() in lowered)
        if hits:
            score = min(1.0, score + 0.25 * min(hits, 2))
    return score
