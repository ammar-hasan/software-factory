# Evals, Tests, and Gates Specification

| Field | Value |
| --- | --- |
| Component | Assurance subsystem |
| Implements | PRD FR-13, FR-14, FR-22 |
| Mechanisms | Gates (blocking) · Scorers (sampling) · Benchmarks (comparing) |

> Three mechanisms answering three different questions. Conflating them is the most common way an
> assurance system becomes theatre.

| Mechanism | Question | Scope | Timing | Blocking |
| --- | --- | --- | --- | --- |
| **Gate** | Is *this* work ready to advance? | One run | Every run, per stage | **Yes** |
| **Scorer** | Are our runs, in aggregate, good? | A sample | After runs | No |
| **Benchmark** | Is config A better than config B? | Fixed tasks | On demand | Gates *adoption* |

---

## 1. Design stance

**E-1 — Gates are deterministic wherever possible.** A gate that requires a model to decide is a
weaker gate. The baseline gates in §3 are almost all deterministic commands over structured tool
output.

**E-2 — Evidence resolves claims; it does not accompany them.** The unit of assurance is
`claim → artifact`. A summary saying "tests pass" without structured results is a gate failure, not a
style issue.

**E-3 — A test that has never failed is unproven, not proven.** This single sentence generates
`regression-proven`, `criterion-observed-failing`, and the test-health tracking in §6.

**E-4 — The grader is a subject, not an authority.** Scorers are themselves measured against human
labels before their verdicts drive change.

---

## 2. Evidence bundle

```
EvidenceBundle {
  id, run, work_item, stage, sealed_at, digest,
  items: [EvidenceItem],
  claims: [ { claim, supported_by: [EvidenceItemId] } ],
  gates: [GateResult],
  calibration: CalibrationStatement,
}

EvidenceItem {
  id, class: test_results | command_transcript | diff | terminal_recording
           | screen_recording | measurement | ci_result | scorer_result | artifact,
  digest, location, captured_at, redacted: bool, truncated: bool,
}
```

**E-5 — Bundles are sealed and immutable.** Post-seal changes create a new bundle referencing the old.
**E-6 — Redaction happens at capture**, never at read.
**E-7 — Every item is addressable**, so a reviewer can open a specific test result from a change
comment without cloning anything.
**E-8 — `truncated` is a field, never a silent property.**

---

## 3. Gates

```
Gate {
  id, stage: [Stage], applies_when: Predicate,
  check: Command | StructuredEvaluation,
  severity: block | warn,
  remediation, timeout_s,
}

GateResult { gate, outcome: pass|fail|skip|error, findings: [Finding], evidence: [EvidenceRef] }
Finding    { criterion, observed, expected, locator, remediation, severity }
```

### 3.1 Baseline gates

| Gate | Stage | Check | Det.? |
| --- | --- | --- | --- |
| `calibration-present` | all | Calibration statement present and schema-valid | yes |
| `blast-radius-clean` | all | Zero contract violations recorded, *and* something was recording them | yes |
| `secret-clean` | Build, Review | No secret-shaped material in diff, logs, or evidence | yes |
| `build-green` | Build | Project build succeeds | yes |
| `tests-pass` | Build, Review | Repository validation passes, structured results attached | yes |
| `regression-proven` | Build | For defect work: the new test **fails at the parent commit** and **passes at the tip** | yes |
| `spec-agreement` | Design, Review | No `contradicted` unit on the surface | yes |
| `delta-present` | Review | Behavioural change on an active unit has an approved Delta | mostly |
| `coverage-of-criteria` | Review | Every affected criterion maps to an exercising test | yes |
| `criterion-observed-failing` | Review | Every new criterion's test was observed failing without the change | yes |
| `evidence-complete` | Review, Verify | Every claim resolves to an evidence item | yes |
| `no-unreviewed-external` | Review | No external effect outside the permitted set | yes |
| `independent-review` | Review | Critic's model+harness differs from Builder's, or explicit opt-in | yes |

### 3.2 `regression-proven` — the keystone

```
run_at(parent_commit, new_tests)  must FAIL
run_at(tip,           new_tests)  must PASS
```

**E-9** — This is executed by the harness, not asserted by the agent. It requires two checkouts and two
test runs, and that cost is accepted deliberately: it is the difference between "a change that looks
right" and "a change that demonstrably fixes something".
**E-10** — Applies to defect-class work by default. Feature work uses `criterion-observed-failing`
(living-spec.md S-15), which is the same idea expressed over acceptance criteria.
**E-11** — It cannot be satisfied by a test that errors for an unrelated reason at the parent commit:
the failure at parent must be an *assertion* failure attributable to the behaviour under change, and
the harness checks the failure class, not merely the exit code.

### 3.3 Repair loop

```
attempt = 0
while gates fail and attempt < repair_budget:        # default 3
    give agent the structured findings verbatim
    attempt += 1
if still failing:
    if escalations_remaining: escalate one tier, retry once
    else: end run gate_failed with findings and a partial sealed bundle
```

**E-12 — There is no pass-by-timeout.** A gate that cannot run is `error`, which is not `pass`.
**E-12.1 — And no pass-by-absence.** A gate whose evidence is a count of observed events is
`unenforceable` when nothing was observing, never `pass`. Zero violations because nothing was
watching is not zero violations, and the difference is the whole claim. `blast-radius-clean`
read `pass` across every run of a factory whose executor had no filesystem confinement, while an
agent's `pip install -e .` sat in the system site-packages; the counter was honest and the
conclusion drawn from it was not. `unenforceable` does not block — a run the operator chose to
allow still runs — it records that the control was not in effect.
**E-13 — Findings go to the agent verbatim.** Paraphrasing a failure loses the detail that fixes it.

---

## 4. Scorers

```
Scorer {
  name, description, agents: [AgentName],
  labels: [ { value, score: 0..1, description } ],
  passing_score, sampling_rate,        # percent of eligible completed runs
  judge: { harness, model },
  self_improvement: bool,
  rubric,                              # the body
}
```

**E-14 — Classification, not grading.** A scorer returns exactly one declared label. Numeric grades
average away the information needed to act.
**E-15 — One question per scorer.** A scorer asking two things produces failures that point nowhere.
**E-16 — Sampled, asynchronous, non-blocking.** Scoring never influences the run it scores.
**E-17 — On-demand scoring** of a single run replaces that scorer's prior result for that run — used to
test rubric changes before raising the sample rate.
**E-18 — Threshold changes re-render history; they never rewrite recorded classifications.**

### 4.1 Judge integrity

**E-19 — The judge must not share the scored agent's model *and* harness** unless explicitly opted in.
**E-20 — Human-agreement calibration.** Before a scorer's verdicts may drive self-improvement, it must
be run against a human-labelled sample (default ≥ 30 runs) and reach an agreement rate above
`min_agreement` (default 0.8, measured as Cohen's κ ≥ 0.6 to discount chance agreement). Below it, the
scorer is marked `untrusted`: its results are visible but may not gate adoption or feed the loop.
**E-21 — Agreement is re-checked periodically** and after any rubric change, because judge drift is
real and silent.
**E-22 — Scorers are versioned.** Comparing results across a rubric change without noting the version
is an error the dashboard must prevent.

---

## 5. Benchmarks

```
Benchmark {
  name, agent, tasks: [ { id, prompt, success_criteria, fixture_ref } ],
  configurations: [ { harness, model/tier, runner, scaffolds } ],
  scorers: [ScorerRef], repetitions,      # default 5
  holdout: [TaskId],                      # never visible to the improvement loop
}
```

**E-23 — Every benchmark runs a built-in `correctness` scorer** marking each trial pass or fail against
the task's success criteria.
**E-24 — Report per configuration:** pass rate with a confidence interval, cost, latency, scorer
distribution, and per-task detail.
**E-25 — No single score, no declared winner.** The report presents the trade-off; the operator decides
(PRD PR-7). A system that picks the winner has quietly chosen the weighting for you.
**E-26 — Variance is reported, not hidden.** With `repetitions ≥ 5`, report the spread; a difference
inside the spread is not a difference.
**E-27 — Cost accounting states exclusions explicitly.**
**E-28 — Contamination control.** `holdout` tasks are excluded from every improvement-loop input and
from any prompt, memory, or skill the loop may write. A proposal validated only on non-holdout tasks
may not be adopted.
**E-29 — Tasks may be created from completed runs**, copying the input; success criteria must be added
before the suite runs — a task without criteria is not a benchmark task.

### 5.1 Standing benchmark

**E-30** — Every factory maintains one **standing benchmark**: a small, fast suite run automatically
before any definition change is adopted. It is the regression net for the factory itself, and it is why
skill promotion (skills.md §3.1) and self-improvement (§7) can require "no standing-benchmark
regression" as a precondition.

---

## 6. Test-suite health

The factory measures the thing it depends on.

| Signal | Meaning | Action |
| --- | --- | --- |
| Flakiness | Mixed outcomes on identical commits | Quarantine proposal; excluded from gate decisions while quarantined, and the quarantine is visible |
| Never-failed criteria | A criterion whose test has never been observed failing | Proposal to add a mutation or negative case |
| Runtime growth | Suite duration trending up | Proposal to parallelise or split |
| Coverage of criteria | Criteria with no exercising test | Coverage work item |

**E-31 — The factory may open work items to fix its own test suite**, subject to the same review as any
other work. A suite that cannot catch regressions makes every gate above decorative.
**E-32 — Quarantine is never silent.** A quarantined test is reported in every gate result that would
otherwise have depended on it.

---

## 7. Self-improvement loop

```
cluster   failures by (scorer, label, agent, failure signature)
diagnose  root cause from runs, packs, ledger, and gate findings
propose   the minimal change: prompt | skill | pack weight | tier | gate threshold | code
validate  against relevant evals AND the holdout set AND the standing benchmark
submit    as a reviewed change with a Regressions-addressed section linking the evidence
```

**E-33 — Opt-in per scorer.**
**E-34 — Nothing is auto-adopted** (PRD PR-7).
**E-35 — Every proposal links the failing runs and scorer results that motivated it.**
**E-36 — Anti-thrash:** a cooling period per target; no re-proposal of a rejected change without new
evidence; a cap on open proposals per factory.

### 7.1 Reward-hacking defences

The loop can improve a measurement without improving reality. Three defences, all mandatory:

**E-37 — Held-out validation.** Proposals are validated on `holdout` tasks the loop cannot read or
write (E-28). A proposal that improves only non-holdout performance is refused.
**E-38 — Self-referential flagging.** A proposal that edits a **scorer, gate, eval, benchmark task,
threshold, or the holdout set** is flagged `self-referential`, requires a second human reviewer, and
**may never be validated by the artefact it modifies**. The loop cannot grade its own homework.
**E-39 — Counter-metrics.** Every proposal reports, alongside its target metric, an unrelated counter
set — cost per change, rework rate, human review time, and gate pass rate on the standing benchmark.
A proposal that moves its target while degrading a counter-metric beyond tolerance is refused. This
is the concrete defence against Goodharting.

**E-40 — Loop effectiveness is itself measured.** Track proposals opened, adopted, rejected, reverted,
and the *measured* effect of adopted ones. A loop whose adopted proposals do not move outcomes is a
defect and must appear as one on the dashboard, not as activity.

---

## 8. Offline operation

**E-41** — Every gate, scorer, and benchmark runs in local mode against a local model endpoint.
Deterministic gates need no model at all, which is why the baseline set is mostly deterministic (E-1).
**E-42** — A scorer whose judge is unavailable records `error`, never `pass` and never `fail`.

---

## 9. Test matrix

| Test | Asserts |
| --- | --- |
| `regression-proven-two-checkouts` | Satisfaction requires a real failure at the parent commit (E-9) |
| `regression-proven-rejects-error` | An unrelated error at parent does not satisfy the gate (E-11) |
| `evidence-complete-catches-bare-claim` | "Tests pass" with no artifact fails (E-2) |
| `no-pass-by-timeout` | A gate that cannot run is `error`, not `pass` (E-12) |
| `repair-then-escalate-then-fail` | The §3.3 order is exact |
| `judge-independence` | A judge sharing model+harness with its subject is refused (E-19) |
| `untrusted-scorer-cannot-gate` | A scorer below agreement threshold cannot drive adoption (E-20) |
| `benchmark-reports-variance` | No single collapsed score; spread present (E-25, E-26) |
| `holdout-isolation` | The improvement loop cannot read or write holdout tasks (E-28) |
| `self-referential-blocked` | A proposal editing a gate cannot be validated by that gate (E-38) |
| `counter-metric-refusal` | A proposal degrading a counter-metric is refused (E-39) |
| `flaky-quarantine-visible` | A quarantined test is reported in dependent gate results (E-32) |
| `offline-gates` | The deterministic baseline runs with no model available (E-41) |
