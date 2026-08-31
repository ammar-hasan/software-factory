# 5. Similarity is lexical and deterministic by default

**Status:** Accepted · **Date:** 2026-08-31

## Context

Contradiction detection, duplicate merging, consolidation, diversity capping, and skill
selection all need a notion of "these two things are about the same subject". Embeddings
are better at this than lexical overlap, by a wide margin.

They also make the memory fabric undeployable offline, non-deterministic across model
versions, and dependent on a service whose behaviour changes underneath us.

## Decision

The default is lexical and structural: content-word Jaccard, containment, and an explicit
negation/antonym screen. Embedding backends are optional adapters behind the same
interface, and their absence never disables the fabric.

Two consequences of this that the tests caught, and that are worth recording:

- **Negation words are not stopwords here.** A stopword list that eats "not" makes every
  contradiction look like a duplicate. The first implementation had this bug and the
  negation path was dead code.
- **Numeric tokens are kept at any length.** Dropping short tokens made "retry after 3s"
  and "retry after 30s" identical. Numbers are load-bearing in engineering claims.

## Consequences

We will miss semantic contradictions that share no vocabulary, and we say so in the code
rather than implying coverage we do not have. The negation screen is documented as
syntactic, and the harder cases are left to review.

The gain: the same inputs produce the same clustering on every machine, offline, forever —
which is what makes pack digests reproducible and benchmarks comparable.

## What would change our mind

Measured false-negative rates on contradiction detection high enough to matter, *with* an
embedding backend that can be run locally and pinned by version. The second condition is
not optional: a non-reproducible default breaks ADR-0002.
