# Completeness Review — Master PRD v1.0.0

| Field | Value |
| --- | --- |
| Reviews | `docs/PRD.md` v1.0.0 (Baseline, pre-review) |
| Review type | Completeness — what is **absent**, not what is wrong |
| Date | 2026-08-31 |
| Method | Entity-lifecycle walk (§9.1) · operational walk · requirement-family sweep · parameter sweep · acceptance-criteria sweep · journey enumeration · failure-mode enumeration · deliverable sweep |
| Result | **117 gaps** — 28 BLOCKING, 72 IMPORTANT, 17 NICE — plus **19 missing acceptance narratives** |

> **How to read this.** Every row is phrased as a requirement that can be pasted into the PRD under an
> ID that slots into the existing `FR-x.y` / `NFR-x.y` scheme. New families are proposed where no
> existing family fits: **FR-23** installation/upgrade/migration · **FR-24** repository onboarding,
> indexing and toolchains · **FR-25** version-control integration mechanics · **FR-26** identity and
> in-factory authorisation · **FR-27** compliance, data protection and provenance · **FR-28**
> extensibility and self-test · **FR-29** cost governance and quota · **NFR-9** operability ·
> **NFR-10** supportability · **NFR-11** normative defaults · **NFR-12** project deliverables.
>
> **Ranks.** **BLOCKING** — v1 cannot ship without it, usually because the design is unrecoverable or
> unsafe otherwise. **IMPORTANT** — v1.x. **NICE** — later.
>
> Where an area is *partially* covered, the covering requirement is cited and the gap is narrowed to
> the missing part. Requirements already covered are not listed.

---

## Summary table

| ID | Rank | Area | Gap |
| --- | --- | --- | --- |
| FR-1.7 | IMPORTANT | Factory lifecycle | No decommissioning path: drain, final export, handle release, disposition of in-flight items |
| FR-1.8 | NICE | Factory lifecycle | No rename/handle-change path with alias retention for in-flight references |
| FR-1.9 | IMPORTANT | Factory lifecycle | State-directory location undefined for multi-repository factories; definition/state separation unstated |
| FR-2.11 | BLOCKING | Definition lifecycle | Runs do not pin or record the definition revision and resolved-config digest they executed under |
| FR-2.12 | IMPORTANT | Definition lifecycle | No activation/reload lifecycle: when a new definition takes effect, what happens to in-flight runs, how to roll back |
| FR-2.13 | IMPORTANT | Definition lifecycle | No concurrent-edit semantics: two operators applying definitions, revision fencing on apply |
| FR-3.13 | IMPORTANT | Agent lifecycle | No deletion/tombstone semantics for agents, automations, runners, scorers referenced by history |
| FR-3.14 | BLOCKING | Agent lifecycle | No conversation-state management: Conductor and specialist conversations grow unbounded across a work item |
| FR-4.11 | IMPORTANT | Work-item lifecycle | No duplicate-linking, merge, or split of work items |
| FR-4.12 | BLOCKING | Work-item lifecycle | No scheduling model: priority, fairness, starvation prevention, admission control across a backlog |
| FR-4.13 | IMPORTANT | Work-item lifecycle | No ageing/reaping of items parked `BLOCKED` indefinitely |
| FR-4.14 | IMPORTANT | Work-item lifecycle | "Work class" / "task class" used by four requirements but never defined as a declared taxonomy |
| FR-5.13 | IMPORTANT | Spec lifecycle | No conflict detection or resolution when two Spec Deltas touch the same unit concurrently |
| FR-5.14 | IMPORTANT | Spec lifecycle | Spec Delta approval requires "a human" but no ownership or approver-routing model |
| FR-5.15 | NICE | Spec lifecycle | No merge/split of spec units, and no bulk re-anchoring after a large refactor |
| FR-6.16 | IMPORTANT | Memory lifecycle | Archive is terminal; no hard-delete path, so a memory can never actually be removed |
| FR-6.17 | IMPORTANT | Memory lifecycle | No memory-store schema migration, compaction, integrity check, or repair |
| FR-6.18 | IMPORTANT | Memory lifecycle | No serialisation model for concurrent writes into a shared scope from parallel runs |
| FR-6.19 | IMPORTANT | Memory detail | Ranking, decay, similarity, diversity-cap and "value density" are named but never defined |
| FR-7.14 | BLOCKING | Skill lifecycle | Skills may ship scripts; nothing says that code is untrusted, sandboxed, pinned, or grant-bounded |
| FR-7.15 | NICE | Skill lifecycle | No cross-factory skill sharing/import with provenance and eval carry-over |
| FR-7.16 | IMPORTANT | Skill detail | Lifecycle constants (N, M, overlap threshold) and the selection precision/recall method are unspecified |
| FR-8.11 | IMPORTANT | Runner lifecycle | Images pinned by digest but no rebuild cadence, patch-refresh path, or cached-layer GC |
| FR-8.12 | BLOCKING | Run lifecycle | No run lease/heartbeat, so an executor that dies leaves a permanently "running" orphan |
| FR-8.13 | BLOCKING | Run lifecycle | No workspace/worktree garbage collection; disk fills within days of normal operation |
| FR-9.12 | BLOCKING | Awareness detail | Budgets have no unit, no tokenizer contract, and no over-budget arbitration between sections |
| FR-9.13 | BLOCKING | Awareness detail | "Change surface" governs five requirements but is never defined or given a derivation algorithm |
| FR-9.14 | IMPORTANT | Awareness detail | Role "weighting" is prose; no declared, inspectable, defaulted composition configuration |
| FR-9.15 | IMPORTANT | Awareness detail | No behaviour defined when the change itself exceeds the model window (large diff review) |
| FR-10.12 | NICE | Tool lifecycle | No versioning or deprecation path for tool signatures consumed by recorded runs |
| FR-11.13 | BLOCKING | Harness detail | Model sampling parameters and the resolved provider model version are not recorded; replay and benchmarks are unsound |
| FR-11.14 | NICE | Harness detail | No prompt/result caching semantics, nor their effect on reported cost |
| FR-11.15 | IMPORTANT | Harness detail | Confidence has no declared scale or format; calibration error has no named metric or minimum sample |
| FR-12.9 | IMPORTANT | Blast radius | The contract is described in prose; no schema and no test that stated contract equals enforced contract |
| FR-13.15 | IMPORTANT | Scorer lifecycle | Rubric edits silently break trend comparability; no rubric version or trend-break marker |
| FR-13.16 | BLOCKING | Gates | No flaky-test policy: `tests-pass` and `regression-proven` are meaningless on a flaky suite |
| FR-13.17 | IMPORTANT | Gates | External CI is named as a blocker but never bound into gates: no subscription, timeout, or re-trigger |
| FR-13.18 | IMPORTANT | Gates | No gate execution model: ordering, short-circuiting, parallelism, gate cost budget |
| FR-13.19 | IMPORTANT | Assurance detail | Repair caps, sampling determinism, judge-agreement threshold, repetitions, and adoption tolerance are unset |
| FR-14.10 | IMPORTANT | Improvement lifecycle | No proposal lifecycle: rejection memory durability, expiry, supersession, conflicts between open proposals |
| FR-14.11 | IMPORTANT | Improvement detail | Cooling period, open-proposal cap, and held-out set size/refresh/contamination rules are unset |
| FR-15.11 | BLOCKING | Evidence lifecycle | Retention can delete evidence that a sealed claim resolves to, silently breaking INV-6 |
| FR-15.12 | BLOCKING | Ledger lifecycle | Append-only ledger has no segmentation, snapshot, or archival, yet NFR-3.2 promises bounded growth |
| FR-15.13 | IMPORTANT | Ledger integrity | A hash chain with no anchored head is re-computable; wholesale rewrite is undetectable |
| FR-15.14 | IMPORTANT | Ledger integrity | No time source, clock-skew tolerance, or monotonic sequencing across planes and executors |
| FR-15.15 | IMPORTANT | Metrics detail | Metric windows, populations, medians, and cost decomposition are undefined; R-12's review-cost metric is never required |
| FR-16.8 | IMPORTANT | Checkpoints | No notification subsystem: routes, dedup, digest, human escalation ladder, delivery failure |
| FR-16.9 | IMPORTANT | Checkpoints | No general gate override/exception path with authority, record, expiry, and reporting |
| FR-17.12 | BLOCKING | Secrets | Definitions carry secret *names*; nothing specifies where the *values* live, how they rotate, or what absence does |
| FR-17.13 | IMPORTANT | Security | The pack itself ingests untrusted repository and issue text; no section-level untrusted labelling |
| FR-17.14 | IMPORTANT | Security | Stop/cancel/emergency-stop semantics undefined for in-flight external actions, partial writes, and mounted secrets |
| FR-18.15 | IMPORTANT | Intake | No event replay or catch-up after downtime; missed events are silently lost |
| FR-18.16 | BLOCKING | Intake | No backpressure or circuit breaker; one signal storm converts directly into unbounded spend |
| FR-18.17 | IMPORTANT | Intake | Webhook signing is mentioned once; no verification, replay window, or adapter-secret rotation requirement |
| FR-18.18 | IMPORTANT | Intake detail | Filter matching semantics undefined: value types, case, glob/regex on paths and branches, evaluation order |
| FR-19.10 | IMPORTANT | Handoff | Duplicate work between a factory run and an external pickup is documented as a risk, never detected |
| FR-20.11 | IMPORTANT | Local-first | `sf init` behaviour on an existing, partial, or foreign `.factory/` is undefined |
| FR-21.8 | IMPORTANT | API | No rate limiting, pagination, or request/response size limits for served deployments |
| FR-21.9 | IMPORTANT | API | No versioning/deprecation policy for the API, CLI flags, or `--json` output stability |
| FR-23.1 | BLOCKING | Installation | Installation, prerequisites, supported versions, offline install, and post-install verification are absent |
| FR-23.2 | BLOCKING | Upgrade | No software upgrade path or version-compatibility policy across coordinator, workers, dashboard, and API |
| FR-23.3 | IMPORTANT | Upgrade | No defined behaviour for partial or rolling upgrades and mixed-version fleets |
| FR-23.4 | BLOCKING | Migration | No on-disk state/schema migration: forward migration, dry-run, backup-before-migrate, downgrade policy |
| FR-23.5 | IMPORTANT | Migration | No definition-schema migration tooling; FR-1.6 only rejects unknown versions |
| FR-24.1 | BLOCKING | Onboarding | §10 assumes "the factory helps create" validation; no requirement implements it, and every gate depends on it |
| FR-24.2 | IMPORTANT | Onboarding | No monorepo support: package graph, affected-target selection, per-package toolchains and owners |
| FR-24.3 | BLOCKING | Onboarding | No index lifecycle: cold build, invalidation, storage budget, behaviour when missing (NFR-2.1 assumes warm) |
| FR-24.4 | IMPORTANT | Onboarding | No toolchain adapter contract: detection, version pinning, hermetic setup, structured result parsing |
| FR-24.5 | IMPORTANT | Onboarding | No dependency/lockfile policy: may an agent add a dependency, and what checks that |
| FR-24.6 | IMPORTANT | Onboarding | No excluded-content policy for generated, vendored, binary, or large files in surface and index |
| FR-25.1 | BLOCKING | Version control | No handling of base drift or merge conflicts, and no mandatory re-validation after integration |
| FR-25.2 | IMPORTANT | Version control | No support for long-running or stacked changes, or resumption after days of base movement |
| FR-25.3 | IMPORTANT | Version control | Merge state is "observed" (FR-4.4) but nothing specifies acquisition, revert detection, or reopen |
| FR-25.4 | IMPORTANT | Version control | No handling of push denial, protected branches, force-push under a run, or deleted branches |
| FR-25.5 | IMPORTANT | Version control | No cross-repository work item, though a factory may declare many repositories (FR-1.2) |
| FR-26.1 | BLOCKING | Authorisation | No in-factory authorisation model: who may approve, override, widen, force-promote, or emergency-stop |
| FR-26.2 | BLOCKING | Identity | No principal model or cross-provider identity mapping, despite decisions being "recorded with identity" |
| FR-26.3 | IMPORTANT | Identity | No human offboarding: revoking a principal, personal-scope memory disposition, pending approvals |
| FR-26.4 | IMPORTANT | Authorisation | Shared deployments are "behind authentication" with no specified auth, session, or audit model |
| FR-26.5 | IMPORTANT | Authorisation | Hosted coordination implies multi-tenancy with no isolation requirement for ledger, memory, or secrets |
| FR-27.1 | IMPORTANT | Compliance | No licence or verbatim-copy scanning of generated code; NFR-8.1 covers only the factory's own dependencies |
| FR-27.2 | IMPORTANT | Compliance | No authorship provenance on the change itself: trailers, attribution manifest, sign-off policy |
| FR-27.3 | BLOCKING | Compliance | No erasure or legal hold; an append-only ledger plus permanent Archive makes deletion architecturally impossible |
| FR-27.4 | IMPORTANT | Compliance | Redaction covers known secret values only; no sensitive-data classification or per-class egress rule |
| FR-27.5 | IMPORTANT | Compliance | No data map: which data classes exist, where each is stored, for how long, and which flows leave the host |
| FR-28.1 | IMPORTANT | Extensibility | Adapters, harnesses, executors and stores are all pluggable in prose with no packaging, discovery, or trust model |
| FR-28.2 | IMPORTANT | Self-test | Skill revisions are eval-gated (FR-7.5); agent prompt and pack changes are not |
| FR-28.3 | IMPORTANT | Self-test | No shipped fixture factory exercising every stage, gate, and executor offline |
| FR-29.1 | BLOCKING | Cost | Budgets are per-agent-run only; no factory, period, or organisation spend cap |
| FR-29.2 | BLOCKING | Cost | Rate limiting is handled reactively (FR-11.10); no client-side concurrency control, backoff, or fairness |
| FR-29.3 | NICE | Cost | No cost attribution or chargeback by team, repository, or work class |
| NFR-2.5 | IMPORTANT | Performance | No cold-index target and no defined measurement corpus for NFR-2.1's "100k files, warm index" |
| NFR-4.4 | IMPORTANT | Usability | NFR-4.1's "under 10 minutes" has no reference environment or reference repository |
| NFR-5.5 | IMPORTANT | Testability | No fault-injection suite; none of the failure modes in §7 of this review are testable as specified |
| NFR-7.3 | NICE | i18n | NFR-7.2 externalises UI strings; agent output language and non-English intake are unaddressed |
| NFR-8.3 | IMPORTANT | Governance | No telemetry policy: off by default, opt-in, documented payload |
| NFR-8.4 | IMPORTANT | Governance | No release integrity: signed artifacts, SBOM, reproducible build, security-fix support window |
| NFR-8.5 | IMPORTANT | Governance | No public versioning/deprecation policy for definition schema, ledger format, API, or CLI |
| NFR-9.1 | BLOCKING | Operability | No backup requirement at all: what constitutes a complete, consistent backup |
| NFR-9.2 | BLOCKING | Operability | No disaster recovery: RPO/RTO, rebuild procedure, ledger-loss and partial-loss handling |
| NFR-9.3 | BLOCKING | Operability | No disk-pressure behaviour; per-run ceilings (FR-8.10) do not protect the coordination plane |
| NFR-9.4 | IMPORTANT | Operability | No single-writer guarantee for the state directory; two coordinators on one `.factory/` is undefined |
| NFR-9.5 | IMPORTANT | Operability | No health checks, SLOs, or alert routing for the factory itself (FR-18.9 covers adapters only) |
| NFR-9.6 | IMPORTANT | Operability | No log rotation, size caps, structured-log format, or level policy |
| NFR-9.7 | IMPORTANT | Operability | No incident-response runbook for credential compromise, poisoned memory, or a bad definition |
| NFR-10.1 | IMPORTANT | Supportability | No diagnostic command or redacted support bundle; FR-21.2 has no `doctor` or `support-bundle` |
| NFR-11.1 | IMPORTANT | Specification | No normative defaults artifact; ~40 thresholds are named without values (see §4) |
| NFR-12.1 | BLOCKING | Deliverables | No contributing guide, code of conduct, maintainer list, or decision process |
| NFR-12.2 | BLOCKING | Deliverables | No security policy or vulnerability disclosure process for an Apache-2.0 project handling credentials |
| NFR-12.3 | BLOCKING | Deliverables | No quickstart, tutorial, or CI-tested reference example factory |
| NFR-12.4 | IMPORTANT | Deliverables | No troubleshooting runbook, FAQ, or error-code index for FR-21.5's catalogue |
| NFR-12.5 | IMPORTANT | Deliverables | §7.17 implies a threat model; none is required as a published document |
| NFR-12.6 | IMPORTANT | Deliverables | No compatibility matrix for OS, VCS, container runtimes, providers, and harnesses |
| NFR-12.7 | IMPORTANT | Deliverables | No changelog or per-version migration guide requirement |
| NFR-12.8 | IMPORTANT | Deliverables | No sizing/capacity guide: disk, CPU, memory, and index cost per repository and per concurrent run |
| NFR-12.9 | IMPORTANT | Deliverables | §11.2's acceptance test has no publication or reproduction requirement (OQ-7 left open) |
| NFR-12.10 | NICE | Deliverables | No offline-browsable documentation site; docs are the one thing that assumes a network |

**Missing acceptance narratives: AN-4 … AN-22** — enumerated in §6.

---

## 1. Lifecycle gaps — the §9.1 entity walk

Each entity in §9.1 was walked through **create · read · update · delete · migrate · version ·
backup/restore · concurrent modification · orphan cleanup**. The matrix records coverage in the PRD as
written. `+` specified, `~` partially specified (requirement cited in the detail below), `—` absent.

| Entity | Create | Read | Update | Delete | Migrate | Version | Backup | Concurrency | Orphans |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Factory | + | + | + | — | — | ~ | — | — | — |
| Agent | + | + | + | — | — | — | — | ~ | — |
| Automation | + | + | ~ | — | — | — | — | — | — |
| Runner | + | + | + | — | — | ~ | — | + | — |
| Scorer | + | + | ~ | — | — | — | — | + | — |
| Skill | + | + | + | + | — | + | — | — | + |
| Spec unit | + | + | + | + | — | ~ | — | — | + |
| Work item | + | + | + | — | — | — | — | ~ | — |
| Run | + | + | ~ | — | — | — | — | + | — |
| Memory | + | + | + | ~ | — | — | ~ | — | + |
| Evidence | + | + | n/a | ~ | — | — | — | + | — |
| Ledger | + | + | n/a | — | — | — | — | ~ | n/a |

Skills (§7.7) and spec units (§7.5) are the two entities whose lifecycles are genuinely complete. Every
other row has a hole, and three columns — **migrate**, **backup**, and **delete** — are empty for the
whole system.

### 1.1 Factory

**FR-1.7 (P1) — IMPORTANT — Factory decommissioning.**
*Gap:* a factory can be created (FR-20.1) and its definition changed (FR-2.6), but there is no way to
end one. Nothing says what happens to in-flight work items, to the handle other teams @-mention, to
the ledger, or to memories in `factory` scope.
*Proposed:* the factory must support a decommission operation that (a) refuses new intake and records
the refusal reason to each source, (b) drains or force-cancels in-flight work items with a recorded
disposition per item, (c) produces a complete signed export (ledger, memory, evidence manifest,
definition revision), (d) releases the handle, and (e) leaves the state directory in a documented
read-only archival form that `sf` can still query. `sf factory decommission --dry-run` reports what
would be drained, cancelled, and exported.

**FR-1.8 (P2) — NICE — Factory and handle rename.**
*Gap:* identity is `name` and the addressable handle is `handle` (FR-1.1), but renaming either is
unaddressed. Work items carry source context (FR-4.6) that may embed the handle; ledger entries
reference the factory.
*Proposed:* renaming must preserve prior names as resolvable aliases for a declared period, must be a
ledger-recorded event, and must not invalidate existing work-item source context. Alias collisions
across factories in one workspace (FR-1.5) are validation errors.

**FR-1.9 (P0) — IMPORTANT — State directory location and definition/state separation.**
*Gap:* FR-20.4 places state under `.factory/`, and FR-1.2 allows a factory to declare *several*
repositories. It is unstated which repository holds `.factory/`, whether the definition tree and the
state directory must be co-located, whether `.factory/` is version-controlled (it must not be — it
holds evidence and secrets-adjacent data), and where state lives when the definition tree is a
separate repository from the worked repositories (the normal case for a platform team).
*Proposed:* the definition tree root and the state root are separately configurable; the default is
`.factory/` beside `factory.yaml`; `sf init` must write an ignore entry for the state root; a state
root may serve exactly one factory (see NFR-9.4); and `sf plan` must print both resolved paths.

### 1.2 Definition (§7.2)

**FR-2.11 (P0) — BLOCKING — Runs pin and record the definition revision.**
*Gap:* FR-2.3 makes validation whole-tree and atomic and FR-9.1 records a *pack* digest, but nothing
records **which definition revision a run executed under**. §9.1's `Run` has `packDigest` and no
`definitionRevision`. Consequently: a run cannot be explained after the definition changes; FR-11.11
replay is not reproducible; benchmark comparisons (FR-13.9) cannot prove the two configurations
differed only in the intended way; and FR-14.4's "traceable to its evidence" fails as soon as anyone
edits a prompt. This is unrecoverable retroactively — history recorded without it stays unexplainable.
*Proposed:* every run resolves its configuration once at dispatch and records (a) the content digest
of the whole definition tree, (b) the fully-resolved, post-inheritance agent/automation/runner/policy
document it actually used, and (c) the digests of every skill and gate offered. A run's configuration
is immutable for its lifetime; a definition change mid-run never affects the running run. `sf run show
--config <run>` reprints the exact resolved configuration from the ledger without reading the
current definition tree.

**FR-2.12 (P0) — IMPORTANT — Definition activation lifecycle and rollback.**
*Gap:* FR-2.3 says a failing definition never partially applies and "the running factory continues on
its last valid definition" — which implies an activation event that is never specified. Unstated:
whether the coordinator watches the tree or requires an explicit apply; how a definition revision is
selected (working tree, committed HEAD, a tag); whether uncommitted local edits can take effect;
latency between commit and activation; and how to roll back to a previous revision without a revert
commit.
*Proposed:* activation is an explicit, ledger-recorded event carrying the source revision, the tree
digest, the actor, and the count of in-flight runs pinned to the prior revision. `sf apply` activates;
`sf apply --revision <rev>` rolls back; local uncommitted edits activate only with `--allow-dirty` and
are recorded as such. The dashboard shows the active revision and how many runs are pinned to older
ones.

**FR-2.13 (P1) — IMPORTANT — Concurrent definition modification.**
*Gap:* two operators may edit and apply concurrently. Git resolves file conflicts; nothing resolves
*activation* races, and nothing prevents an apply from a stale working copy silently reverting another
operator's just-activated change.
*Proposed:* activation is compare-and-swap on the currently active revision. `sf apply` refuses when
the active revision is not the parent of the revision being applied, unless `--force` is passed, and
records the override. The same fencing applies to the API.

### 1.3 Agents, automations, runners, scorers

**FR-3.13 (P1) — IMPORTANT — Deletion and tombstoning of definition resources.**
*Gap:* FR-2.4 makes dangling *references* validation errors, but nothing covers deleting a resource
that **history** references. Delete `agents/builder/` and every historical run, gate result, scorer
result and ledger entry naming it becomes unresolvable, violating INV-8. There is also no defined
behaviour for deleting a resource with runs in flight.
*Proposed:* deleting a resource with in-flight runs is refused unless the runs are cancelled first.
Deleted resources leave a tombstone in the state directory carrying the last resolved configuration
and the revision range over which they existed, so historical runs remain fully explainable.
`sf lint` warns when a tombstoned name is reused for a new resource of a different kind.

**FR-3.14 (P0) — BLOCKING — Long-lived conversation state.**
*Gap:* FR-3.6 makes the Conductor the sole holder of the requester conversation and FR-3.7 requires
that revisions **continue the existing conversation** with the same specialist. Across a work item
spanning triage, design, three build passes, review and verify, that conversation exceeds any model's
window. Nothing specifies compaction, summarisation, persistence, or resumption after a coordinator
restart. FR-9.5's budgeting governs the Awareness Pack, not accumulated conversation. This is the
most likely first production failure and it is invisible in the current text.
*Proposed:* conversation state is a first-class, persisted, budgeted object per (work item, agent).
It declares a maximum size and a compaction strategy that preserves decisions, open questions,
human corrections and cited evidence references while shedding tool-call detail. Compaction events are
ledger-recorded with before/after digests, and the compacted material remains retrievable through a
tool (FR-9.6). Resumption after restart reconstructs conversation state from the ledger. Compaction
must never silently drop a human instruction or an unanswered question.

### 1.4 Work items

**FR-4.11 (P1) — IMPORTANT — Duplicate linking, merge, and split.**
*Gap:* FR-4.5 and FR-18.7 give idempotency for *redelivery of the same event*. They do nothing for the
common case: the same defect reported twice through two sources, or one request that must become three
changes. There is no `duplicate-of`, no merge, and no split.
*Proposed:* a work item may be marked a duplicate of another (transferring source contexts so replies
still reach both origins), and may be split into child items that inherit spec slice and precedent and
link back to the parent. Both operations are ledger-recorded, reversible, and available from CLI,
dashboard, and the originating tool (consistent with FR-4.8).

**FR-4.12 (P0) — BLOCKING — Scheduling, prioritisation, and fairness.**
*Gap:* NFR-3.1 requires ≥10 concurrent runs and FR-3.10 (P1) allows a per-agent concurrency cap, but
**nothing decides what runs next**. With a backlog larger than capacity there is no priority field, no
ordering rule, no fairness across repositories or intake sources, no starvation guarantee for old
items, no preemption, and no admission control. Two implementers would build a FIFO queue and a
priority heap and get incomparably different factories.
*Proposed:* work items carry a declared priority derived from work class (FR-4.14) and source, with a
documented default ordering (priority, then age, with an anti-starvation ageing term). The scheduler
declares its policy in `policy/`, enforces per-repository and per-source fairness shares, exposes queue
depth and oldest-waiting age as metrics (FR-15.3), and refuses admission with a recorded reason when
queue depth exceeds a declared bound. Human-initiated work (`sf work`, a direct mention) is a distinct
class that must not be starved by automation-initiated work.

**FR-4.13 (P1) — IMPORTANT — Ageing and reaping of blocked items.**
*Gap:* FR-16.4 parks an unanswered checkpoint as `BLOCKED: awaiting_human` "rather than holding a run
open". Nothing then reaps it. Over months the board fills with items blocked on people who have left,
credentials that were never added, and external dependencies that resolved silently.
*Proposed:* each blocker type declares a maximum age and an action on expiry (re-notify, escalate to a
declared fallback approver, or auto-cancel with a recorded reason). Blocked items are re-evaluated on a
declared cadence — `awaiting_ci` re-checks CI, `missing_credential` re-checks the secret, and
`external_dependency` re-checks the dependency — and unblock themselves when the condition clears.

**FR-4.14 (P1) — IMPORTANT — Declared work-class taxonomy.**
*Gap:* "task class" or "work class" carries load in FR-7.7 (split by task class), FR-11.5
(de-escalation per task class), FR-16.6 (autonomy level per work class), FR-13.3 (`regression-proven`
"for defect work"), and §11.1 (≥40% "for defect-class work") — and is never defined. How the factory
knows a work item is defect-class is unspecified, so the single strongest gate in the document
(FR-13.3) has no trigger condition.
*Proposed:* the factory declares a work-class taxonomy in `policy/` with, at minimum, `defect`,
`feature`, `refactor`, `migration`, `chore`, and `investigation`. Classification is assigned at triage
by a deterministic rule set with a model fallback, is recorded with its basis, is correctable by a
human, and is the documented trigger for class-conditional gates, autonomy levels, tier ladders, and
metrics. Misclassification is a scoreable outcome.

### 1.5 Spec units

**FR-5.13 (P1) — IMPORTANT — Concurrent Spec Delta conflicts.**
*Gap:* FR-5.3 routes all spec change through deltas, and FR-5.5 detects `contradicted` state between
*active units*. Nothing detects two **open, unmerged deltas** modifying the same unit, or a delta
authored against a unit revision that has since changed.
*Proposed:* a delta records the unit revision it was authored against; a delta whose base revision is
stale is flagged and must be rebased before approval; two open deltas touching one unit are surfaced
to both authors and to the approver, and the `spec-agreement` gate treats an unrebased delta as a
blocking finding.

**FR-5.14 (P1) — IMPORTANT — Spec ownership and approver routing.**
*Gap:* FR-16.1 requires "a human to approve the Spec Delta" and FR-16.3 records the deciding identity,
but there is no ownership model on spec units. Anyone may approve anything, including the person whose
agent proposed it.
*Proposed:* spec units and spec areas declare owners (as principals — see FR-26.2). A delta routes to
the owners of every unit it touches; approval by a non-owner requires an override that is recorded;
self-approval of a delta whose provenance is the approver's own run is refused by default and
configurable in `policy/`. Unowned areas fail lint, mirroring FR-7.13 for skills.

**FR-5.15 (P2) — NICE — Unit merge, split, and bulk re-anchoring.**
*Gap:* FR-5.11 retires units and `supersedes` chains them, but there is no merge of two units into one
or split of one into several, and no bulk operation for the case that actually happens — a large
refactor that orphans hundreds of `implements` anchors at once (FR-5.5 `orphaned`).
*Proposed:* merge and split are delta operations preserving both directions of the supersession graph.
`sf spec reanchor` proposes anchor updates in bulk from rename/move detection, presented as one
reviewable delta rather than hundreds of drift reports.

### 1.6 Memory

**FR-6.16 (P1) — IMPORTANT — Hard erasure of memories.**
*Gap:* FR-6.1 makes Archive terminal and audit-retained; FR-6.12 archives on budget breach. There is no
path by which a memory is actually *deleted*. A memory containing a customer name, a credential
fragment, or a legally-privileged detail is permanent by design.
*Proposed:* a hard-erase operation removes memory content while leaving an id-preserving tombstone that
records the erasure actor, reason, and time, so provenance chains (FR-6.6) stay traversable and
derived memories are demoted per FR-6.5's transitive invalidation. Erasure is authorised (FR-26.1),
ledger-recorded, and propagates to any pack cache or index containing the content. See FR-27.3.

**FR-6.17 (P1) — IMPORTANT — Memory store migration, compaction, and repair.**
*Gap:* FR-6.14 mandates an embedded file-backed store and FR-6.15 mandates export/import, but there is
no on-disk format version, no migration when it changes, no compaction, and no integrity check or
repair when the store is corrupted by an unclean shutdown.
*Proposed:* the store declares a format version; `sf memory migrate` performs versioned, backed-up,
dry-runnable migration; `sf memory verify` detects corruption and reports what is recoverable; and a
corrupt store degrades to read-only with a recorded reason rather than failing runs (PR-9).

**FR-6.18 (P1) — IMPORTANT — Concurrent writes to a shared scope.**
*Gap:* FR-6.10 makes extraction asynchronous and post-run; NFR-3.1 allows ten concurrent runs. Nothing
specifies what happens when several extractions write into one `repository` or `factory` scope at once:
whether writes are serialised, whether budget enforcement (FR-6.12) is atomic, whether two runs can
nominate the identical claim twice, or how contradiction detection (FR-6.5) behaves mid-write.
*Proposed:* writes into a shared scope are serialised through a single writer with an idempotency key
derived from (content digest, scope, kind), so identical nominations converge rather than duplicating.
Budget enforcement and policing passes take a scope-level lock; readers are never blocked and never
observe a partial policing pass.

**FR-6.19 (P0) — IMPORTANT — Normative retrieval and lifecycle algorithms.**
*Gap:* FR-6.7 mandates a pipeline of "freshness decay → relevance rank → diversity cap", FR-6.5
detects duplication "by similarity", FR-6.8 gives each kind "a decay function", and FR-6.12 archives by
"lowest value density". None of these five is defined: no ranking function, no similarity metric or
threshold, no decay curves, no cap value, no value-density formula, and no default budgets per scope.
Two implementers build two unrelated memory systems and neither can be said to violate the PRD.
*Proposed:* specify each as a named, defaulted, overridable function in `memory/policy.yaml`, with the
reference implementation's defaults published in the normative defaults artifact (NFR-11.1) and its
behaviour pinned by golden tests. Retrieval must be deterministic given identical inputs, to satisfy
FR-9.1.

### 1.7 Skills

**FR-7.14 (P0) — BLOCKING — Skill-bundled executable code.**
*Gap:* FR-7.1 permits a skill directory to contain "scripts and templates". FR-7.11 correctly forbids a
skill from *granting* access — but a bundled script is code that **executes** inside the workspace, and
nothing subjects it to the Tool Registry's typed declaration (FR-10.1), side-effect classes (FR-10.2),
grant model (FR-10.7), blast-radius contract (FR-12.1), or supply-chain pinning (FR-17.9). A skill
proposed by the self-improvement loop (FR-7.12, FR-14.3) could therefore introduce executable code on a
review path tuned for prose.
*Proposed:* bundled scripts are declared, typed tools subject to every Tool Registry rule; they execute
only under the run's blast-radius contract; their content digests are pinned in the skill frontmatter
and verified at load; a skill body may not invoke an undeclared script; and any proposal that adds or
modifies a bundled script is flagged for stricter review in the same way FR-14.7 flags self-referential
changes.

**FR-7.15 (P2) — NICE — Cross-factory skill distribution.**
*Gap:* FR-2.9 (P1) allows a definition to extend a base definition, which is the closest thing to
sharing. There is no way to publish, import, or version a skill independently of a whole definition
tree, and no story for how the ecosystem PR-1 implies would actually share procedures.
*Proposed:* skills may be imported from a declared source by name and version with pinned digests; the
import carries the origin's eval set and its recorded eval results; imported skills enter at `trial`
and must earn `active` locally (FR-7.4) rather than inheriting the origin's status.

**FR-7.16 (P0) — IMPORTANT — Skill lifecycle constants and selection measurement.**
*Gap:* FR-7.8 retires a skill "not selected in N eligible runs" or "failing for M consecutive windows"
— N, M, and window length are never given. FR-7.6 merges skills whose triggers "overlap beyond a
threshold" and whose bodies are "substantially similar" — no metric, no threshold. FR-7.9 requires
selection **recall** ("should have been selected") without saying how a system observes what should
have happened, which is the hard part and the only part that matters.
*Proposed:* publish defaults for N, M, and window; define overlap as a named similarity measure over
descriptions plus observed co-selection rate, with a stated threshold; and define recall operationally
— for example, a skill is retrospectively judged as "should have been selected" when a scorer or a
human review of a failed run cites the procedure that skill encodes. Without that operational
definition FR-7.9 cannot be implemented at all.

### 1.8 Runners and runs

**FR-8.11 (P1) — IMPORTANT — Runner image lifecycle.**
*Gap:* FR-17.9 pins runner images by digest and fails lint on unpinned references — which is right, and
which creates a gap the PRD never closes: a pinned image is a frozen image. Nothing covers who rebuilds
it, on what cadence, how a security patch reaches it, how the pinned digest is updated and reviewed, or
how stale cached layers and old images are reclaimed from disk.
*Proposed:* runner images declare a source (a build definition or an upstream reference plus digest)
and a maximum age; `sf lint` warns past that age; `sf runner refresh` proposes digest updates as a
reviewable definition change carrying the vulnerability delta; and image and layer reclamation is part
of the storage policy in FR-8.13.

**FR-8.12 (P0) — BLOCKING — Run leases, heartbeats, and orphan reaping.**
*Gap:* NFR-1.2 covers *coordinator* restart. Nothing covers the far more common inverse: the executor
dies. An `ssh-worker` loses its network, a container is OOM-killed by the host, a laptop sleeps
mid-run. The coordination plane holds a run in `running` forever, its workspace is never cleaned, its
budget is never released, its work item never advances, and its concurrency slot is consumed
permanently. There is no heartbeat, no lease, no timeout distinct from the agent's wall-clock budget
(FR-3.11), and no reaper.
*Proposed:* every run holds a time-bounded lease renewed by a heartbeat from the execution plane at a
declared interval. A lease that expires marks the run `executor_lost` — a typed terminal status with a
partial evidence bundle (NFR-1.4) — releases its concurrency slot and budget, schedules its workspace
for reclamation, and returns a decision point to the Conductor. The reaper's actions are
ledger-recorded. Heartbeat interval, lease duration, and grace period are declared with defaults.

**FR-8.13 (P0) — BLOCKING — Workspace garbage collection and disk reclamation.**
*Gap:* FR-8.4 gives every run "a dedicated worktree or checkout, never a shared mutable directory" and
FR-20.4 puts state under `.factory/`. Nothing ever deletes any of it. Ten concurrent runs against a
large repository, each with its own checkout, plus evidence bundles, transcripts, recordings and
indexes, will exhaust a workstation disk in days. FR-15.10's retention covers *artifact classes*, not
workspaces, and FR-8.10's per-run disk ceiling bounds one run, not the accumulation.
*Proposed:* workspaces are reclaimed on run completion by default, with a declared retention for failed
runs to support debugging, and a declared total ceiling for the state root. Reclamation is a recorded,
auditable pass (mirroring FR-15.10) that reports what it removed and how much it freed. Evidence and
ledger data are never reclaimed by this pass — only workspaces, caches, and images. `sf gc --dry-run`
reports reclaimable bytes by class.

**FR-17.14 (P0) — IMPORTANT — Cancellation, pause, and emergency-stop semantics.**
*Gap:* FR-4.8 lets any human stop a work item, FR-15.7 lets a human pause or stop a live run, and
FR-16.7 halts every run in a factory and "revokes in-flight external actions where revocable". The
*semantics* are absent. Unstated: whether a stop interrupts mid-tool-call or at the next turn boundary;
what happens to a partially applied patch; whether checkpoints are rolled back or preserved; whether
mounted secrets (FR-12.8) are destroyed immediately; what "revocable" means concretely; what happens to
an external action already recorded-before-execution (NFR-1.2) whose outcome is unknown; and what the
resume path is after a pause.
*Proposed:* define three levels — pause (turn-boundary, state preserved, budget clock stopped, secrets
retained for a declared maximum), cancel (turn-boundary, workspace preserved for a declared period,
secrets destroyed, partial evidence sealed), and abort (immediate, best-effort, workspace quarantined
for inspection). For each, specify the disposition of in-flight tool calls, external actions of unknown
outcome (recorded as `outcome_unknown` and surfaced for human reconciliation), checkpoints, and
secrets. Resume-after-pause is defined or explicitly unsupported.

**FR-19.10 (P1) — IMPORTANT — Duplicate-work detection.**
*Gap:* FR-19.5 is admirably honest that picking work up "does not claim, lock, or pause it" and asks
the docs to "warn plainly about duplicate work". A warning is not a mechanism. Nothing detects that a
factory run and an external agent are editing the same work item, and nothing reconciles two branches
handed back for one item (FR-19.6).
*Proposed:* the tool surface exposes an advisory claim with a declared expiry that is visible to the
Conductor and the dashboard; the Conductor must not dispatch a new run on an item with a live claim
without recording an override; and handing back a second branch for one work item produces a recorded
reconciliation decision point rather than a silent overwrite.

### 1.9 Evidence and the ledger

**FR-15.11 (P0) — BLOCKING — Retention must not break claim resolution.**
*Gap:* INV-6 and FR-22.6 require every claim in a stage summary to resolve to an Evidence row.
FR-15.10 lets retention delete evidence per class. FR-22.5 makes evidence immutable once sealed. These
three cannot all hold: the first retention pass over a completed work item's recordings breaks INV-6
for every summary referencing them, and `evidence-complete` becomes unverifiable retroactively — on
exactly the old changes an auditor would want to check.
*Proposed:* retention replaces expired evidence with an immutable stub carrying the original digest,
class, size, and expiry record, so claims still resolve to a *provably-once-existing* artifact and the
distinction between "never had evidence" and "evidence expired under policy" is machine-checkable.
Retention must refuse to expire evidence attached to an open change or an unresolved work item, and
must honour legal hold (FR-27.3). `sf ledger verify` checks stub integrity.

**FR-15.12 (P0) — BLOCKING — Ledger segmentation, snapshots, and archival.**
*Gap:* FR-15.1 makes the ledger append-only and hash-chained; FR-4.10 and INV-8 make it the sole source
of truth from which all state is reconstructible; NFR-3.2 asserts "Ledger and memory growth are bounded
by retention and consolidation policy (FR-15.10, FR-6.12)" — but FR-15.10 governs artifact classes, not
ledger entries, and FR-6.12 governs memory. **No mechanism bounds the ledger, and any mechanism that
truncated it would destroy the reconstruction invariant.** Every tool call is a ledger entry (FR-10.10);
a busy factory writes millions per month. Rebuilding derived state by replaying the entire history from
genesis also gets linearly slower forever.
*Proposed:* the ledger is written in sealed segments. A snapshot is a signed, verifiable materialisation
of all derived state as of a segment boundary, whose digest is chained into the ledger. Older segments
may be moved to archival storage or pruned per policy **only** when a snapshot covering them exists;
reconstruction then replays from the most recent snapshot. `sf ledger verify` verifies chain continuity
across sealed, archived and pruned segments and reports the earliest reconstructible point. Segment
size, snapshot cadence, and default archival policy are declared.

**FR-15.13 (P1) — IMPORTANT — Chain head anchoring.**
*Gap:* INV-5 chains `prevHash` to `hash`, which detects *edits* by an attacker who cannot recompute the
chain — but anyone with write access to the state directory can rewrite the entire ledger and recompute
every hash. Nothing anchors the head. "Tamper-evident" (FR-15.1) is therefore only true against
accidental corruption, not against the insider threat FR-17 otherwise takes seriously, and `sf ledger
verify` will happily verify a fabricated history.
*Proposed:* the chain head is periodically signed with a key held outside the state directory, and/or
published to an append-only external location where the operator chooses to configure one; verification
reports the last anchored point and the number of unanchored entries after it. Local mode must have a
usable default (a signing key outside `.factory/`) so PR-2 still holds.

**FR-15.14 (P1) — IMPORTANT — Time source and clock skew.**
*Gap:* every ledger entry has `ts` (§9.1), transitions record timestamps (FR-4.3), budgets are
wall-clock (FR-3.11), retention and decay are time-based, cron is UTC (FR-18.3), and leases would be
(FR-8.12). Nothing specifies the time source, or what happens when the execution plane's clock differs
from the coordination plane's — which it will, across `ssh-worker` and `cloud` executors, and severely
on a laptop that has slept.
*Proposed:* the coordination plane is the authoritative time source; execution-plane timestamps are
recorded as reported *and* as received, with the skew recorded. Ledger ordering is by monotonic
sequence (`seq`), never by timestamp. Skew beyond a declared bound is a recorded warning and, past a
second bound, refuses to seal a run. Wall-clock budgets are measured against monotonic clocks, not
wall time, so suspend/resume does not consume budget.

**FR-22.8 (P1) — IMPORTANT — Evidence storage tiering and size control.**
*Gap:* FR-22.2 and FR-22.3 make screen and browser recordings first-class evidence. Recordings are
large. Nothing declares a per-class size cap, a compression or transcoding requirement, deduplication
by digest across runs (the same test output is attached to many runs), an external blob store option,
or what happens when a recording exceeds the cap mid-capture.
*Proposed:* per-class size caps with declared defaults; capture that would exceed a cap is truncated
*explicitly* and marked as such (consistent with FR-22.7); evidence is content-addressed and stored
once regardless of how many runs reference it; an optional external store is an adapter behind the same
addressable interface (mirroring FR-6.14), with local file-backed storage as the default so PR-2 holds.

**FR-22.9 (P1) — IMPORTANT — Evidence integrity verification.**
*Gap:* FR-22.5 makes evidence immutable once sealed and addressable by digest, but nothing verifies
that on read, and nothing defines behaviour when an artifact is missing or its digest mismatches —
which is exactly what a reviewer trusting an evidence bundle needs to know.
*Proposed:* evidence digests are verified on retrieval; a mismatch or absence is a typed error surfaced
in the dashboard and in `evidence-complete`, never a silent empty render. `sf audit --evidence` verifies
a work item's whole bundle and reports missing, stubbed (FR-15.11), and corrupted artifacts separately.

**FR-10.12 (P2) — NICE — Tool signature versioning.**
*Gap:* FR-10.1 requires typed tools; nothing versions those types. A recorded run's tool calls
(FR-10.10) become uninterpretable when a signature changes, and FR-11.11's replay against recorded
responses breaks silently.
*Proposed:* tool declarations carry a version; recorded calls record the version used; replay refuses
to substitute a different version without an explicit flag; deprecating a signature is a reviewed
definition change with a declared support window.

---

## 2. Operational gaps

The PRD specifies a runtime in detail and an *operated system* not at all. There is no requirement
anywhere covering installation, upgrade, backup, restore, disk exhaustion, or alerting. §12's
milestones ship features; none of them ships an operable product.

### 2.1 Installation and upgrade (new family FR-23)

**FR-23.1 (P0) — BLOCKING — Installation and prerequisites.**
*Gap:* the word "install" appears once in the PRD, describing the problem statement. NFR-4.1 promises
"time to first useful run … under 10 minutes, including `sf init`" — but `sf init` presupposes an
installed `sf`, and nothing says how it got there. Also absent: minimum supported versions of the
version-control client, the container runtime, and the platform; behaviour when a prerequisite is
missing or too old; whether installation requires network (contradicting PR-2 if it does); and how an
air-gapped operator installs at all.
*Proposed:* declare supported installation methods including a fully offline one (a self-contained
archive with pinned, verifiable digests); enumerate prerequisites with minimum versions and the exact
error emitted when each is missing (per NFR-4.2); and provide `sf doctor` (see NFR-10.1) which verifies
prerequisites, sandbox availability (FR-8.7), model endpoint reachability, and state-directory
writability, and prints a remediation for each failure.

**FR-23.2 (P0) — BLOCKING — Upgrade and version compatibility.**
*Gap:* nothing describes upgrading the software. A deployment has a coordinator, one or more executors,
possibly remote `ssh-worker` hosts, a dashboard, and an API — all of which can hold different versions.
Nothing states which combinations are supported, what happens when they disagree, whether an upgrade
can be rolled back, or whether in-flight work survives.
*Proposed:* declare a version-compatibility policy (a supported skew window between coordinator and
executor, and between API and client), enforce it at handshake with a typed refusal naming both
versions, and require that upgrading the coordinator preserves in-flight work items by draining or by
pinning them to a compatible executor. Every release states its minimum compatible predecessor.

**FR-23.3 (P1) — IMPORTANT — Partial and rolling upgrade.**
*Gap:* the Hybrid and Cloud topologies (§6.3) imply pools of workers that cannot be upgraded
atomically. Nothing defines behaviour during the window: whether new runs may start on old executors,
what happens to a run whose executor is drained mid-flight, and how FR-20.5's parity conformance holds
when two executor versions are live at once.
*Proposed:* executors support drain (finish current runs, accept no new ones) as a first-class,
ledger-recorded state; the scheduler (FR-4.12) excludes draining executors; and a run may never be
migrated mid-flight between executor versions — it completes or is reaped (FR-8.12).

**FR-23.4 (P0) — BLOCKING — On-disk state migration.**
*Gap:* FR-1.6 versions the *definition* schema and rejects unknown versions. Nothing versions the
**state**: the ledger format, the memory store, indexes, evidence layout. When any of those formats
changes in v1.1, every existing installation's history is at risk, and there is no migration, no
dry-run, no backup-before-migrate, and no downgrade story. Because FR-4.10 makes the ledger the sole
source of truth, a botched state migration is total data loss.
*Proposed:* every persisted store declares a format version; `sf migrate` performs versioned migration
with a mandatory verified backup, a `--dry-run` that reports what would change, and per-store
verification after; the daemon refuses to start against a newer state version than it understands, with
a message naming both versions; and downgrade is either supported or explicitly documented as
unsupported per store. Migration is ledger-recorded.

**FR-23.5 (P1) — IMPORTANT — Definition schema migration tooling.**
*Gap:* FR-1.6 rejects a definition whose schema version is unknown, "with a message naming the
supported set". For an operator with 40 agents and 200 skills, a rejection is not a migration path.
*Proposed:* `sf migrate definition --to <version>` applies mechanical transformations as a reviewable
diff in the operator's own repository, reports what it could not migrate automatically, and emits
deprecation warnings for one full version before removal. Every schema version ships with a migration
guide (NFR-12.7).

**FR-20.11 (P1) — IMPORTANT — `sf init` against existing state.**
*Gap:* FR-20.1 describes `sf init` on a clean repository. Undefined: `sf init` where `factory.yaml`
already exists, where a partially-written definition exists from an interrupted init, where `.factory/`
exists from an older version, or where the repository already belongs to another factory (which FR-1.4
lints but does not prevent at init).
*Proposed:* `sf init` detects each case and either refuses with a remediation, adopts the existing
definition after validation, or offers a migration (FR-23.5). It is idempotent and never destroys
existing state; a partially-written definition is completed or rolled back atomically (consistent with
FR-2.3).

### 2.2 Data safety (new family NFR-9)

**NFR-9.1 (P0) — BLOCKING — Backup.**
*Gap:* the word "backup" does not appear in the PRD. FR-15.2 promises that losing "the database" costs
time, not history — because the ledger can rebuild it — but says nothing about losing the ledger, which
is unrecoverable and takes memory, evidence, and every work item with it. FR-6.15's memory export and
FR-21.2's `export` are portability features, not a backup story: neither is scheduled, neither is
verified, and neither is defined as complete.
*Proposed:* define the complete backup set (ledger segments, snapshots, memory store, evidence,
definition revision pointer, secret *references*), require a consistent point-in-time capture that does
not require stopping the factory, provide `sf backup` and `sf restore` with post-restore verification
(`sf ledger verify` plus a state-reconstruction check), state what is deliberately excluded (secret
values, workspaces, caches), and document a recommended schedule per topology.

**NFR-9.2 (P0) — BLOCKING — Disaster recovery.**
*Gap:* there is no recovery objective, no rebuild procedure, and no defined behaviour for partial loss.
FR-15.1's `sf ledger verify` *detects* a broken chain; nothing says what to do next. A laptop losing
`.factory/` is a total loss of the organisational memory the whole design exists to accumulate (P1).
*Proposed:* declare RPO and RTO targets per topology; specify the recovery procedure for each failure
class (derived-state loss → rebuild from ledger; ledger segment loss → restore from backup and report
the unrecoverable window; total loss → restore from backup); require that a broken chain is
quarantined rather than truncated, with the verifiable prefix preserved and the divergence point
reported; and require a documented recovery drill as part of the release process.

**NFR-9.3 (P0) — BLOCKING — Disk pressure.**
*Gap:* FR-8.10 terminates a *run* that exceeds its disk ceiling and FR-20.7 requires resource courtesy,
but nothing protects the **coordination plane**. When the host disk fills, the ledger cannot append —
and an append-only hash-chained log that fails a partial write is the single most dangerous corruption
mode in this design. Nothing reserves headroom, nothing sheds load, nothing warns in advance.
*Proposed:* the state root declares a reserved headroom and two thresholds. At the soft threshold the
factory warns, surfaces a needs-attention flag, and runs reclamation (FR-8.13). At the hard threshold
it refuses to start new runs with a typed error, completes or reaps in-flight runs, and continues to
accept ledger appends from the reserve. Ledger writes are atomic — a torn write is never possible —
and a failed append halts the factory rather than continuing unrecorded (PR-3).

**NFR-9.4 (P1) — IMPORTANT — Single writer for the state root.**
*Gap:* nothing prevents two coordinator processes from opening one `.factory/`, which is the default
outcome of a user running `sf work` in two terminals, or of a stale process surviving a restart. With an
append-only chained ledger, two writers produce a permanently broken chain.
*Proposed:* the state root is guarded by an exclusive lock carrying the holder's process identity, host,
and start time; a second process fails fast with a message naming the holder; a stale lock is detected
and reclaimed by a documented rule; and read-only commands (`sf metrics`, `sf dash`, `sf ledger verify`)
work concurrently without the lock. Network filesystems are explicitly supported or explicitly refused.

### 2.3 Running it (NFR-9 continued, NFR-10)

**NFR-9.5 (P1) — IMPORTANT — Health, SLOs, and alerting.**
*Gap:* FR-18.9 makes *adapter* health visible and FR-17.11 raises a security event on violations. There
is no health model for the factory itself: no liveness or readiness surface, no service objectives, no
alert routing, and no way for an on-call engineer to be told that the queue has been stalled for six
hours or that every run has failed since a definition change. §2.2 names on-call follow-ups as intake
*for* the factory; nobody is on call *for* the factory.
*Proposed:* expose health and readiness endpoints and `sf status`; declare alertable conditions with
defaults (queue depth or oldest-waiting age beyond bound, run failure rate above threshold, executor
lease expiries, adapter unhealthy beyond a duration, disk soft threshold, budget cap approached,
ledger verification failure, scorer backlog growth); route alerts through declared notification routes
(FR-16.8); and make every alertable condition visible in the dashboard's needs-attention view (FR-15.6).

**NFR-9.6 (P1) — IMPORTANT — Logging and rotation.**
*Gap:* the ledger is specified precisely; ordinary operational logs are not mentioned at all. No format,
no levels, no rotation, no size cap, no retention, and no statement of what may not be logged. Run
transcripts are separately unbounded — a long agentic run can produce enormous output, and FR-10.9's
"truncation … explicit" applies to tool results, not to log volume.
*Proposed:* structured logging with declared levels and a documented schema; rotation with size and age
caps and a total cap for the log directory; redaction applied to logs as an output boundary (FR-17.3);
and a declared per-run transcript size cap whose exceedance truncates explicitly and records the fact.

**NFR-9.7 (P1) — IMPORTANT — Incident response runbook.**
*Gap:* the PRD requires detection in several places — FR-17.11 security events, FR-6.5 poisoning,
FR-12.5 violations, FR-13.8 untrustworthy scorers — and specifies the response for none of them.
*Proposed:* a runbook requirement covering at minimum: leaked credential (rotate, revoke, identify every
run that held it via FR-17.10, purge from transcripts and evidence, re-run affected audits); poisoned
memory (quarantine the subtree, force re-derivation, report affected runs); compromised definition
(revert revision via FR-2.12, identify runs pinned to it via FR-2.11); and untrusted-input incident
(identify the injection source, list the runs that ingested it). Each has a `sf` command path and a
ledger-recorded closure.

**NFR-10.1 (P1) — IMPORTANT — Diagnostics and support bundle.**
*Gap:* FR-21.2's command families include no diagnostic command. For an open-source project, the single
highest-leverage support artifact is a one-command redacted bundle, and it is not required.
*Proposed:* `sf doctor` verifies the environment and prints remediation per finding; `sf support-bundle`
produces a redacted archive (versions, resolved configuration with secret names only, recent ledger
entries, health, gate outcomes, timings, last errors) with an explicit manifest of what it contains and
an explicit statement of what it excludes, so an operator can hand it to a maintainer safely.

### 2.4 Cost, quota, and multi-user concurrency (new family FR-29, FR-26)

**FR-29.1 (P0) — BLOCKING — Aggregate spend caps.**
*Gap:* FR-3.11 caps a single agent's run (wall-clock, tool calls, tokens, cost) and R-5 names cost
blowout as a High risk mitigated by exactly those per-run bounds. Nothing bounds the **sum**. One
automation matching a broad filter, one signal storm (see FR-18.16), or one Conductor loop dispatching
specialists repeatedly stays inside every per-run budget while spending without limit. There is no
per-work-item, per-day, per-factory, or per-organisation cap, and no defined behaviour on approach.
*Proposed:* declare caps at work-item, agent, factory, and period scope, with soft and hard thresholds.
At the soft threshold the factory notifies and restricts escalation (FR-11.4) to human-approved cases;
at the hard threshold it refuses new dispatches with a typed error, parks affected items as
`BLOCKED: budget_exceeded` (the blocker type already exists in FR-4.7), and continues only
human-initiated work. Spend against every cap is a dashboard metric with a projection to period end.

**FR-29.2 (P0) — BLOCKING — Provider rate-limit governance.**
*Gap:* FR-11.10 handles being rate limited *after the fact*, as a degradation. Nothing prevents it:
there is no client-side concurrency limit per provider, no token bucket, no backoff or jitter policy,
no retry budget, and no fairness rule when ten runs contend for one endpoint — which is the normal case
for the local-model configuration PR-2 makes the reference. A local endpoint saturated by ten
concurrent runs degrades every run simultaneously, and nothing in NFR-3.1 accounts for that.
*Proposed:* each provider declares maximum concurrency, requests-per-interval, and token-per-interval
limits; the harness enforces them with a documented queueing and backoff policy (including jitter and a
retry budget that counts against FR-3.11); contention is fair across agents by a declared rule; and
waiting time is recorded separately from inference time so latency metrics (FR-15.3) are not corrupted
by queueing.

**FR-29.3 (P2) — NICE — Cost attribution.**
*Gap:* FR-15.3 gives cost per change and FR-11.12 gives usage per run per model per stage. There is no
attribution to a team, repository, or work class, so an organisation cannot answer which part of the
business the spend served — the natural next question after §11.1's cost metric.
*Proposed:* every run records an attribution key derived from repository, work class, and requesting
principal; the metrics API aggregates cost by any of them; export is available for external accounting.

**FR-21.8 (P1) — IMPORTANT — API limits.**
*Gap:* FR-21.4 specifies an API mirroring the CLI and FR-15.8 puts the dashboard behind authentication
in hosted mode, but there is no rate limiting, no pagination contract, no maximum request or response
size, and no timeout — on an API that can list an unbounded ledger and trigger runs that cost money.
*Proposed:* declare pagination with stable cursors for every list endpoint, maximum page and payload
sizes, per-principal rate limits with typed `429`-class errors carrying a retry hint (extending
FR-21.5's catalogue), and per-endpoint timeouts. Local loopback mode may relax limits but must not
remove pagination.

**FR-21.9 (P1) — IMPORTANT — Interface versioning and output stability.**
*Gap:* FR-21.4 says the API is "versioned" without stating the scheme, the support window, or the
deprecation process. FR-21.3's `--json` output is the machine interface every operator will script
against, and nothing declares it stable or gives it a version.
*Proposed:* declare semantic versioning for the API and the CLI, a minimum support window for a
deprecated API version, a deprecation signal in responses, and a schema version embedded in every
`--json` payload. Breaking either without a major version is a defect.

---

## 3. Missing requirement families

Whole areas a system of this shape needs that the PRD never opens.

### 3.1 Repository reality (new family FR-24)

The PRD's model of a repository is: it has code, a module graph, a validation command, and an index.
Real repositories are not like that, and §10 disposes of the difference in one assumption sentence —
"Repositories have some form of runnable validation, or the factory helps create it" — that no
requirement implements.

**FR-24.1 (P0) — BLOCKING — Onboarding a repository with no validation.**
*Gap:* the entire assurance layer presumes runnable tests. `tests-pass`, `regression-proven`,
`coverage-of-criteria` (FR-13.2), the Prover role, three-way agreement's `unverified` state (FR-5.5),
and AN-1's whole narrative all collapse on a repository with no test suite — which is the majority of
the legacy repositories where this product's value is highest. FR-5.12 provides a spec on-ramp
(`sf spec induct`); there is no test on-ramp.
*Proposed:* onboarding detects the presence, shape, and runnability of validation and records a
capability profile per repository. Where validation is absent, the factory (a) declares which gates are
degraded and how, in the pack and in the evidence bundle, rather than silently passing them (PR-9), and
(b) offers a bootstrap path that proposes a minimal validation harness plus characterisation tests for
the change surface as a reviewable first work item. `regression-proven` is never satisfiable by
degradation — a defect fix in an unvalidated repository must state that explicitly and route to a human.

**FR-24.2 (P1) — IMPORTANT — Monorepo support.**
*Gap:* "monorepo" appears once, meaning a monorepo *of factory definitions* (FR-1.5). A monorepo of
*code* — many packages, several languages, one history, per-package ownership and validation — is
unaddressed. FR-1.2 lets a factory declare several repositories but not several projects inside one.
Every mechanism that scopes by repository (memory scope `repository` in FR-6.9, conventions, hazards,
runners) is at the wrong granularity, and running the whole suite for a one-package change is
prohibitive.
*Proposed:* a repository may declare projects with their own roots, owners, toolchains, validation
commands, and runners. Change surface (FR-9.13), affected-target selection, memory scope, conventions,
and gate selection all resolve at project granularity. Partial or sparse checkout is supported where
the version-control client allows, and the pack's Terrain section is bounded to affected projects.

**FR-24.3 (P0) — BLOCKING — Index lifecycle.**
*Gap:* the index is load-bearing — FR-9.3's deterministic-first pack, FR-10.3's symbol and graph tools,
FR-5.8's anchor digests, AN-2's offline run all depend on it — and it is specified nowhere. NFR-2.1
gives a 10-second pack target for 100k files "with a warm index" and NFR-2.3 (P1!) asks for incremental
update. Nothing says how the index is first built, how long that takes, how much disk it consumes, when
it is invalidated, whether it can be shared or cached across runs and workspaces, what happens on a
branch switch, or what the factory does when it is missing, stale (FR-9.9 says "refreshed or excluded
with a recorded reason" — refreshed how, at what cost?), or corrupt.
*Proposed:* the index is a first-class store with a declared format version, a cold-build command with
a published time and space cost per repository size, incremental update on change (promote NFR-2.3 to
P0), sharing across workspaces of the same repository, explicit invalidation rules, and a documented
degraded mode when absent — the pack states which sections are reduced and why. Index build progress
and failures are observable, and an index build is a schedulable unit of work subject to FR-4.12.

**FR-24.4 (P1) — IMPORTANT — Toolchain adapter contract.**
*Gap:* multi-language reality is handled by `setupCommands` (FR-8.1) and by the assumption that tools
exist. FR-10.4 names "test execution with structured results, formatter and linter execution, build
execution" as baseline tools — with no contract for how a given language's tools are discovered,
version-pinned, or parsed into the structured results FR-10.6 mandates.
*Proposed:* a toolchain adapter contract, symmetric with FR-18.2's integration contract: detect,
declare required versions, provide hermetic setup, and normalise build, test, lint, format and coverage
output into the typed structures the Tool Registry publishes. Adding a language must not touch harness
or orchestration code. A repository with an undetected toolchain degrades explicitly (PR-9) rather
than parsing prose.

**FR-24.5 (P1) — IMPORTANT — Dependency and lockfile policy.**
*Gap:* nothing says whether an agent may add, upgrade, or remove a dependency — one of the most common
and most consequential things a build agent does. There is no allowlist, no lockfile-regeneration rule,
no vulnerability check, no licence check (see FR-27.1), and no gate. The blast-radius contract
(FR-12.1) bounds *paths*, so writing a lockfile inside the workspace is permitted by construction.
*Proposed:* dependency policy is declared in `policy/`: whether additions are permitted and from which
sources, whether upgrades are permitted and within what version range, whether lockfiles must be
regenerated by the project's own tool rather than hand-edited, and which checks gate the change
(vulnerability, licence, transitive count). A dependency change is a declared, separately-reviewable
part of the diff and appears in the evidence bundle with its resolution rationale.

**FR-24.6 (P1) — IMPORTANT — Derived, vendored, binary, and large content.**
*Gap:* no requirement distinguishes hand-written source from generated code, vendored trees, lockfiles,
fixtures, or binary assets. All of them will be indexed (inflating FR-24.3), included in the change
surface (inflating FR-9.13), diffed into the Critic's pack (FR-9.7), and treated as anchorable by spec
units (FR-5.2).
*Proposed:* declared classification rules per repository mark paths as generated, vendored, binary, or
large. Classified paths are excluded from the change surface and index by default; a change touching
generated output requires the generator's input to change too, and the gate says so; binary and
oversized files are summarised by digest rather than diffed into a pack.

### 3.2 Version-control integration mechanics (new family FR-25)

The PRD is careful that the factory *opens changes* and humans merge (NG-1, FR-4.4). Everything between
producing a diff and a human merging it is missing.

**FR-25.1 (P0) — BLOCKING — Base drift and merge conflicts.**
*Gap:* "conflict" appears in the PRD as a tool capability ("patch application with conflict detection",
FR-10.4), a blocker for *spec* conflicts (FR-4.7 `conflicting_spec`), and an error code (FR-21.5). The
ordinary case — the base branch moved while the work item was in flight, and the change no longer
applies — has no requirement, no blocker type, no policy, and no re-validation rule. On any active
repository this happens to a large fraction of work items, and it is the first thing a real deployment
will hit.
*Proposed:* declare an integration policy per repository (rebase or merge), a detection point (base
advanced since the run's checkpoint C0), an agent-attempted resolution bounded like any other repair
loop (FR-13.5), a new blocker type `base_conflict` carrying the conflicting paths and the exact action
needed (FR-4.7), and a **mandatory re-run of the stage's gates after any integration** — evidence
gathered against the old base is stale and must be marked so, not reused. A change whose conflicts the
agent cannot resolve returns to the Conductor with findings, never as a passing change.

**FR-25.2 (P1) — IMPORTANT — Long-running and stacked changes.**
*Gap:* the lifecycle in §6.4 is drawn as one pass. Nothing supports a work item that lives for weeks, a
change too large to review in one piece, or a stack of dependent branches. FR-4.9 (P1) gives work-item
dependencies but nothing gives *change* dependencies, and FR-12.2's checkpoints are within-run.
*Proposed:* a work item may produce an ordered stack of changes with declared dependencies; the factory
tracks each change's base, restacks on base movement (FR-25.1), and gates each independently while
reporting the stack's aggregate state. A change exceeding a declared size threshold triggers a
decomposition proposal at Design rather than a review no human will do properly.

**FR-25.3 (P1) — IMPORTANT — Post-merge outcome tracking.**
*Gap:* FR-4.4 says merge state is "observed and reported, never assumed", FR-15.3 counts merged changes
"sourced separately; may lag", FR-9.2's Hazards section wants "past reverts", and FR-11.7 scores
calibration against "post-merge reverts". Four requirements consume post-merge outcomes and **none
specifies acquiring them**. Nothing reopens a work item when its merged change is reverted or breaks
the main branch, so the factory never learns from its worst outcomes — the ones that matter most.
*Proposed:* the factory subscribes to merge, revert, and post-merge validation outcomes for every
change it opened (the git-host adapter already carries the events per FR-18.11), records them against
the work item, feeds them into Hazards, calibration scoring, and scorers, and reopens or annotates the
work item on revert or post-merge failure with a recorded reason. Where the integration is absent, the
metric is `unavailable with reason` per FR-15.5 — but the requirement to consume it must exist.

**FR-25.4 (P1) — IMPORTANT — Push and branch failure handling.**
*Gap:* FR-17.1 declares a repository identity (`EXECUTOR` or `CREATOR`) but nothing covers what happens
when the push fails: protected branch, denied permission, revoked token mid-run, branch name collision,
a force-push under a running work item, or a branch deleted after handoff. A run that cannot push has
produced work that FR-19.6 correctly calls invisible.
*Proposed:* branch naming is a declared, collision-safe policy; push failures produce typed errors with
the exact permission needed, park the item as `BLOCKED: missing_credential` or a new
`push_denied` blocker, and **preserve the workspace** so the work is not lost; force-push or deletion of
a branch under an active work item is detected and surfaced as a decision point.

**FR-25.5 (P1) — IMPORTANT — Cross-repository work items.**
*Gap:* FR-1.2 lets a factory declare several repositories and JTBD-4 is explicitly "execute and validate
a mechanical migration **across repositories**" — but a work item has one source context (FR-4.6), a
run has one workspace (FR-8.4), and no requirement coordinates a change that must land in three
repositories together. The document's own job-to-be-done is unsupported by its own model.
*Proposed:* a work item may span repositories, producing one change per repository with a declared
relationship (independent, ordered, or all-or-nothing-by-review); gates run per repository and
aggregate; the evidence bundle spans them; and handoff presents the set as one reviewable unit with the
ordering constraint stated. Where all-or-nothing cannot be enforced (it cannot, without merge authority
— NG-1), the limitation is stated in the handoff.

### 3.3 Identity and authorisation inside the factory (new family FR-26)

**FR-26.1 (P0) — BLOCKING — In-factory authorisation model.**
*Gap:* the PRD repeatedly requires that "a human" do something consequential — approve a Spec Delta
(FR-16.1), widen blast radius (FR-12.7), force a skill promotion without evidence (FR-7.4), override an
adoption block (FR-13.11), adopt an improvement proposal (FR-14.5), emergency-stop the factory
(FR-16.7), cancel or re-stage any work item (FR-4.8), and resolve a checkpoint (FR-16.3). **No
requirement says which humans may do which of these.** FR-16.2 correctly places *merge* authority in
external permissions — but none of the actions above is a merge, and none is enforceable by a git host.
U4's job (JTBD-8, "audit and constrain exactly what each agent can reach") is fully specified while the
equivalent question for humans is not asked.
*Proposed:* declare a role model in `policy/` mapping principals to permitted actions, with defaults
(for example: any authenticated principal may cancel their own work item; only declared owners may
approve a delta for their area; only declared operators may widen blast radius, force a promotion,
override an adoption block, or change policy; emergency stop is available to any operator). Every
authorised action records the principal and the role that permitted it. `sf audit` extends to report
human authority alongside agent reachability. Actions that only an external system can enforce remain
external, and lint continues to fail on any claim otherwise (FR-16.2).

**FR-26.2 (P0) — BLOCKING — Identity resolution and principals.**
*Gap:* FR-18.2's adapter contract includes "resolve identity" and FR-16.3 requires recording "the
deciding human's identity" — with no principal model behind either. The same person arrives as a chat
account, a git-host account, a tracker account, and a local shell user. Nothing maps them to one
identity, so audit trails cannot be joined, per-principal authority (FR-26.1) is unimplementable,
personal memory scope (FR-6.9 `personal`) has no owner, and FR-18.6's author-trust filters cannot
reference membership consistently.
*Proposed:* a principal is a first-class entity with stable identity and a set of provider identities,
resolved by declared mapping rules with an explicit unknown-principal policy (default: unknown
principals may not resolve checkpoints or approve anything, and their intake is subject to FR-18.6's
restrictive default). Every ledger entry with a human actor references a principal, not a provider
handle. Principal resolution failures are recorded, never guessed.

**FR-26.3 (P1) — IMPORTANT — Human offboarding.**
*Gap:* when a person leaves, nothing revokes their principal, reassigns their pending approvals
(silently blocking work items per FR-16.4 forever — see FR-4.13), disposes of their `personal`-scope
memories and `preference` memories (FR-6.3), or reassigns the skills and spec areas they own (FR-7.13,
FR-5.14).
*Proposed:* an offboarding operation deactivates a principal, reassigns or expires their owned
resources with a recorded disposition, reroutes pending checkpoints to a declared fallback, and marks
their personal-scope memories for expiry or transfer. Deactivation never rewrites history.

**FR-26.4 (P1) — IMPORTANT — Authentication for shared deployments.**
*Gap:* FR-21.7 specifies local loopback plus a file token; FR-15.8 says hosted deployment is "the same
application behind authentication"; FR-21.5 has "authentication required" and "not authorised" error
codes. The authentication and session model itself is never specified — no mechanism, no session
lifetime, no revocation, no service-account/token model for CI, no audit of authentication events.
*Proposed:* declare the supported authentication mechanisms for shared deployments, token issuance,
lifetime and revocation, machine principals for CI use, and audit logging of authentication and
authorisation decisions into the ledger. Transport security requirements for non-loopback binding are
stated (and refusing to bind off-host without them is the default).

**FR-26.5 (P1) — IMPORTANT — Tenant isolation.**
*Gap:* §6.3's Cloud topology has "hosted coordination", and FR-1.5 allows several factories in one
definition tree. Nothing states whether one deployment may serve multiple factories or teams, or what
isolates their ledgers, memories, evidence, secrets, and metrics if so.
*Proposed:* state the isolation model explicitly: either one deployment serves exactly one factory (and
lint enforces it), or declare isolation guarantees per store, cross-factory query rules, and the
blast radius of a compromised factory on its neighbours. Silence here is a security gap, not a
simplification.

### 3.4 Compliance, data protection, and provenance (new family FR-27)

**FR-27.1 (P1) — IMPORTANT — Licence and copy scanning of generated code.**
*Gap:* NFR-8.1 requires "dependency licence reporting in CI" for the factory's own dependencies. There
is no check on the code the factory *produces* — no verbatim-copy detection, no licence-compatibility
check on introduced code or dependencies, and no record of licence obligations. For an Apache-2.0
project whose output lands in other people's repositories, this is the compliance question every legal
team asks first.
*Proposed:* a `licence-clean` gate at Review: introduced dependencies are checked against a declared
allowed-licence set; introduced code is checked for verbatim reproduction of known-licensed corpora
where such a check is configured; findings are structured (FR-13.4) and attached to the evidence
bundle. Where no scanner is configured, the gate degrades to an explicit recorded statement (PR-9),
never to silence.

**FR-27.2 (P1) — IMPORTANT — Authorship provenance of generated changes.**
*Gap:* FR-16.5 requires that externally-produced artifacts be *attributable* to factory, agent, tier,
and work item — which covers the comment and the change description. It does not cover the **commit
history**, which is what survives, gets audited, and is subject to contributor agreements. Nothing
requires machine-readable provenance in commits, nothing addresses sign-off or contributor-agreement
regimes, and nothing lets a repository declare that AI-generated changes must be labelled or must not
be accepted.
*Proposed:* commits produced by the factory carry declared, machine-readable trailers naming the
factory, work item, agent role, model tier, and the definition revision (FR-2.11); an attribution
manifest accompanies each change listing which hunks were model-generated, human-corrected, or
tool-generated where determinable; the sign-off regime is declared per repository, and a repository may
declare that a human must attest before handoff. Attribution is never stripped by rebase or squash
policy.

**FR-27.3 (P0) — BLOCKING — Erasure and legal hold.**
*Gap:* the design makes deletion architecturally impossible and never says so. The ledger is
append-only (FR-15.1), Archive is permanent (FR-6.1), spec unit ids survive forever (INV-4), and
evidence is immutable once sealed (FR-22.5). Meanwhile transcripts contain issue text written by
customers, memories contain `preference` records about named people (FR-6.3), and evidence contains
screen recordings of real systems. A deletion request, a mistakenly-committed customer dataset, or a
retention obligation therefore has no path. FR-15.10's retention is a timer, not a targeted erasure,
and it deletes the wrong things (see FR-15.11).
*Proposed:* specify targeted erasure as redaction-by-reference: content lives in a content-addressed
store that can be erased, while the ledger retains only digests, so erasure preserves chain integrity
(INV-5) and reconstruction (INV-8) while removing the data. Erasure is authorised (FR-26.1), scoped
(one work item, one principal, one artifact class), ledger-recorded as an event, and propagated to
memory, packs, indexes, and caches. A legal-hold flag suspends all retention and erasure for a scope
and is itself recorded. `sf erase --dry-run` reports every location a subject's data resides. Retrofitting
this after v1 means rewriting every store.

**FR-27.4 (P1) — IMPORTANT — Sensitive-data classification.**
*Gap:* FR-17.3 redacts "known secret values" and is honest that redaction is a backstop. Nothing
addresses personal data, customer data, or regulated content, which enter through issue text, logs, test
fixtures, and screen recordings — all of which the factory ingests, stores durably, puts in packs, and
may transmit to a remote inference provider. FR-17.8's data locality lets the operator *choose* where
data goes but gives them no way to know **what** is going.
*Proposed:* declare data classes and detection rules; classify content at ingest; declare per-class
handling (may it enter a pack, a memory, an evidence bundle, a remote inference request, an external
comment); refuse or redact at the boundary the class forbids, recording the refusal; and surface class
counts per work item so an operator can see what the factory is holding.

**FR-27.5 (P1) — IMPORTANT — Data map.**
*Gap:* FR-17.7's `sf audit` enumerates *agent reachability* and FR-20.6's `--egress` enumerates
destinations. Neither answers U4's actual question at review time: what data classes does this factory
hold, in which stores, for how long, and which of them cross a boundary.
*Proposed:* `sf audit --data` produces a data map from the definition and policy: every store, the data
classes it holds, its retention, its erasure path, its backup location, and every flow that leaves the
host with its class and destination. It runs offline and without executing anything, like FR-17.7.

### 3.5 Extensibility and testing the factory itself (new family FR-28)

**FR-28.1 (P1) — IMPORTANT — Extension model.**
*Gap:* the PRD promises pluggability in five places — integration adapters (FR-18.2), harness adapters
(FR-11.1), model providers (FR-11.2), memory backends (FR-6.14), tool servers (FR-10.8) — and FR-8.2
enumerates executors as a **closed set of four**, contradicting FR-0.1's orthogonality claim for anyone
with a fifth kind of compute. There is no packaging format, no discovery mechanism, no version
constraint, no capability declaration, no sandboxing of third-party extension code, and no trust model
— despite FR-17.4 correctly treating tool-server *descriptions* as untrusted.
*Proposed:* one extension contract covering all extension points including executors: a manifest
declaring kind, version, compatible core versions, and required capabilities; discovery from declared
sources with pinned digests (FR-17.9); third-party extension code runs with declared, default-deny
capabilities and is subject to the same audit as agents (FR-17.7); and `sf plan` shows which extension
supplied each resolved behaviour.

**FR-28.2 (P1) — IMPORTANT — Agent and pack regression suite.**
*Gap:* the PRD gates skill revisions on their own evals (FR-7.5: "a revision that regresses its own eval
set must be rejected by the gate") and gates configuration changes on benchmarks (FR-13.11) — but a
change to an **agent's prompt body** (FR-3.9, the agent's most important field) or to **Awareness Pack
composition** (FR-9.7, the document's declared largest quality lever) is an ordinary file edit that
passes `sf validate` and ships. The self-improvement loop is explicitly allowed to propose exactly these
edits (FR-14.3).
*Proposed:* agents and pack composition declare eval sets in the same way skills do; changing an agent
body, its role weights, its tier, or its pack composition triggers those evals in CI and is gated on no
regression beyond a declared tolerance, with a recorded override path. This closes the loop FR-14.3
opens.

**FR-28.3 (P1) — IMPORTANT — Shipped self-test.**
*Gap:* NFR-5.2 makes model interactions stubbable and NFR-5.4 requires a coverage gate and the
conformance suite. Neither gives an **operator** a way to verify their own installation end to end.
FR-20.5's parity suite is a release gate run by the project, not something a user runs against their
machine, their sandbox, their container runtime, and their model endpoint.
*Proposed:* ship a fixture factory and `sf selftest` that runs a complete work item through every stage
and gate against the local executor with stubbed models, then optionally against the configured model
endpoint, and reports per-subsystem results. It must run offline, complete in a declared time budget,
and be the first thing a bug report includes (NFR-10.1).

**NFR-5.5 (P1) — IMPORTANT — Fault injection.**
*Gap:* the PRD asserts many failure behaviours — PR-9's degradation, NFR-1.2's crash safety, NFR-1.4's
partial failure, FR-11.10's provider degradation, FR-18.9's adapter degradation — and provides no way
to test any of them. §11.3's subsystem acceptance table tests injected drift and injected contradictory
memory, and stops there.
*Proposed:* a fault-injection harness able to simulate provider unavailability and rate limiting, tool
timeouts, executor loss, disk exhaustion, clock skew, adapter outage, corrupted index, corrupted
ledger segment, and partial upgrade — with a test per declared degradation behaviour, run in CI.

### 3.6 Smaller absent areas

**NFR-7.3 (P2) — NICE — Output language and locale.**
*Gap:* NFR-7.2 externalises *dashboard* strings and forbids locale assumptions in parsing. Nothing
covers the language agents write in: issue text arrives in any language, spec units are prose, memories
are prose, and evidence summaries are read by humans who may not read English. There is no declared
factory locale and no rule for what happens when intake language differs from spec language.
*Proposed:* a factory declares a working language for its durable artifacts (spec, memory, skills) and
a reply-language policy for requester-facing output (default: reply in the requester's language, record
durable artifacts in the working language, and note the translation in provenance).

**NFR-8.3 (P1) — IMPORTANT — Telemetry policy.**
*Gap:* FR-20.6's no-phone-home is the right default and is stated for *local* factories. The project
never states its position for hosted or default installs, and "telemetry" in the PRD means internal
pack and improvement telemetry (FR-9.8, FR-14.8), not product analytics. An open-source project must
state this explicitly or lose trust the first time anyone reads a packet capture.
*Proposed:* no product telemetry is collected without explicit opt-in; if opt-in exists, the payload is
documented field-by-field, is inspectable with a command before sending, contains no repository
content, and appears in `sf audit --egress` like any other destination. Update checks count as
telemetry and are opt-in.

**NFR-8.4 (P1) — IMPORTANT — Release integrity.**
*Gap:* FR-17.9 pins runner images and tool-server endpoints by digest, and NFR-8.1 covers licensing —
but nothing covers the integrity of `sf` **itself**: no signed releases, no checksums, no SBOM, no
reproducible-build goal, and no security-fix support window. A tool that holds credentials and executes
code is a high-value supply-chain target, and its own supply chain is unspecified.
*Proposed:* every release is signed and published with checksums and an SBOM; verification instructions
are part of the install path (FR-23.1); a declared number of minor versions receives security fixes;
and reproducible builds are a stated goal with a measured status.

**NFR-8.5 (P1) — IMPORTANT — Versioning and deprecation policy.**
*Gap:* four independently-versioned surfaces exist — definition schema (FR-1.6), state/ledger format
(implied), API (FR-21.4), CLI — and no policy governs any of them: no scheme, no compatibility promise,
no deprecation period, no removal rule.
*Proposed:* one published policy covering all four: semantic versioning, what constitutes a breaking
change for each, a minimum deprecation period with warnings emitted through `sf lint` and API
responses, and a support matrix (NFR-12.6).

---

## 4. Missing detail in existing requirements

Requirements that state a *what* with no testable *how*. Each entry names the specific missing
parameter — the thing on which two competent implementers would diverge and both claim conformance.

### 4.1 The nine that change the system's identity

**FR-9.13 (P0) — BLOCKING — Define "change surface".**
*Gap:* the term governs the spec slice (FR-5.6), the Terrain and Precedent and Hazards sections
(FR-9.2), the Critic's pack (FR-9.7), the `spec-agreement` gate (FR-13.2), and drift detection
(FR-5.5) — and is never defined. Before a change exists (Triage), there is no diff to derive it from,
so the term is not even well-formed at the stage where it is first used. Missing: inputs at each stage,
the derivation algorithm, the expansion rule (direct paths only? reverse dependencies? call graph to
what depth?), the size cap, and behaviour when the surface is empty or enormous.
*Proposed:* define change surface as a typed, staged object with a documented derivation per stage
(pre-change: from the request, prior work items on the same symbols, and error signal locations;
post-change: from the diff plus reverse dependencies to a declared depth), a declared expansion depth
and size cap, an explicit empty case, and a recorded digest so two runs can be compared. It must be
computed by deterministic tools (PR-6) and be inspectable via `sf` for debugging.

**FR-9.12 (P0) — BLOCKING — Budget units and token accounting.**
*Gap:* FR-9.5 gives the pack "a total budget and per-section budgets by agent role" and FR-11.9 keeps
context "at or under the tier's effective window" — with no unit (tokens? bytes? characters?), no
tokenizer contract for providers that do not expose one, no rule for what happens when the sum of
section budgets exceeds the total, no arbitration order when sections compete, and no defined behaviour
when a *single* mandatory item (one spec unit, one diff hunk) exceeds its section budget alone.
FR-3.11's token budget has the same ambiguity.
*Proposed:* budgets are denominated in tokens against a declared tokenizer per provider, with a
documented byte-based approximation and a safety margin where none is available; specify the
arbitration order when sections contend (mandatory sections first in a declared priority, discretionary
sections shrink by declared weights); specify the single-oversized-item rule (summarise with a
retrieval pointer per FR-9.5, never truncate mid-item); and record actual versus budgeted size per
section (FR-9.8 records sizes but not the budget it was measured against).

**FR-11.13 (P0) — BLOCKING — Record model invocation parameters and resolved version.**
*Gap:* §9.1's `Run` records `tier` — not the model, not its version, not temperature, top-p, seed, stop
conditions, or the system-prompt assembly. FR-11.11's deterministic replay, FR-13.9's benchmark
comparison, FR-11.5's evidence-based de-escalation, and §11.2's entire central-bet acceptance test all
require knowing exactly what was invoked. Providers also change the model behind a stable alias, which
silently invalidates every recorded baseline, and nothing detects it.
*Proposed:* every model invocation records the provider, the requested model identifier, the
provider-reported resolved version or fingerprint where available, all sampling parameters, and the
digest of the assembled prompt. A change in resolved version against a stored baseline is detected and
flagged as a benchmark-invalidating event; benchmark results carry the resolved versions they were
produced under, and comparing results across different resolved versions is refused or loudly marked.

**FR-13.16 (P0) — BLOCKING — Flaky-test policy.**
*Gap:* `tests-pass` and `regression-proven` (FR-13.2, FR-13.3) — the strongest gates in the document —
are defined as though tests are deterministic. FR-13.13 *tracks* flakiness at P1 and proposes work
items about it. Nothing tells a gate what to do when a test fails intermittently. Without a policy, a
flaky suite either blocks every change (the factory stops) or is retried until green (the gate is
worthless), and the PRD permits both readings.
*Proposed:* define flakiness detection (repeated runs at the same commit with divergent outcomes,
recorded per test), a quarantine set that a repository declares or the factory proposes, a bounded
retry budget for gate execution with every attempt recorded, and the rule that a quarantined test's
failure is a warning while a *new* failure is blocking. `regression-proven` explicitly requires the new
test to be non-flaky: it must fail deterministically at the parent commit across a declared number of
repetitions, otherwise the gate reports `inconclusive` — a third outcome the current binary model lacks.

**FR-17.12 (P0) — BLOCKING — Secret backing-store contract.**
*Gap:* FR-2.8 states that definitions carry secret *names*, never values — correctly. FR-12.8 mounts
"exactly the secrets its agent declares … destroyed at run end". **Where the values come from is never
specified.** No backing store, no file format, no environment convention, no external secret-manager
interface, no rotation mechanism (FR-17.10 is "rotation-aware" at P1 without a mechanism), no behaviour
when a declared secret is absent at dispatch, and nothing for the local-first case where PR-2 forbids
depending on an external service. This is the one unspecified interface that every deployment must
implement on day one.
*Proposed:* a secret provider interface with at least a local file-backed default (documented format,
restrictive permissions enforced and checked, excluded from version control and from backups per
NFR-9.1), an environment provider, an OS keychain provider where available, and an external-manager
adapter; declared-but-absent secrets fail dispatch with `missing_credential` (FR-4.7) naming the secret
and the provider consulted, never with a partially-provisioned run; rotation invalidates cached values
within a declared bound.

**FR-4.12 · FR-29.1 · FR-29.2 · FR-24.3 · FR-25.1** are detailed above and belong to this class too:
each is a *what* with no *how* at all.

### 4.2 The parameter table

Every row is a threshold, unit, default, timeout, or algorithm named in the PRD and left unset. The
proposed fix for the whole table is **NFR-11.1** below; the rows marked with an ID also warrant their
own requirement.

| Requirement | Missing parameter | Proposed ID |
| --- | --- | --- |
| FR-3.5 | How "same model" is determined across providers, aliases, and versions; what counts as a different harness | FR-11.13 |
| FR-3.11 | Default budgets per role; currency and rounding for `max cost`; whether budgets are per run or per work item | NFR-11.1 |
| FR-5.5 | How often agreement is recomputed, at what cost, and over what scope; how acceptance criteria are "mapped" to tests | FR-13.19 |
| FR-5.8 | The anchoring algorithm: how a range digest survives reformatting, renames, and moves; the false-drift rate target | FR-5.15 |
| FR-6.2 | Default TTLs per memory kind (FR-6.3 promises "default lifetime"; none is given) | FR-6.19 |
| FR-6.5 | Similarity metric and threshold for duplication; what "same scope" means for contradiction | FR-6.19 |
| FR-6.7 | Decay function, relevance ranking function, diversity-cap value, budget truncation order | FR-6.19 |
| FR-6.12 | "Value density" formula; default count and byte budgets per scope | FR-6.19 |
| FR-7.6 | Overlap threshold; "substantially similar" metric | FR-7.16 |
| FR-7.8 | N (unselected runs), M (failing windows), window length | FR-7.16 |
| FR-7.9 | Precision/recall definitions and thresholds; how recall is observed at all | FR-7.16 |
| FR-7.10 | Selection budget default; the expected-value ranking function | FR-7.16 |
| FR-8.1 | Default timeouts per phase (setup, tool, total); default instance shapes | NFR-11.1 |
| FR-9.5 | Total and per-section budget defaults per role (OQ-1 acknowledges this is open with no v1 answer) | FR-9.12 |
| FR-9.9 | Freshness thresholds: how old an index or memory may be before exclusion | FR-24.3 |
| FR-10.1 | The enumeration of cost classes and their meaning | NFR-11.1 |
| FR-11.4 | "Same signature" definition for a repeated gate failure; the confidence threshold; the enumeration of complexity signals and their thresholds | FR-11.15 |
| FR-11.6 | Confidence scale and representation (numeric range? ordinal labels?); per-criterion output schema | FR-11.15 |
| FR-11.7 | The calibration-error metric and its minimum sample size before it is reported | FR-11.15 |
| FR-11.8 | Bounded number of repair attempts for schema failures | FR-13.19 |
| FR-11.9 | The tier threshold below which scaffolding activates; "effective window" definition | FR-9.12 |
| FR-12.3 | Speculation bounds: how many branches, what share of budget, retention of discarded ones | FR-12.9 |
| FR-13.5 | Default repair-attempt cap per gate and per stage | FR-13.19 |
| FR-13.6 | Sampling semantics: per run or per agent, deterministic or random, and the seed | FR-13.19 |
| FR-13.8 | Human-agreement threshold and the labelled-sample size required to compute it | FR-13.19 |
| FR-13.9 | Default repetitions; the variance-estimate method; the minimum detectable effect | FR-13.19 |
| FR-13.11 | Adoption "tolerance" per metric | FR-13.19 |
| FR-14.6 | Cooling period; cap on open proposals per factory and per target | FR-14.11 |
| FR-14.7 | Held-out set size, refresh cadence, and how contamination is prevented as the loop reads the ledger | FR-14.11 |
| FR-14.8 | The threshold at which "a loop whose proposals do not move outcomes" is declared a defect | FR-14.11 |
| FR-15.3 | Default metric window; the population for each median; the cost decomposition components; "change size" buckets | FR-15.15 |
| FR-15.10 | The documented default retention per artifact class (OQ-8 leaves this open with no v1 answer) | FR-15.11 |
| FR-16.4 | The time bound before a checkpoint escalates, and before it parks | FR-4.13 |
| FR-17.11 | The violation-rate threshold and its window | NFR-11.1 |
| FR-18.3 | Cron dialect; whether descriptors are a fixed vocabulary; behaviour on missed schedules after downtime | FR-18.15 |
| FR-18.4 | Value types, case sensitivity, glob/regex support on paths and branches, evaluation order and cost | FR-18.18 |
| FR-20.7 | The default "share of the machine" for cpu, memory, disk, and concurrency | NFR-11.1 |
| NFR-2.1 | Whether 10s is a target or a requirement; the reference repository and hardware; what "warm" means | NFR-2.5 |
| NFR-4.1 | The reference environment, repository, and model endpoint for the 10-minute claim | NFR-4.4 |
| §11.1 | "Defect-class work" definition (see FR-4.14); the measurement window; the comparable-human-change baseline for review time | FR-4.14 |

**NFR-11.1 (P0) — IMPORTANT — Normative defaults artifact.**
*Proposed:* every default, threshold, timeout, budget, unit, and enumeration in the PRD is collected in
one machine-readable defaults document that ships with the product, is served by `sf schema defaults`
alongside FR-2.2's schemas, is the single source the code reads, and is verified in CI to match the
code. A value named in a requirement but absent from that document is a defect. This is the mechanism
that makes the table above closable and keeps it closed.

### 4.3 The rest, individually

**FR-9.14 (P0) — IMPORTANT — Role shaping as configuration.**
FR-9.7 expresses pack composition as prose ("Scout weights Terrain, Hazards and Precedent"). Weights
are not defined, not declared anywhere in the FR-2.1 tree, not inspectable, and not overridable —
despite FR-14.3 explicitly allowing the improvement loop to propose changes to "an Awareness Pack
weight", which cannot be proposed against prose. *Proposed:* pack composition is a declared, defaulted,
per-role configuration file with numeric section weights and budgets, shown by `sf plan`, diffable, and
gated by FR-28.2.

**FR-9.15 (P1) — IMPORTANT — Oversized inputs.**
Nothing defines behaviour when the *subject matter* exceeds the window: a 4,000-line diff for the
Critic, a spec slice of 60 units, a test log of 200MB. FR-9.5 governs the pack's own sections;
FR-10.9 makes truncation explicit for a single tool result. *Proposed:* declare a per-stage maximum
reviewable change size, above which the stage decomposes (review by module with an aggregation pass),
escalates (FR-11.4), or returns a decomposition finding — and never silently reviews a truncated diff.

**FR-11.15 (P1) — IMPORTANT — Confidence and calibration definitions.**
*Proposed:* declare the confidence representation (a schema-validated scale with defined semantics per
level), the required per-criterion structure, the named calibration metric, its computation window and
minimum sample, and the reporting rule below that sample. FR-11.6's "confidence without cited evidence
is treated as zero" needs a machine-checkable definition of what counts as a citation.

**FR-12.9 (P1) — IMPORTANT — The blast-radius contract as an artifact.**
FR-12.1 requires the contract to be "machine-checked by the executor, not merely stated" and FR-12.6
requires it to be stated affirmatively to the agent. There is no schema for it, and nothing tests that
the version shown to the agent equals the version the executor enforces — the exact divergence that
would make PR-5's promise false. *Proposed:* one schema, one source, rendered for the agent and
enforced by the executor from the same document, with a conformance test asserting equivalence and a
test asserting every violation class is both denied and recorded (FR-12.5).

**FR-13.15 (P1) — IMPORTANT — Scorer rubric versioning.**
FR-13.7 carefully handles `passingScore` changes ("re-renders history but never rewrites recorded
classifications") and says nothing about editing the **rubric body**, which changes what the labels
mean. Trend lines then silently compare incomparable things, and FR-14's loop acts on the difference.
*Proposed:* rubric changes bump a scorer version recorded on every `ScoreResult`; trends across a
version boundary are marked as a discontinuity in the dashboard and refused as evidence for adoption
(FR-13.11) unless a re-scoring of the baseline sample is performed under the new version.

**FR-13.17 (P1) — IMPORTANT — External CI as a gate input.**
NG-5 says the factory drives CI and consumes its results, FR-4.7 has an `awaiting_ci` blocker, and
FR-18.11 subscribes to check-suite events — but no requirement binds CI results into a gate. Missing:
which CI outcomes gate which stages, the wait timeout, re-trigger policy on infrastructure failure,
how partial or in-progress results are treated, and what happens when CI never reports.
*Proposed:* declare CI-backed gates with a required check set per stage, a wait timeout after which
the item parks as `BLOCKED: awaiting_ci` with the pending checks named, a bounded re-trigger policy
distinguishing infrastructure failure from test failure, and a rule that in-progress checks never
satisfy a gate.

**FR-13.18 (P1) — IMPORTANT — Gate execution model.**
FR-13.2 lists eleven baseline gates without saying how they run: order, whether a blocking failure
short-circuits the rest (cheaper but hides findings) or all gates run (complete findings, higher cost),
parallelism, per-gate and total time budget, and behaviour when a gate's own check errors — which is
distinct from the gate failing and has no representation in the current `block`/`warn` model.
*Proposed:* declare execution order and grouping, require that all gates run by default so findings are
complete, declare a total gate budget, and add a third gate outcome `error` (the check could not be
evaluated) that is never silently treated as a pass.

**FR-14.10 (P1) — IMPORTANT — Improvement proposal lifecycle.**
FR-14.6 forbids re-proposing "a change already rejected without new evidence" — implying durable
rejection memory that is never specified: where it lives, how long it lasts, what "new evidence" means,
and how it survives a definition revert. Also missing: proposal expiry when the underlying runs age
out of retention, and what happens when two open proposals modify the same target.
*Proposed:* proposals are first-class entities with states (open, adopted, rejected, superseded,
expired), a durable rejection record carrying the reason and the evidence considered, a definition of
"new evidence" as a materially different failure cluster, an expiry when their motivating runs are no
longer retained, and conflict detection between open proposals touching one target.

**FR-15.15 (P1) — IMPORTANT — Metric definitions.**
§11.1's targets and §7.15's fourteen metrics are stated as names. Missing: the default window, the
population each median is taken over, whether counts are of work items or runs, the components of the
cost decomposition, "change size" bucketing, and — specifically — R-12's stated mitigation
("bundle-size vs. review-time metric") which appears in the risk table and in no requirement.
*Proposed:* one normative metric definition per row of FR-15.3 with window, population, and formula;
add the evidence-bundle-size against human-review-time metric that R-12 relies on; and require
`unavailable with reason` rendering (FR-15.5) to name the missing integration.

**FR-16.8 (P1) — IMPORTANT — Notification subsystem.**
FR-16.4 requires a checkpoint to "escalate its notification"; FR-19.7 offers "notification routes";
NFR-9.5 needs alert delivery. There is no notification subsystem: no route definition, no addressing
of principals (FR-26.2), no deduplication, no digesting, no escalation ladder for humans, no quiet
period, and no behaviour when delivery fails — while FR-16.4's entire mechanism depends on delivery.
*Proposed:* declared routes with per-event-class routing, dedup and digest windows, an escalation
ladder (route, then fallback principal, then park), delivery-failure handling that never silently drops
a checkpoint notification, and delivery status visible on the work item.

**FR-16.9 (P1) — IMPORTANT — Gate override path.**
FR-13.11 provides "an explicit, recorded human override path" for adoption blocks only. Every other
blocking gate has none — so a `secret-clean` false positive or a `coverage-of-criteria` failure on a
genuinely untestable criterion is terminal, and operators will respond by disabling the gate in policy,
which is far worse than a recorded override.
*Proposed:* a uniform override mechanism for blocking gates: authorised by role (FR-26.1), scoped to
one work item, carrying a mandatory reason, expiring, ledger-recorded, surfaced in the evidence bundle
and in the change description, and counted as a metric. Some gates may be declared non-overridable
(`blast-radius-clean`, `secret-clean` are the obvious candidates) and that list is explicit.

**FR-17.13 (P1) — IMPORTANT — Untrusted content inside the pack.**
FR-17.5 requires untrusted content to be delivered "inside labelled, delimited regions". The Awareness
Pack is assembled *by the trusted plane* from repository files, issue text, prior comments, and CI
output (FR-9.2 sections 3–6, 10) and is presented to the model as authoritative context with citations
(FR-9.4). Nothing says pack sections carry a trust label, so the highest-authority channel in the
system is also the one most likely to carry injected instructions.
*Proposed:* every pack item carries a trust class alongside its citation (definition-sourced,
tool-computed, memory-Canon, untrusted-external), sections composed of untrusted content are delimited
and labelled as such, and FR-17.5's refusal rule applies to tool calls whose parameters trace to an
untrusted pack item exactly as it does to inline untrusted regions.

**FR-18.15 (P1) — IMPORTANT — Event replay and catch-up.**
*Gap:* NFR-1.1 promises no lost work items for "every accepted intake event". Nothing covers events
that arrive while the factory is down: no backlog window, no replay from the provider, no missed-cron
policy (FR-18.3 defines the schedule and not what happens to a schedule missed during downtime), and no
declaration of loss when replay is impossible.
*Proposed:* adapters declare whether they support replay and over what window; on reconnection the
factory replays within that window with idempotency (FR-18.7); missed schedules follow a declared
policy (skip, run once, run all); and events irrecoverably missed are recorded as a declared gap with
its time range so NFR-1.1's promise is auditable rather than assumed.

**FR-18.16 (P0) — BLOCKING — Intake backpressure and circuit breaking.**
*Gap:* FR-18.14 (P1) deduplicates *monitoring signals* by fingerprint. Nothing bounds intake generally.
A misconfigured filter (FR-18.4 explicitly allows one event to match several automations, each starting
its own run), a repository-wide label operation, a bulk import, or a retry storm converts directly into
unbounded concurrent runs and unbounded spend, with no cap between the webhook and the model.
*Proposed:* per-source and per-automation rate limits with declared defaults; burst detection that
opens a circuit breaker, sheds further events with a recorded reason and a notification (never a silent
drop, per NFR-1.1), and requires a human to close it; and a maximum work-items-per-period cap per
factory. Shedding is a recorded rejection, which NFR-1.1 already permits.

**FR-18.17 (P1) — IMPORTANT — Webhook authenticity.**
*Gap:* FR-18.1 says "generic signed webhooks" and nothing else. No signature verification requirement,
no algorithm, no replay window, no timestamp tolerance, no per-source secret, no rotation, and no
recorded rejection of unauthenticated deliveries — for the one component exposed to the public
internet.
*Proposed:* every webhook source declares a verification method and secret name (FR-17.12);
verification failures and replays outside a declared timestamp window are rejected and recorded as
security events (FR-17.11); secrets are rotatable without downtime through an overlap period; and
unverified sources are refused by lint rather than merely warned about.

**FR-18.18 (P1) — IMPORTANT — Filter matching semantics.**
*Gap:* FR-18.4 admirably specifies AND-across-keys, OR-within-key, omitted-matches-everything, and
`in`/`not_in` — and then leaves the matching itself unspecified. Path and branch filters are the ones
that matter and are exactly where implementations differ: exact match, prefix, glob, or regex? Case
sensitivity? Are patterns anchored? What is the evaluation cost bound on a large event? What happens
when a filter value is malformed?
*Proposed:* declare the value grammar per key kind (exact for labels and states; glob with declared
semantics for paths and branches; no unbounded regex), case sensitivity per key, anchoring rules, a
per-event evaluation cost bound, and malformed-pattern rejection at validation time (FR-2.4) rather
than at match time.

**NFR-2.5 (P1) — IMPORTANT — Cold-index performance and reference corpus.**
*Proposed:* declare a reference repository corpus (sizes, languages, file counts) and reference
hardware; state cold-index build time and disk cost per corpus, incremental update latency, and pack
assembly time against each; publish measured results per release so NFR-2.1's target is verifiable
rather than aspirational.

**NFR-4.4 (P1) — IMPORTANT — Reference environment for time-to-first-run.**
*Proposed:* NFR-4.1's ten-minute claim names its reference environment, repository, model endpoint, and
what the "useful run" produces, and is measured in CI on each release like any other acceptance test.

**FR-11.14 (P2) — NICE — Caching semantics.**
*Gap:* prompt and result caching materially changes both latency and the cost figures §11.1 targets and
FR-15.4 labels as estimates. Nothing declares whether caching exists, what its key is, how it interacts
with FR-9.1's determinism requirement, or how cached tokens are reported.
*Proposed:* declare caching behaviour, its key derivation, its interaction with pack digests, and
require cached versus uncached usage to be reported separately so cost trends are not confounded.
