# Awareness Pack Specification

| Field | Value |
| --- | --- |
| Component | Awareness Pack assembly |
| Implements | PRD FR-9 |
| Depends on | Tool Registry (deterministic reads), Living Spec, Memory Fabric, Skill Registry, Ledger |

> An agent's output quality is bounded above by what it knew when it started. The pack is where that
> bound is set. Treat every token in it as spent on the agent's behalf.

---

## 1. Contract

```
assemble(work_item, agent_config, repo_state, spec, memory, ledger, skills, seed) -> AwarenessPack
```

**A-1 — Determinism.** Given identical inputs the function returns a byte-identical pack. The digest
is recorded on the run and is the join key for all pack telemetry.

**A-2 — Purity.** Assembly performs reads only. It never mutates the repository, memory, or the spec.

**A-3 — Offline.** Assembly completes with no network beyond an optionally-configured local index
service. Every section declares its offline degradation (§6).

**A-4 — Bounded.** Assembly has a wall-clock budget (default 10s warm, 60s cold). On expiry it emits
what it has, marks unfinished sections `degraded`, and records why. It never blocks a run indefinitely.

---

## 2. Structure

```
AwarenessPack {
  digest, assembled_at, budget_used, seed,
  sections: [Section],
  omissions: [ { section, reason } ],
  degradations: [ { section, reason } ],
}

Section {
  id, title, priority, budget_tokens, actual_tokens,
  items: [Item],
  retrieval_tool: string,          # how to get more of this section
  truncated: bool,
}

Item {
  content,
  citation: { kind: file|run|memory|spec|commit|test|ci, ref, locator },
  origin: deterministic | model_generated | human_authored,
  confidence: 0..1 | null,         # required when origin != deterministic
  freshness: { computed_at, valid_until | null },
}
```

**A-5 — Every item is cited.** An item without a resolvable `citation` is a defect and must not be
emitted. There are no uncited assertions in a pack.

**A-6 — Origin is explicit.** `model_generated` items carry the run that produced them and their
confidence. An implementer must be able to render a pack with all model-generated content stripped and
still have a usable pack.

---

## 3. The ten sections

| # | Id | Purpose | Primary source | Origin |
| --- | --- | --- | --- | --- |
| 1 | `mission` | What is being asked, and what done means | Work item, stage definition, acceptance criteria | deterministic |
| 2 | `spec-slice` | The intent that governs this change | Living Spec, anchor intersection | deterministic |
| 3 | `terrain` | The shape of the code being touched | Static analysis, module graph, test topology | deterministic |
| 4 | `precedent` | What was tried here before | Ledger, version history | deterministic |
| 5 | `hazards` | What breaks around here | CI history, revert history, flake data, prior findings | deterministic |
| 6 | `conventions` | The rules that actually apply | Canon memory, repository rules | mixed |
| 7 | `toolbelt` | What the agent can do this run | Tool Registry, grants | deterministic |
| 8 | `skills` | Ranked, budgeted procedure offer | Skill Registry | deterministic |
| 9 | `contract` | Blast radius, budget, ladder, required outputs | Policy + executor | deterministic |
| 10 | `open-questions` | What is unresolved and who can resolve it | Prior stages, calibration statements | deterministic |

**A-7 — Eight of ten sections are fully deterministic.** Only `conventions` (which may include
consolidated memory) and any explicitly-marked summary within a section may be model-generated. This
is the concrete form of PR-6, and it is why the pack is the same for a small model and a large one.

### 3.1 `mission`

The work item's request text (in an untrusted region — see HARNESS.md L-1), its source context, the
acceptance criteria in force, and the stage's definition of done. Never summarised; the original text
is what the requester wrote.

### 3.2 `spec-slice`

Selection algorithm:

```
surface   = change_surface(work_item)      # paths, symbols, modules
units     = spec.units_intersecting(surface, status=active)
units    += spec.units_in_state(surface, CONTRADICTED)      # always included
units    += spec.constraints_covering(surface)              # cross-cutting
rank by:  (contradicted first, then anchor overlap, then criteria count)
```

Contradicted units are **never** budget-dropped. An agent must not work a surface while unaware that
its intent is in dispute.

### 3.3 `terrain`

Altitude matters more than volume. Emit, in order: the modules containing the surface; their direct
dependency and dependent edges; the entry points that reach the surface; the tests that cover it; the
build targets that include it; and ownership metadata if present. Do **not** emit file contents here —
that is `repo.read`'s job, and the retrieval tool is named in the section.

### 3.4 `precedent`

Prior work items whose surface intersects this one, with outcome: merged, reverted, abandoned,
rejected — and the reason where recorded. This is the single highest-value section for avoiding
repeated failure, and it is available only because the ledger exists.

### 3.5 `hazards`

Mechanically derived, never guessed:

| Hazard | Derivation |
| --- | --- |
| Flaky tests on this surface | Tests with mixed outcomes on identical commits |
| Recently reverted changes | Version history: revert commits touching the surface |
| High-churn files | Change frequency over the window |
| Repeated review findings | Ledger: findings clustered by surface |
| Incident-linked paths | Signal intake fingerprints mapped to paths |
| Long-broken checks | CI history for the surface |

### 3.6 `conventions`

Canon-lane memories of kind `convention` scoped to this repository, plus repository rule files. Every
item cites its memory id and its promotion evidence. Candidate-lane memories appear only if the agent
opted in, and are labelled `unverified` inline.

### 3.7 `toolbelt`

Each granted tool with its typed signature, cost class, and at least one worked example. Ungranted
tools are **not** listed — an agent should not spend attention on doors that are locked.

### 3.8 `skills`

The ranked offer (see [skills.md](skills.md) §5). Records offered, loaded, and used.

### 3.9 `contract`

Generated from the enforced blast-radius contract, including the courage clause (HARNESS.md §5.3),
the budgets, the escalation ladder, and the required output schema.

### 3.10 `open-questions`

Unresolved `unknowns` from prior stages' calibration statements, each with who can answer it, so an
agent knows what it is *not* expected to resolve alone.

---

## 4. Budgeting

**A-8 — Two-level budget.** A total pack budget, and per-section budgets derived from role weights.

Default weights (fractions of the pack budget; normalised, then clamped by section minima):

| Section | Scout | Architect | Builder | Critic | Prover | Conductor |
| --- | --- | --- | --- | --- | --- | --- |
| mission | .10 | .12 | .10 | .10 | .12 | .20 |
| spec-slice | .10 | .30 | .15 | .25 | .20 | .10 |
| terrain | .25 | .12 | .25 | .10 | .10 | .05 |
| precedent | .20 | .10 | .08 | .08 | .05 | .15 |
| hazards | .20 | .06 | .12 | .20 | .18 | .05 |
| conventions | .05 | .10 | .12 | .12 | .05 | .05 |
| toolbelt | .04 | .05 | .08 | .05 | .15 | .10 |
| skills | .03 | .05 | .05 | .05 | .05 | .10 |
| contract | .02 | .05 | .03 | .03 | .05 | .10 |
| open-questions | .01 | .05 | .02 | .02 | .05 | .10 |

These are **starting values, not settled ones**. They are a declared open question (PRD OQ-1) and are
a first-class self-improvement target: pack telemetry (§5) measures which sections were used, and the
improvement loop proposes reweighting with benchmark evidence.

**A-9 — Summarise, never truncate mid-item.** A section over budget drops whole items from the tail of
its ranking and appends a pointer: `N more items available via {retrieval_tool}`. A half-item is worse
than an absent one.

**A-10 — Minima.** `mission`, `contract`, and contradicted units in `spec-slice` have hard minima and
are never dropped. If minima exceed the total budget, assembly fails loudly rather than shipping a pack
missing its mission.

**A-11 — Tier-aware totals.** The pack budget is a fraction (default 0.35) of the active tier's
*effective* working-set ceiling — not its nominal window — leaving room for the run itself.

---

## 5. Telemetry

Recorded per run and joined on the pack digest:

| Signal | Use |
| --- | --- |
| Section sizes, requested vs. actual | Detect chronic over/under-budget sections |
| Items cited by the agent in its output | Direct measure of usefulness |
| On-demand retrievals, by section | An item retrieved often belongs in the pack |
| Sections with zero engagement | Candidate for weight reduction |
| Pack digest vs. run outcome | Correlate pack composition with gate pass rate |
| Assembly duration by section | Performance budget enforcement |

**A-12 — Pack efficiency** = (tokens in cited items) / (total pack tokens). Reported per agent per
window. A falling value means the pack is growing without helping.

**A-13 — `sf pack diff <run-a> <run-b>`** renders exactly what the second run knew that the first did
not. When two runs on the same task diverge, this is the first place to look.

---

## 6. Offline degradation

| Section | Degradation when a source is unavailable |
| --- | --- |
| mission | None — always available locally |
| spec-slice | None — spec is in the repository |
| terrain | Falls back from a cached index to on-demand analysis, marked `degraded` |
| precedent | Local ledger only; remote history omitted with reason |
| hazards | CI-derived hazards omitted with reason; version-history hazards retained |
| conventions | Local memory only |
| toolbelt | None — grants are local |
| skills | None — skills are files |
| contract | None |
| open-questions | None |

**A-14** — Degradation is always *stated in the pack itself*, in `degradations`, so the agent knows the
shape of its own blind spot and can lower its confidence accordingly (HARNESS.md O-4).

---

## 7. Anti-requirements

Things assembly must **not** do, each because it has been a failure mode elsewhere:

- **Do not summarise the request.** The requester's words are the requirement.
- **Do not include the previous agent's reasoning transcript in the Critic's pack.** Outputs and
  evidence only; shared reasoning defeats independent review.
- **Do not include everything because it fits.** Unused context is not free — it displaces attention.
- **Do not silently substitute a stale cache.** Stale is a degradation and must be declared.
- **Do not let a single source dominate.** Diversity capping applies to memory (see memory.md §6) and
  to precedent: one prior work item may not supply more than 40% of a section's items.
- **Do not put secrets in a pack.** Ever, under any section, including as an example.
