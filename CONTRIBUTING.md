# Contributing

## The short version

```bash
pip install -e ".[dev]"
ruff format . && ruff check . && mypy && pytest
```

All four must be clean before you open a change. CI runs exactly these.

## What this project is opinionated about

Read [`docs/PRD.md`](docs/PRD.md) before proposing anything structural. It states what
the system is for, what it deliberately does not do, and — in Appendix C — which review
findings we accepted, which we deferred, and which we declined and why.

A few standing positions, so you are not surprised in review:

- **Files are the source of truth.** If it changes the factory's behaviour, it is a file
  in a repository. UIs and APIs are editors over files, never a second store.
- **Instructions never grant access.** What an agent can *reach* comes from configuration.
  A change that lets a prompt widen access will be rejected regardless of how convenient
  it is.
- **Evidence over assertion.** A claim in a summary must resolve to an artifact. This
  applies to the code too: a PR that says "this is faster" needs a measurement.
- **Everything must be able to shrink.** Memory, skills, and specs all need pruning paths
  as first-class as their growth paths. A feature that only adds will be asked for its
  removal path.
- **Degrade, don't fail.** Missing integration, unavailable model, no network: do less,
  explicitly, and say so. Never produce unverified work silently.

## Code

**Match the surrounding code.** Comment density, naming, and idiom included.

**Comment the *why*, not the *what*.** The code says what it does. A comment earns its
place by explaining a decision a reader would otherwise question — a threshold, an
ordering that matters, a rule that exists because of a specific failure. Several
functions here carry a sentence about the failure mode they prevent; that is the standard.

**Typed and strict.** `mypy` runs in strict mode over `src/`. No `Any` escapes without a
comment saying why.

**Errors carry remediation.** Every `FactoryError` requires a remediation string, because
an error a user cannot act on is an error that gets reported to us instead of fixed by
them.

## Tests

**Name the behaviour, not the function.** `test_a_disputed_memory_never_reaches_an_agent`
tells a reader what breaks if it fails. `test_retrieve_3` does not.

**Test the refusals.** Most of this codebase's value is in what it declines to do: what
did not get admitted, promoted, escalated, or advanced. A subsystem with only happy-path
tests is untested.

**Write the failing test first for a defect fix.** Watch it fail for the right reason —
an assertion about behaviour, not an import error. This is the `regression-proven` gate
applied to ourselves, and it is not optional here either.

**No network in tests.** The whole system is testable without a model or a network; keep
it that way.

## Commits

Explain the decision, not the diff. A reader six months from now needs to know why the
thresholds are what they are, not that a file changed.

## Changes to the factory's own definition

Changes under `policy/`, `scorers/`, or anything affecting gates, grants, or the held-out
eval set are held to a stricter standard than application code: a second reviewer, and no
self-approval. They alter what every future run is allowed to do.

## Governance

Pre-1.0, maintainers decide by consensus; disagreements are resolved in the issue thread
with the reasoning written down. Substantial design changes go through an ADR in
[`docs/adr/`](docs/adr).
