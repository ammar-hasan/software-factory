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

#: Unicode-aware. The previous `[a-z0-9_]+` saw only ASCII, so a claim written in a
#: non-Latin script tokenized to whatever ASCII it happened to embed -- two *different*
#: Japanese claims that both mentioned "BOM" scored Jaccard 1.0 and the second was rejected
#: as a duplicate, while two *identical* claims with no ASCII at all tokenized to the empty
#: set, scored 0.0, and slipped past duplicate and contradiction detection entirely. The
#: same regex backs skill-description collision checks and selection scoring, so a
#: non-English skill library lost both.
_WORD = re.compile(r"\w+")

#: Scripts that do not put spaces between words. One `\w+` run in these is a clause, not a
#: word, so indexing it whole would make every pair of distinct claims score 0.0. Character
#: bigrams give them something to overlap on.
_UNSEGMENTED = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u0e00-\u0e7f\uac00-\ud7af]"
)

#: Words that flip a claim's meaning. Never filtered out, at any length: this module's
#: job includes telling "X must happen" from "X must not happen".
_NEGATORS = frozenset({"not", "never", "no", "without", "avoid", "cannot", "neither"})

#: How many content words two claims must actually share before either is treated as being
#: about the other's subject. `containment` divides by the *smaller* token set, so a
#: two-word canon memory scored 1.0 against any longer claim reusing both words: "tests
#: pass" was read as contradicting "the deploy script does not pass tests to the runner",
#: admission rejected the newcomer, and `detect_contradictions` quarantined *both* --
#: dropping a real canon memory out of retrieval because an unrelated claim reused two
#: common words.
_MIN_SHARED_TOKENS = 3

#: How far a negator may sit from a shared content word and still be read as negating it.
_NEGATOR_WINDOW = 4


def _bigrams(word: str) -> list[str]:
    if len(word) < 2:
        return [word]
    return [word[index : index + 2] for index in range(len(word) - 1)]


def _scan(text: str) -> list[str]:
    """Content words in order, with duplicates kept. ``tokens`` is this, deduplicated.

    Order matters to the negation test, which asks whether a negator sits near the subject
    the two claims share rather than merely somewhere in the sentence.
    """
    found: list[str] = []
    for word in _WORD.findall(text.casefold()):
        if _UNSEGMENTED.search(word):
            found.extend(_bigrams(word))
            continue
        # Numbers are kept at any length: "retry after 3s" and "retry after 30s" are
        # different claims, and dropping short numeric tokens makes them identical.
        # Negators are kept at any length too: the length filter was silently eating
        # "no", which made every contradiction phrased with it invisible to admission
        # control and to the policy pass.
        if word in _STOPWORDS:
            continue
        if len(word) > 2 or word.isdigit() or word in _NEGATORS:
            found.append(word)
    return found


def tokens(text: str) -> set[str]:
    """Content words, case-folded, with stopwords and one-character noise removed."""
    return set(_scan(text))


def comparable(text: str) -> bool:
    """Whether this text yields anything a similarity score can be computed from.

    ``jaccard`` and ``containment`` both return 0.0 for an empty token set, and every
    caller reads 0.0 as "not similar". That conflates "these differ" with "this could not
    be analysed", so anything that cares about the difference asks here first.
    """
    return bool(tokens(text))


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
    left_tokens, right_tokens = tokens(left), tokens(right)
    shared = left_tokens & right_tokens
    if len(shared) < _MIN_SHARED_TOKENS or containment(left, right) < topic_threshold:
        return False

    # The negator has to attach to the subject the two claims share, not merely appear
    # somewhere in one of them. Without this, any sentence containing "not" contradicted
    # any sentence that reused a couple of its words.
    left_negated = _negates_shared(left, shared)
    right_negated = _negates_shared(right, shared)
    if left_negated != right_negated:
        return True

    return any(
        (positive in left_tokens and negative in right_tokens)
        or (negative in left_tokens and positive in right_tokens)
        for positive, negative in _ANTONYMS
    )


def _negates_shared(text: str, shared: set[str]) -> bool:
    """True when a negator sits within ``_NEGATOR_WINDOW`` words of a shared content word."""
    sequence = _scan(text)
    negator_positions = [i for i, word in enumerate(sequence) if word in _NEGATORS]
    if not negator_positions:
        return False
    shared_positions = [i for i, word in enumerate(sequence) if word in shared]
    return any(
        abs(negator - subject) <= _NEGATOR_WINDOW
        for negator in negator_positions
        for subject in shared_positions
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
