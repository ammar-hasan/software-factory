# Skill Lifecycle Specification

| Field | Value |
| --- | --- |
| Component | Skill Registry and lifecycle |
| Implements | PRD FR-7 |
| Operations | promote · evolve · merge · split · sunset |

> Every skill library becomes a junk drawer unless removal is as easy as addition. This subsystem is
> built around one measurement — **selection quality** — and one rule: **a skill that cannot show
> evidence does not advance.**

---

## 1. Record

```
Skill {
  name, version, status,           # draft | trial | active | deprecated | retired
  scope: factory | agent:<name>,
  description,                     # the selection surface — this is what gets matched
  appliesTo: { roles: [], stages: [], surfaces: [glob], languages: [] },
  owners: [string], review_by: date,
  evals: [EvalTaskRef],
  supersedes: [SkillRef], superseded_by: SkillRef?,
  body,                            # the instructions
  assets: [path],
  metrics: SkillMetrics,
}

SkillMetrics {
  offered, loaded, used,           # per window
  helped, hindered, neutral,       # outcome-linked
  precision, recall, collision,    # see §4
  eval_pass_rate, eval_trend,
  last_selected_at,
}
```

**K-1 — `description` is the product.** Selection happens on the description; the body only matters
once selected. A skill with a perfect body and a vague description is a broken skill, and §4 measures
exactly that.
**K-2 — Every non-draft skill has an owner and a review date.** Unowned or undated fails lint.
**K-3 — Skills change knowledge, never access.** A body implying it grants a tool, secret, or scope
fails lint (PRD PR-4).

---

## 2. States

| State | Loadable | In selection budget | Requires |
| --- | --- | --- | --- |
| `draft` | Only when explicitly named | No | — |
| `trial` | Yes, for a declared sample fraction | Yes, capped | ≥1 eval task; description passes §4.1 |
| `active` | Yes | Yes | Eval evidence of improvement over baseline |
| `deprecated` | Yes, with a warning and a successor pointer | Yes | A stated successor or reason |
| `retired` | No | No | Recorded retirement reason |

**K-4** — Every transition is a definition change, reviewed like code. There is no runtime state
machine mutating skill status behind the operator's back.

---

## 3. The five operations

### 3.1 Promote (`draft → trial → active`)

```
draft -> trial:
    require description passes discoverability check (§4.1)
    require >= 1 eval task with success criteria
    require owner and review date
    set sample_fraction (default 0.25)

trial -> active:
    require N >= min_trials (default 20) eligible runs observed
    require eval_pass_rate(with) - eval_pass_rate(baseline) >= min_lift (default 0.10)
    require no regression on the factory's standing benchmark beyond tolerance
    require precision >= min_precision (default 0.6)
```

**K-5 — Promotion without evidence is impossible through the normal path.** A forced promotion
requires an explicit override flag, records the overriding human, and marks the skill
`promoted_without_evidence` until evidence arrives — visible in every listing.

### 3.2 Evolve (revise in place)

A revision proposal carries: the failing runs that motivated it, the diff, and the eval set run
**before and after**.

```
accept_revision if:
    eval_pass_rate(after) >= eval_pass_rate(before)          # no self-regression
    and no standing-benchmark regression beyond tolerance
    and version incremented
```

**K-6 — A revision that regresses its own eval set is rejected by the gate**, not merely flagged.
**K-7 — Evolution is bounded.** A skill revised more than `max_revisions_per_window` (default 3) is
flagged: it is probably two skills (→ split, §3.4) or the wrong abstraction.

### 3.3 Merge

**Trigger.** Two skills where `overlap(appliesTo) > τ_overlap` (default 0.7) **and**
`similarity(body) > τ_body` (default 0.6) **and** both are `trial` or `active`.

```
propose_merge(a, b):
    successor = synthesise(a, b)      # union of appliesTo, reconciled body,
                                      # union of evals, max(version)+1
    require successor passes union(a.evals, b.evals)
    require successor precision >= max(a.precision, b.precision) - ε
    on adoption: a, b -> deprecated with successor pointer, then retired after grace period
```

**K-8 — The successor must pass the union of both eval sets before either predecessor retires.** A
merge that loses a capability is not a merge; it is a regression with extra steps.
**K-9 — Grace period before retirement** (default one review window) so a bad merge can be reverted by
reactivating the predecessors rather than by reconstructing them.

### 3.4 Split

**Trigger.** A skill's eval results **diverge by task class**: it passes class A at ≥ 0.8 and fails
class B at ≤ 0.4 over sufficient trials. Also triggered by K-7 (revision churn).

```
propose_split(s):
    classes  = cluster(s.evals, by=outcome × task_features)
    children = [ narrow(s, c) for c in classes ]   # sharper description, subset of evals,
                                                   # narrowed appliesTo
    require each child passes its own eval subset at >= s's rate on that subset
    require pairwise collision(children) <= s.collision       # splitting must not blur selection
    on adoption: s -> deprecated pointing at all children
```

**K-10 — Splitting must improve selection, not just narrow scope.** If the children collide with each
other as much as the parent collided with its siblings, the split is refused: the problem was the
description, not the breadth.

### 3.5 Sunset (`→ deprecated → retired`)

Proposed when **any** holds:

| Condition | Default threshold |
| --- | --- |
| Not selected in N eligible runs | N = 200 |
| Eval set failing for M consecutive windows | M = 3 |
| Every anchor it references is orphaned | any |
| Fully covered by another active skill | coverage ≥ 0.95 |
| Past its review date with no owner action | grace 30d |
| `hindered > helped` over the window | any |

**K-11 — Retirement is always a reviewed change** (PRD PR-7), never automatic.
**K-12 — Retired skills remain in history and remain resolvable by name+version**, so old runs stay
explicable.
**K-13 — Sunset proposals are batched** into one periodic review rather than trickling, so pruning is
a single deliberate act instead of a stream of interruptions.

---

## 4. Selection quality

The measurement everything else depends on.

### 4.1 Discoverability check

A description must state: **when** to use the skill (trigger conditions), **what** it produces, and
**what it is not for**. Checks:

| Check | Fails when |
| --- | --- |
| Trigger present | No condition under which the skill applies is stated |
| Boundary present | No statement of what it does not cover |
| Distinctiveness | Similarity to a sibling's description exceeds `τ_collision` (default 0.75) |
| Specificity | Consists only of generic terms with no domain anchor |
| Length | Outside [20, 500] characters |

### 4.2 Metrics

```
precision = helped / loaded                     # of the times it was loaded, how often it helped
recall    = helped / (helped + missed)          # missed = runs where a later signal shows it
                                                # should have been loaded and was not
collision = max similarity to any sibling description
```

**K-14 — `helped` is outcome-linked, not self-reported.** A skill counts as having helped when it was
loaded and cited in a run that passed its gates; as having hindered when it was loaded and cited in a
run that failed a gate whose failure references its guidance. Neutral otherwise.
**K-15 — `missed`** is detected retrospectively: a failed run whose failure signature matches a skill's
`appliesTo` and whose gate finding matches the skill's stated purpose, where the skill was not offered.
**K-16 — A skill below `min_precision` gets its description revised before its body is blamed.** The
registry must say which of the two it thinks is wrong, because fixing the body of a skill that is never
correctly selected changes nothing.

---

## 5. The offer

Skills reach an agent through a **ranked, budgeted offer** in the pack (awareness.md §3.8).

```
eligible = [ s for s in registry
             if s.status in {trial, active, deprecated}
             and matches(s.appliesTo, {role, stage, surface, language})
             and (s.status != trial or sampled(s.sample_fraction)) ]

score(s) = w1 * appliesTo_match
         + w2 * precision
         + w3 * recency_of_help
         - w4 * collision
         - w5 * (status == deprecated)

offer = top_k(eligible, by score, k = selection_budget)      # default k = 7
```

**K-17 — The offer is bounded** (default 7). Beyond a small number, additional options degrade
selection rather than improving it — the reason §3.4's split exists.
**K-18 — Order is by score, never by filesystem order or name.** Alphabetical ordering is a silent bias.
**K-19 — The pack records offered, loaded, and used** for every run — the raw data for §4.
**K-20 — Deprecated skills are offered with their warning and successor pointer**, so in-flight work is
not broken by a deprecation, but they are down-weighted.

---

## 6. Induction — where new skills come from

**K-21** — The registry may propose a `draft` skill from **repeated successful behaviour** in the
ledger: `N ≥ min_observations` (default 5) runs solving the same task class with a common procedure,
where that procedure is not covered by an existing skill.

```
propose_induction:
    cluster runs by (task class, tool sequence signature, outcome=passed)
    extract the common procedure
    generate a description that passes §4.1
    generate an eval set from the source runs' tasks
    emit a draft skill proposal citing every source run
```

**K-22 — Induced skills enter as `draft` and follow the ordinary promotion path.** Being derived from
successful runs is a hypothesis, not evidence: those runs may have succeeded for other reasons.
**K-23 — Induction and memory `procedure` memories are the same pipeline.** A `procedure` memory that
reaches Canon is exactly a skill candidate (memory.md §3), and the two subsystems share the trigger.

---

## 7. Anti-patterns designed against

| Anti-pattern | Defence |
| --- | --- |
| Library grows monotonically | Sunset triggers (§3.5), batched review (K-13) |
| Overlapping skills fire together | Merge trigger (§3.3), collision metric (§4.2) |
| One skill tries to do everything | Split trigger (§3.4), revision-churn flag (K-7) |
| Skills promoted on vibes | Evidence-gated promotion (K-5) |
| Skill body blamed for a description problem | K-16 |
| Selection degraded by library size | Bounded offer (K-17) |
| Skill silently grants capability | K-3 lint |
| Merge quietly loses a capability | Union-eval requirement (K-8) |
| Deprecation breaks in-flight work | Deprecated stays loadable with a pointer (K-20) |
| Induced skill overfits its source runs | Draft entry + ordinary promotion path (K-22) |

---

## 8. Test matrix

| Test | Asserts |
| --- | --- |
| `promotion-requires-lift` | `trial → active` refused without `min_lift` (K-5 path) |
| `revision-regression-refused` | A revision failing its own evals is rejected (K-6) |
| `merge-requires-union-pass` | Successor failing either predecessor's evals blocks the merge (K-8) |
| `split-requires-selection-gain` | A split whose children collide as much as the parent is refused (K-10) |
| `sunset-proposed-not-applied` | Every sunset trigger produces a proposal, never an automatic retirement (K-11) |
| `offer-bounded-and-ranked` | Offer size ≤ budget and ordered by score, not by name (K-17, K-18) |
| `helped-is-outcome-linked` | Self-reported usefulness never increments `helped` (K-14) |
| `collision-detected` | Two near-identical descriptions fail the discoverability check |
| `access-lint` | A body implying a capability grant fails lint (K-3) |
| `retired-still-resolvable` | Old runs still resolve their skill version (K-12) |
