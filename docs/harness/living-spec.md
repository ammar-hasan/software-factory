# Living Spec and Delta Specification

| Field | Value |
| --- | --- |
| Component | Living Spec + Delta engine |
| Implements | PRD FR-5 |
| Purpose | Keep intent, code, and tests in checkable agreement over time |

> A spec that only humans read is documentation. A spec that gates changes is infrastructure. This one
> is the second kind: it is machine-anchored, mechanically drift-detected, and it can block a change.

---

## 1. Model

```
SpecUnit {
  id,                              # stable forever; never reused
  title, status,                   # draft | active | deprecated | retired
  intent,                          # what must be true, behaviourally
  acceptance: [Criterion],
  constraints: [Constraint],
  implements: [CodeAnchor],
  verifies:   [TestAnchor],
  supersedes: [SpecUnitId],
  provenance: [Source],
  confidence: 0..1,
  owners: [string],
  reviewed_at,
}

Criterion  { id, statement, verified_by: [TestAnchor], observed_failing: bool }
Constraint { id, kind: perf|security|compat|data|ux, statement, checked_by: [Check] }
CodeAnchor { path, symbol?, range?, digest, resolved_at }
TestAnchor { path, test_id, digest, resolved_at }
```

**S-1 — Ids are immutable.** Renaming a file, moving a unit, or rewording intent never changes an id.
Retired ids remain resolvable forever, so old changes and ledger entries keep their meaning.
**S-2 — One unit, one behaviour.** A unit covering two independent behaviours cannot be selectively
retired and fails lint.
**S-3 — Criteria are individually checkable.** A criterion no test could distinguish from its negation
fails lint. "The system should be fast" is not a criterion; "p99 under 200ms at 100 rps" is.

---

## 2. Anchors and drift

**S-4 — Anchors are content-addressed.** Every anchor stores a digest of the range it points at. Drift
is `digest(current) != anchor.digest` — a mechanical comparison, never a model judgement.

**S-5 — Anchor resolution** proceeds: exact path+symbol → path+fuzzy symbol → path only → unresolved.
The resolution level is recorded; degraded resolution lowers the unit's agreement confidence.

**S-6 — Drift is a signal, not a verdict.** Code changing under an anchor means either the intent
changed (needs a Delta) or the implementation was refactored (needs a re-anchor). The engine
distinguishes them by asking whether the anchored tests still pass:

| Digest changed | Tests pass | State | Action |
| --- | --- | --- | --- |
| no | yes | `agreed` | none |
| no | no | `contradicted` | **block Build**; either the code or the intent is wrong |
| yes | yes | `drifted` | re-anchor proposal; behaviour appears preserved |
| yes | no | `contradicted` | block; behaviour changed without a Delta |
| anchor unresolved | — | `orphaned` | retirement or re-anchor proposal |
| no test anchors | — | `unverified` | warn; queue coverage work |

---

## 3. Agreement computation

```
agreement(unit) -> {agreed, unverified, drifted, contradicted, orphaned}

for anchor in unit.implements:
    if not resolve(anchor):            return orphaned
    drifted |= digest_changed(anchor)
if unit.verifies is empty:             return unverified
results = run_or_read_cached(unit.verifies)
if any failed:                         return contradicted
if two active units disagree on the same anchor about the same behaviour:
                                       return contradicted (both)
return drifted if drifted else agreed
```

**S-7 — Agreement is computed, cached by (unit digest, repo head), and invalidated on either change.**
It must be cheap enough to run on every pack assembly.
**S-8 — Cross-unit contradiction** (two active units mandating incompatible behaviour on the same
anchor) marks **both** contradicted. As with memory (memory.md M-9), the newer is not assumed right.
**S-9 — Contradicted units are always in the pack** (awareness.md §3.2), never budget-dropped.

---

## 4. Deltas

**S-10 — No agent writes `specs/` directly.** Every change is a `SpecDelta` reviewed on its own terms.

```
SpecDelta {
  id, work_item, author_run,
  changes: [ Add{unit} | Modify{id, field_patches} | Supersede{old_id, new_unit}
           | Retire{id, reason} | Reanchor{id, anchors} ],
  rationale,                       # why intent changes, in behavioural terms
  provenance: [Source],
  impact: ImpactReport,
  review_state: proposed | approved | rejected | applied,
}

ImpactReport {
  units_affected, criteria_added, criteria_removed,
  tests_required: [TestAnchor],    # tests that must exist before this can be applied
  agreement_before, agreement_after_projected,
  behaviour_change: none | additive | breaking,
}
```

**S-11 — Design-stage output is a Delta plus a draft change, not code.** The Delta is reviewable before
implementation exists, which is the whole point: intent is cheaper to correct than code.
**S-12 — `behaviour_change: breaking` requires an explicit human approval**, separate from ordinary
change review, and names what breaks.
**S-13 — A Delta that removes a criterion must say what replaces it or why it is no longer required.**
Silent criterion removal is the easiest way to make a failing system look healthy, so it is gated.
**S-14 — Deltas are applied atomically.** A partially-applied Delta is impossible; validation runs over
the projected tree before anything is written.

---

## 5. Gates

| Gate | Stage | Condition |
| --- | --- | --- |
| `spec-agreement` | Design, Review | No `contradicted` unit on the change surface |
| `delta-present` | Review | Behaviour changed on an `active` unit ⟹ an approved Delta covers it |
| `coverage-of-criteria` | Review | Every criterion in the affected units maps to a test exercising it |
| `criterion-observed-failing` | Review | Every *new* criterion's test has been observed failing without the change |
| `no-orphan-growth` | Review | The change does not increase the orphaned-unit count |

**S-15 — `criterion-observed-failing` is the anti-vacuous-test gate.** A test that passes with and
without the change proves nothing. This gate is the spec-side twin of `regression-proven`
(evals.md §3) and together they are the strongest defence against plausible-but-wrong changes.

**S-16 — `delta-present` decides "behaviour changed" mechanically**: a diff touching an anchored range
whose covering tests changed outcome, or whose anchored digest changed while public signatures changed.
Ambiguous cases escalate to the Critic with the evidence, and the Critic must answer explicitly — it
may not skip.

---

## 6. Induction — the on-ramp

Most repositories have no spec. Without an on-ramp this whole subsystem is unusable, so induction is
a first-class feature, not a nicety.

```
sf spec induct [--paths ...] [--from-tests] [--from-history]
```

**S-17 — Induction proposes, never writes.** Output is a Delta of `draft` units at low confidence.
**S-18 — Sources, in descending trustworthiness:**

| Source | Yields | Confidence |
| --- | --- | --- |
| Existing tests | Criteria (a test *is* an executable criterion) with anchors already resolved | high |
| Public API signatures and types | Constraints and interface intent | medium |
| Documentation and comments | Intent statements | low |
| Version history | Decisions, from revert and fix patterns | low |

**S-19 — Test-derived units start `agreed`** by construction: the test is the verification. This makes
induction immediately valuable rather than a pile of unverified prose.
**S-20 — Induction is incremental and resumable**, scoped to paths, so a large repository is onboarded
module by module as work touches it. A repository need never stop to write a spec.
**S-21 — Promotion `draft → active` is a human decision.** An inducted unit does not gate anything until
a person accepts it, so induction can never block a team that has not opted in.

---

## 7. Queries

```
sf spec get <id> | --anchor <path[:symbol]>
sf spec agreement [--surface <paths>]        # the agreement-state distribution
sf spec drift                                # drifted + orphaned, with proposed re-anchors
sf spec cover                                # criteria without tests; tests without criteria;
                                             # criteria never observed failing
sf spec diff <rev-a> <rev-b>                 # behavioural difference between two revisions
sf spec why <id>                             # provenance, deltas, and every state change
```

**S-22 — `sf spec diff` answers the question no repository can answer today:** what is this system now
supposed to do that it was not supposed to do before?

---

## 8. Failure modes designed against

| Failure | Defence |
| --- | --- |
| Spec becomes write-only documentation | It gates changes (§5); a spec nobody reads still blocks a bad change |
| Spec becomes bureaucracy | Deltas only where behaviour changes; skip-with-reason recorded; induction on-ramp |
| Spec drifts silently | Content-addressed anchors, mechanical drift detection (S-4) |
| Vacuous criteria | `criterion-observed-failing` (S-15) and the lint in S-3 |
| Criterion laundering (delete the failing criterion) | S-13 gates removal |
| Two units quietly disagree | Cross-unit contradiction marks both (S-8) |
| Refactors generate false alarms | `drifted` with passing tests is a re-anchor proposal, not a block |
| Ids churn and history breaks | Ids immutable and retired ids resolvable forever (S-1) |

---

## 9. Test matrix

| Test | Asserts |
| --- | --- |
| `drift-detected-mechanically` | Editing an anchored range flips agreement without any model call |
| `contradiction-blocks-build` | A failing anchored test blocks the Build stage |
| `behaviour-change-requires-delta` | A behavioural change with no Delta fails Review 100% of the time |
| `vacuous-criterion-refused` | A criterion whose test passes at the parent commit fails S-15 |
| `criterion-removal-gated` | A Delta removing a criterion without a replacement is refused (S-13) |
| `cross-unit-contradiction` | Both units are marked, not just the newer (S-8) |
| `refactor-is-reanchor` | Digest change with passing tests yields a re-anchor proposal, not a block |
| `induction-yields-agreed-units` | Test-derived units come out `agreed` (S-19) |
| `induction-does-not-gate` | `draft` units gate nothing (S-21) |
| `id-stability` | Move, rename, and reword preserve the id and all references (S-1) |
| `delta-atomicity` | A Delta failing validation writes nothing (S-14) |
