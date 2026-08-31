# Memory Fabric Specification

| Field | Value |
| --- | --- |
| Component | Memory Fabric |
| Implements | PRD FR-6 |
| Property | Self-organising · self-regulating · self-policing · self-evolving · self-filtering |

> Append-only memory is a liability that grows. This subsystem is designed around one asymmetry:
> **the cost of a wrong memory is unbounded and compounding; the cost of a missing memory is one
> retrieval.** Every default below follows from that asymmetry.

---

## 1. Lanes

```
  write ──► WORKING ──nominate──► CANDIDATE ──promote──► CANON
              │                      │                     │
              └── discarded          └── reject ──► ARCHIVE ◄── demote / expire / supersede
```

| Lane | Lifetime | Default visibility | Exit |
| --- | --- | --- | --- |
| `WORKING` | The run | Owning run only | Discarded at run end unless nominated |
| `CANDIDATE` | `ttl`, default 30d | Opt-in only, always labelled `unverified` | Promotion, rejection, or expiry |
| `CANON` | Until superseded or invalidated | All agents in scope | Demotion to `ARCHIVE` |
| `ARCHIVE` | Retention policy | Explicit query only | Never re-enters; re-derivation required |

**M-1** — Default retrieval lane is `CANON` alone. An agent sees Candidate content only if its
configuration opts in, and then every such item is labelled `unverified` inline.
**M-2** — Archive is not a wastebasket; it is the audit record. Nothing is hard-deleted before its
retention expiry, and deletion is itself ledger-recorded.

---

## 2. Record

```
Memory {
  id, lane, kind, scope,
  content,                         # the claim, one claim per memory
  provenance: [Source],            # required, non-empty
  parents: [MemoryId],             # memories this was derived from
  evidence: [EvidenceRef],
  confidence: 0..1,
  created_at, updated_at,
  ttl | expires_on,
  decay: DecayFn,
  promotion: PromotionRecord | null,
  supersedes: [MemoryId],
  superseded_by: MemoryId | null,
  contradicts: [MemoryId],
  access: { last_used_at, use_count, helped_count },
  digest,
}

Source { kind: run|tool|file|human|test|ci|external, ref, locator, excerpt_digest }
```

**M-3 — One claim per memory.** A memory containing two claims cannot be selectively invalidated and
is rejected at admission.
**M-4 — Provenance is mandatory and non-empty.** No provenance, no admission. There is no path that
creates an unsourced memory.
**M-5 — Content-addressed sources.** Each source stores a digest of the excerpt it came from, so
staleness is detected mechanically rather than by re-asking a model.

---

## 3. Kinds

| Kind | Claim shape | Default TTL | Promotion needs | Decay |
| --- | --- | --- | --- | --- |
| `fact` | Something true about the system | 90d | Corroboration or verification | Re-resolve source digest |
| `convention` | How this team does things | 180d | Human confirmation, or 3 corroborating runs | Usage-based |
| `decision` | A choice and its rationale | none | Human confirmation | Superseded only |
| `failure` | An approach that did not work, and why | 180d | Verification (the failure reproduced) | Re-resolve anchor |
| `preference` | A human stated a preference | none | The human's own statement | Superseded only |
| `procedure` | A repeatable method (skill candidate) | 60d | Eval evidence (see skills.md) | Eval-based |
| `anchor` | A stable pointer into code | 30d | Anchor resolves | Re-resolve every pass |
| `metric` | An observed measurement | 30d | Reproduced measurement | Hard expiry |

**M-6** — `failure` memories are the highest-value kind and the most neglected elsewhere. "We tried X
and it broke because Y" prevents more waste than "X is the answer" creates. They are given long TTLs
and are weighted heavily in the `hazards` pack section.

---

## 4. Admission control (self-regulating)

A write into `CANDIDATE` is accepted only if **all** hold:

| # | Check | Rejection reason |
| --- | --- | --- |
| 1 | All required fields present for the kind | `incomplete` |
| 2 | Exactly one claim (structural check + length ceiling) | `compound_claim` |
| 3 | Provenance non-empty and every source resolvable | `unsourced` |
| 4 | Not a near-duplicate of an existing memory in scope | `duplicate` → merge instead |
| 5 | Does not contradict a `CANON` memory in scope | `contradiction` → §5.1 |
| 6 | Scope is within the writing agent's granted scopes | `out_of_scope` |
| 7 | The scope is under budget, or eviction can make room | `budget` |
| 8 | Content contains no secret-shaped material | `secret_suspected` |

**M-7** — Rejection is recorded with its reason. Rejection reasons are a monitored signal: a spike in
`unsourced` means an agent's extraction prompt is wrong, not that memory is broken.
**M-8** — Admission runs asynchronously after a run ends and never blocks it.

---

## 5. Policing (self-policing)

A **policy pass** runs on a schedule and after every N admissions. It is deterministic where possible
and idempotent always.

### 5.1 Contradiction detection

```
for each new or updated memory m in scope S:
    candidates = index.similar(m, scope=S, threshold=τ_contra)
    for c in candidates:
        if entails_negation(m, c):        # deterministic for typed claims; judged otherwise
            quarantine(m); quarantine(c)
            open ContradictionCase(m, c)
```

**M-9 — Both sides are quarantined**, not the newer one. The system does not assume recency implies
correctness; a stale-but-right memory is common when code was reverted.
**M-10 — A ContradictionCase is resolved by evidence or by a human.** Resolution promotes one, archives
the other, and records the resolving evidence. Unresolved cases past a deadline archive **both** and
raise a work item — because operating on a disputed claim is worse than operating without it.
**M-11 — Contradiction is transitive-aware.** Resolving a case re-evaluates every memory whose parents
include either side.

### 5.2 Staleness

```
for each memory m with anchors or source digests:
    if not resolve(m.source.locator) or digest_changed(m.source):
        if kind == anchor:        demote(m, reason=anchor_orphaned)
        else:                     m.confidence *= stale_penalty; flag(m, needs_revalidation)
```

**M-12** — Staleness is checked by digest, not by asking a model whether something is still true.

### 5.3 Duplication and consolidation (self-organising)

```
clusters = index.cluster(scope, threshold=τ_dup)
for cluster with |cluster| >= 2:
    merged = merge(cluster)          # union of provenance, max confidence, earliest created_at,
                                     # union of evidence, union of parents
    supersede(cluster, merged)
```

**M-13** — Merging preserves the **union** of provenance. A merged memory is better-sourced than any of
its inputs, never worse.
**M-14 — Consolidation** raises specificity to generality: N specific memories sharing a pattern
produce one general memory that *links to*, rather than replaces, the specifics. The specifics move to
`ARCHIVE` and remain resolvable.

### 5.4 Poisoning containment

The critical mechanism (PRD R-2).

```
invalidate(m, reason):
    m.lane = ARCHIVE; record(reason)
    for d in descendants(m):                       # transitive over `parents`
        if every source in d.provenance is invalidated:
            invalidate(d, reason=provenance_collapsed)
        else:
            d.confidence *= collapse_penalty
            if d.lane == CANON and d.confidence < canon_floor:
                demote(d, reason=weakened_provenance)
```

**M-15 — Transitive invalidation is mandatory and complete.** A memory whose entire provenance chain is
invalidated is archived automatically. A partially-weakened memory is penalised and may fall out of
Canon, but is not silently kept at full confidence.
**M-16 — Single-source Canon is bounded.** A `CANON` memory whose provenance traces to exactly one
unverified source may not exceed `single_source_confidence_cap` (default 0.6), which keeps it below
the weight at which it can dominate a pack.
**M-17 — Blast radius of a bad memory is measurable.** `sf memory blast <id>` reports every memory,
run, and pack that would be affected by invalidating it — run *before* accepting a high-fan-out claim.

---

## 6. Retrieval and filtering (self-filtering)

A fixed, ordered pipeline. Each stage is individually testable.

```
1. SCOPE      hard filter to scopes the agent is granted
2. LANE       CANON only, unless opted in
3. DISPUTE    drop quarantined and open-contradiction memories entirely
4. FRESHNESS  drop expired; apply decay to confidence
5. RELEVANCE  rank by relevance to the surface and task
6. DIVERSITY  cap per source, per parent-cluster, and per kind
7. BUDGET     truncate at the section budget, whole items only
8. CITE       emit with id, lane, confidence, and provenance summary
```

**M-18 — Stage 3 is a drop, not a demotion.** Disputed memories never reach an agent, in any lane.
**M-19 — Diversity cap (stage 6):** no single source may supply more than `diversity_cap` (default 30%)
of returned items, and no single parent-cluster more than 50%. This is what stops one confident
extraction from colouring every subsequent run.
**M-20 — Retrieval is budgeted in wall-clock** and returns partial results on expiry, marked partial.
**M-21 — Every returned memory is cited** in the pack with its id, so `sf memory why <id>` closes the
loop from output back to source.
**M-22 — Retrieval never writes.** Usage statistics are recorded out-of-band, asynchronously.

---

## 7. Promotion (self-evolving)

`CANDIDATE → CANON` requires at least one satisfied criterion, recorded on the memory:

| Criterion | Definition |
| --- | --- |
| `corroboration` | A different run, using a **different model or a different tool path**, independently produced an equivalent claim |
| `verification` | A deterministic check directly exercising the claim passed (a test, a resolved anchor, a reproduced failure) |
| `human` | A human explicitly confirmed it |

**M-23 — Corroboration requires independence.** Two runs of the same agent on the same model
corroborate nothing; that is one observation sampled twice.
**M-24 — Promotion is recorded** with the criterion, the corroborating run or evidence, and the actor.
`CANON` membership is always explicable.
**M-25 — Verification beats corroboration.** Where a claim *can* be checked deterministically,
corroboration alone does not promote it. Two agents agreeing is weaker than one test passing.
**M-26 — Demotion is symmetric and cheap.** Any single piece of contradicting evidence demotes; the
bar to enter Canon is high and the bar to leave is low, per the asymmetry in this document's preamble.

---

## 8. Decay and eviction (bounded growth)

**M-27 — Decay functions** by kind: hard expiry (`metric`), digest re-resolution (`fact`, `anchor`),
usage-based (`convention`: unused for the window ⟹ confidence decays), eval-based (`procedure`), and
supersession-only (`decision`, `preference`).

**M-28 — Value density** for eviction ranking:

```
value(m) = confidence
         × recency_weight(last_used_at)
         × log(1 + helped_count)
         × kind_weight(kind)
         ÷ size_tokens(m)
```

**M-29 — Budget enforcement.** Each scope declares `max_items` and `max_bytes`. On breach the pass
consolidates first, then archives lowest-value-density memories until under budget, and records
exactly what it dropped. Memory must be able to shrink (PR-8).

**M-30 — `helped_count`** increments only when a memory was cited in a pack for a run that *passed its
gates*. Being retrieved is not being useful.

---

## 9. Scopes

`run ⊂ work-item ⊂ repository ⊂ factory ⊂ team`, plus `personal` as a sibling of `team`.

**M-31** — A memory is visible only within its scope and to agents granted that scope.
**M-32** — Cross-scope promotion (e.g. repository → factory) is an explicit, audited operation
requiring the same evidence as Canon promotion, plus a second scope-widening justification.
**M-33** — `personal` memories never widen automatically, under any circumstance.

---

## 10. Interfaces

```
memory.write(candidate) -> Accepted{id} | Rejected{reason}
memory.query(scope, surface, kind?, lane?, budget) -> [CitedMemory]
memory.promote(id, criterion, evidence) -> Promoted | Refused{reason}
memory.demote(id, reason) -> Demoted
memory.merge([ids]) -> MergedId
memory.split(id, [claims]) -> [ids]           # correcting a compound claim admitted before M-3
memory.invalidate(id, reason) -> InvalidationReport   # includes transitive effects
memory.why(id) -> ProvenanceTree
memory.blast(id) -> ImpactReport
memory.export(scope) / memory.import(bundle)
```

**M-34 — `memory.why`** returns the complete provenance tree, lane history, promotion record, and
every mutation with actor and reason. This is the primary trust instrument: a memory a human cannot
trace is a memory a human should not accept.

---

## 11. Storage

**M-35** — Default backend is embedded and file-backed under `.factory/memory/`: an append-only JSONL
log plus a rebuildable index. The log is the truth; the index is a cache.
**M-36** — Similarity search must have a **deterministic, dependency-free default** (lexical + structural
signals). Embedding backends are optional adapters behind the same interface; their absence must not
disable the fabric (PR-2).
**M-37** — Export and import use a documented plain-text format. Round-tripping must be lossless and is
covered by a conformance test.

---

## 12. Test matrix

| Test | Asserts |
| --- | --- |
| `admission-rejects` | Each of the eight admission checks rejects, with the right reason |
| `contradiction-quarantines-both` | M-9 |
| `unresolved-contradiction-archives-both` | M-10 |
| `invalidation-cascades` | A poisoned root archives every descendant whose provenance collapses (M-15) |
| `partial-collapse-penalises` | A partially-weakened descendant is demoted, not archived |
| `single-source-cap` | Confidence never exceeds the cap (M-16) |
| `dispute-never-retrieved` | A quarantined memory cannot appear in any pack (M-18) |
| `diversity-cap` | One source cannot exceed its share (M-19) |
| `promotion-requires-independence` | Same-model corroboration is refused (M-23) |
| `verification-beats-corroboration` | A checkable claim is not promoted by agreement alone (M-25) |
| `budget-shrinks` | 10k-write soak stays within `max_items` and `max_bytes` (M-29) |
| `helped-count-gating` | Retrieval alone does not increment `helped_count` (M-30) |
| `why-is-complete` | Provenance tree covers every mutation |
| `export-roundtrip` | Lossless (M-37) |
| `no-secrets` | Secret-shaped content is refused at admission |
