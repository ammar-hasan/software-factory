# 6. The stage graph is configuration, not architecture

**Status:** Accepted · **Date:** 2026-08-31

## Context

The eight default stages — intake, triage, design, build, review, verify, handoff,
complete — read as obvious. A bias review pointed out why that is suspicious: they were
adopted by analogy with how teams already describe delivery, not derived from the problem
statement, and the first draft then protected them with "any transition not in the table
is a defect".

That is how an unexamined choice becomes an invariant.

## Decision

The stage graph is data. A factory may declare its own, subject to two invariants that
*are* derived rather than assumed:

1. every work item has exactly one current stage; and
2. at least one non-skippable verification stage precedes handoff.

The second is not a workflow preference. The conductor reads attacker-writable text, so
unbounded skip authority is an injection primitive: text that persuades the conductor to
skip review removes review. Review is therefore non-skippable by default, and skipping it
needs a human decision recorded against an identified principal.

## Consequences

`validate_graph()` checks a custom graph rather than assuming the default. Skipping is
allowed, recorded, and bounded. A transition justified only by untrusted input is refused
outright.

Whether eight stages is right at all remains an open question in the PRD (OQ-1), and we
expect it to change.

## What would change our mind

This one is written to be changed. Evidence that a different decomposition — fewer
stages, or a per-work-class graph — produces better outcomes on the benchmark would move
us, and the design is already shaped so that moving is cheap.
