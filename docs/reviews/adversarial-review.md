# Adversarial Review — Master PRD v1.0.0

| Field | Value |
| --- | --- |
| Document under review | `docs/PRD.md` v1.0.0 (baseline, pre-review) |
| Review type | Hostile / adversarial. Assume the design is wrong until it survives. |
| Method | Attack the thesis, the threat model, each mechanism, the economics, the internal consistency, and the feasibility. Every finding names a requirement and a concrete failure. |
| Findings | 12 CRITICAL · 30 MAJOR · 9 MINOR (51 total) |

**Reading rule.** CRITICAL means the design as written is wrong or unsafe and shipping it produces
harm or produces a false result. MAJOR means it will fail in practice — deadlock, get disabled, get
gamed, or cost more than it returns. MINOR means fix it before it becomes one of the other two.
Every finding ends with **Remedy**, written as a requirement you can paste into §7 or §11.

---

## Findings index

| ID | Severity | Area | Summary |
| --- | --- | --- | --- |
| AR-01 | CRITICAL | Thesis / §11.2 | The benchmark corpus and the memory fabric leak into each other: either AC-4's memory ablation is a no-op or AC-1 is measuring retrieval of the answer key |
| AR-02 | CRITICAL | Thesis / §11.2 | Conditions A/B get one attempt and no verifier; C/D get a verifier plus a repair loop. AC-1 and AC-2 measure best-of-n, not awareness |
| AR-03 | CRITICAL | Security / Memory | FR-6.4 defines corroboration over *runs*, not over *sources* — untrusted issue text launders into Canon and becomes a cited "convention" in every pack |
| AR-04 | CRITICAL | Security | FR-17.5 requires taint-tracking through a language model. It is unimplementable, and §11.3 asserts it as a passing acceptance test |
| AR-05 | CRITICAL | Self-improvement | Grader capture needs no edit to a scorer, gate, or eval file, so FR-14.7's self-referential flag never fires; held-out task sets do not defend against it |
| AR-06 | CRITICAL | Security / Governance | FR-17.6 and FR-14.3 contradict each other, and NFR-8.2 makes definition changes *equal* to code changes when they need to be stricter. No separation of duties over the factory's own definition |
| AR-07 | CRITICAL | Gates | `regression-proven` (FR-13.3) is satisfied by an import error at the parent commit. The strongest stated defence is a one-line bypass |
| AR-08 | CRITICAL | Blast radius | FR-12.1/12.2 bound and undo the *workspace* only. Memory candidates, spec deltas and skill proposals survive rollback, violation, and budget kill |
| AR-09 | CRITICAL | Secrets | FR-12.8 mounts live secrets into the untrusted execution plane and FR-13.2's `secret-clean` gate is value-matching sold as a control |
| AR-10 | CRITICAL | Orchestration | FR-3.3 gives the Conductor unbounded stage-skip authority, and FR-3.6 makes the Conductor the one agent that reads attacker-controlled text |
| AR-11 | CRITICAL | Security | FR-17.1's `CREATOR` repository identity can carry merge and admin scope. NG-1 is a product intention with no enforcement behind it |
| AR-12 | CRITICAL | Security / Observability | FR-15.7's live steering channel is the "explicit human decision" that FR-17.5 defers to, and its identity model is unspecified |
| AR-13 | MAJOR | Thesis | AC-3 is a ratio whose denominator the designers control, with no difficulty calibration and no pre-registration |
| AR-14 | MAJOR | Thesis | §11.2 has no statistical power: n=40 tasks, repetitions are not independent samples, nine comparisons, no effect size, no correction |
| AR-15 | MAJOR | Thesis / Calibration | AC-5 is degenerate: FR-11.6's zero-confidence floor rewards a model that cannot cite |
| AR-16 | MAJOR | Thesis / Cost | AC-2's 3× cost claim excludes exactly the costs the harness adds |
| AR-17 | MAJOR | Thesis | The central bet is asserted about "the harness" but measured as a property of one operator's curation state, which is never recorded |
| AR-18 | MAJOR | Awareness Pack | FR-9.1 determinism is contradicted by FR-6.7 decay, FR-6.10 timeouts and FR-9.9 freshness. §11.3 asserts the contradiction as an acceptance test |
| AR-19 | MAJOR | Living Spec | `contradicted` blocks Build and the only fix is a work item whose own Design gate is blocked by the same state |
| AR-20 | MAJOR | Living Spec | FR-5.8's range digests produce mass false drift on any reformat and cannot see the failure that matters |
| AR-21 | MAJOR | Living Spec / Gates | FR-5.7 is a blocking gate resting on an undecidable judgement, and §11.3 claims 100% recall for it |
| AR-22 | MAJOR | Memory | FR-6.5's contradiction detector and duplication merger fight over the same input; the merger wins and produces incoherent Canon |
| AR-23 | MAJOR | Memory | FR-6.6 transitive invalidation plus FR-6.1's no-return Archive collapses Canon after one bad source |
| AR-24 | MAJOR | Skills | FR-7.4 promotion evidence and FR-7.8 retirement thresholds form a trap that kills every low-frequency skill |
| AR-25 | MAJOR | Skills | FR-7.9's selection recall has no oracle and cannot be computed as specified |
| AR-26 | MAJOR | Skills | FR-7.6 merge and FR-7.7 split oscillate, and registry proposals are outside FR-14.6's anti-thrash |
| AR-27 | MAJOR | Small models | FR-11.9 puts decomposition — the hardest reasoning step — on the weakest model, and assumes verifiers that do not exist |
| AR-28 | MAJOR | Calibration / Routing | The zero-confidence floor plus FR-11.4's triggers produce an escalation storm on precisely the small-model configuration the bet depends on |
| AR-29 | MAJOR | Economics | Budgets are per-agent (FR-3.11). There is no per-work-item budget anywhere in the document, and rework resets every one |
| AR-30 | MAJOR | Performance | NFR-2.1, NFR-3.1 and FR-8.4 are jointly infeasible, and NFR-2.3 (incremental indexing) is P1 |
| AR-31 | MAJOR | Economics | Every always-on assurance subsystem is unbudgeted; a factory can spend more on introspection than on work |
| AR-32 | MAJOR | Operability | The FR-16.1 checkpoint set guarantees the defaults are disabled within a week, and FR-16.4's remedy converts an absent human into a stalled backlog |
| AR-33 | MAJOR | Operability | FR-13.12 evidence bundles raise review cost, against §11.1's ≤1.0× review-time target. R-12's mitigation is a metric, not a control |
| AR-34 | MAJOR | Ledger | FR-15.10 retention deletes exactly what FR-15.2 and INV-8 claim is reconstructible, and breaks the hash chain's meaning |
| AR-35 | MAJOR | Conformance | Judgemental gates make FR-20.5's parity suite — a release blocker — permanently red or quietly weakened |
| AR-36 | MAJOR | Scorers | FR-13.8's human-agreement validation is infeasible at the scale of the reference topology, so R-9's mitigation evaporates first |
| AR-37 | MAJOR | Benchmarks | FR-13.9 forbids declaring a winner; FR-13.11, FR-7.4 and FR-11.5 all mechanically declare one |
| AR-38 | MAJOR | Definitions | FR-2.10's replace-semantics and always-apply factory secrets violate FR-17.2 default-deny and cause silent capability loss |
| AR-39 | MAJOR | Review independence | FR-3.5 is unsatisfiable in the reference topology and in acceptance-test condition C |
| AR-40 | MAJOR | Metrics | §11.1's headline metrics are merge-derived and permanently unavailable in the offline topology the project calls its reference |
| AR-41 | MAJOR | Handoff | FR-19.5's deliberate absence of a lease produces duplicated irreversible external actions |
| AR-42 | MAJOR | Security | `sf audit --egress` cannot be complete while FR-8.3 setup commands and FR-8.5 allowlists exist, yet §11.3 asserts "zero destinations" |
| AR-43 | MAJOR | Supply chain | FR-17.9 pins tool-server *endpoints*; the schema and behaviour behind them are mutable at any time |
| AR-44 | MAJOR | Awareness Pack | FR-9.5's over-budget summarisation breaks FR-9.4's citation guarantee, and FR-9.6 confounds AC-4's pack ablation |
| AR-45 | MAJOR | Gates | `blast-radius-clean` is a zero-tolerance gate over an event class that ordinary toolchains generate on every build |
| AR-46 | MAJOR | Gates | `coverage-of-criteria` validates a mapping declared by the agent being gated |
| AR-47 | MAJOR | Scorers | FR-13.7's `passingScore` re-render silently rewrites which failures the improvement loop is allowed to investigate |
| AR-48 | MAJOR | Self-improvement | The improvement agent is the highest-privilege reader in the system and reads untrusted transcripts while authoring definition changes |
| AR-49 | MAJOR | Definitions | FR-2.3 atomic validation with no pinned snapshot leaves local operators silently running a stale definition |
| AR-50 | MAJOR | Reliability | NFR-1.2's idempotency-keyed external actions require provider support that comment and chat APIs do not offer |
| AR-51 | MAJOR | Factory model | JTBD-4 (cross-repository migration) has no home: FR-18.13, FR-1.3, FR-1.4 and INV-2 jointly forbid it |
| AR-52 | MAJOR | Living Spec | FR-5.12 induction is an on-ramp that becomes an immediate tax with no mechanical off-ramp |
| AR-53 | MAJOR | Security / Audit | FR-6.9's `personal` memory scope defeats U4's audit guarantee, and `sf audit` is definition-only by construction |
| AR-54 | MINOR | Gates | `no-unreviewed-external` is a post-hoc detector presented in a table of blocking gates |
| AR-55 | MINOR | Consistency | FR-3.2 and FR-5.4 disagree about whether Design produces code, and the spec-approval checkpoint arrives after implementation starts |
| AR-56 | MINOR | Consistency | OQ-2, OQ-3 and OQ-6 re-open questions that P0 requirements have already decided |
| AR-57 | MINOR | Intake | FR-18.3 interprets schedules in UTC only; every business-hours automation drifts an hour twice a year |
| AR-58 | MINOR | Definitions | FR-2.2 generates schemas from the parser, so semantic rules are enforced but undocumented, and NFR-4.3 inherits the gap |
| AR-59 | MINOR | Execution | FR-8.7's `--allow-unsandboxed` leaves no mark on the artifacts the unsandboxed run produces |
| AR-60 | MINOR | API | FR-21.7's loopback-plus-token local transport is weak on a shared host and exposes the dashboard to browser-originated requests |
| AR-61 | MINOR | Memory | FR-6.12's "lowest value density" archives the rare-but-critical memories first |
| AR-62 | MINOR | Metrics | §11.1's "evidence-gate false-pass rate: 0" is unfalsifiable and has no defined audit procedure |

