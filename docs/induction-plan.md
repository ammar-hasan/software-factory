# Induction plan — the announcement-video gaps

| Field | Value |
| --- | --- |
| Status | Planned. Nothing here is adopted until it lands in the PRD through the ordinary change path. |
| Source | [`reviews/source-gap-analysis.md`](reviews/source-gap-analysis.md) — 80 items extracted, 44 covered, 22 partial, 8 absent, 5 divergent |
| Sequencing | Written after the PRD was implemented, per the constraint that the video's changes go **on top of** a completed implementation rather than into a moving one |

---

## Why this document exists

The gap analysis compares an external product announcement against this PRD. It is a research
artifact: it ranks and describes, and deliberately adopts nothing. Turning 30 actionable items
into requirements is a separate act of judgement, and it should be visible as one — otherwise
"the analysis said so" becomes the reason a requirement exists, which is not a reason.

So this plan states, for each theme: **what we would build, what it costs, and what would make
us not build it.** The last is the important column. An induction plan with no falsification
criteria is a wish list.

---

## 1. Computer use as a declared tool class (V25, V11, V47)

**The gap.** FR-22.3 already says an agent may drive a browser or desktop session and that the
session is recorded. Nothing behind it exists: no tool, no effect class, no grant, no session
contract. That is the worst state for a capability to be in — the document promises it and
`sf audit` would report an agent as unable to do something the document says it can, or worse,
report nothing at all about a capability that arrives later without a grant model.

**What we would build.**
- A `UI` effect class beside `READ`/`WRITE`/`EXEC`/`EXTERNAL`. Driving a browser is not a read
  and not an exec: it can click "delete", and an effect model that cannot express that has a
  hole in the middle of it.
- `ui.session`, `ui.click`, `ui.type`, `ui.observe` as declared tools with schemas, so the
  grant model covers them like everything else and text cannot widen them.
- A **session contract** on the same footing as `BlastRadius`: which origins the session may
  reach, whether it may authenticate, and an explicit refusal to enter credentials the run was
  not granted. A browser session inside a run that can type is a run that can exfiltrate
  through a form, and the network policy does not see it.
- The recording is not optional for a UI session. Everywhere else recording degrades to a
  stated absence (FR-22.3); here the session *is* the evidence, and an unrecorded UI session is
  an action nobody can review.

**Cost.** Substantial. A driver (Playwright or equivalent), a display in the container executor,
and a materially larger attack surface. This is the largest single item in the analysis.

**What would make us not build it.** If the measured defect-escape rate for user-facing changes
is not materially worse than for non-user-facing ones, the capability is expensive theatre. That
is measurable from O-3 before any of it is written, and it should be measured first.

## 2. Sub-agent delegation and the delegation tree (V55)

**The gap.** `Run` has no parent. The model has exactly one level of delegation — Conductor
dispatches specialist — and nothing describes an agent dispatching a sub-run. Nothing forbids it
either, which is the actual problem: an unspecified capability that arrives anyway arrives
without a budget, without attribution, and without a view that shows it happened.

**What we would build.**
- `Run.parent_run_id`, and a budget rule that a child's spend counts against its parent's. A
  sub-agent whose cost lands in a separate bucket is a way to exceed a work item's budget by
  delegating.
- Depth and fan-out ceilings, declared. Unbounded delegation is unbounded spend, and the failure
  mode is a run that looks stalled while forty descendants work.
- A delegation view answering "which agents served this request, and what did each cost". We
  already attribute spend by agent (FR-26.5); this is the tree that makes it legible.

**Cost.** Moderate. Mostly data-model and view work; the orchestration already exists.

**What would make us not build it.** If specialists never need sub-agents in practice — if the
stage machine's decomposition is sufficient — this adds a dimension nobody uses. Check by
measuring how often a specialist's turn budget is exhausted on work a sub-run would have done.

## 3. Incidental discoveries become their own work items (V30)

**The gap.** A run that finds an unrelated defect can neither file it nor spawn a sibling item.
Today the finding lands in a transcript nobody reads again. This is the cheapest item in the
analysis and possibly the highest-value: the discovery is free, and losing it is pure waste.

**What we would build.**
- A `Discovery` output field on any stage, carrying what was found and where.
- A sibling work item created from it — *sibling*, not child: it is not part of this work, and
  making it a child would make this work item's completion depend on it.
- Where a tracker adapter exists, an agent-authored tracker item, clearly attributed to the
  factory (FR-16.5) so a human reading it knows a machine filed it.
- A cap. An agent that files forty issues per run has found one thing and reported it forty
  times, and the backpressure layer already knows how to say so.

**Cost.** Small.

**What would make us not build it.** If discoveries are mostly noise -- low precision would make
this a machine for generating backlog. Measurable by sampling: file them behind a flag for a
month and have a human rate them.

## 4. Human review comments as an improvement input (V68)

**The gap.** FR-14.2 clusters *scorer* failures. A reviewer's complaint is a failure mode no
rubric has encoded yet — which makes it the most valuable input the loop could have, and the one
it currently ignores.

**What we would build.**
- Review comments as a clustering input beside scorer failures, with the same signature-based
  grouping.
- The same anti-capture defences apply unchanged, and one more matters here: a proposal driven
  by review comments must not optimise for *fewer comments*. Fewer comments is achievable by
  producing changes nobody reviews carefully. The outcome partner for this input is O-2 (revert
  rate), not comment count.

**Cost.** Small, given `improvement/` exists.

**What would make us not build it.** If review comments are mostly about taste, clustering them
produces a machine for enforcing one reviewer's preferences on everyone.

## 5. Recording post-production (V48)

**The gap.** We require recordings and say nothing about making one cheap to watch. A
three-minute recording a reviewer must scrub through is evidence in the same sense that a
2,000-line diff is evidence.

**What we would build.** Click emphasis, keyboard callouts, dead-spot removal — all deterministic
post-processing, none of it a model call.

**Cost.** Moderate, and entirely dependent on item 1 existing first.

**What would make us not build it.** Straightforwardly measurable: if reviewers do not watch the
recordings we already produce, making them nicer will not change that. Measure watch-through
before building the editor.

## 6. Post-handoff explanation (V49)

**The gap.** A reviewer looking at a change cannot ask the agent why it did something. The
plumbing exists — FR-18.8 replies in place, FR-4.5 keeps the work item addressable — and the
capability does not.

**What we would build.** A bounded question channel on a handed-off work item, answering from
the conversation state and the evidence bundle rather than by re-running anything. The
conversation lifecycle (FR-29) already keeps what would be needed.

**Cost.** Small.

**What would make us not build it.** If reviewers do not ask. Cheap to find out: log the
questions people ask in the thread today.

## 7. Benchmarks driving routing (V74, V75)

**The gap.** FR-11.5 accepts a routing proposal and nothing produces one. Benchmarks compare
configurations that vary harness, tier, runner and scaffolds — but not the *pack*, which is the
variable this project's central bet is actually about.

**What we would build.**
- Pack composition as a benchmark dimension. If "a modest model in an excellent harness beats a
  frontier model in a poor one" is the thesis, the pack must be a thing we can vary and measure.
- A routing proposal generated from benchmark results, entering the same improvement loop with
  the same gates — no auto-adoption.

**Cost.** Moderate.

**What would make us not build it.** If tier choice does not move outcomes, per-step routing is
tuning noise. §11.2 is designed to answer exactly this, and its result should gate this work.

## 8. A provisioned agent suite (V8)

**The gap.** FR-2.1 requires only one agent, and `sf init` could in principle produce a factory
with no fleet. In practice the scaffold ships five agents — so this is a requirement that is
weaker than the implementation, which is the safe direction but still a document that
under-describes what the product does.

**What we would do.** Tighten FR-2.1 to require a conductor plus at least one specialist, which
is what `sf validate` effectively enforces already. No code.

**Cost.** None.

---

## The five divergences, and whether they stand

The analysis found five places we do something deliberately different. Reviewed here rather than
silently kept:

| Item | Their approach | Ours | Does our rejection stand? |
| --- | --- | --- | --- |
| V1 | Cloud-hosted factories | Local-first; cloud is a topology (§6.3) | **Yes.** This is the modification that makes the project distinct, and PR-2 is load-bearing throughout. |
| V17 | Skills are how agents reach integrations | FR-7.11: skills change knowledge, never access | **Yes, emphatically.** A skill that grants access is a grant model text can widen, which is the thing FR-17.5 exists to prevent. |
| V23, V24 | Marketing and SEO factories | NG-4: software work only | **Yes, for now.** The gates are the product, and `regression-proven` has no analogue for a blog post. FR-23.2's advisory mode is the nearest honest offering. |
| V32 | Talk to the triage agent directly | FR-3.6 routes through the Conductor | **Partially.** The reason — one accountable interface — holds. But V49's post-handoff question channel is the same need arriving through a door we have not opened, and item 6 above is the answer. |

---

## What has been inducted

| Item | Requirement | Status |
| --- | --- | --- |
| 3 — incidental discoveries | FR-31 | **Done.** `discoveries` on every stage schema, siblings not children, capped at three per run with the cap recorded. |
| 4 — review comments as an improvement input | FR-33 | **Done.** `Source.REVIEW_COMMENT` clusters beside assurance failures and never merges with them; a review-driven proposal measured against comment count is refused by name. |
| 6 — post-handoff explanation | FR-32 | **Done.** `sf explain` answers from the recorded conversation, never by re-running, and says so when the record is silent. |
| 8 — a provisioned agent suite | FR-2.1 | **Done.** `sf validate` requires a conductor *and* a specialist; the requirement was weaker than every real factory including the scaffold. |
| 1 — computer use | — | Not built. Waiting on the measurement below. |
| 2 — sub-agent delegation | FR-34 | **Done.** Depth and fan-out bounded and declared, a child's spend folded up to its root, and `sf delegation` as the tree. |
| 5 — recording post-production | — | Not built. Depends on 1, and on watch-through data. |
| 7 — benchmarks driving routing | — | Not built. Waiting on §11.2's result. |

Five are done: the four the sequencing section below called small and independent, plus item 2,
which is moderate and had nothing in front of it. The three that remain are the ones with a
measurement in front of them, and building those first would be building on the assumption the
measurement exists to test.

Item 2's framing shifted while building it. The plan treats it as a feature to add; the module
treats it as a hazard to bound, because the gap was never that delegation was missing -- it was
that nothing forbade it, and an unspecified capability that arrives anyway arrives without a
budget, without attribution, and without a view that shows it happened.

One thing the work changed about the plan. Item 3's cap was going to be a `maxItems` on the
schema, and that would have been wrong: a schema violation rejects the *whole stage output*, so
an agent that reported nine findings would lose its build as well. The schema describes what a
discovery must look like and the coordinator decides how many are acted on, which is also what
lets the cap be recorded rather than fatal.

## Sequencing

Items 3, 4, 6 and 8 are small, independent, and worth doing first — three of them are cheap and
the fourth is free. Item 2 is moderate and unblocks nothing else. Items 1, 5 and 7 are large, and
each has a measurement that should precede it: 1 and 5 depend on measuring whether visual
evidence changes outcomes, and 7 depends on §11.2's result. Building any of the three before its
measurement would be building on the assumption the measurement exists to test.
