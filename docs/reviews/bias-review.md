# Bias and Epistemics Review — Master PRD v1.0.0

| Field | Value |
| --- | --- |
| Reviews | `docs/PRD.md` v1.0.0 (Baseline, pre-review) |
| Review type | Bias, framing, and epistemic soundness — not completeness, not correctness |
| Date | 2026-08-31 |
| Findings | 61 |

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
| BR-61 | Cultural / contextual | FR-18.3, FR-18.11–13, §7.5 | Assumes a UTC-scheduled, PR-based, tracker-plus-chat, spec-writing working culture as universal |

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
