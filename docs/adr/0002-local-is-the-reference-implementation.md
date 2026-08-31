# 2. Local is the reference implementation

**Status:** Accepted · **Date:** 2026-08-31

## Context

Most systems in this space are cloud products with a local mode bolted on, and the local
mode is always the one that lags. The alternative is to make the single-machine case the
one everything else is defined against.

## Decision

The local case is the reference. Coordination and execution are the same process tree on
one machine, separated by process boundary and sandbox rather than by network. Cloud and
self-hosted execution are *deployment choices* that swap the executor and nothing else:
switching topology must change `runners/` and executor settings, and no agent, skill,
spec, scorer, or automation file.

## Consequences

Every capability must have a local implementation or be explicitly marked as a hosted
extension. This is why:

- the memory store is an append-only JSONL file with a rebuildable index, not a service;
- similarity is lexical and dependency-free by default (see ADR-0005);
- the ledger is one JSONL file readable with `tail`;
- the default tier ladder includes a local tier, and `sf lint` warns when it does not.

It also constrains the roadmap: a feature that only works with a hosted control plane is
not a feature of this system.

## Enforcement

CI runs the entire suite with `connect`, `connect_ex`, `create_connection` and
`getaddrinfo` denied (`scripts/run_offline_tests.py`). All tests pass with no network. A
change that needs a network to be tested has taken a dependency it should not have, and
the job fails.

## What would change our mind

If the parity conformance suite (FR-20.5) becomes impossible to keep green — that is, if
holding local and cloud to identical behaviour costs more than it returns. We would then
say so explicitly and demote local to a supported-but-different mode, rather than letting
it rot silently.
