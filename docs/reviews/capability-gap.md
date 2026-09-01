# Capability gap — this factory against the reference product's full documented surface

| Field | Value |
| --- | --- |
| Document | Gap analysis, second pass |
| Compared against | The reference product's own documentation (`.research/slices/`, ~11k lines across nine areas), not the announcement video |
| Prior pass | [`source-gap-analysis.md`](source-gap-analysis.md) — 80 items from an 8:56 announcement |
| Status | Analysis. Nothing here is adopted until it lands in the PRD through the ordinary change path. |

> **Naming.** The source names a vendor and its products. None of those names appear here or
> anywhere in this repository. The comparison target is *the reference product*.

---

## Why a second pass

The first gap analysis read a nine-minute announcement. An announcement selects: it shows what
demonstrates well and omits the platform underneath. Reading the product's own documentation
instead turns up a different and larger surface — an orchestration platform, a hosted execution
tier, a self-hosting story with three backends, a REST API with two SDKs, a scheduler, and an
enterprise analytics tier — most of which the video never mentions.

The finding of the first pass was that our design "has been written from the inside out": strong
on mechanism, thin on surface. **This pass sharpens that.** The gaps are not mechanisms we lack.
They are *ways in*: ways to start work, ways to call the factory from other software, ways to run
more than one factory, and ways to see across them.

---

## Summary by area

| Area | Their surface | Ours | Verdict |
| --- | --- | --- | --- |
| Factory definition as code | Agents, automations, filters, per-agent model and harness, custom agents | Same, plus schema versioning, inheritance with origin tracking, and `sf validate`/`lint` | **Ahead** |
| Assurance | Review agent, human approval points | Gates vs scorers vs benchmarks kept separate; `regression-proven` with parent-failure classification | **Ahead** |
| Memory | Memory stores, auto-memory, automatic creation from conversations | Four lanes, provenance, source-disjoint corroboration, an eight-stage retrieval filter, `sf memory why` | **Ahead, except conversation mining** |
| Skills | Skill files, arguments, name conflicts, skills-as-agents, suggested skills | Full lifecycle: promote / evolve / merge / split / sunset, evidence-gated | **Ahead on lifecycle, behind on invocation** |
| Provenance and audit | Run history, artifacts | Hash-chained ledger, sealed segments, retention classes, `sf explain` | **Ahead** |
| Identity and permissions | Team access, identity mapping, host-app tokens | Principals, capabilities, duty separation, checkpoints | **Comparable** |
| Integrations | Git host, tracker, chat, error monitoring, generic webhooks | Chat and git host ship; adapter contract for the rest | **Behind** |
| Triggers and scheduling | Scheduled agents: create, list, pause, edit, delete, from CLI and API | A cron field in the schema that nothing reads | **Behind — and worse than absent** |
| Multi-agent orchestration | Parent/child, messaging, named patterns, approval mode, fleet cancellation | Depth-2 delegation with a fan-out ceiling and a tree view | **Behind** |
| Multiple factories | A factory per team, with cross-factory visibility | FR-1.5 names a workspace file; no code | **Behind** |
| API and SDKs | REST API, Python SDK, TypeScript SDK, API keys | A CLI, and a tool surface that lists itself and binds no socket | **Behind** |
| Hosted execution | Cloud agents with environments, accounts, plan limits | Local-first by design; cloud is a topology | **Divergent, deliberately** |
| Self-hosted execution | Worker with Direct / Docker / Kubernetes backends, run routing | Local, container and ssh-worker runners | **Behind on routing and pooling** |
| Enterprise analytics | Summary / users / events endpoints | `sf metrics` over one ledger | **Behind** |
| Web surface | Monitor, inspect, share runs | Six views, read-only, loopback | **Comparable in content, behind in reach** |

---

## The gaps that matter, ranked

Ranked by *what a user cannot do*, not by how much code each would take.

### 1. Scheduled work never fires — and the schema says it does

`TriggerSchedule` is a declared model with a validated cron expression. `Trigger` requires it when
the provider is `schedule` and forbids it otherwise, so a definition author gets a helpful error if
they misuse it. Nothing then reads it. A factory can declare a nightly dead-code sweep, pass
`sf validate` and `sf lint` clean, and never run it once.

This is worse than an absent feature. An absent feature is discovered in the documentation; this one
is discovered in a month, by noticing that something never happened. It is the same class as every
finding this project keeps making about itself — a control that existed and was not wired in — and
it is the single most defensible thing to build next.

**Rank: HIGH. Cost: small.**

### 2. There is no way to run more than one factory

FR-1.5 says multiple factories may share a definition tree via a workspace file. There is no
workspace model, no loader, no command, and no view. Every command takes one factory root, and
every metric folds one ledger.

A team-per-factory arrangement is how the reference product is meant to be used at any size above
one team, and the question it makes answerable — *which of our factories is actually working* — is
one no single-factory tool can answer. Cross-factory reporting is also the honest form of the
enterprise analytics tier: the same folds, over more than one ledger.

**Rank: HIGH. Cost: moderate.**

### 3. Nothing can call the factory except a person at a terminal

`sf serve` prints the tool surface and deliberately binds no socket, with a good reason recorded:
the work items a running factory holds live in the orchestrator's state, and a command that has none
would be offering an option that does nothing. The reason is sound and the consequence is that the
factory is not callable. The reference product's whole automation story — an error monitor opening a
draft change, a webhook starting a run, a script sharding a backlog — rests on an API.

The interesting constraint is ours, not theirs: an API is an authenticated decision channel, and
this project has already refused to build an unauthenticated one into the dashboard. Any API here
has to carry the identity and capability model that `sf` currently gets from the operator's shell.

**Rank: HIGH. Cost: large.**

### 4. Orchestration is one level deep, with no fleet control

We have delegation: depth 2, fan-out 4, a spend rollup and a tree. The reference product has a
parent/child model with messaging between agents, named patterns (supervisor/worker, fan-out/fan-in,
critic/verifier, review swarm, DAG), an approval mode, and a way to cancel an entire fleet.

Two pieces are genuinely missing rather than merely un-named: **messaging between running agents**,
and **cancellation**. A fleet you cannot stop is a spend you cannot stop, and our budget ceiling
bounds the total without giving anyone a way to intervene before it is reached.

**Rank: MEDIUM-HIGH. Cost: moderate.** Cancellation is the half worth building first.

### 5. Skills cannot be invoked, only offered

Our skill lifecycle is the strongest part of the system and is ahead of the reference product's.
But a skill here is only ever *selected by the registry for a run*. Theirs can be invoked directly
as an agent, takes arguments, and can be put on a schedule.

The lifecycle machinery makes this cheap: a skill already declares scope, owners and evals. What is
missing is an argument schema and an entry point.

**Rank: MEDIUM. Cost: small.**

### 6. Run routing has no labels and no pool

We can run local, in a container, or on one ssh worker named in the definition. The reference
product routes a run to a worker by label, from the CLI, from a schedule, from an integration and
from the API, with a pool behind it.

**Rank: MEDIUM. Cost: moderate.**

### 7. Conversation mining

Carried over from the first pass (V69) and still open. Now cheaper than it was: the chat adapter
exists, so the source of conversations is real rather than hypothetical. The trust model constrains
this hard — a claim mined from chat is `untrusted` and needs source-disjoint corroboration before it
can be canon — and those constraints are the interesting part of building it.

**Rank: MEDIUM. Cost: moderate.**

### 8. Computer use

Carried over (V25, V11, V47) and unchanged: promised in FR-22.3, with no tool, effect class, grant
or session contract behind it. The induction plan defers it pending a measurement, and that deferral
still stands on its own terms — but it is the largest single capability either product describes
that this one does not have.

**Rank: MEDIUM. Cost: large.**

---

## Where we are ahead, and why it is not a consolation

Five areas above are marked **Ahead**, and they are the load-bearing ones: assurance separation,
the memory fabric's provenance, the skill lifecycle, the ledger, and definition validation. This
matters because the central bet is that a modest model inside an excellent harness beats a frontier
model inside a poor one, and every one of those is harness.

But a harness nobody can start work in is not a harness anybody uses. The pattern across every gap
above is the same: **we built the inside and not the doors.** Scheduling, workspaces, an API, fleet
control and skill invocation are all doors. None of them is intellectually difficult next to
`regression-proven` or the eight-stage retrieval filter. All of them are the difference between a
system that works and a system somebody can operate.

That is the finding.

---

## What has been closed since this analysis

Written after the fact, so the ranking above stands as it was made rather than being
rewritten to look prescient.

| Gap | Rank | Status |
| --- | --- | --- |
| Scheduled work never fires | HIGH | **Closed.** `sf schedule list/due/run`; a missed window fires once and reports what it skipped; due-ness derived from the ledger. |
| No way to run more than one factory | HIGH | **Closed.** `sf workspace init/list/validate/metrics`; FR-1.4's repository-overlap rule can now fire at all. |
| Nothing can call the factory but a person | HIGH | **Closed.** `sf api serve`, authenticated on every request including reads, capability-checked, keys stored hashed. |
| No fleet control | MEDIUM-HIGH | **Half closed.** `sf stop` ends a run between turns. Agent-to-agent messaging is still absent. |
| Skills cannot be invoked | MEDIUM | **Closed.** Declared arguments, `sf skill render` and `sf skill run`. |
| Computer use | MEDIUM | **Closed.** `UI` effect class, session contract, declared `ui.*` tools, mandatory recording, credential refusal. |
| Chat and git-host integrations | — | **Closed.** Both ship; the git host also makes three outcome metrics computable. |
| Run routing has no labels or pool | MEDIUM | Open. |
| Conversation mining | MEDIUM | Open. Cheaper now that the chat adapter is real. |

## What the live runs changed about this analysis

The analysis above was written from documents. Then the factory was run five times against a
real hosted model, and that found four defects in the keystone gate alone — none of which
1,400 tests had caught, all in the same direction: **the gate refused correct work.**

* The commit a run sits on was declared and never written, so the gate compared against a
  commit that does not exist in any young repository.
* A test's failure class was decided by the length of its name, because the classifier read
  a line the test runner truncates to terminal width.
* The gate required *every* new test to fail at the parent, so writing invariant tests
  beside a regression test was punished.
* A single malformed tool call discarded a run that had passed every gate.

None of these was reachable from the test suite, because every test in it was written by
somebody who already knew what the gate meant. That is the finding worth carrying: **a
harness is not verified by its own authors' tests.** The gap that mattered most was not in
the comparison table at all — it was that nothing here had ever met a model it did not
script.
