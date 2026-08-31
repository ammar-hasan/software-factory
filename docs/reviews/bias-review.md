# Bias and Epistemics Review — Master PRD v1.0.0

| Field | Value |
| --- | --- |
| Reviews | `docs/PRD.md` v1.0.0 (Baseline, pre-review) |
| Review type | Bias, framing, and epistemic soundness — not completeness, not correctness |
| Date | 2026-08-31 |
| Findings | 72 |

**Scope note.** This review does not assess whether the system described would work. It assesses
whether the document has *earned* its claims: whether requirements are derived from the stated
problems or imported from the one product the authors studied, whether load-bearing assumptions are
argued or asserted, and whether the measurement and acceptance apparatus is constructed to find out
if the thesis is true or to confirm that it is.

**Structural finding that frames all others.** §14 "Traceability" maps each problem P1–P8 forward to
the sections that address it. It never runs the other direction. Nothing in the document requires a
requirement to name the problem that motivates it. As a result, entire requirement families — FR-3
(role taxonomy), FR-4 (stage machine), FR-8 (runners), FR-18 (integration event matrices), FR-19
(factory tool server), FR-22 (recordings) — appear nowhere in §14 and are never asked to justify
themselves. The traceability table produces the *appearance* of derivation while leaving the majority
of the P0 surface unmotivated. **Correction: invert §14. Every FR family must cite the P-number it
serves or be marked `derived: none` and demoted below P0.** Applying that rule mechanically would
demote roughly a third of the current P0 set.

---

## Summary table

| ID | Bias type | Area | Finding |
| --- | --- | --- | --- |
| BR-01 | Anchoring | §7.4 FR-4.2 | The 8-stage linear pipeline is the reference product's UI stage list, not a derivation from P1–P8 |
| BR-02 | Anchoring | §7.3 FR-3.1, INV-1 | "Exactly one Conductor" is a hosted-product identity constraint imported as an architectural invariant |
| BR-03 | Anchoring | §7.3 FR-3.2 | The five specialist roles copy the reference's default agent set; role specialisation is never tested as a hypothesis |
| BR-04 | Anchoring | §7.1 FR-1.3, FR-1.4 | "One policy per factory," sized by product surface, lint-enforced — a multi-tenant SaaS artifact that contradicts §2.2 |
| BR-05 | Anchoring | §7.13 FR-13.6, FR-13.7 | The scorer schema is copied field-for-field, including a UI re-render behaviour, from a form in the reference product |
| BR-06 | Anchoring | §7.15 FR-15.3 | The metric set is the reference dashboard's metric set, designed to make vendor activity legible, not to answer P2 |
| BR-07 | Anchoring | §7.18 FR-18.4, FR-18.11–13 | Filter semantics and the event/filter matrices are lifted verbatim, anchoring the adapter contract to one host's vocabulary |
| BR-08 | Anchoring | §7.19 FR-19.2 | The tool-server surface is the reference's tool list minus onboarding; several tools are meaningless in a local-first system |
| BR-09 | Anchoring | §7.11 FR-11.1, NFR-6.2 | Pluggable foreign harnesses exist because the reference sells them; they falsify FR-9.1 and NFR-5.3 |
| BR-10 | Anchoring | FR-17.1, FR-22.2–3 | `EXECUTOR`/`CREATOR` and screen-recording evidence are demo artifacts elevated to P0 |
| BR-11 | Unjustified assumption | §2.3 | "Why now" asserts the thesis as its own premise; the argument is circular |
| BR-12 | Unjustified assumption | §1.1 | The five-mechanism table states what each mechanism "buys the agent" with no evidence and no separate test |
| BR-13 | Unjustified assumption | §4.1 U2, §11.1 | Assumes reading a machine-written change is cheaper than writing it; asserted as a success criterion |
| BR-14 | Unjustified assumption | §2, §11.1 | Assumes the bottleneck in software delivery is producing changes. Never argued. Nothing measures the alternative |
| BR-15 | Unjustified assumption | §7.13, PR-3 | Assumes "quality" = gates pass + evidence attached; conflates verifiable with correct |
| BR-16 | Unjustified assumption | FR-13.3 | `regression-proven` is assumed to defeat plausible-but-wrong changes; a same-agent test defeats the gate trivially |
| BR-17 | Unjustified assumption | FR-22.4, R-12 | Assumes humans will read evidence bundles; the risk is named and then not measured |
| BR-18 | Unjustified assumption | FR-7.9, FR-5.10, FR-13.13 | Three P0 metrics are defined over quantities that are not computable without ground truth |
| BR-19 | Unjustified assumption | FR-11.6, FR-11.7 | Assumes models can self-report calibrated confidence and that citation requirements fix miscalibration |
| BR-20 | Unjustified assumption | PR-1 vs FR-14.3 | "Files are the source of truth" and "the loop edits the definition" are in unexamined tension |
| BR-21 | Unjustified assumption | §10, FR-13.2 | "…or the factory helps create it" hides the single largest scope item in the document behind a conjunction |
| BR-22 | Confirmation bias | §11.2 conditions A, B | Both controls are strawmen; no competent single-agent baseline exists in the test |
| BR-23 | Confirmation bias | §11.2 AC-3, AC-5 | AC-3 passes when the harness is worth *less* than model capability; AC-5 benchmarks against an unvalidated reference |
| BR-24 | Confirmation bias | §11.2 AC-2 | The 3× cost criterion is satisfiable by the provider price list alone and excludes all harness cost |
| BR-25 | Confirmation bias | §11.2 AC-4 | The ablation has no effect size, no multiplicity correction, and its wording presumes its conclusion |
| BR-26 | Confirmation bias | §11.2 closing line | Only AC-1 and AC-3 are allowed to falsify; the criteria most likely to fail are exempted |
| BR-27 | Confirmation bias | §11.2 Setup, §12 M8 | Task selection is unspecified and the test is scheduled after every design decision is sunk |
| BR-28 | Confirmation bias | §13.2 OQ-7 | Benchmark contamination — a validity threat to the whole test — is an open question due *at* the test |
| BR-29 | Confirmation bias | §11.2 | No pre-registration, no blinding, no held-out set, no analysis plan, no stopping rule |
| BR-30 | Selection/survivorship | §11.1 row 1 | The 40% target's denominator is controlled by the system being measured |
| BR-31 | Survivorship + Goodhart | FR-15.3 Autonomy | Conditioned on merge and on one correction channel; gamed by abandonment and follow-up PRs |
| BR-32 | Goodhart | FR-15.3 Cost per change | Median cost of *opened* changes; gamed by flooding with trivial changes |
| BR-33 | Goodhart | FR-15.3 Rework rate | Stage-return rate is gamed by skipping stages (FR-3.3) and by burning repair attempts inside one stage |
| BR-34 | Goodhart | FR-15.3 Gate pass rate | The improvement loop is explicitly authorised to edit gate thresholds (FR-14.3); the metric rewards it |
| BR-35 | Goodhart | §11.1 row 6 | "≥60% of adopted proposals have positive effect" is gamed by proposing only trivially-winnable changes |
| BR-36 | Goodhart | §11.1 row 7 | "False-pass rate 0 on an audited sample" is unfalsifiable at plausible sample sizes and measures the wrong thing |
| BR-37 | Goodhart | FR-9.8, FR-15.3 | "Pack efficiency" is gamed by deleting the sections least often quoted and most often decisive |
| BR-38 | Goodhart | FR-15.3 Memory health | Contradiction rate goes to zero when Canon is empty; no utility counter-metric exists |
| BR-39 | Goodhart | FR-15.3 Skill health | Conversion and retirement rates are gamed by manufacturing trivial trials and retiring unused skills |
| BR-40 | Goodhart | FR-5.5, FR-5.11 | Agreement-state distribution is improved by retiring drifting spec units rather than reconciling them |
| BR-41 | Survivorship | §11 entire | Measurement stops at merge. There is no post-merge outcome metric anywhere in the document |
| BR-42 | Framing / anthropomorphism | §7.12, PR-5, FR-12.6 | "Courage" turns a containment mechanism into a motivational one and makes prompt text a P0 requirement |
| BR-43 | Framing / anthropomorphism | §1.1, §7.9 | "Awareness" and "it retrieves rather than recalls" assert a mechanism the design cannot enforce |
| BR-44 | Framing | Document-wide | The factory metaphor imports throughput as the primary good; NG-6 disclaims the consequence but nothing implements the disclaimer |
| BR-45 | Framing / anthropomorphism | Glossary, FR-3.3 | "Conductor" licenses an unspecified routing decision; stage-skipping has no procedure, gate, or metric |
| BR-46 | Framing | §1.1, §7.11, §11.2 | Pre-emptive rebuttal ("not a slogan", "mechanism rather than a hope", "falsifiable") substitutes for evidence |
| BR-47 | Framing | §1 | "Most attempts at this fail in one of two ways" is an uncited population claim structured as a false dilemma |
| BR-48 | Framing | PR-6, FR-13.3 | Memorable principles that are false or unimplementable as literally stated |
| BR-49 | Framing | §7.14 | "Self-improvement" names an outcome and presumes the sign of the effect; the loop is on by default |
| BR-50 | Stakeholder | §4.1 | Every persona owns or operates the factory; the reviewer of record has no persona and no protection |
| BR-51 | Stakeholder | §4.2, §11.1 | Junior engineers and human learning are entirely absent, despite P1 being a learning problem |
| BR-52 | Stakeholder | NG-6 | The headcount non-goal is a statement of intent with no design consequence |
| BR-53 | Stakeholder | §10, FR-13.2 | Teams without CI get a factory whose entire quality story silently evaporates |
| BR-54 | Stakeholder | NFR-2.1, NFR-3.1, FR-20.3 | Low-resource operators are excluded by the performance targets while U5 is claimed as a persona |
| BR-55 | Stakeholder | NFR-7.2 | Non-English operation degrades silently; only UI chrome is internationalised |
| BR-56 | Stakeholder | NFR-7.1, FR-21.1, FR-22.3 | Accessibility is P1, dashboard-only, and video evidence has no textual equivalent |
| BR-57 | Stakeholder | §7.17 | Security is present; compliance is absent. No signed decisions, no segregation of duties, no retention floor |
| BR-58 | Optimism / planning | §12 M4, M8 | Milestones bundle unsolved research problems as single exit criteria and defer the acceptance test to last |
| BR-59 | Optimism / planning | NFR-2.1, NFR-4.1, NFR-2.4 | Targets are precise where they are easy and absent where they are hard |
| BR-60 | Optimism / planning | FR-0.2, FR-20.5, NFR-5.3, FR-9.1 | Determinism and cross-executor parity are asserted over a stochastic, time-varying system |
| BR-61 | Optimism / planning | §13.1 | Every risk mitigation is a citation to an unproven requirement; no residual, owner, trigger, or kill criterion |
| BR-62 | Optimism / planning | §13.1 | Three foreseeable failure modes are missing from the risk register, all of them ones the thesis would rather not have |
| BR-63 | Omission | §11, FR-15.3 | Human time consumed by the factory is created by a dozen requirements and measured by none |
| BR-64 | Omission | FR-11.12, FR-10.10 | Cost accounting covers model tokens and tool calls; the harness itself is free by omission |
| BR-65 | Omission | §11.1 | Every target is absolute or self-referential; there is no requirement to measure the counterfactual |
| BR-66 | Omission / self-serving | FR-3.5 | `allowSharedBlindSpot: true` disables the only review-independence mechanism and is neither metered nor gated |
| BR-67 | Omission / self-serving | §13.2 | All eight open questions are tuning questions; not one could sink the design |
| BR-68 | Omission / self-serving | FR-13.7, FR-15.5 | Quality measurement is sampled and optional, yet §11.1 reports as if it always ran |
| BR-69 | Cultural / contextual | FR-18.3, FR-16.4 | UTC-only schedules and always-on checkpoints assume a co-located, always-available team |
| BR-70 | Cultural / contextual | FR-4.4, NG-5, §7.5 | A branch-and-pull-request trunk workflow and mandatory spec-first practice are assumed universal |
| BR-71 | Cultural / contextual | FR-7.13, FR-16.3, FR-16.5 | An individual-ownership model is enforced by lint; collective and rotating stewardship fail validation |
| BR-72 | Cultural / contextual | §2.3, FR-8.5, §11.2 | The local-first story rests on a premise about small models that the acceptance test never actually tests |

---

## 1. Anchoring bias and imitation

The document was written after studying one commercial product in this space. The imitation is not
confined to vocabulary; it reaches the invariants. Below, "the reference" means that product.

### BR-01 — The stage machine is a copied UI, not a derivation

**Bias:** anchoring.
**Text:** FR-4.2: "Stages: `INTAKE → TRIAGE → DESIGN → BUILD → REVIEW → VERIFY → HANDOFF → COMPLETE`,
with `CANCELLED` and `BLOCKED` reachable from any stage. Legal transitions must be an explicit table
in code, and any transition not in the table is a defect."

**Distortion.** This is the reference's activity-view stage list with two additions (`VERIFY`,
`HANDOFF`) and one rename. No problem in §2 requires a linear stage sequence. P1 asks that work
*compound*; P2 asks for attributable cost and return; P4 asks that intent survive. A linear pipeline
serves none of these — it serves a dashboard that needs work items in columns. Worse, the pipeline is
then hardcoded as a P0 invariant ("any transition not in the table is a defect"), which means the
single most consequential architectural commitment in the document was made by imitation and is now
protected from revision by a defect definition.

**What an independent derivation produces.** From P1 and P4 the natural primitive is an artifact
graph, not a phase sequence: a work item accumulates *claims* (a reproduction, a scope estimate, a
spec delta, a diff, a verification) each with provenance, and "stage" is a derived label over which
claims exist. That model handles FR-3.3's stage-skipping natively instead of as an exception, handles
partial re-entry natively, and makes the compounding requirement (P1) structural rather than bolted
on via §7.6.

**Correction.** Demote FR-4.2's enum to a *default profile* of a declared, per-work-class state graph
(`policy/stages.yaml`). Add: "FR-4.2a (P0) — The default stage set is a starting configuration, not
an invariant. A factory may declare its own stages and transitions; the conformance suite must pass
against a two-stage factory (`INTAKE → HANDOFF`) as well as the default." Add to §13.2: "OQ-9 — Is a
linear stage model correct, or is it an artifact of the studied product?"

### BR-02 — "Exactly one Conductor" is a hosted-product constraint

**Bias:** anchoring.
**Text:** FR-3.1: "Every factory declares exactly one agent with role `CONDUCTOR`. Zero or more than
one is a validation error." Reinforced as INV-1 and in the glossary ("Exactly one per factory").

**Distortion.** In the reference, the coordinating agent is singular because it owns an
@-mentionable handle in a chat product and must map 1:1 to a workspace identity. That is a naming and
addressing constraint of a hosted multi-tenant service. The PRD keeps the singleton and drops the
reason: it retains the handle (glossary: "Handle — the name a team @-mentions") in a system whose
headline persona (U5) has no chat tool at all. Meanwhile the singleton creates a real cost the
document never acknowledges: FR-3.6 routes *all* specialist questions through one agent, and FR-3.7
requires it to hold continuing conversations with each specialist. That is a serialisation point and
a single context window that accumulates every work item's history — precisely the failure mode P1
describes, reintroduced at the coordination layer.

**What an independent derivation produces.** Coordination is a *function* (route, ask, hand off), not
an agent. It can be a deterministic policy engine calling a model only for genuinely ambiguous
routing, and it can be instantiated per work item so that no single context accumulates unbounded
state.

**Correction.** Replace FR-3.1 with: "A factory declares exactly one *coordination policy*.
Coordination may be realised by a deterministic router, by one agent, or by one agent instance per
work item; the choice is a configuration, and the routing decision is recorded identically in all
three cases." Delete INV-1 or restate it over policies. Move `handle` to the chat adapter's
configuration, where it belongs.

### BR-03 — The role taxonomy is imported and never tested

**Bias:** anchoring, plus an untested hypothesis presented as a requirement.
**Text:** FR-3.2 (P0): "Built-in roles: `CONDUCTOR`, `SCOUT`, `ARCHITECT`, `BUILDER`, `CRITIC`,
`PROVER`, `CUSTOM`. Role determines default gates, default skills, default Awareness Pack
composition, and default stage association."

**Distortion.** Scout/Architect/Builder/Critic are a rename of the reference's four default agents
(triage, spec, implement, review). Prover is the one addition and it exists to absorb the reference's
recorded-browser-session demo (see BR-10). No problem in §2 mentions role specialisation. The
proposition that decomposing work across four differently-prompted models produces better output than
one model with the same Awareness Pack is an *empirical hypothesis* — and a contested one, since each
handoff loses context (which is P1's complaint) and multiplies cost (which is P2's complaint). The
PRD makes it a P0 requirement and then, in §11.2, never tests it: conditions A–D vary harness and
model tier, never the role decomposition.

**Correction.** Demote FR-3.2's role set to P1 with a default single-agent configuration at P0. Add
to §11.2 a condition **E (treatment): full factory harness, small model, single agent covering all
stages** and an acceptance criterion: "AC-6 — C's pass rate exceeds E's by more than the variance
estimate, and C's cost per passing task is not more than 1.5× E's. Failing AC-6 falsifies role
specialisation and the role set is deleted." Note explicitly in §7.3 that FR-3.5's independent-Critic
requirement is the *only* role separation with a stated mechanism (independent failure modes) and
therefore the only one currently justified.

### BR-04 — Factory sizing rules contradict the document's own §2.2

**Bias:** anchoring, plus internal contradiction.
**Text:** FR-1.3 (P0): "A factory applies **one policy** across all of its intake sources. Repository
groups requiring different policies must be separate factories." FR-1.4 (P0): "Sizing guidance must
be enforced by lint… `sf lint` warns when a factory's repositories have no shared release cadence or
dependency edges." Glossary: "Sized by *product surface*, not by team."

**Distortion.** One-policy-per-tenant is a permission and billing boundary in a hosted product; it is
how a vendor scopes credentials and charges. Imported into a local-first tool it becomes a
constraint with no beneficiary. It also contradicts §2.2, which says "Team size matters less than
*repetition*. A two-person team with a noisy issue tracker benefits as much as a two-hundred-person
one" — a two-person team with three unrelated repositories is now told by lint to run three factories,
three definitions, three ledgers, three memory stores.

**What an independent derivation produces.** Policy is an attribute of a *work class*, not of a
deployment. One factory, many policies, selected by the same filter machinery FR-18.4 already
specifies.

**Correction.** Delete FR-1.4 entirely (a lint rule enforcing an unmotivated sizing heuristic is
active harm). Rewrite FR-1.3: "A factory declares one or more named policies and a deterministic rule
mapping each work item to exactly one. `sf plan` shows the mapping." This also removes the FR-18.13
"at most one tracker per factory" restriction, which follows from FR-1.3 and from nothing else.

### BR-05 — The scorer schema is copied, including a UI behaviour

**Bias:** anchoring.
**Text:** FR-13.6 (P0): "A scorer declares: `name`, `description`, target `agents`, `labels` (each
with a value, score in [0,1], and description), `passingScore`, `samplingRate`, judge `model`, and an
optional `selfImprovement` flag; the body is the rubric." FR-13.7: "Changing `passingScore`
re-renders history but never rewrites recorded classifications."

**Distortion.** This is the reference's scorer configuration form, field for field, camel-case
included, down to FR-13.7's re-render semantics — which is a *display* behaviour of a hosted results
page, transcribed into a functional requirement of a local-first system that may have no dashboard
running. More importantly: no problem in §2 asks for LLM-judge classification. P2 asks "is this worth
it?", which is a question about cost, rework, and merge outcomes — all deterministically observable.
The document reaches for a model-judged rubric because the reference did, then has to spend FR-13.8
building an entire trustworthiness apparatus (human-agreement thresholds, flagging) to compensate for
a mechanism it did not need.

**Correction.** Move FR-13.6 and FR-13.7 to P1. Promote to P0 a deterministic scorer class:
classification by observable outcome (gate results, revert within N days, human push after handoff,
review comment count, reopen rate). Reword FR-13.8 as a *precondition*: "No model-judged scorer may
be enabled until its agreement with a human-labelled sample of at least 50 runs is reported, and no
model-judged scorer may drive FR-14 proposals below an agreement threshold declared in policy."

### BR-06 — The metric set is the reference's dashboard

**Bias:** anchoring.
**Text:** FR-15.3's table: "Runs… Changes opened… Changes merged (sourced separately; may lag)…
Autonomy (share of merged factory changes needing no human code push before merge)… Cycle time…
Cost per change (decomposable by cost component and by change size)…" plus FR-15.4 ("labelled
estimates") and FR-15.5 ("aggregate run counts include evaluation, benchmark, and improvement runs").

**Distortion.** These are the reference dashboard's tiles in order, including its two published
caveats, elevated to P0 requirements. That set was designed to make a vendor's activity legible to a
buyer. It is not a set designed to answer P2 ("what share of changes needed rework, whether last
month's model swap helped, which stage the agents are actually good at"). The distortion is in what
the imported set *cannot* express: there is no metric of harm, no metric of human cost, and no metric
after merge (see BR-41, BR-51, and §4 below). A team could hit every number in FR-15.3 while shipping
more defects, consuming more reviewer hours, and degrading its codebase, and the dashboard would show
improvement throughout.

**Correction.** Keep the imported metrics but require each to ship with a named counter-metric in the
same view (specified per-metric in §4 of this review), and add three P0 metrics the reference has no
reason to want: **human minutes per merged change (all sources)**, **post-merge defect attribution
(reverts, follow-up fixes, incidents within 30 days)**, and **decline rate (factory changes closed
unmerged, with reason)**. Add: "FR-15.3a (P0) — No metric may be displayed without its declared
counter-metric in the same view."

### BR-07 — The integration surface is one host's vocabulary

**Bias:** anchoring.
**Text:** FR-18.4 (P0) filter semantics ("every declared key must match (AND); within a key, any
listed value matches (OR); an omitted key matches everything; and keys support `in`/`not_in` forms").
FR-18.11 (P0) enumerating "issue created/labelled/assigned/mentioned; change
opened/closed/merged/labelled/assigned/mentioned/ready/reopened/synchronised… with filters on
repository, branch, base branch, path, label, author, assignee, mentioned user or team, reviewer,
review state, workflow, and conclusion." FR-18.12, FR-18.13 similarly for chat and trackers.

**Distortion.** FR-18.4 is transcribed verbatim from the reference's documentation. FR-18.11–13 are
its three integrations' event and filter matrices. The effect is that FR-18.2's "adapter contract"
— the requirement meant to guarantee portability (PR-10) — is defined by whatever one git host, one
chat tool, and one tracker happen to emit. Any provider with a different event model is a
second-class adapter, and the normalisation layer will leak the original vocabulary. Note the
irony: the document's stated defence against vendor coupling (R-11) cites FR-18.2, the very
requirement that carries the coupling.

**Correction.** Reduce FR-18.2 to a minimal normalised event: `{source, kind ∈ {work_requested,
work_commented, artifact_changed, check_completed, scheduled}, subject_ref, actor, actor_trust,
payload, dedup_key}`. Move FR-18.11–13's matrices to a non-normative Appendix C labelled "example
provider mappings." Keep FR-18.4 but state that it is a convention chosen for familiarity, not a
derived requirement.

### BR-08 — The tool-server surface is copied into a context where it makes no sense

**Bias:** anchoring.
**Text:** FR-19.2 (P0): "Tool surface: list factories; list, search, and get work items; get a work
item with local setup guidance; message the Conductor; read the conversation; send work in or hand
work back; list notification routes; complete a work item; fetch and validate definition files."

**Distortion.** This is the reference's tool list with its onboarding tools removed. In a local-first
factory, "list factories" returns one, "list notification routes" returns none, and "message the
Conductor / read the conversation" describes a chat affordance the solo operator does not have. The
job actually named in §4.2 (JTBD-7, and the handoff narrative implied by FR-19.4/19.6) is: *pull a
work item into a local worktree, work it, hand it back to the same record*. That is two tools.
Everything else is surface area with security consequence — FR-19.5 already concedes the design's
central weakness ("Picking work up does not claim, lock, or pause it") and mitigates it with
documentation.

**Correction.** Reduce FR-19.2 P0 to: `get_work_item(ref, start_working) → {context, branch, setup}`
and `hand_back(ref, branch_or_change_ref, note)`. Move the rest to P2. Replace FR-19.5's
documentation mitigation with a mechanism: "FR-19.5 (P0) — `get_work_item(start_working=true)`
records a soft claim on the work item, visible to the Conductor, which must not dispatch a
conflicting run while a claim is live; claims expire and are ledger-recorded."

### BR-09 — Pluggable foreign harnesses contradict two other P0 requirements

**Bias:** anchoring, with an unnoticed contradiction.
**Text:** Glossary: "Harness — The agent runtime driving the model loop. `loom` is built in; external
harnesses are adapters." FR-11.1 (P0) makes the harness interface implementation-independent.
NFR-6.2 (P0) forbids dependence on a specific harness.

**Distortion.** The reference supports multiple harnesses because it sells a terminal and integrates
competing agent CLIs; multi-harness support is a commercial position. Imported here, it collides with
the document's own foundations. FR-9.1 requires that pack assembly be "a pure function… Given
identical inputs it must produce an identical pack; the pack's digest is recorded." NFR-5.3 requires
"identical inputs produce identical non-model outputs." A foreign harness controls its own prompt
assembly, its own tool dispatch, its own context management and its own compaction — so under a
foreign harness the pack digest is recorded and then ignored, and the determinism claim is
unverifiable. FR-3.5 compounds this: the Critic must differ from the Builder in "model *and* harness",
which makes multi-harness support load-bearing for the review-independence story.

**Correction.** Move external-harness adapters to P2 and mark them, per PR-2, as an extension. Add to
§13.1: "R-13 — Foreign harnesses void the determinism (NFR-5.3), pack-digest (FR-9.1), and parity
(FR-20.5) guarantees; runs under a foreign harness must be flagged in the ledger and excluded from
conformance and from §11.2." Rewrite FR-3.5 to require independence along an axis the factory
controls: different model family *and* a Critic pack that excludes Builder reasoning (which FR-9.7
already specifies and which is the mechanism that actually does the work).

### BR-10 — Demo artifacts elevated to P0

**Bias:** anchoring on a product demonstration.
**Text:** FR-17.1's credential table: "Repository identity | Checkout and push | `EXECUTOR` (acting
principal) or `CREATOR` (requesting user); declared, not inferred." FR-22.2 (P0) lists "screen or
browser recordings for user-facing changes" as an evidence class; FR-22.3 (P0) makes browser-driven
verification "optional but first-class"; AN-1 requires "for a user-facing change — a UI recording."

**Distortion.** `EXECUTOR`/`CREATOR` is a multi-user hosted identity model; in local mode (PR-2, the
*reference implementation*) there is exactly one principal and the distinction is noise carried
through the schema, the audit report, and the docs. Screen recording is the reference's most
demonstrable feature and is the least evidenced requirement here: no problem in §2 concerns
unverifiable UI changes, and the document never asks the obvious question — whether a video *increases*
reviewer time relative to a deterministic assertion. FR-22.3's degradation path only handles the
recording being *absent*, never it being long, unwatched, or inaccessible (see BR-56).

**Correction.** Mark the `CREATOR` strategy "hosted extension (PR-2)" and require local mode to
reject it at validation. Demote FR-22.3 to P2 and require, before promotion: a measurement of
reviewer time with and without recordings on matched changes. Reword AN-1 to drop the UI recording,
which currently makes a P2 capability load-bearing in the document's flagship narrative.

---

## 2. Unjustified load-bearing assumptions

These are propositions the document treats as settled. Each carries weight; none is argued.

### BR-11 — §2.3 assumes the thesis it is used to justify

**Bias:** circular reasoning presented as context.
**Text:** §2.3: "Three things changed. Coding agents became good enough that a well-scoped change is
routinely achievable. Structured tool use became reliable enough to build deterministic scaffolding
around a model rather than inside it. And small models became good enough that, given excellent
context, they handle most factory work — which makes the harness, not the model bill, the thing worth
optimising."

**Distortion.** The third clause *is* the central bet of §1.1. §1.1 presents the bet as a design
constraint to be tested in §11.2; §2.3 presents it as an established fact about the world that
justifies building the thing. Whichever is true, the document cannot have it both ways. All three
claims are unsourced, and none carries a definition — "good enough", "routinely achievable",
"reliable enough", "most factory work" are all unquantified, so none is checkable and none can be
wrong.

**Correction.** Rewrite §2.3 as declared premises with falsification conditions, e.g.: "Premise W3 —
small models, given assembled context, reach ≥85% of large-model pass rate on the task classes in
§11.2. This premise is untested; §11.2 tests it. If it is false, §7.9–§7.11 do not pay for themselves
and the design should route to larger models by default." Delete every unquantified "good enough."

### BR-12 — The five-mechanism table asserts its own value

**Bias:** assertion in place of evidence; hypothesis stated as specification.
**Text:** §1.1's table, column "What it buys the agent": Awareness — "The agent starts every task
already knowing… It retrieves rather than recalls." Tools — "The model is spent on judgement, not on
arithmetic." Confidence — "The harness routes low-confidence work to verification." Courage — "A bold
approach costs a rollback, not an incident." Quality — "A change is not presentable until its gates
pass."

**Distortion.** Five independent empirical claims, each about a causal effect on model output, stated
in the present indicative as if measured. Then §11.2 tests only their conjunction: AC-4 ablates four
of the five (Confidence/calibration is not ablated at all) with no effect-size requirement. The
document therefore claims five mechanisms and can substantiate at most "the bundle beats nothing."

**Correction.** Restate the table's third column as "Hypothesised effect" and add a fourth column,
"Test", naming the §11.2 condition or ablation that would confirm it — leaving visibly blank the ones
with no test. Add calibration to AC-4's ablation set.

### BR-13 — "Review is cheaper than writing" is assumed, targeted, and never argued

**Bias:** convenient assumption; target set to the assumption.
**Text:** §4.1 U2 success criterion: "reviewing a factory change is faster than writing it, and they
trust its evidence." §11.1: "Review is cheap | Median human review time for a factory change vs. a
comparable human change | ≤ 1.0×."

**Distortion.** Review cost is dominated by unfamiliarity with the change's intent, and a
machine-authored change is maximally unfamiliar: the reviewer did not form the mental model that
produced it, and the diff carries no social context about why alternatives were rejected (FR-12.3
records rejected alternatives as *more* material to read, not less). The evidence bundle (FR-13.12)
adds structured test results, diffs, transcripts, recordings, gate outcomes, scorer results and a
calibration statement to the reviewer's queue. The document asserts the direction of the effect
in a persona success criterion and then makes the same assertion the target. Note that a ratio of
exactly ≤1.0× is also the weakest possible bar: any value at or below parity passes, including 1.0×
with a bundle ten times larger.

**Correction.** Replace the §11.1 row with a *measured, unassumed* pair: "Median reviewer minutes per
merged factory change" and "Median reviewer minutes per merged human change", reported side by side
with no target for v1, plus "share of factory changes where the reviewer reports lower confidence
than for a comparable human change." Change the U2 success criterion to "reviewing a factory change
is *no more effortful* than reviewing a comparable human change, and the reviewer can say why they
trust or distrust it."

### BR-14 — The bottleneck assumption is never stated, let alone defended

**Bias:** framing bias with the largest possible consequence.
**Text:** The entire §11.1 target set: reach-a-reviewed-change rate, autonomy, cost per change,
rework, cycle time. §1: "turns them into a steady stream of reviewable, mergeable changes."

**Distortion.** Every target measures the rate of change *production*. This presupposes that
production is the constraint. If the constraint is review capacity, decision latency, requirements
quality, or deployment risk tolerance, then a system that increases production increases queue length,
work-in-progress, and context-switching, and every §11.1 target will improve while delivery gets
worse. This is not a hypothetical: it is the standard failure mode of throughput optimisation at a
non-bottleneck station, and the document's own metaphor (§1, "factory") comes from the literature that
established it. The document never identifies the constraint, never measures it, and has no metric
that would move if it were wrong.

**Correction.** Add to §2 a subsection "§2.4 — What we believe the constraint is, and how we would
know we are wrong," stating the belief explicitly and naming the disconfirming observation (reviewer
queue depth rising, time-in-review rising, merged-per-week flat while opened-per-week rises). Add to
§11.1: "Delivery is actually faster | Merged changes per engineer per week, factory-attributed and
total | Total must not fall." Add to §13.1: "R-14 — The factory increases production at a station
that is not the constraint, raising WIP and review latency while every dashboard metric improves.
Mitigation: WIP cap per repository, queue-depth metric, and a hard stop when time-in-review rises."

### BR-15 — "Quality" is defined as "passed the checks we wrote"

**Bias:** operationalisation bias; the measurable substituted for the meaningful.
**Text:** §1.1: "Quality | FR-13 Gates & evidence | Verification is not advice. A change is not
presentable until its gates pass and its evidence bundle is attached." PR-3: "No stage completes on
an agent's say-so. Claims carry artifacts."

**Distortion.** Gates check that assertions resolve to artifacts. They do not check that the artifacts
*support* the assertions, that the change is the right change, or that it does not break something no
test covers. The `evidence-complete` gate (FR-22.6) fails "a claim with no corresponding artifact" —
which is a syntactic property. INV-6 makes this explicit: "Every claim in a stage summary resolves to
an `Evidence` row." An agent that makes only claims it can trivially evidence passes perfectly. The
document's definition of quality is therefore closed under the agent's own choice of what to claim.

**Correction.** Add: "FR-13.15 (P0) — Adversarial evidence audit. On a sampled share of completed work
items, a human or independent agent is given the summary and the bundle and asked whether each claim
is *supported*, not merely *accompanied*, by its artifact. The support rate is a P0 metric and the
§11.1 'nothing is silently wrong' row measures it." Reword §1.1's Quality row to "A change is not
presentable until its gates pass — noting that gates check evidence exists, not that it is
sufficient."

### BR-16 — `regression-proven` is assumed to defeat the failure mode it is credited with

**Bias:** unjustified confidence, load-bearing for R-1.
**Text:** FR-13.3 (P0): "A fix without a test that demonstrably fails before it and passes after it
does not pass Build. **This single gate is the strongest available defence against
plausible-but-wrong changes.**" R-1 lists it first among four mitigations for the document's only
Critical risk about correctness.

**Distortion.** The test and the fix are produced by the same agent from the same reading of the
defect. If the agent misunderstands the defect, it writes a test that encodes the misunderstanding;
that test fails at the parent commit and passes at the tip, and the gate is satisfied by a change that
is confidently, evidencedly wrong. The gate proves the change *does something the agent intended*. It
says nothing about whether the intent matched the report. §11.3's subsystem acceptance ("`regression-
proven` cannot be satisfied by a test that passes at the parent commit") tests only the mechanical
property, not the failure mode it is claimed to prevent.

**Correction.** Delete the superlative. Add: "FR-13.3a (P0) — For defect work, the regression test's
*correspondence to the report* is a separate, gated check: the test must cite the reported symptom
(the failing input, the error text, the reproduction step) from the work item's source context, and
the Critic must independently confirm the correspondence without seeing the Builder's rationale
(FR-9.7)." Add to R-1's mitigation column that the residual risk is unquantified until BR-15's
adversarial audit runs.

### BR-17 — The document assumes evidence gets read, names the risk, then does not measure it

**Bias:** convenient assumption with a decorative mitigation.
**Text:** R-12: "**Evidence theatre**: bundles grow without adding trust | Medium | Evidence must
resolve claims, not accompany them (FR-22.6); bundle-size vs. review-time metric." FR-22.4 (P0):
"Evidence must be reviewable in the tool where the human already is."

**Distortion.** The risk is correctly identified and then defused by two things that do not address
it. FR-22.6 is the resolution rule already shown (BR-15) to be syntactic. The "bundle-size vs.
review-time metric" appears nowhere in FR-15.3, has no ID, no definition, no target, and no owner — it
exists only in the mitigation cell of the risk it is mitigating. Meanwhile the whole §7.22 apparatus
is P0 and its cost — capture, storage (FR-15.10 retention), attachment (FR-22.4), and reviewer
attention — is unbudgeted.

**Correction.** Promote the mitigation to a real requirement: "FR-15.3b (P0) — Evidence utilisation:
per evidence class, record whether it was opened, by whom, and the reviewer's marking of it as
decisive / useful / ignored. An evidence class whose 'ignored' rate exceeds a declared threshold is
proposed for removal from the default bundle." Add a §11.1 row: "Evidence earns its place | Share of
bundle bytes in classes with >50% ignored rate | Decreasing."

### BR-18 — Three P0 metrics are defined over uncomputable quantities

**Bias:** measurement optimism; specifying a number that cannot be produced.
**Text:** FR-7.9 (P0): "The registry must compute, per skill: selection precision (selected and
helped), **recall (should have been selected)**, and collision rate." FR-5.10 (P1) and FR-13.13 (P1):
"criteria whose tests have never failed (suspicious…)"; "A test that has never failed is unproven, not
proven."

**Distortion.** *Recall* requires knowing, for every run where a skill was not selected, whether it
should have been — i.e. a counterfactual ground truth that does not exist. "Selected and helped"
requires attributing a run's outcome to one loaded skill among a ranked budget of several (FR-7.10),
which is a causal-inference problem, not a computation. And "a test that has never failed is unproven"
is asserted as an epistemic principle, but the population of never-failed tests is dominated by tests
of things that simply do not break; treating never-failing as suspicious will generate a permanent
stream of false improvement work. In all three cases the document specifies the number, gates on it
(FR-7.9: "Skills below threshold are flagged"), and does not specify how it is obtained.

**Correction.** Replace FR-7.9's recall with a measurable proxy and say so: "selection recall is
estimated only by deliberate A/B injection — a declared share of runs is given the skill it was not
ranked for, and outcome difference is recorded; recall is reported with its confidence interval or as
`unavailable` (FR-15.5)." Replace "selected and helped" with "selected and cited in the output, plus
outcome difference under ablation." For FR-13.13, replace "never failed" with "never failed *and*
mutation-testing or fault-injection shows it does not detect an introduced fault" — the property
actually intended.

### BR-19 — Calibration is assumed to be elicitable

**Bias:** assumption about model capability, load-bearing for the escalation ladder.
**Text:** FR-11.6 (P0): "Every agent's final output must include a structured self-assessment:
confidence per acceptance criterion… Confidence without cited evidence must be treated as zero by
downstream gates." FR-11.4 (P0): a run may escalate when "the agent's calibrated confidence stayed
below threshold after retrieval." FR-11.7 (P0) scores calibration and calls miscalibration "a defect."

**Distortion.** Self-reported confidence from language models is weakly related to correctness and is
sensitive to prompt phrasing, output length, and the request for confidence itself. The document
assumes not only that confidence can be elicited but that it can be *calibrated per acceptance
criterion*, that requiring citations converts it into a reliable signal, and that it is a sound
trigger for spending more money (FR-11.4). The "treated as zero" rule fixes nothing: it converts an
unreliable confidence signal into an unreliable citation-production signal, and citation production
is exactly what a model can do fluently while being wrong. FR-11.7's framing ("a miscalibrated agent…
is a defect") assigns blame to the agent for a property of the technology.

**Correction.** Demote calibration-triggered escalation (FR-11.4 clause 2) to P1 pending evidence.
Add: "FR-11.7a (P0) — Before stated confidence may gate or trigger anything, the factory must report
its discrimination (AUC of stated confidence against observed outcome) over at least 200 scored runs.
Below a declared floor, confidence is recorded but must not influence routing." Reword FR-11.7 to
describe miscalibration as an observed property of a model/prompt pair, not a defect of an agent.

### BR-20 — "Files are the source of truth" and a self-editing loop are in unexamined tension

**Bias:** omission of an internal contradiction.
**Text:** PR-1: "If it changes the factory's behaviour, it is a file in a repository, reviewable and
revertible." FR-14.3 (P0): "Proposals may target the factory's **own definition** — a prompt, a skill,
an Awareness Pack weight, a tier assignment, a gate threshold, a runner."

**Distortion.** The two are compatible mechanically (proposals become file changes) and incompatible
epistemically: once the loop can edit gate thresholds and pack weights, the definition's *history* no
longer records human intent, it records an optimisation trajectory against a scorer. PR-7 ("people
dispose") is the stated safeguard, but a human reviewing the 40th threshold nudge with a green
benchmark attached is rubber-stamping, and the document knows this — it is exactly the R-10 "operator
overload" dynamic, applied to the one place where oversight matters most. Nothing bounds the rate,
the cumulative drift, or the fraction of the definition that is machine-authored.

**Correction.** Add: "FR-14.10 (P0) — Definition provenance. Every definition file records the share
of its current content that originated from an accepted improvement proposal, and `sf lint` fails when
a policy file (`policy/`, `scorers/`) exceeds a declared machine-authored share. Cumulative drift from
the last human-authored baseline is a dashboard metric." Add to §13.1: "R-15 — Definition capture: the
loop optimises the definition against its own scorers faster than humans can meaningfully review, and
oversight becomes ceremonial."

### BR-21 — The largest scope item in the document is hidden inside a conjunction

**Bias:** omission by grammar.
**Text:** §10 Assumptions: "Repositories have some form of runnable validation, **or the factory helps
create it.**"

**Distortion.** Everything in §7.13 depends on the first clause. `build-green`, `tests-pass`,
`regression-proven`, `coverage-of-criteria` (FR-13.2) all presume a working, fast, reliable test
harness. The second clause — building test infrastructure for a repository that has none — is a
larger engineering problem than most of §7 and has no requirement, no milestone in §12, no acceptance
criterion in §11.3, and no risk in §13.1. It exists as a subordinate clause in the assumptions
paragraph. Its presence lets the document avoid ever describing the no-validation configuration,
which is the configuration many of the teams described in §2.2 are actually in.

**Correction.** Split the clause. Keep "repositories have runnable validation" as a stated assumption
with a scope boundary. Delete "or the factory helps create it" or promote it to a named, scheduled
FR family. Add: "FR-13.16 (P0) — Degraded assurance mode. Where no runnable validation exists, the
factory must (a) refuse to report autonomy or gate-pass metrics, (b) mark every produced change
`assurance: none` in a machine-readable field carried into the change description, and (c) state on
every handoff which gates could not run." See also BR-53.

---

## 3. Confirmation bias in the acceptance test (§11.2 and §11.1)

§11.2 is the document's claim to rigour. It is the section most in need of it. Read as a whole, the
test is constructed so that the outcome that would be embarrassing cannot occur.

### BR-22 — Both control conditions are strawmen

**Bias:** confirmation bias via baseline selection.
**Text:** §11.2 conditions: "A (control) | Bare harness: task text only, no pack, no gates, no skills,
no memory | Large. B (control) | Bare harness | Small."

**Distortion.** No one runs a frontier model this way. The realistic comparator — the one every reader
will actually have to choose between — is a capable single-agent coding tool with repository access,
file and search tools, the ability to run tests, and a few paragraphs of project instructions. That
baseline is missing. Condition A gives the large model *no ability to read the repository*, which
means AC-1 ("C's pass rate exceeds A's") is essentially guaranteed and measures nothing about the
harness: it measures that reading the code helps. The parenthetical gloss on AC-1 — "*A small model in
this harness beats a large one outside it*" — states a conclusion the experiment cannot support,
because "outside it" has been defined as "blindfolded."

**Correction.** Replace condition A and add two:
- **A′ (control, realistic):** large model, off-the-shelf agent loop with repo read/search/edit/test
  tools and the repository's own contributor instructions. No pack, no gates, no skills, no memory.
- **B′ (control, realistic):** the same at small tier.
- **F (control, cheap):** large model, A′ tooling, plus *only* the Awareness Pack (no gates, skills,
  memory, roles, or stage machine) — the cheapest thing that might explain the result.
Restate AC-1 against A′, and add "AC-1b — C's pass rate exceeds F's" as the criterion that actually
tests whether the rest of §7 earns its cost.

### BR-23 — AC-3 passes precisely when the bet is losing, and AC-5's reference is unvalidated

**Bias:** criterion whose pass condition is compatible with the thesis being false.
**Text:** "AC-3 — C's pass rate is at least 85% of D's. *(Most of the value comes from the harness.)*"
"AC-5 — C's calibration error is no worse than D's."

**Distortion.** AC-3's pass condition is C being *worse* than D. The parenthetical does not follow
from the criterion: if the harness lifts the large model as much as the small one (the most likely
outcome — better context helps stronger models too), then C ≈ 0.85·D is exactly what you would see,
AC-3 passes, and the correct operational conclusion is "use the harness *and* the large model," which
is the opposite of the bet. Nothing in the test can distinguish "the harness substitutes for model
capability" from "the harness is a multiplier and you should still pay for the big model" — and the
second is the commercially and practically decisive question. AC-5 has the same structure: D's
calibration is never established as adequate, so "no worse than D" is a comparison to an unknown.

**Correction.** Replace AC-3 with a criterion about *marginal value*, which is what the bet actually
claims: "AC-3′ — The harness lift for the small tier exceeds the harness lift for the large tier:
(C − B′) > (D − A′), with the difference exceeding the pooled variance estimate. A result where
(D − A′) ≥ (C − B′) falsifies the bet regardless of C's absolute pass rate." For AC-5, require an
absolute floor: stated confidence must discriminate outcomes at a declared AUC in condition C, or
calibration-driven routing (FR-11.4) is disabled.

### BR-24 — AC-2 measures the price list, not the harness

**Bias:** criterion satisfiable without the thing being tested.
**Text:** "AC-2 — C's cost per passing task is below A's by at least 3×."

**Distortion.** Price ratios between small and large tiers routinely exceed 10×. A small model in a
harness need only avoid being 3× *less* efficient in tokens to satisfy AC-2 — and it will, because it
is compared against a control that has no tools and therefore burns very few tokens. The criterion
inverts: the more wasteful condition A is made, the harder AC-2 becomes; as specified, A is maximally
frugal. Second, "C's cost" is undefined and, by §11.2's silence, excludes the entire harness: indexing,
pack assembly, memory policing (FR-6.5's continuous pass), gate re-runs, bounded repair loops
(FR-13.5), discarded speculative branches (FR-12.3), sampled scorer runs (FR-13.7), and escalations.
FR-13.10 requires benchmarks to "state explicitly what [cost] does and does not include"; §11.2 does
not apply its own rule to itself.

**Correction.** "AC-2′ — C's *fully loaded* cost per passing task — including indexing, pack assembly,
all model calls at any tier, all repair and speculative work, all gate and scorer executions
attributable to the task, and amortised memory/skill maintenance — is below A′'s by at least 3×, and
this decomposition is published." Add the same fully-loaded definition to FR-15.3's "cost per change."

### BR-25 — The ablation is statistically undefined and rhetorically pre-decided

**Bias:** confirmation bias; garden of forking paths.
**Text:** "AC-4 — Ablating any one of {Awareness Pack, gates, skills, memory} from C **measurably
reduces** its pass rate, **establishing that each subsystem earns its place**."

**Distortion.** "Measurably" is undefined: no effect size, no significance criterion, no correction
for four simultaneous comparisons. With 40 tasks × 5 repetitions and a binary outcome, the noise floor
is large; with four ablations, at least one apparent reduction is near-certain under the null. The
clause "establishing that each subsystem earns its place" tells the reader what the result will mean
before the result exists, and note that "earns its place" is not the same claim as "reduces pass rate"
— a subsystem could reduce pass rate slightly while costing more than the reduction is worth, and
AC-4 would still pass. Calibration (FR-11) is absent from the ablation set despite being one of the
five mechanisms in §1.1.

**Correction.** "AC-4′ — For each of {Awareness Pack, gates, skills, memory, calibration}, ablation
reduces pass rate by at least a pre-registered effect size δ, at a significance level corrected for
five comparisons, *and* the cost of the subsystem per task is less than the value of the pass-rate
delta at a declared value-per-passing-task. A subsystem failing either test is removed from v1 or
demoted to opt-in." Pre-register δ and the value-per-task before the run.

### BR-26 — Falsification is granted only to the criteria most likely to pass

**Bias:** selective falsifiability.
**Text:** "Failing AC-1 or AC-3 falsifies the central bet and requires the design to change, not the
target."

**Distortion.** AC-1 is near-guaranteed (BR-22) and AC-3 passes even when the bet is losing (BR-23).
The three criteria with real teeth — AC-2 (cost), AC-4 (per-subsystem value), AC-5 (calibration) — are
excluded from falsification. The sentence reads as a commitment to intellectual honesty and functions
as an exemption. A reader skims it and concludes the authors have bound themselves; they have bound
themselves to the two outcomes they cannot lose.

**Correction.** "Failing **any** of AC-1b, AC-2′, AC-3′, AC-4′, or AC-5′ falsifies the corresponding
claim. AC-3′ or AC-1b falsifies the central bet and the design changes. AC-4′ falsifies the subsystem
tested and that subsystem is removed from v1. AC-2′ falsifies the cost claim and §1.1's cost language
is deleted. No target is revised after data collection begins."

### BR-27 — Task selection is unspecified and the test runs after all decisions are sunk

**Bias:** selection bias plus commitment escalation.
**Text:** "**Setup.** A fixed benchmark suite of at least 40 tasks drawn from real work items across
at least three repositories and at least three task classes (defect fix, small feature, refactor)."
§12: "M8 — Hardening | … | §11.2 acceptance test run and published."

**Distortion.** Who draws the tasks, from what population, under what inclusion rule, and at what
point relative to development, are all unspecified. The natural process — the authors pick 40 items
they consider representative from repositories they know — selects for tasks the factory handles.
"Defect fix, small feature, refactor" excludes the classes most likely to fail: cross-cutting changes,
ambiguous requests, changes requiring domain knowledge not in the repository, and changes where the
right answer is "don't". Scheduling the test at M8, after nine milestones of construction, guarantees
that a failing result meets maximum sunk cost. The document even anticipates this and closes the exit:
its own line says the design must change, not the target — a promise made by people who at that point
will have built everything.

**Correction.** (a) Specify sampling: "tasks are drawn by uniform random sample from work items closed
in a declared prior window, with the only exclusion being items requiring credentials the benchmark
cannot hold; the sample is drawn and frozen before M2, and published." (b) Add a fourth task class:
"ambiguous or under-specified requests, where a correct outcome may be a clarifying question or a
refusal," scored accordingly. (c) Move a reduced version of the test to a **go/no-go gate at M2 and
M4** — 20 tasks, conditions A′/B′/C/F only — so that a negative result arrives while the design can
still change. (d) Pre-commit in §12 to what is deleted if M2's reduced test fails.

### BR-28 — The validity threat to the whole test is an open question due at the test

**Bias:** deferral of an inconvenient question.
**Text:** §13.2: "OQ-7 | Should benchmark tasks be shipped as a public suite, given contamination
risk? | Assurance | **M8**."

**Distortion.** Contamination is not a publication question, it is a validity question, and it applies
whether or not the suite is published: tasks drawn from real work items in public repositories may
already be in training data, and the effect will differ between tiers — which is the exact comparison
AC-1 and AC-3 make. If large models have memorised more of the sample than small ones, condition D is
inflated and AC-3 is easier; if the reverse, harder. The document files this under "open questions"
due at the same milestone as the test it invalidates.

**Correction.** Move contamination to a §11.2 methodology requirement due at M2: "Task provenance must
be recorded (repository visibility, item date, whether the fix is publicly visible). At least 40% of
tasks must be drawn from private repositories or from items dated after the declared training cutoff
of every model under test. Results are reported split by contamination-risk stratum, and a
between-stratum difference exceeding the variance estimate invalidates the aggregate comparison."

### BR-29 — No pre-registration, no blinding, no held-out set, no analysis plan

**Bias:** the absence that makes all the above unfixable in practice.
**Text:** §11.2 in full. Compare FR-14.7, which *does* require held-out validation — for the
self-improvement loop, but not for the document's own central claim.

**Distortion.** The document imposes on its subsystem (FR-14.7: "validated against a **held-out** task
set the proposing loop cannot see") a standard it does not impose on itself. §11.2 has no
pre-registered analysis plan, no held-out split, no blinding of scorers to condition (and scorers are
model judges under FR-13.6, running on transcripts that reveal which condition produced them), no
stopping rule, and no pre-declared handling of ties, partial passes, or excluded runs. Every degree of
freedom listed is one the authors will exercise after seeing the data.

**Correction.** Add to §11.2: "**Pre-registration.** Before the first run, publish in the repository:
the frozen task list with provenance, the exact success criterion per task, the analysis script, the
effect sizes and significance criteria, the exclusion rules, and the stopping rule. The commit hash is
cited in the published result. **Blinding.** Condition labels are stripped from transcripts before
scoring; scorer judges see outputs only. **Held-out.** 25% of tasks are sealed until the analysis
script is frozen. **Deviation log.** Every departure from the pre-registration is recorded with a
reason in the published result."

### BR-30 — The headline §11.1 target's denominator is controlled by the system being measured

**Bias:** selection bias built into a target.
**Text:** §11.1: "The factory does real work | Share of intake work items reaching a reviewed change |
≥ 40% for defect-class work."

**Distortion.** "Intake work items" are what the automations admitted. FR-18.4 gives filters, FR-18.6
requires that "default templates must set a restrictive filter rather than an open one," and FR-18.3
lets the operator narrow triggers arbitrarily. The metric therefore measures the ratio of easy work to
admitted work, and the fastest route to 40% is to admit less. A factory admitting only issues already
labelled `factory-ready` by a human will post an excellent number while doing nothing a human was not
already going to route. The "defect-class" qualifier narrows it further with no definition of who
classifies.

**Correction.** Report the metric against the *unfiltered* stream: "Share of **candidate events**
(all events matching the provider subscription before filters) that reach a reviewed change," with
the filtered figure alongside and the admission rate shown explicitly. Add a §11.1 row: "Coverage |
Share of candidate events admitted | Reported, no target — a falling admission rate with a rising
success rate is measurement, not improvement."

---

## 4. Survivorship, selection, and Goodhart

Every metric below can improve while the underlying reality gets worse. For each, the specific gaming
strategy is stated — not as an accusation of intent, but because a self-improvement loop (FR-14) that
optimises against these metrics will find these strategies whether anyone intends them or not. That is
the point: FR-14.3 explicitly authorises the loop to edit prompts, pack weights, tier assignments and
gate thresholds, so these are not hypothetical adversaries. They are the search space.

### BR-31 — Autonomy is conditioned on merge and on a single correction channel

**Bias:** survivorship bias, doubly.
**Text:** FR-15.3: "Autonomy | Share of merged factory changes needing no human code push before
merge." §11.1: "≥ 25% and rising."

**Distortion.** The denominator is *merged* changes, so every change abandoned, closed, or quietly
rewritten from scratch leaves the population and improves the number. The numerator counts one
correction channel — a code push to the same branch — so a reviewer who instead files a follow-up
issue, opens a separate fix PR, or asks the factory to redo it entirely leaves the change "autonomous."

**Gaming strategy.** Abandon anything that attracts review comments (close it, open a fresh one), and
route all corrections into follow-up work items. Both are natural behaviours, not sabotage. Autonomy
rises monotonically while the work done by humans is unchanged.

**Counter-metrics (add to FR-15.3, P0).** *Abandonment rate*: factory changes closed unmerged, with
reason. *Correction-elsewhere rate*: merged factory changes with a human-authored fix touching the same
files within 30 days. *Redo rate*: work items whose change was discarded and re-worked. Report autonomy
only alongside all three; forbid displaying autonomy alone (per BR-06's FR-15.3a).

### BR-32 — Cost per change is a median over a population the factory chooses

**Bias:** Goodhart, via denominator control.
**Text:** FR-15.3: "Cost per change | Median cost of changes opened in the window." §11.1: "Cost is
known and falling | Median cost per opened change, trend over 90 days | Decreasing."

**Distortion.** Median over *opened* changes, with no weighting by size, value, or whether the change
merged. FR-3.8 explicitly invites custom agents for "documentation, security sweep, dependency hygiene,
release notes, dead-code removal" — all of which produce cheap, numerous changes.

**Gaming strategy.** Add a scheduled automation that opens dependency-bump or lint-fix changes. Each
costs a fraction of a real change. The median drops immediately and keeps dropping as the trivial
population grows. The trend line in §11.1 goes green while the cost of the work anyone cares about is
unchanged or rising.

**Counter-metrics.** Report cost per change *stratified by change size* (FR-15.3 already offers this
decomposition — make it mandatory, not optional) and cost per **merged** change; report the full
distribution (p10/p50/p90) not the median; report cost per merged change *that survived 30 days
without a follow-up fix*. Add a §11.1 row: "Trivial-change share | Share of opened changes under 20
changed lines | Reported, no target."

### BR-33 — Rework rate measures the one form of rework the system can avoid recording

**Bias:** Goodhart, via definitional narrowness.
**Text:** FR-15.3: "Rework rate | Share of work items returning to an earlier stage at least once."
§11.1: "Rework is contained | Share of work items returning to an earlier stage more than once |
≤ 15%."

**Distortion.** "Returning to an earlier stage" is a transition in the stage machine. Two large classes
of rework are invisible to it: (a) FR-13.5's bounded repair loop, which re-runs gates inside one stage
and never transitions; (b) FR-3.3's stage skipping, where the Conductor "must be able to skip stages
that do not apply" — a skipped stage cannot be returned to.

**Gaming strategy.** Skip Design routinely (record a reason, as FR-3.3 permits), so there is no Design
to return to. Raise the repair-attempt bound so failures resolve inside Build. Rework rate falls to
near zero while total work per item rises.

**Counter-metrics.** *Repair attempts per work item* (total, and per gate). *Stage-skip rate with
outcome*: share of items where a stage was skipped, and their pass rate versus non-skipped. *Human-side
rework*: reviewer-requested changes after handoff. Redefine the §11.1 row over total attempts, not
transitions.

### BR-34 — Gate pass rate rewards the loop for weakening gates

**Bias:** Goodhart, with an explicit authorisation to game.
**Text:** FR-15.3: "Gate pass rate | First-attempt pass rate per gate." FR-14.3 (P0): "Proposals may
target the factory's own definition — a prompt, a skill, an Awareness Pack weight, a tier assignment,
**a gate threshold**, a runner."

**Distortion.** The document names gate thresholds as a legitimate target of automated optimisation and
separately makes gate pass rate a headline metric. FR-14.7 flags scorer/gate/eval edits as
"self-referential" and requires "stricter review" — it does not forbid them, does not define "stricter,"
and puts a human in front of a proposal that arrives with a green benchmark attached.

**Gaming strategy.** Propose a series of individually reasonable threshold relaxations, each validated
on the held-out set (which measures pass rate, not escaped defects). Pass rate rises; the gates admit
more.

**Counter-metrics.** *Gate strictness index*: for each gate, the share of a fixed, frozen "known-bad"
corpus it still rejects — re-run on every gate change, and a fall is a hard block. *Escaped-defect
rate*: defects reaching merge that a gate was designed to catch. Amend FR-14.7: "A proposal editing a
gate, scorer, or eval must be accompanied by the strictness index before and after on the frozen
known-bad corpus, must not reduce it, and cannot be adopted on benchmark evidence alone."

### BR-35 — The improvement-loop target rewards timidity

**Bias:** Goodhart, plus self-assessment.
**Text:** §11.1: "The loop works | Adopted improvement proposals with a measured positive effect |
≥ 60%." FR-14.8: "Track proposals opened, adopted, rejected, and reverted, and the measured effect of
adopted ones."

**Distortion.** The loop chooses which proposals to make, and the metric counts the hit rate of
*adopted* proposals as judged by the loop's own scorers. Nothing measures ambition, coverage, or the
size of the effect — only the sign, on the proposals that survived to adoption.

**Gaming strategy.** Propose only prompt-wording and pack-weight tweaks whose effect is near-certain
and measurable within one benchmark window. Never propose anything structural, because structural
changes have uncertain sign. Hit rate stabilises above 60% and the loop never touches anything that
matters.

**Counter-metrics.** *Proposal ambition distribution*: proposals by target class (prompt / weight /
skill / gate / structural), with the adoption and effect rate of each. *Effect magnitude*: median
absolute effect of adopted proposals, not the share positive. *Coverage*: share of distinct scorer
failure clusters that ever produced a proposal — a loop that only picks the easy clusters is a loop
that is not working. Replace the §11.1 row with the magnitude and coverage figures; delete the hit-rate
target, which is unfalsifiable in the useful direction.

### BR-36 — A target of "zero on an audited sample" is either unmeasurable or the wrong measurement

**Bias:** Goodhart plus statistical theatre.
**Text:** §11.1: "Nothing is silently wrong | Evidence-gate false-pass rate on an audited sample | 0."

**Distortion.** Two problems. Statistically, "0" on a sample is not a target — it is what you observe
whenever the sample is small and the rate is low, and it carries no information about the rate. There
is no sample size, no confidence bound, and no definition of "false pass." Substantively, the
evidence gate (FR-22.6, INV-6) checks that claims *resolve to artifacts*; a "false pass" under that
definition is a summary claim with a missing artifact, which is a link-checking failure. The failure
anyone cares about — an artifact that does not support the claim it is attached to — is not what is
being audited.

**Gaming strategy.** Constrain agent summaries to claims that trivially resolve ("applied a patch"
→ diff; "ran the suite" → results file). Never make a claim about *correctness* or *sufficiency*,
because those need real evidence. The false-pass rate is structurally zero.

**Correction.** Replace with: "Evidence supports its claims | Share of sampled claims judged
*supported* (not merely accompanied) by an independent human or differently-configured auditor, with a
95% lower confidence bound | ≥ 0.95 at n ≥ 100 claims per quarter." Add an adversarial arm: auditors
plant unsupported claims into a share of bundles and the gate's detection rate is reported.

### BR-37 — "Pack efficiency" penalises the sections that matter most

**Bias:** Goodhart, on a quantity that is not observable.
**Text:** FR-15.3: "Pack efficiency | Share of pack content used, and on-demand retrieval rate."
FR-9.8: "record… what was retrieved on demand, and what went unused. An unused section is waste and a
repeatedly-retrieved item belongs in the pack."

**Distortion.** "Used" is undefined and unobservable: there is no way to know which pack content
influenced a model's output. Any implementation will proxy it — most likely by textual overlap with
the output — which systematically undercounts context that *prevented* an action. §9.2's Hazards
section ("flaky tests, recent incidents, known-fragile paths, past reverts") is precisely the section
whose value is a road not taken, and it will score as waste every time.

**Gaming strategy.** Trim sections with low quoted-back overlap. Hazards and Precedent go first.
Pack efficiency rises, packs get cheaper, and the factory loses the context that stops it repeating a
known failure — which will not show up in pass rate on a benchmark of tasks selected for solvability
(BR-27).

**Correction.** Delete "share of pack content used" as a metric. Replace with outcome-conditioned
ablation: "Per section, the pass-rate and violation-rate delta when the section is withheld, measured
on a sampled share of production runs (a permanent, low-rate A/B), reported with confidence intervals."
Reword FR-9.8's second sentence: "An unused section may be waste or may be a prevented failure; only
withholding it measures which."

### BR-38 — Memory health metrics are optimised by having no memory

**Bias:** Goodhart, absent a utility term.
**Text:** FR-15.3: "Memory health | Canon size, Candidate backlog, contradiction rate, invalidation
rate." FR-6.4 makes Canon promotion demanding; FR-6.12 requires bounded growth.

**Distortion.** Three of the four are *bad-thing* counters and one is a size. All four are minimised by
admitting nothing. A factory with an empty Canon has zero contradictions, zero invalidations, zero
backlog, and satisfies FR-6.12's budget trivially. Nothing in FR-15.3 measures whether memory ever
*helped*, which is the only reason §7.6 exists (P1: "laptop agents don't compound").

**Gaming strategy.** Raise the admission bar in `memory/policy.yaml` (FR-14.9 explicitly makes
"admission thresholds" an improvement target) until nominations rarely qualify. Every memory metric
improves. P1 remains unsolved and the dashboard says memory is healthy.

**Counter-metrics.** *Canon utility*: share of runs in which a Canon memory was included in the pack
and cited in the output, and the pass-rate delta for those runs under ablation. *Candidate starvation*:
share of nominations rejected, with reasons, trended. *Compounding*: for a fixed repeated task class,
the pass-rate and cost trend across successive runs — the direct test of P1, which the document
currently never performs.

### BR-39 — Skill health metrics reward churn

**Bias:** Goodhart.
**Text:** FR-15.3: "Skill health | Selection precision/recall, trial→active conversion, retirement
rate."

**Distortion.** Conversion rate and retirement rate are both rates over a population the factory
creates. FR-7.12 lets the factory induct new draft skills from ledger patterns; FR-7.8 lets it propose
retirement for skills "not selected in N eligible runs."

**Gaming strategy.** Induct narrow, near-certain skills (their trials pass, conversion rises) and
retire the long tail of rarely-selected skills (retirement rate rises). Both numbers look like a
healthy lifecycle. The skill library converges on trivia and loses coverage of rare-but-important task
classes — the exact opposite of what FR-7.7's split rationale ("oversized skills degrade selection")
was trying to protect.

**Counter-metrics.** *Task-class coverage*: share of observed task classes with at least one active
skill scoring above threshold, trended. *Retirement regret*: pass-rate change on task classes served by
a skill in the two windows after its retirement. Report conversion and retirement rates only alongside
coverage. (See also BR-18 on the uncomputability of recall.)

### BR-40 — Spec agreement improves when you retire the units that disagree

**Bias:** Goodhart, with an explicitly-provided mechanism.
**Text:** FR-5.5's agreement states (`agreed`, `unverified`, `drifted`, `contradicted`, `orphaned`);
FR-15.3: "Spec health | Agreement-state distribution, drift rate, criteria without coverage." FR-5.11:
"Retirement is first-class (PR-8). A unit may be retired with a reason; retired units leave the active
slice."

**Distortion.** `drifted` and `orphaned` units are, by FR-5.5's own table, "retirement candidate[s]".
Retiring them removes them from the active slice and therefore from the agreement-state distribution.
The metric improves by deleting the evidence of the problem, and the document's own design principle
(PR-8, "every subsystem must be able to shrink") supplies the justification.

**Gaming strategy.** Route every `drifted` and `orphaned` unit to retirement rather than reconciliation.
Agreement distribution goes green; the spec shrinks to only the parts that happen to still match the
code — which is precisely P4's failure ("the only surviving record of what the system was supposed to
do is the code") reconstituted with a dashboard.

**Counter-metrics.** *Retirement reason audit*: retirements by reason, with the share retired while in
`drifted`/`orphaned` state highlighted as a distinct figure. *Spec coverage of change surface*: share of
merged changes whose touched code was covered by at least one `active` unit — the metric that falls when
the spec is hollowed out. Add a gate: "A unit may not be retired in the same window it entered
`drifted` state without an explicit human rationale recorded."

### BR-41 — Measurement stops at merge

**Bias:** survivorship bias, at document scale. The single largest measurement omission.
**Text:** FR-15.3's full metric list; §11.1's seven targets; FR-4.4: "`COMPLETE` means *handed to a
human with evidence*, never *merged* or *deployed*. Merge state is observed and reported, never
assumed."

**Distortion.** FR-4.4 is used as a scope boundary, and the metric suite honours it: the last
observation in the entire document is merge. There is no revert rate, no incident attribution, no
defect-escape rate, no time-to-first-regression, no post-merge follow-up-fix rate, no rollback count.
Yet merge is used throughout §11 as the quality proxy (autonomy, cycle time, "changes merged"), and
merge is a *social* act performed by a human under time pressure who is, per §11.1, being asked to do
it in ≤1.0× the time. The document therefore measures its output at the precise moment the numbers
look best and stops. FR-11.7 mentions "post-merge reverts" once, as an input to calibration scoring —
so the data is assumed available, and is simply not used for the question that matters.

**Correction.** Add a P0 metric family: "FR-15.3c (P0) — Post-merge outcomes. For every merged
factory-attributed change: revert within 30 days; follow-up fix touching the same files within 30
days; incident or alert attributable to the change; and the same three figures for a matched
population of human-authored changes in the same repositories." Add to §11.1: "Output is safe |
Revert-plus-follow-up-fix rate for factory changes vs. matched human changes | ≤ 1.0×, reported with
confidence interval." Add to §13.1: "R-16 — The factory's quality is measured only up to merge, so a
sustained increase in escaped defects would not appear in any metric in this document."

---

## 5. Framing, language, and anthropomorphism

### BR-42 — "Courage" converts a containment mechanism into a motivational one

**Bias:** anthropomorphism, with a direct implementation consequence.
**Text:** §1.1 table: "**Courage** | FR-12 Blast-radius contract | … A bold approach costs a rollback,
not an incident." PR-5: "**Reversibility buys boldness.** Cheap, total undo is the precondition for
creative agent behaviour. Invest in checkpoints so agents can afford to be brave." §7.12 epigraph:
"Agents are timid because being wrong is expensive." FR-12.6 (P0): "The contract is stated to the agent
affirmatively… The purpose is to license bold approaches inside a safe envelope."

**Distortion.** "Agents are timid because being wrong is expensive" is a claim about motivation applied
to a system that has no cost model, no stake, and no fear. Whatever conservatism a model exhibits comes
from training distribution and prompt phrasing, not from a rational response to consequences it cannot
perceive. The metaphor is not harmless: it produces FR-12.6, a **P0 requirement that the pack contain
affirmative encouragement**. An implementer will read that and write "you may take bold approaches; undo
is cheap" into every pack. That is prompt-level nudging shipped as a safety mechanism, and its plausible
effect — more destructive attempts inside the permitted envelope, more speculative branches, more
tokens, more wall-clock — is neither budgeted nor measured. The genuinely valuable part of §7.12 (the
machine-checked contract, checkpoints, `rollback` as a tool, violation recording) does not need the
metaphor and is weakened by association with it.

**Correction.** Rename §7.12 "Containment, checkpoints, and reversibility." Delete PR-5's second and
third sentences; keep "cheap, total undo is a precondition for permitting write and exec side effects
at all." Delete FR-12.6's motivational clause and reword: "FR-12.6 (P0) — The contract is stated to the
agent as a complete, machine-checkable enumeration of permitted paths, side-effect classes, external
actions, resource ceiling, and undo cost. It is descriptive; it contains no exhortation." Add a
counter-metric to FR-15.3: "Speculative waste | Tokens and wall-clock in discarded speculative branches
as a share of run total | Reported, with a declared ceiling."

### BR-43 — "Awareness" asserts a mechanism the design cannot enforce

**Bias:** anthropomorphism.
**Text:** §1.1: "**Awareness** | FR-9 Awareness Pack | The agent starts every task already knowing the
spec slice that governs it… **It retrieves rather than recalls.**" §7.9 epigraph: "*The single largest
lever on output quality is what the agent knows when it starts.*"

**Distortion.** "Already knowing" and "retrieves rather than recalls" describe an epistemic state the
system does not create. Assembling text into a context window does not cause a model to prefer that
text over its parametric priors; the model will still recall, and where pack content and prior conflict
the outcome is unspecified and unmeasured. The epigraph's superlative ("the single largest lever") is an
empirical ranking claim with no comparison — and §11.2's AC-4, as written (BR-25), cannot rank the
subsystems even if it runs. The name also imports a whole psychology: "awareness" implies the agent
notices what it is unaware of, which is exactly what it cannot do and exactly what FR-11.6's "explicitly
enumerated unknowns" then asks it to do (BR-19).

**Correction.** Rename to **Context Pack** throughout (glossary, FR-9, §1.1, §6.1). Replace "It
retrieves rather than recalls" with "It is given, rather than assumed to know — the design does not
guarantee the model prefers given content over its priors." Downgrade the epigraph to: "How much of the
relevant context an agent starts with is one lever on output quality; §11.2 AC-4′ measures its size
relative to the others." Add: "FR-9.12 (P1) — Prior conflict detection. Where an agent's output asserts
a fact contradicted by a cited pack item, the conflict is recorded and surfaced in the evidence bundle."

### BR-44 — The factory metaphor imports a value system the document then disclaims in one line

**Bias:** framing bias, document-wide.
**Text:** The title, §1, §5's glossary, §6.1's layered diagram, and the whole of §11.1. Against it:
NG-6: "Not a headcount-reduction tool. The design target is throughput and quality of decisions, and
every checkpoint in §7.16 exists to keep humans in the loop where judgement matters."

**Distortion.** The metaphor carries commitments the document never examines: that units of work are
interchangeable, that throughput is the primary good, that defects are a rate rather than events with
owners, and that people are stations. Those commitments are then realised precisely: §11.1's targets are
throughput, cycle time, cost per unit, and defect rate — an industrial dashboard with no term for
craft, comprehension, learning, or maintainer consent. NG-6 disclaims the most visible consequence and
is the only line in 1,600 that does; it names *intent*, and nothing in the design would behave
differently if the intent were the opposite (see BR-52). "Quality of decisions" appears in NG-6 and is
measured nowhere.

**Correction.** Either state the metaphor's limits in §1 ("the factory metaphor is used for the
intake→handoff pipeline only; it does not imply interchangeability of work, of people, or that
throughput is the objective") or replace it. Add to §11.1 at least one non-throughput row, e.g.
"Comprehension is maintained | Share of merged factory changes whose reviewer reports being able to
explain the change without the agent's help | ≥ 0.9." Make NG-6 falsifiable or delete it (BR-52).

### BR-45 — "Conductor" licenses an unspecified decision procedure

**Bias:** metaphor smuggling a capability claim.
**Text:** Glossary: "**Conductor** | The single coordinating agent… Routes work, dispatches specialists,
asks humans questions, hands off results." FR-3.3 (P0): "The Conductor must be able to skip stages that
do not apply, enter partway when context suffices, and return work to an earlier stage. Skipping must be
recorded in the ledger with a reason."

**Distortion.** A conductor interprets a score with an ensemble that has rehearsed it. The metaphor
implies judgement, shared understanding, and accountability for the whole performance — none of which is
specified. What FR-3.x actually requires is a router with a chat log. The gap shows in FR-3.3: the most
consequential decision in the run lifecycle ("skip Design", "enter at Build", "this is small") has no
decision procedure, no schema (compare FR-11.8, which requires structured output contracts for every
*stage*, but not for routing), no gate, no scorer, and no metric. "Recorded with a reason" is a log line,
not a check. A wrong skip is exactly how a plausible-but-wrong change (R-1) gets started, and R-1's four
mitigations all operate downstream of it.

**Correction.** Rename to **Coordinator** or **Router** throughout. Add: "FR-3.13 (P0) — Routing
decisions (stage selection, skip, re-entry, specialist choice, escalation to human) are structured
outputs against a declared schema, carrying the evidence considered and a confidence, subject to the
`calibration-present` gate, and sampled by a scorer. Skip precision — the pass rate of items whose
stages were skipped, versus matched items whose were not — is a P0 metric." Add to §13.1: "R-17 —
Mis-routing: a wrong skip or premature stage entry poisons every downstream stage; currently unmeasured."

### BR-46 — Pre-emptive rebuttal substitutes for evidence

**Bias:** persuasive framing.
**Text:** §1.1: "This is not a slogan; it is the design constraint that every subsystem in this document
answers to." §7.11 epigraph: "This is where 'lighter models do wonders' becomes a mechanism rather than
a hope." §7.20 epigraph: "The modification that makes this project distinct." §11.2: "The claim in §1.1
is falsifiable and must be tested before v1 ships."

**Distortion.** Each sentence anticipates the reader's objection and answers it by assertion at the
moment of introducing the thing objected to. "Not a slogan" is what a slogan says. "Mechanism rather than
a hope" precedes FR-11.1–11.12, none of which contains evidence that the mechanism works — FR-11.9's
small-model scaffolding is itself flagged "must be measurable in benchmarks", i.e. not yet measured.
"Falsifiable" is doing the heaviest lifting of all, over a test that grants falsification only to the
criteria that cannot fail (BR-26). The cumulative effect is a document that reads as rigorous while
deferring every substantiation to a test scheduled last.

**Correction.** Delete all four. Where the underlying claim matters, replace with the status: "§1.1's
bet is untested. §11.2 is the test. Until it runs, every requirement justified by the bet — FR-9, FR-10,
FR-11, FR-12, FR-13 — is provisional." Add a standing header to §7: "Requirements in this section are
specified in detail and validated in none; detail is not evidence."

### BR-47 — An uncited population claim, structured as a false dilemma

**Bias:** framing.
**Text:** §1: "**Most attempts at this fail in one of two ways.** They either wrap a single agent in a
webhook and call it a factory, or they build a closed platform where the customer owns the backlog but
not the machine that works it. **Software Factory** is a third thing."

**Distortion.** "Most attempts" is a claim about a population, with no citation, no definition of the
population, and no evidence of failure — and it is constructed so that the only two named alternatives
are trivially inadequate, making the document's design the sole survivor by elimination. The
document's actual evidence base is one commercial product plus press coverage; from that base, the
population claim is not available. The "third thing" framing then does load-bearing work throughout
(§1.2, §7.20, NG-1–NG-6 are all positioned against these two strawmen).

**Correction.** Rewrite: "We studied one commercial product in this space in depth and read press
coverage of others. That is the evidence base for this document. We have not surveyed the field, and the
claims below about what alternatives do or do not do are impressions, not findings." Then state the
design's actual differentiator (local-first, definition-as-files) as a *choice with costs*, not as a
correction of others' failures.

### BR-48 — Principles that are memorable and false as stated

**Bias:** rhetorical compression producing unimplementable requirements.
**Text:** PR-6: "**Compute the computable.** Any fact obtainable by a deterministic tool must not be
left to the model to infer." FR-13.3: "**This single gate is the strongest available defence** against
plausible-but-wrong changes." PR-3: "**Evidence over assertion.** No stage completes on an agent's
say-so."

**Distortion.** PR-6 as written is unimplementable: almost any fact is obtainable by *some* deterministic
tool at *some* cost, so the universal quantifier forbids all model inference about the codebase. Taken
literally it forbids the model from concluding "this function is dead" without running a reachability
analysis, which is often more expensive than the change. There is no cost or availability condition.
FR-13.3's superlative is examined at BR-16. PR-3 conflicts with FR-11.6, which requires exactly an
agent's say-so about its own confidence — the document's own gate `calibration-present` (FR-13.2) checks
that the say-so is *present and schema-valid*, not that it is true.

**Correction.** PR-6: "Prefer deterministic computation over model inference where a tool exists, its
cost is below a declared threshold, and its result is more reliable than the inference. Where the model
infers a fact a tool could have established, the pack must record the choice and its reason." FR-13.3:
delete the superlative (BR-16). PR-3: add "— except self-assessment, which is an assertion by
construction and is therefore scored (FR-11.7), not trusted."

### BR-49 — "Self-improvement" presumes the sign of its own effect, and ships on by default

**Bias:** framing; naming an outcome as if it were the mechanism.
**Text:** §7.14 title and FR-14.1: "Self-improvement is opt-in **per scorer**." FR-14.8: "A loop whose
adopted proposals do not move outcomes is itself a defect and must show as one."

**Distortion.** The mechanism is: an LLM judge classifies sampled runs against a rubric a human wrote;
failures are clustered; a model proposes edits to the factory's own prompts, skills, weights, and gate
thresholds; a benchmark the same system defines validates them. Calling that "self-improvement" asserts
the sign of the effect in the name, and the name then appears in the metric (§11.1 "The loop works"), the
milestone (§12 M6), and the risk mitigations. FR-14.8 admits the loop might be a defect — and yet
FR-14.1 makes it opt-in *per scorer*, i.e. enabled by turning on any scorer with the flag, with no
requirement that it demonstrate positive effect before it is permitted to run. The burden of proof is
placed on discovering it does not work, not on showing it does.

**Correction.** Rename §7.14 "Proposal loop" and rename the flag `proposalLoop`. Add: "FR-14.1a (P0) —
The proposal loop is disabled by default and may only be enabled for a scorer after that scorer has
passed FR-13.8's human-agreement threshold. It automatically disables itself for a target class where
the last N adopted proposals show no measured positive effect on a held-out set, and re-enabling
requires a human decision recorded in the ledger."

---

## 6. Scope and stakeholder bias

### BR-50 — The reviewer of record has no persona, and their interest is defined as speed

**Bias:** stakeholder omission.
**Text:** §4.1: U1 Factory Operator, U2 Contributing Engineer, U3 Engineering Manager, U4
Security/Compliance Reviewer, U5 Solo maintainer. U2's success: "reviewing a factory change is faster
than writing it, and they trust its evidence."

**Distortion.** Four of five personas own, configure, or measure the factory. The one who *inherits its
output* is folded into U2, whose defining trait is that they also send work in — i.e. they consented.
The reviewer who did not choose to receive factory changes, and cannot decline them, has no persona, no
job in §4.2, no requirement, and no metric. U2's stated success is *speed* and *trust*, both framed as
properties the factory should induce in the reviewer, not as protections the reviewer holds. There is no
requirement anywhere allowing a reviewer or repository to rate-limit, opt out, or set a quality floor.

**Correction.** Add "U6 — Reviewer of record / repository maintainer. Receives changes they did not
request, into a queue they own. *Success:* can decline a factory change cheaply and without argument;
can cap how much factory work reaches their queue; can tell at a glance what was and was not verified."
Add: "FR-16.8 (P0) — Per-repository intake contract. A repository may declare, in its own tree, a
maximum number of open factory changes, required evidence classes, and an opt-out. The factory must
honour it and must not open changes beyond the cap; the contract is checked by `sf lint`." Add a §11.1
row: "Reviewers are not swamped | Open factory changes per repository, p90 | Below the declared cap."

### BR-51 — Human learning is absent from a document whose first problem is learning

**Bias:** scope bias; the solution's shape excludes the beneficiary.
**Text:** P1: "The reasoning, the dead ends, the discovered conventions, and the correction the human
made all evaporate… **There is no mechanism by which the organisation gets better at using agents.**"
Addressed, per §14, by "§7.6 Memory, §7.7 Skills, §7.14 Self-improvement, §7.15 Ledger."

**Distortion.** P1 is stated as an organisational learning problem and solved entirely as a machine
learning-storage problem. All four cited sections route knowledge into machine-readable stores consumed
by agents. Not one requirement puts anything in front of a person for the purpose of them learning it.
There is no job in §4.2 about a human understanding the codebase better, no metric in §11 about human
capability, and no risk in §13.1 about deskilling — despite the design routing triage, design,
implementation, and first-pass review away from humans, which is the entire path by which a junior
engineer currently learns a codebase. §11.1's autonomy target (≥25% and rising) is, read literally, a
target for the share of changes no human touched.

**Correction.** Add to §13.1: "R-18 — Deskilling and reviewer atrophy. Routing triage, design,
implementation and first-pass review to agents removes the path by which engineers build system
knowledge; reviewers of changes they did not write lose the ability to review them well. Impact: High.
Mitigation: currently none." Add a §11.1 row: "People still build knowledge | Share of work items where
a human authored code, and share of merged changes reviewed by someone who has authored code in that
module in the last 90 days | Reported, no target v1." Add to §4.2: "JTBD-10 — Learn a subsystem well
enough to review its changes independently."

### BR-52 — NG-6 is a statement of intent with no design consequence

**Bias:** self-serving disclaimer.
**Text:** NG-6: "Not a headcount-reduction tool. The design target is throughput and quality of
decisions, and every checkpoint in §7.16 exists to keep humans in the loop where judgement matters."

**Distortion.** Nothing in the design would differ if headcount reduction *were* the goal. The
checkpoints cited (§7.16) are all overridable in `policy/` by the operator (FR-16.1: "all overridable"),
FR-16.6 offers an `autonomous-to-change` autonomy level, and §11.1's targets are throughput, cost, and
autonomy — the exact metric set a headcount-reduction programme would choose. The clause "quality of
decisions" names an objective that is measured nowhere in the document. The non-goal is therefore
unfalsifiable and costless: it reassures without constraining.

**Correction.** Either delete NG-6 as unearned, or make it real. A real version: "NG-6 — The factory's
metrics must not be usable as individual productivity measures. Ledger and dashboard aggregate to
repository and work-class only; per-human attribution beyond the checkpoint decisions required by
FR-16.3 and FR-16.5 is not computed, not exported, and not available through the API. `sf audit` reports
any configuration that would enable it." That is a requirement someone can violate.

### BR-53 — Teams without CI get a factory whose quality story silently evaporates

**Bias:** scope bias, hidden by a subordinate clause (see BR-21).
**Text:** FR-13.2's baseline gate table: `build-green`, `tests-pass`, `regression-proven`,
`coverage-of-criteria`. §10: "Repositories have some form of runnable validation, or the factory helps
create it." §2.2: "A two-person team with a noisy issue tracker benefits as much as a two-hundred-person
one."

**Distortion.** The two-person team with the noisy tracker is the least likely to have a maintained test
suite, and is named in §2.2 as a primary beneficiary. For that team, four of eleven baseline gates
cannot run, `regression-proven` — described in FR-13.3 as the strongest defence against wrong changes —
is inoperative, and `evidence-complete` degrades to checking that a diff exists. The document never
describes this configuration. It does not say which gates degrade to `warn`, what the evidence bundle
contains instead, whether autonomy and gate-pass metrics may still be reported (FR-15.5's own rule says
unavailable metrics must not read as zero — unapplied here), or what the change description must say.
The result is that the teams with the least assurance get the factory's full confidence-signalling
apparatus with none of its substance.

**Correction.** Adopt FR-13.16 from BR-21 verbatim. Additionally: "FR-13.17 (P0) — `sf init` must detect
the absence of runnable validation and refuse to enable autonomy levels above `advisory` (FR-16.6) until
validation exists or the operator explicitly overrides with a recorded decision." Add a row to §11.3
subsystem acceptance: "Degraded assurance | A repository with no test command produces changes marked
`assurance: none`, reports autonomy as `unavailable`, and cannot be configured above `advisory`."

### BR-54 — The performance targets exclude the persona the document leads with

**Bias:** resource-context bias.
**Text:** NFR-3.1 (P0): "A factory handles at least 10 concurrent runs on a workstation-class machine."
NFR-2.1 (P0): "Awareness Pack assembly… under 10s for a repository of 100k files with a warm index."
FR-20.3 (P0): local model endpoint as a first-class provider. §4.1 U5: "Wants the whole thing on a
laptop with a local model and no account anywhere."

**Distortion.** "Workstation-class" is undefined. A machine serving a local model already commits most
of its memory and all of its accelerator to inference; a "warm index" of 100k files commits more; ten
concurrent runs each with an isolated worktree (FR-8.4) commit disk and page cache. The three
requirements are individually plausible and jointly describe a machine U5 does not have. Nowhere does the
document state a minimum hardware profile, a single-run degraded target, or what happens when local
inference and indexing contend — FR-20.7's "resource courtesy" defaults to "a share of the machine"
without saying what happens to the targets under that share. AN-2, the offline narrative, is written as
if none of this matters.

**Correction.** Add: "NFR-2.5 (P0) — Declared hardware profiles. The document declares at least two:
`laptop` (8 cores, 16 GB, no discrete accelerator, local small model) and `workstation` (16 cores, 64
GB). Every performance target in §8.2–§8.3 states which profile it applies to. On `laptop`, the P0
targets are: one concurrent run; pack assembly under 30s for a 20k-file repository; and explicit,
recorded degradation of pack sections when the budget is exceeded." Restate NFR-3.1 as
`workstation`-only. Re-examine AN-2 against the `laptop` profile and state honestly how long it takes.

### BR-55 — Non-English operation degrades behaviour, not just labels

**Bias:** cultural/linguistic bias treated as a localisation task.
**Text:** NFR-7.2 (**P1**): "All user-facing strings are externalised; no locale assumptions in parsing."

**Distortion.** String externalisation covers UI chrome. The substance of the system is natural
language written by the operator: spec unit `intent` and `acceptance` (FR-5.2), skill `description`
(FR-7.1), scorer rubrics (FR-13.6), memory `content` (FR-6.2), agent prompt bodies (FR-3.9), gate
remediation hints (FR-13.2), ledger reasons. Several P0 mechanisms operate *on* that text:
FR-7.9's discoverability scoring and collision detection compare descriptions; FR-6.5 detects
"duplication by similarity"; FR-6.5 detects "contradiction between memories." All are
language-dependent and all degrade for a team writing in a language the embedding or judge model
handles less well — silently, showing up only as worse skill selection and a growing Candidate backlog
that nobody can attribute. The document has no requirement to declare a language, no requirement to
report quality per language, and files the whole area at P1.

**Correction.** Add: "FR-2.11 (P0) — `factory.yaml` declares the natural language of its definition and
knowledge content. `sf lint` warns on mixed-language content within a scope." Add: "FR-7.14 (P0) —
Selection precision, collision rate, and memory duplication/contradiction detection rates are reported
per declared language; where a mechanism has not been validated for the declared language, it reports
`unavailable` (FR-15.5) rather than a number." Promote NFR-7.2 to P0 for parsing and formatting
(numbers, dates, sorting, collation) since those affect behaviour, not presentation.

### BR-56 — Accessibility is P1, dashboard-only, and contradicted by the evidence design

**Bias:** stakeholder omission.
**Text:** NFR-7.1 (**P1**): "Dashboard meets WCAG 2.2 AA." FR-21.1 (P0): "The CLI is the complete
surface. Anything the dashboard or API can do, `sf` can do." FR-15.7 (P0): live runs "observable *and
steerable*". FR-22.2/22.3: screen and browser recordings as a first-class evidence class.

**Distortion.** Three gaps. (a) The accessibility requirement is P1 while the surfaces it covers are P0
— it can be deferred indefinitely without violating anything. (b) It covers the dashboard only, while
FR-21.1 makes the CLI the *complete* surface; there is no requirement for screen-reader-friendly output,
non-colour status encoding, or navigable structured output, and FR-15.7's live-run view is exactly the
kind of continuously-updating region that is hostile to assistive technology without deliberate design.
(c) FR-22.3's degradation path handles a recording being *absent* ("an explicit statement that visual
evidence is absent") but not a recording being unconsumable — a blind reviewer receives a bundle whose
key artifact is a silent video with no textual equivalent, and every gate passes.

**Correction.** Promote NFR-7.1 to P0 and extend: "NFR-7.1 (P0) — The dashboard meets WCAG 2.2 AA. CLI
output encodes status without relying on colour, is parseable with `--json` for every command (FR-21.3),
and streaming views provide a non-streaming equivalent. Live-run steering (FR-15.7) is fully operable
by keyboard and exposes state changes as discrete, announceable events." Add: "FR-22.8 (P0) — Every
non-textual evidence artifact ships with a textual equivalent: recordings carry a step-by-step
transcript of actions and observed results, generated at capture and checked by `evidence-complete`.
An artifact without an equivalent does not satisfy any gate."

### BR-57 — Security is present; compliance is absent

**Bias:** scope bias; the harder regulatory stakeholder is dropped.
**Text:** §7.17 (FR-17.1–17.11), U4 "Security / Compliance Reviewer. *Success:* can audit the factory
from the repository plus one report." FR-16.3: resolution "recorded with the deciding human's identity."
FR-15.10: "Retention is configurable per artifact class… with a documented default."

**Distortion.** U4's name says compliance; §7.17 delivers security. The gaps are specific and load-
bearing in any regulated change process: (a) **No segregation of duties.** The factory proposes and
reviews; FR-3.5's independence requirement has an opt-out (`allowSharedBlindSpot: true`) with no audit
consequence (BR-66 below). (b) **No non-repudiable human decision.** FR-16.3 records
identity; nothing signs. A ledger that is hash-chained (FR-15.1) proves the record was not altered after
the fact; it does not prove who approved. (c) **No retention floor.** FR-15.10 offers configurable
retention, which in a regulated context is a deletion mechanism, not a control. (d) **No change-control
attestation** — nothing produces the artifact an auditor needs (what changed, who approved, what
evidence, what was tested). (e) §13.1 does not list "unusable in regulated change processes" as a risk,
though it plainly is one for a large share of §2.2's audience.

**Correction.** Add: "FR-16.9 (P0) — Human decisions at checkpoints are cryptographically signed by the
deciding identity where the deployment provides one, and the signature is chained into the ledger.
Where signing is unavailable, the decision is recorded as `unattested` and `sf audit` reports the
share." "FR-15.11 (P0) — Retention policy supports minimums as well as maximums; a class may be marked
`retain-until-released`, and a deletion pass that would violate a minimum fails loudly." "FR-17.12 (P0)
— `sf audit --change-control <work-item>` emits the complete attestation record for one change: intent,
spec delta, approvals with identities, gates run and their results, evidence digests, and the ledger
range." Add: "R-19 — Segregation of duties: the factory both authors and reviews changes;
`allowSharedBlindSpot` removes even the model-level separation. Mitigation: make its use a reported
metric and lint error outside a declared exemption."

---

## 7. Optimism and planning bias

### BR-58 — Milestones bundle unsolved research problems as single exit criteria

**Bias:** planning fallacy; scope compression.
**Text:** §12: "**M4 — Knowledge** | Living Spec + Delta, Memory Fabric, Skill lifecycle | Three-way
agreement computed; memory promotion and policing live." "**M8 — Hardening** | Audit, redaction,
injection containment, retention, error catalogue | §11.2 acceptance test run and published."

**Distortion.** M4 contains three subsystems, each of which is an open research area. "Three-way
agreement computed" — mechanically relating a natural-language spec unit to code anchors and to a
covering test, robustly across refactors, is not a solved problem; FR-5.8's content-addressed anchors
detect *change*, not *agreement*, and the gap between the two is where all the difficulty is. "Memory
policing live" is FR-6.5's four detectors — contradiction, staleness, duplication, transitive poisoning
— of which transitive invalidation over a provenance DAG at production scale is by itself substantial.
The base rate for systems in this class is instructive: knowledge bases with automated contradiction
detection and earned promotion are attempted regularly and usually end as append-mostly stores with a
manual cleanup script, which is exactly the P5 failure the section exists to avoid. §12 gives no dates,
no staffing assumption, no sequencing risk, no dependency between milestones, and — most tellingly — no
milestone whose exit criterion is a negative result.

**Correction.** Split M4 into M4a (Living Spec + Delta, exit: spec deltas reviewed as changes; *no*
agreement computation), M4b (Memory Fabric lanes and admission only, exit: Candidate quarantine works,
promotion is human-only), M4c (agreement computation and automated policing, exit: measured precision
and recall against a hand-labelled corpus of at least 200 cases — and if precision is below a
pre-declared floor, the subsystem ships as advisory-only). Add to §12 a column "What is deleted if the
exit criterion is not met" and fill it for every milestone.

### BR-59 — Targets are precise where they are easy and absent where they are hard

**Bias:** estimate anchoring on the measurable.
**Text:** NFR-2.4 (P0): "The CLI starts in under 300ms for non-executing commands." NFR-2.1 (P0):
"Awareness Pack assembly completes within a declared budget (target: under 10s for a repository of 100k
files with a warm index)." NFR-4.1 (P0): "Time to first useful run on a fresh repository: under 10
minutes, including `sf init`."

**Distortion.** NFR-2.4 is precise, easy, and irrelevant — it is a target because it is measurable, not
because it matters. NFR-2.1's 10s is asserted for a pack whose sections 4 (Precedent, from the ledger),
5 (Hazards, from version-control and CI history), and 6 (Conventions, from Canon memory) each require
mining historical data, and whose memory retrieval runs FR-6.7's seven-stage pipeline (scope → lane →
contradiction → freshness decay → relevance rank → diversity cap → budget truncation) while FR-9.9
requires stale-input detection and refresh *inside* assembly. On a cold or invalidated index — the
common case after any significant merge — this is not a 10s operation. NFR-4.1's "10 minutes" and
"useful" are both undefined, and the path includes indexing a repository, resolving a runner, and
reaching a model endpoint. Meanwhile the hard things carry no number at all: there is no target for
memory policing throughput, no target for scorer cost as a share of run cost, no target for
end-to-end work-item latency, and no target for the repair loop.

**Correction.** Delete NFR-2.4 or demote it to P2. Restate NFR-2.1 per hardware profile (BR-54) and
split it: "deterministic sections under Xs; historical sections under Ys; on cold index, assembly must
degrade by dropping sections with a recorded reason rather than exceeding budget." Define "useful" in
NFR-4.1 as a specific observable ("produces a branch containing a passing test"). Add targets for the
three unbudgeted subsystems above, or mark them explicitly "unbudgeted in v1" so the omission is
visible.

### BR-60 — Determinism and cross-executor parity are asserted over a stochastic, time-varying system

**Bias:** optimism about verifiability; a release blocker that will be waived.
**Text:** FR-0.2 (P0): "A factory definition must be portable between topologies with byte-identical
files. A conformance suite asserts **identical stage transitions and gate outcomes** across executors
for a fixed task set." FR-20.5 (P0): "Divergence is a release blocker." NFR-5.3 (P0): "Deterministic
core: identical inputs produce identical non-model outputs." FR-9.1 (P0): "Assembly is a pure function
of (work item, agent config, repository state, spec, memory, ledger, skill registry) **plus a seed**."

**Distortion.** Two problems, both serious. (a) Stage transitions and gate outcomes are downstream of
model sampling, tool timing, test flakiness (which FR-13.13 acknowledges exists), network conditions,
and filesystem semantics that differ between a local subprocess, an OCI container, and a remote worker.
Requiring *identical* outcomes across executors is either achieved by stubbing the models — in which
case the suite proves the orchestration is deterministic, which is worth having but is not what FR-0.2
claims — or it is flaky, and a flaky release blocker is a blocker that gets waived on the third
occurrence. The document does not say which. (b) FR-9.1's input vector includes the ledger and memory,
both of which are append-only and time-varying, and FR-6.7 includes an explicit *freshness decay* term.
The pack is therefore a function of wall-clock time. "Plus a seed" is appended as if it closes the gap;
it does not — a seed fixes sampling, not the contents of a growing ledger.

**Correction.** Rewrite FR-0.2/FR-20.5: "The conformance suite asserts, across executors, identical
*pack digests*, identical *gate definitions applied*, identical *evidence bundle structure*, and
identical *ledger event types and ordering*, with models stubbed (NFR-5.2). Outcome parity for
model-driven runs is explicitly **not** asserted; divergence in outcomes is reported as a distribution
and monitored, not blocked." Rewrite FR-9.1: "Assembly is a pure function of an explicit *input
snapshot* — work item, agent config, repository commit, spec revision, memory snapshot id, ledger
sequence bound, skill registry revision — plus a seed and a pinned assembly clock. The snapshot
identifier is recorded with the digest so any pack is exactly reproducible."

### BR-61 — The risk register mitigates risks by citing requirements that are themselves unproven

**Bias:** self-serving structure; risk theatre.
**Text:** §13.1's Mitigation column, in full. R-1 (Critical): "`regression-proven` (FR-13.3),
independent Critic configuration (FR-3.5), `evidence-complete` (FR-22.6), calibration scoring
(FR-11.7)." R-2 (Critical): "Lanes, earned promotion, transitive invalidation (FR-6.4–6.6)." R-4
(Critical): "Structural grants, untrusted-region labelling, refusal at grant boundaries (FR-17.4–17.6)."

**Distortion.** Every mitigation is a pointer to a requirement in the same document. None cites
evidence that the requirement works, and several point at requirements this review has shown to be
weak or circular: FR-13.3 does not defend against the failure mode it is credited with (BR-16); FR-3.5
has an opt-out (BR-66); FR-22.6 is syntactic (BR-15); FR-11.7 depends on elicitable calibration
(BR-19). The register has no residual-risk rating, no owner, no trigger condition, no detection
mechanism, and no kill criterion — so no risk can ever be observed to have materialised, and no risk
can close. A register in which every risk is "mitigated" by construction is a register that will not
warn anyone.

**Correction.** Add four columns to §13.1: **Residual** (rating after mitigation, given that the
mitigation is unvalidated), **Detection** (the specific metric or event that would show this risk
materialising), **Owner**, and **Kill criterion** (the observation at which the design changes). Fill
Detection for every row; several will be blank, and a blank Detection cell on a Critical risk is itself
the most important finding the register can produce. Add: "No risk may be rated below its unmitigated
level until its mitigation has evidence recorded in §11.3."

### BR-62 — Three foreseeable failure modes are missing, and they are the inconvenient ones

**Bias:** omission bias in risk identification.
**Text:** §13.1's twelve risks. R-10 is the closest to the first gap: "**Operator overload**: too many
checkpoints, factory ignored | Medium | Time-bounded checkpoints, autonomy levels, needs-attention
triage."

**Distortion.** R-10 is about the operator being asked too many *questions*. The three absent risks are:
(a) **Reviewer flood** — the factory produces changes faster than the humans who must review them can
absorb, which is the direct consequence of succeeding at §11.1's first target and has no mitigation
anywhere in the document (see BR-50). (b) **Comprehensibility decay** — a codebase increasingly composed
of changes no human authored, reviewed under time pressure, with the reasoning discarded at run end;
nothing measures it and §7.6's memory captures reasoning for *agents*, not for people (BR-51). (c)
**Harness maintenance burden** — 22 requirement families, a spec engine, a memory fabric with a
continuous policy pass, a skill lifecycle, three executors, a conformance suite, and an improvement
loop, all maintained by a team whose stated motivation (P8) was not wanting to depend on someone else's
workflow machinery. The document never asks whether the harness is cheaper to own than the problem it
solves. All three are risks whose acknowledgement would complicate the thesis; all three are absent.

**Correction.** Add: "R-20 — Reviewer flood. Impact: High. Detection: open factory changes per
repository p90; time-in-review trend; decline rate. Mitigation: per-repository intake contract and WIP
cap (FR-16.8, BR-50)." "R-21 — Comprehensibility decay. Impact: High. Detection: share of merged changes
whose reviewer can explain them unaided (BR-44); share of modules with no recent human author.
Mitigation: none currently; requires a human-authorship floor to be considered." "R-22 — Harness
maintenance burden exceeds the cost of the problem. Impact: High. Detection: engineer-hours maintaining
the factory definition and runtime versus estimated hours saved. Kill criterion: if the ratio exceeds
1.0 over two quarters, subsystems are removed in ascending order of AC-4′ effect size."

---

## 8. Omission bias and self-serving structure

What a document does not measure is a claim about what it does not want to know.

### BR-63 — The factory creates human work in a dozen places and measures it in none

**Bias:** omission; the cost that would undercut the benefit.
**Text:** Human work created by requirement: spec-delta approval (FR-16.1), question answering
(FR-16.1), merge decision (FR-16.1, NG-1), blast-radius widening approval (FR-12.7), improvement-proposal
review (FR-14.5), self-referential change review (FR-14.7), Candidate memory review (FR-6.13), skill
lifecycle transitions as reviewed changes (FR-7.3), spec unit promotion from induction (FR-5.12),
needs-attention triage (FR-15.6), scorer human-agreement labelling (FR-13.8), and the review of the
change itself. Measured in FR-15.3: none of it.

**Distortion.** §11.1 claims "Review is cheap" on the basis of one ratio for one activity, and the
document's economic argument (P2, "is this worth it?") is settled entirely on the model-cost side of the
ledger. Every requirement above adds minutes of skilled human attention per work item, and the total is
plausibly larger than the review time the document does measure. Not measuring it is what allows "cost
per change" to trend downward while the true cost per change rises.

**Correction.** Add: "FR-15.3d (P0) — Human attention accounting. Every checkpoint, review, labelling
task, and triage action records the identity, the wall-clock from presentation to resolution, and the
class. The dashboard reports **human minutes per merged change, decomposed by class**." Add to §11.1:
"Total cost is known | Human minutes per merged change plus fully-loaded machine cost per merged change
| Both reported; the combined figure must trend down for the cost claim in §1.1 to hold."

### BR-64 — The harness is free by omission

**Bias:** omission; boundary drawn where the numbers are favourable.
**Text:** FR-11.12 (P0): "Tokens, latency, retries, escalations, and cost per run per model per stage
are recorded." FR-10.10 (P0): "Every tool call is ledger-recorded with duration and cost class."
FR-15.4: cost figures "must be labelled **estimates**… and must state what they exclude."

**Distortion.** Two mechanisms account for model calls and tool calls. Neither accounts for: repository
indexing and re-indexing (NFR-2.3), pack assembly compute, the continuously running memory policy pass
(FR-6.5), scorer judge runs (FR-13.6, which are themselves model calls on a sampled share of every
run), benchmark suites (FR-13.9, repetitions × configurations × tasks), self-improvement investigation
and validation runs (FR-14.2), bounded repair loops re-running gates and tests (FR-13.5), discarded
speculative branches (FR-12.3), and CI compute consumed by gate execution. FR-15.5 acknowledges that
"aggregate run counts include evaluation, benchmark, and improvement runs" — so the document knows these
runs exist and counts them in the *numerator of activity* while excluding them from the *cost per
change*. That is the most favourable possible accounting and it is not flagged as such. FR-15.4's
"state what they exclude" is the right instinct applied too narrowly: it asks for a disclaimer, not for
the number.

**Correction.** Add: "FR-15.3e (P0) — Fully loaded cost. Report **total factory cost per merged
change** = (all model spend at any tier, including scorers, benchmarks, and improvement runs) + (all
compute: indexing, pack assembly, policy passes, runners, CI triggered by gates) ÷ merged changes in
the window. The productive-only figure may also be shown, always beside the fully loaded one, never
alone." Apply the same definition to §11.2's AC-2′ (BR-24).

### BR-65 — There is no counterfactual anywhere

**Bias:** omission; the comparison that could embarrass the thesis is absent from production.
**Text:** §11.1's seven targets: ≥40%, ≥25%, ≤1.0×, decreasing, ≤15%, ≥60%, 0. Six are absolute
thresholds; one (≤1.0×) is a ratio against "a comparable human change" with no requirement to
instrument it.

**Distortion.** Absolute thresholds cannot answer P2. "40% of defect-class work items reach a reviewed
change" is uninterpretable without knowing what share reached a reviewed change before the factory
existed, or what share would with a single interactive agent and no factory. "Cost per change
decreasing" is uninterpretable without the alternative's cost curve. The only comparison in the entire
document is §11.2's benchmark, which is a laboratory setting with hand-selected tasks (BR-27), run once,
at M8 (BR-27). Nothing requires a control arm in production, where it would be cheap: the same
repositories contain human-authored changes continuously.

**Correction.** Add: "FR-15.3f (P0) — Matched comparison. For every §11.1 metric with a human analogue
(cycle time, review minutes, revert rate, follow-up-fix rate, change size), the dashboard reports the
same metric over a matched population of human-authored changes in the same repositories and window.
Metrics without a human analogue are marked as such." Restate every §11.1 target as a ratio to the
matched population, not an absolute. Add: "A period in which the factory-attributed population cannot
be matched (too few comparable human changes) reports `unavailable` (FR-15.5), not a bare number."

### BR-66 — The one review-independence mechanism has an unmetered off switch

**Bias:** omission; an escape hatch left unobserved.
**Text:** FR-3.5 (P0): "The Critic must not run on the same model *and* harness as the Builder for the
same work item **unless the definition explicitly opts in with `allowSharedBlindSpot: true`**. Default
configurations must differ; `sf lint` warns otherwise. Rationale: independent review requires
independent failure modes."

**Distortion.** The rationale is correct and the requirement is one of the strongest in the document.
It also ships with a single boolean that removes it, a `warn` (not `fail`) lint level, no requirement to
record a justification, no dashboard metric, no effect on any gate, and no mention in §11.3's acceptance
table or in R-1's mitigation column — which cites FR-3.5 as a mitigation for the document's most
serious correctness risk without noting that it is optional. The pressure to set it is real and
predictable: sharing a model halves configuration effort and cost, and nothing pushes back.

**Correction.** "FR-3.5 (P0) — … `allowSharedBlindSpot: true` requires an adjacent `rationale` string,
is a `sf lint` **error** outside a declared, expiring exemption in `policy/`, is reported as a
first-class dashboard figure (share of work items reviewed under a shared blind spot), and disqualifies
the affected work items from the autonomy metric and from §11.1's evidence-audit sample." Add the flag's
usage rate to §11.3's Harness acceptance row. Add its state to `sf audit` output (FR-17.7).

### BR-67 — Every open question is a tuning question

**Bias:** self-serving structure.
**Text:** §13.2, all eight: default pack budget split (OQ-1); Candidate visibility (OQ-2); minimum
corroboration for promotion (OQ-3); induction cadence (OQ-4); default tier ladder (OQ-5); how much
transcript the Critic may see (OQ-6); public benchmark suite (OQ-7); default retention (OQ-8).

**Distortion.** Not one of these, resolved either way, changes the design. They are all parameter
choices inside subsystems whose existence is assumed. The questions that could change the design are
absent: Is a linear stage model right (BR-01)? Does role specialisation beat one agent with the same
pack (BR-03)? Is the bottleneck change production (BR-14)? Is persistent cross-run memory net-positive,
or is a clean context per run better (BR-38)? Can reviewers absorb the output (BR-50, BR-62)? Should the
spec subsystem be mandatory (BR-70)? An open-questions section that contains only safe questions is not
an admission of uncertainty; it is a demonstration of confidence wearing the costume of one.

**Correction.** Add to §13.2, each owned and dated before the milestone that would commit to it: "OQ-9
— Is the linear stage model correct, or an artifact of the product we studied? (M2)". "OQ-10 — Does role
specialisation outperform a single agent with the same pack, at what cost multiple? (M2, via §11.2
condition E)". "OQ-11 — What is the constraint in our users' delivery process, and does producing more
changes relieve it or worsen it? (M2)". "OQ-12 — Is persistent memory net-positive against a clean-context
baseline? (M4)". "OQ-13 — Should the Living Spec be mandatory, opt-in, or absent for small teams? (M4)".

### BR-68 — Quality measurement is sampled and optional; the targets are reported as if it always ran

**Bias:** omission; the document's own unavailability rule not applied to itself.
**Text:** FR-13.7 (P0): "Scoring is sampled, asynchronous, and must never block or influence the run it
scores." FR-13.6: `samplingRate` is an operator-set field. FR-15.5 (P0): "Metrics that require an
integration the factory does not have must be shown as *unavailable with reason*, never as zero."

**Distortion.** Scorers are the mechanism behind §11.1's "The loop works" row, behind the calibration
metric, and behind FR-14's entire input. Their sampling rate is a cost dial, and the first thing a
cost-constrained operator turns down. FR-15.5 states exactly the right rule — do not report a number you
cannot compute — and the document never applies it to its own headline metrics. A factory running at a
1% sampling rate will still render every §11.1 row as a number.

**Correction.** Add: "FR-15.5a (P0) — Every metric declares the minimum observation count and sampling
coverage required for it to be reported at all. Below that floor the metric renders `unavailable
(insufficient sample: n=…, required=…)`, including in `--json` output and the API. §11.1 targets are
evaluated only on metrics above their floor." Add the floor to each §11.1 row.

---

## 9. Cultural and contextual bias

### BR-69 — UTC-only scheduling and always-on checkpoints assume a co-located, always-available team

**Bias:** contextual bias.
**Text:** FR-18.3 (P0): "for schedules a cron expression or descriptor, **interpreted in UTC**."
FR-16.4 (P0): "An unanswered checkpoint escalates its notification and eventually parks the work item as
`BLOCKED: awaiting_human`."

**Distortion.** UTC-only is presented as a simplification and is a policy: it makes "every weekday
morning" unexpressible for any team, and silently wrong across daylight-saving boundaries for the
operator who computes the offset once. FR-16.4's escalation has no concept of working hours, on-call
rotation, timezone, or holiday: a checkpoint raised at 02:00 local escalates its notification into the
night, and a factory that keeps producing overnight lands its entire output on whoever is awake first.
For a distributed team this systematically shifts review load toward one timezone. Neither requirement
acknowledges a cost.

**Correction.** "FR-18.3 (P0) — Schedules declare a timezone; UTC is the default, not the only option.
Descriptors resolve against the declared zone including DST." "FR-16.4 (P0) — Checkpoint escalation
respects a declared availability calendar per notification route (working hours, timezone, on-call
rotation). Outside availability, the item parks as `BLOCKED: awaiting_human` without escalating.
Distribution of checkpoint resolution by timezone is a reported metric so load shifting is visible."

### BR-70 — A branch-and-pull-request trunk workflow and mandatory spec-first practice are assumed universal

**Bias:** contextual bias presented as an architectural boundary.
**Text:** FR-4.4 (P0): "`COMPLETE` means *handed to a human with evidence*, never *merged*." NG-1: "The
factory opens changes; humans merge them." NG-5: "Not a replacement for CI. It *drives* CI and consumes
its results." §7.5 in full, at P0: FR-5.3 ("No agent edits the spec directly"), FR-5.4 ("Design-stage
output is a Spec Delta plus a draft change, not code"), FR-5.7 (a behavioural change without a delta
"must fail the Review gate").

**Distortion.** (a) The change model — a branch, a reviewable unit, a merge event, a CI system reporting
check suites — is one workflow among several. Teams using patch-series review, change-set-based review
with amend-and-resubmit, commit queues, or centralised version control do not have "a change" that is
"opened" and "merged", and FR-18.11's event vocabulary encodes the assumption further. It is stated as
a *non-goal boundary*, which makes it look like a principled scope decision rather than an inherited
assumption. (b) §7.5 makes spec-first practice mandatory at P0 for every factory. R-6 concedes "Spec
becomes bureaucracy and slows work" at Medium, mitigated by "Deltas only where behaviour changes;
skip-with-reason; induction on-ramp" — but FR-5.7 makes the delta *gate-blocking* for any behavioural
change, which is most changes, and for the two-person team of §2.2 the ceremony is the dominant cost.
The design's answer to "some teams don't work this way" is a skip-with-reason and a lint warning.

**Correction.** (a) Add to §3.3: "NG-7 — v1 assumes a branch-and-reviewable-change workflow with a
merge event and a CI system reporting results. Other version-control and review models are out of scope
for v1; this is an assumption, not a principled boundary, and FR-18.2's normalised event set (BR-07) is
the extension point." (b) Demote §7.5 to opt-in: "FR-5.0 (P0) — The Living Spec subsystem is enabled per
factory and disabled by default. When disabled, FR-5.7's gate does not apply and the factory records
`spec: none` on every change. Enabling it is a deliberate, reversible choice." Add OQ-13 from BR-67.

### BR-71 — An individual-ownership model is enforced by lint

**Bias:** cultural bias with a validation consequence.
**Text:** FR-7.13 (P0): "Every non-draft skill declares an owner and a review date. **Undated or unowned
skills fail lint.**" FR-16.3 (P0): resolution "recorded with the deciding human's identity." FR-16.5
(P0): attribution to "the factory, the agent, the model tier, and the work item."

**Distortion.** Named-individual ownership is one convention. Teams practising collective code
ownership, rotating stewardship, or team-level accountability have no individual to name, and FR-7.13
converts that into a build failure — the strongest enforcement level in the document, applied to a
cultural preference. Note the asymmetry: FR-16.2 correctly insists that policy files must not claim to
enforce what only an external system can enforce, and lint fails on violations; but FR-7.13 uses the
same mechanism to enforce an organisational norm that is not a safety property.

**Correction.** "FR-7.13 (P0) — Every non-draft skill declares a responsible party — an individual, a
team, or a rotation — and a review date. Lint fails on absence, never on the party's *kind*." Apply the
same to FR-16.3: record the deciding *principal*, which may be a team account, and note in the schema
that per-individual attribution is optional and interacts with NG-6's revised form (BR-52).

### BR-72 — The local-first story rests on a premise the acceptance test never tests

**Bias:** contextual optimism, with a gap between the claim and the experiment.
**Text:** §2.3: "small models became good enough that, given excellent context, they handle most factory
work." FR-20.3 (P0): "the default tier ladder must include a local-small tier so a laptop-only
configuration is a supported configuration, not a hack." FR-8.5 (P0): network policy `none` "must still
permit configured model inference if that endpoint is local." §11.2's conditions: "C (treatment) | Full
factory harness | **Small**."

**Distortion.** §7.20 and persona U5 and narrative AN-2 all rest on a *locally hosted* small model
producing usable work. §11.2's condition C says "Small" — a tier, not a deployment. A hosted small model
from a frontier provider and a quantised model running on a laptop are separated by a large capability
gap, and it is the second that the local-first thesis needs. The acceptance test as written can pass
entirely on hosted small models, and §7.20's central claim — the one §1.2 calls "the local-first
modification" and §7.20's epigraph calls "the modification that makes this project distinct" — would
remain untested. FR-11.9's small-model scaffolding (decomposition, per-step verification, context
windowing) is specified precisely because the gap is expected, and it too is only "measurable in
benchmarks", not measured.

**Correction.** Add a fifth condition to §11.2: "**G (treatment, local):** full factory harness, a
locally hosted model at the `local-small` tier, on the declared `laptop` hardware profile (BR-54),
network policy `none`." And an acceptance criterion: "AC-7 — G's pass rate is at least 60% of C's, and
G completes the AN-2 narrative end to end with zero outbound network destinations reported by
`sf audit --egress`. Failing AC-7 falsifies the local-first claim in §1.2 and §7.20; U5, AN-2, and
FR-20.3 are then restated as aspirations with the gap quantified, not as supported configurations."

---

## Closing note on the document as a whole

Three subsections are unearned in their entirety and should be rewritten from their problems rather
than repaired:

- **§2.3 "Why now"** asserts the document's own thesis as an established precondition for the document
  (BR-11). It is not context; it is the conclusion, relocated.
- **§11.2 "The central-bet acceptance test"** is presented as the document's falsification commitment
  and, as constructed, cannot produce the falsifying result (BR-22 through BR-29). It requires
  replacement, not amendment — the corrections in §3 of this review are a starting draft.
- **§13.2 "Open questions"** contains eight questions, none of which threatens any decision already
  made (BR-67). Its function in the document is to signal humility. Its content does not deliver any.

The strongest material in the PRD is where it constrains itself against its own convenience: FR-16.2
(policy is not enforcement), FR-17.4 (the execution plane is untrusted, structurally), FR-15.5 (do not
report a number you cannot compute), FR-13.8 (a judge must be validated before its verdicts drive
change), FR-14.7 (held-out validation, self-referential flagging), and FR-9.7 (the Critic does not see
the Builder's reasoning). Each of these is a rule that costs the authors something. The document's
central problem is that it applies this standard to its subsystems and not to its own thesis: FR-14.7
demands a held-out set of the improvement loop, and §11.2 has none; FR-15.5 forbids reporting a metric
the system cannot compute, and §11.1 reports seven; FR-13.8 forbids a judge from driving change before
its agreement is measured, and §1.1's five mechanisms drive the entire design before any of them is.
