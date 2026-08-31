# 4. Memory corroboration is computed over sources, not runs

**Status:** Accepted · **Date:** 2026-08-31

## Context

A memory system needs a rule for when an unproven claim becomes trusted knowledge. The
natural rule is corroboration: if two independent runs reached the same conclusion,
believe it.

An adversarial review of the design found the hole. "Two runs" is not "two observations".
Two runs that both read the same planted issue comment agree with each other perfectly,
and the rule promotes attacker-supplied text into canon — where it is then rendered as a
*cited convention* in every subsequent context pack, with a provenance trail that looks
impeccable.

## Decision

Corroboration intersects **provenance sets**. Two observations corroborate only when
their sources are disjoint. Additionally:

- Same run, or same model, never corroborates: that is one observation sampled twice.
- Where a claim can be checked deterministically, agreement does not promote it at all.
  One passing test beats two agents agreeing.
- Untrusted content may never enter canon, whatever corroborates it. Trust is carried as
  an attribute and propagates monotone-downward, so a derived claim is only as trusted as
  its weakest input.
- A canon memory tracing to one unverified source is confidence-capped, so it cannot
  dominate a pack even if it gets in.

## Consequences

Promotion is slower and more claims stay in the candidate lane. That is the intended
trade: the cost of a wrong memory is unbounded and compounding, while the cost of a
missing memory is one retrieval.

Invalidation cascades transitively — a memory whose entire provenance collapses is
archived automatically, and one that keeps an independent source is penalised rather than
silently kept at full confidence.

## What would change our mind

Measurement showing the candidate lane grows without bound because promotion is too
strict, *and* that the un-promoted claims were mostly correct. Then the answer is more
verification paths, not weaker corroboration.
