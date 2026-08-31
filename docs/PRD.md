# Software Factory — Master Product Requirements Document

| Field | Value |
| --- | --- |
| Document | Master PRD |
| Version | 2.1.0 |
| Status | Revised after design review, then after an adversarial implementation review |
| Owner | The Software Factory Authors |
| Licence | Apache-2.0 |
| Supersedes | 2.0.0 |
| Review record | [`reviews/`](reviews/) — 62 adversarial, 117 completeness, 72 bias findings; dispositions in Appendix C |

> **Reading order.** §1–§4 are the *why*. §5–§7 are the *what* (concepts, architecture, and the
> complete functional requirement set). §8–§12 are the *how well* (non-functional requirements, data
> model, metrics, rollout, risk). Every requirement carries a stable ID (`FR-x.y`, `NFR-x.y`) and a
> priority: **P0** (must ship in v1), **P1** (must ship in v1.x), **P2** (planned).

---

## 1. Executive summary

A **software factory** takes in requests — bug reports, feature asks, support escalations, alerts,
review requests — and turns them into a steady stream of reviewable, mergeable changes, worked by a
coordinated fleet of specialist agents rather than by one chat session at a time.

Most attempts at this fail in one of two ways. They either wrap a single agent in a webhook and call
it a factory, or they build a closed platform where the customer owns the backlog but not the
machine that works it.

**Software Factory** is a third thing: an open, **local-first** factory whose entire definition —
agents, prompts, skills, automations, runners, scorers, specs, and policy — lives as version
controlled files in the user's own repository, and whose runtime executes identically on a laptop, a
self-hosted server, or a cloud runner. There is no control plane the operator does not own.

### 1.1 The central bet

> **A modest model inside an excellent harness beats a frontier model inside a poor one.**

This is not a slogan; it is the design constraint that every subsystem in this document answers to.
The harness — not the model — is the product. Concretely, the factory commits to making every agent
that runs inside it *more capable than the same agent is anywhere else*, through five mechanisms
specified in §7:

| Mechanism | Requirement family | What it buys the agent |
| --- | --- | --- |
| **Awareness** | FR-9 Awareness Pack | The agent starts every task already knowing the spec slice that governs it, the shape of the code it will touch, what was tried before, what broke last time, and which conventions apply. It retrieves rather than recalls. |
| **Tools** | FR-10 Tool Registry | Anything that can be computed *is* computed. The model is spent on judgement, not on arithmetic, file listing, symbol lookup, or format conversion. |
| **Confidence** | FR-11 Calibration | Agents state confidence with cited evidence. The harness routes low-confidence work to verification or escalation instead of letting a guess through as a result. |
| **Courage** | FR-12 Blast-radius contract | Every run is checkpointed and reversible. A bold approach costs a rollback, not an incident — and the agent is told, in machine-checkable terms, exactly what it is safe to attempt. |
| **Quality** | FR-13 Gates & evidence | Verification is not advice. A change is not presentable until its gates pass and its evidence bundle is attached. |

The measurable claim this makes, and which §11 turns into an acceptance test, is:

> **Lighter and cheaper models, run inside this harness, reach quality that heavier models do not
> reach outside it — and the factory escalates to a heavier model only where evidence shows
> escalation is warranted.**

### 1.2 The local-first modification

Everything described here runs **without any hosted service**. `sf run` on a developer laptop
executes the same work item, through the same stages, with the same agents, gates, memory, and
evidence as a fleet running against a shared queue. Cloud and self-hosted execution are *deployment
choices*, not different products (§7.8, §7.20). The local mode is the reference implementation;
hosted modes are optimisations of it.

---

## 2. Problem statement

### 2.1 What is broken today

**P1 — Laptop agents don't compound.** An engineer running an interactive agent locally produces a
good change and nothing else. The reasoning, the dead ends, the discovered conventions, and the
correction the human made all evaporate when the session closes. The next engineer, and the next
agent, start from zero. There is no mechanism by which the organisation gets better at using agents.

**P2 — No one can answer "is this worth it?"** Teams adopt coding agents and cannot say what they
cost per merged change, what share of changes needed rework, whether last month's model swap helped,
or which stage of their process the agents are actually good at. Spend is visible; return is not.

**P3 — Governance is accidental.** Every developer installs their own agent, points it at their own
keys, grants it their own credentials, and connects their own tool servers. The union of those
choices is the organisation's real security posture, and nobody has ever seen it written down.

**P4 — Specs rot instantly.** Intent is expressed in a ticket, refined in a thread, half-implemented
in a branch, and corrected in review. Six weeks later the only surviving record of what the system
was *supposed* to do is the code, which is also the thing you were trying to check.

**P5 — Memory is either absent or a liability.** Systems that give agents persistent memory
generally do so by appending. Contradictions accumulate, stale facts outlive the code they described,
one bad inference poisons every downstream run, and nobody can trace a claim back to its source.

**P6 — Skills accrete and never retire.** Reusable agent procedures get added and never removed.
Overlapping skills fire at once, obsolete ones fire on stale assumptions, and an oversized skill
library degrades selection quality for every task.

**P7 — Frontier models are used as a substitute for engineering.** Because the harness is thin, the
only lever available when quality is poor is a more expensive model. This is the most expensive
possible fix for what is usually a context problem.

**P8 — The factory is a black box you rent.** When the workflow that ships your software is defined
inside a vendor's product, you cannot review it, diff it, roll it back, test it, or run it when the
vendor is down or has moved on.

### 2.2 Who has this problem

Any engineering team with **repeatable work that outlives a single session**: a maintained backlog, a
support-to-defect pipeline, review load across repositories, recurring migrations, or on-call
follow-ups. Team size matters less than *repetition*. A two-person team with a noisy issue tracker
benefits as much as a two-hundred-person one.

### 2.3 Why now

Two changes are observable and one is a hypothesis, and they are labelled accordingly because the
baseline version of this section asserted the thesis as its own premise.

**Observable.** Coding agents now complete well-scoped changes at a rate that makes a pipeline worth
building around them. Structured tool use is reliable enough that deterministic scaffolding can sit
*around* a model rather than being simulated inside it.

**Hypothesis, not premise.** That small models, given excellent context, handle most factory work.
This is the central bet (§1.1). It is the thing §11.2 exists to test, and it is not evidence for
itself. If it is false, this project is still useful — a factory with a large model in it is a
factory — but its cost argument and its offline argument both weaken substantially, and this document
would need revising rather than defending.

---

## 3. Vision, principles, and non-goals

### 3.1 Vision

> Every engineering organisation runs a software factory that it owns outright, defined in its own
> repository, improving measurably every week, on whatever models and hardware it chooses — with its
> people spending their attention on the decisions that deserve it.

### 3.2 Design principles

Numbered so requirements can cite them. Where a requirement conflicts with a principle, the
requirement must say so and justify it.

- **PR-1 — Files are the source of truth.** If it changes the factory's behaviour, it is a file in a
  repository, reviewable and revertible. UIs and APIs are editors over files, never a second store.
- **PR-2 — Local is the reference implementation.** Any capability that cannot run on one laptop with
  no network beyond a model endpoint is a hosted *extension*, and must be marked as such.
- **PR-3 — Evidence over assertion.** No stage completes on an agent's say-so. Claims carry artifacts.
- **PR-4 — Instructions never grant access.** What an agent *can reach* comes from configuration and
  external permissions. Changing what an agent is *told* must never change what it is *able* to do.
- **PR-5 — Reversibility buys boldness.** Cheap, total undo is the precondition for creative agent
  behaviour. Invest in checkpoints so agents can afford to be brave.
- **PR-6 — Compute the computable.** Any fact obtainable by a deterministic tool must not be left to
  the model to infer.
- **PR-7 — Nothing is adopted without a human decision.** Self-improvement proposes; people dispose.
- **PR-8 — Every subsystem must be able to shrink.** Memory, skills, and specs all need pruning paths
  as first-class as their growth paths.
- **PR-9 — Degrade, don't fail.** Missing integration, unavailable model, offline network: the
  factory does less, explicitly, and says so. It does not produce unverified work silently.
- **PR-10 — Portability is a feature.** Model, harness, runner, and storage are independent choices.
  No requirement may assume a specific vendor.

### 3.3 Non-goals

- **NG-1** — Not an autonomous merge system. The factory opens changes; humans merge them. (§7.16)
- **NG-2** — Not an IDE, editor, or terminal replacement.
- **NG-3** — Not a model provider. It routes to models; it does not serve them.
- **NG-4** — Not a general workflow engine. It is opinionated about the software delivery lifecycle.
- **NG-5** — Not a replacement for CI. It *drives* CI and consumes its results.
- **NG-6** — Not a headcount-reduction tool. The design target is throughput and quality of
  decisions, and every checkpoint in §7.16 exists to keep humans in the loop where judgement matters.

---

## 4. Users and jobs to be done

### 4.1 Personas

**U1 — Factory Operator (platform/DevEx engineer).** Owns the factory definition. Needs the whole
system legible as code, safe to change, and cheap to roll back. *Success:* can explain and modify any
factory behaviour by reading and editing files.

**U2 — Contributing Engineer.** Sends work in, picks work up, reviews what comes out. Needs the
factory to be a better colleague than a nuisance: good context, honest uncertainty, real evidence.
*Success:* reviewing a factory change is faster than writing it, and they trust its evidence.

**U3 — Engineering Manager.** Needs to know cost, throughput, quality, autonomy, and trend — and to
defend those numbers. *Success:* can answer "is this working, and where is it weakest?" from the
dashboard alone.

**U4 — Security / Compliance Reviewer.** Needs to enumerate what every agent can reach, where data
goes, and what is retained. *Success:* can audit the factory from the repository plus one report.

**U6 — Change Reviewer.** Inherits every change the factory produces and pays for its quality in
attention. Distinct from U2 because reviewing machine-authored changes at volume is a different job
from contributing: the reviewer did not build the context, cannot ask the author follow-up questions
in the usual way, and faces a queue whose size the factory controls. Needs: evidence that reduces
work rather than adding it, honest uncertainty, and a bounded queue. *Success:* reviewing is
genuinely faster than writing, **measured** (O-6), not assumed — and the reviewer can say no.

**U5 — Solo maintainer / small team.** Wants the whole thing on a laptop with a local model and no
account anywhere. *Success:* `sf init && sf run` does real work offline.

### 4.2 Jobs to be done

| ID | Job | Primary persona |
| --- | --- | --- |
| JTBD-1 | Turn a reported defect into a reviewed change without a human writing the first draft | U2 |
| JTBD-2 | Keep a backlog moving under a consistent triage and delivery policy | U3 |
| JTBD-3 | Give every incoming change a competent first-pass review before a human sees it | U2 |
| JTBD-4 | Scope, execute, and validate a mechanical migration across repositories | U1 |
| JTBD-5 | Prove that a model, prompt, or skill change actually improved outcomes | U3 |
| JTBD-6 | Keep specification, code, and tests in provable agreement over time | U2 |
| JTBD-7 | Run the entire above on owned hardware, with owned models, offline | U5 |
| JTBD-8 | Audit and constrain exactly what each agent can reach | U4 |
| JTBD-9 | Make the factory itself measurably better each week without manual tuning | U1 |

### 4.3 Acceptance narratives

**AN-1 (defect → change).** An error tracker fires. An automation matching that signal opens a work
item. Triage reproduces the fault, cites the failing path, and reports scope as small. The Conductor
skips design. Build reproduces the failure in a new test, fixes it, runs the repository's own
validation, records a terminal session and — for a user-facing change — a UI recording. Review
independently re-runs the failing test at the parent commit to confirm the test would have caught it,
checks the change against the spec slice, and returns findings. The Conductor posts the change, its
evidence bundle, and the review verdict back to the originating thread and stops. A human merges.

**AN-2 (offline solo).** A maintainer with no network beyond a local model endpoint runs
`sf work "make the CSV importer tolerate BOM headers"`. The factory assembles an Awareness Pack from
the local index, runs triage → build → review with a small local model, produces a branch, a test, an
evidence bundle, and a spec delta. Nothing left the machine.

**AN-3 (proving an improvement).** A scorer shows the build agent skipping tests on 18% of sampled
runs. Self-improvement groups the failures, proposes a skill edit, and opens it as a change with the
failing runs linked. The operator benchmarks the current and proposed configuration over a fixed task
set with repetitions, sees pass rate rise and cost fall, and merges. The scorer's next window
confirms the shift against the recorded baseline.

---

## 5. Core concepts and glossary

| Term | Definition |
| --- | --- |
| **Factory** | One deployed instance of the pattern: a set of repositories, a fleet of agents, an execution target, and one policy. Sized by *product surface*, not by team (§7.1). |
| **Factory definition** | The versioned file tree that fully describes a factory. The only source of truth (PR-1). |
| **Conductor** | The single coordinating agent in a factory. The only agent that talks to the requester. Routes work, dispatches specialists, asks humans questions, hands off results. Exactly one per factory. |
| **Specialist agent** | A role-scoped agent: Scout (triage), Architect (design), Builder (implementation), Critic (review), Prover (verification), or a custom role. |
| **Handle** | The name a team @-mentions to reach a factory's Conductor. Distinct from the factory's name. |
| **Work item** | One request the factory acts on, holding identity from intake to handoff across any number of runs. |
| **Stage** | A phase a work item occupies: Intake, Triage, Design, Build, Review, Verify, Handoff, Complete, Cancelled. |
| **Run** | One execution of one agent against one work item, with a transcript, ledger entries, artifacts, and a cost record. |
| **Automation** | A resource binding a trigger (with filters) to an agent and a starting prompt. |
| **Runner** | The compute profile a run executes on: OS, architecture, image, size, setup commands. |
| **Executor** | The backend that realises a runner: `local`, `container`, `ssh-worker`, or `cloud`. |
| **Harness** | The agent runtime driving the model loop. `loom` is built in; external harnesses are adapters. |
| **Awareness Pack** | The assembled, budgeted context handed to an agent at run start (§7.9). |
| **Tool Registry** | The typed catalogue of deterministic and effectful tools an agent may call (§7.10). |
| **Living Spec** | The versioned, structured statement of intended behaviour, addressable by unit (§7.5). |
| **Spec Delta** | A reviewable, provenance-carrying proposed change to the Living Spec. |
| **Three-way agreement** | The checked invariant that spec unit, implementing code, and covering test are mutually consistent. |
| **Memory Fabric** | The self-regulating memory subsystem, organised in lanes (§7.6). |
| **Lane** | A memory tier: Working, Candidate, Canon, or Archive. Promotion between lanes is earned. |
| **Skill** | A versioned, discoverable procedure an agent can load. Has a lifecycle (§7.7). |
| **Scorer** | An evaluator that classifies sampled completed runs against a rubric (§7.13). |
| **Benchmark** | A fixed task suite run across configurations with repetitions, for comparison (§7.13). |
| **Gate** | A blocking check a stage must pass before the work item may advance (§7.13). |
| **Evidence Bundle** | The artifact set proving a claim: test output, diffs, recordings, logs, scorer results. |
| **Ledger** | The append-only, hash-chained record of everything the factory did (§7.15). |
| **Blast radius** | The machine-checked bound on what a run may modify, and how it is undone (§7.12). |
| **Escalation ladder** | The ordered model/effort tiers a run may climb, with evidence required at each step (§7.11). |

---

## 6. System architecture

### 6.1 Layered view

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  INTAKE            Git host · chat · trackers · webhooks · schedules · CLI    │
│                    · Factory MCP · monitoring signals                        │
├──────────────────────────────────────────────────────────────────────────────┤
│  ORCHESTRATION     Automations → Conductor → stage machine → work items      │
│                    dispatch · policy · human checkpoints · handoff           │
├──────────────────────────────────────────────────────────────────────────────┤
│  HARNESS  (Loom)   Awareness Pack · Tool Registry · calibration · blast       │
│                    radius · escalation ladder · turn loop · budget control    │
├──────────────────────────────────────────────────────────────────────────────┤
│  KNOWLEDGE         Living Spec + Delta · Memory Fabric · Skill Registry       │
├──────────────────────────────────────────────────────────────────────────────┤
│  ASSURANCE         Gates · Scorers · Benchmarks · Evidence · Self-improvement │
├──────────────────────────────────────────────────────────────────────────────┤
│  EXECUTION         Runner profiles → executors: local · container ·           │
│                    ssh-worker · cloud                                        │
├──────────────────────────────────────────────────────────────────────────────┤
│  FOUNDATION        Definition loader · Ledger · Secrets · Metrics · Store     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Planes

Two planes, and the boundary between them is the security story (§7.17).

- **Coordination plane** — reads the definition, owns the stage machine, schedules runs, resolves
  policy, holds the ledger, and serves the dashboard/API. Trusted. Never executes repository code.
- **Execution plane** — checks out code, runs setup, invokes tools, executes commands and tests.
  Untrusted by construction; isolated per run; receives only the credentials its agent declares.

In **local mode both planes are the same process tree on one machine**, separated by process
boundary and sandbox rather than by network. This is what makes local the reference implementation
(PR-2) rather than a degraded mode: the same code paths run, with a different executor.

### 6.3 Deployment topologies

| Topology | Coordination | Execution | Model inference | Intended user |
| --- | --- | --- | --- | --- |
| **Solo** | Local process | Local subprocess sandbox | Local endpoint or remote API | U5 |
| **Workstation** | Local process | Local container | Any | U2 |
| **Team self-hosted** | Owned server | Owned container/VM pool | Owned or vendor | U1, U4 |
| **Hybrid** | Owned server | Owned workers + burst cloud | Any | U1 |
| **Cloud** | Hosted coordination | Hosted runners | Any | U3 |

**FR-0.1 (P0)** — Topology must be selectable per factory, and switching topologies must require no
change to agents, skills, scorers, automations, or specs; only `runners/` and executor settings change.

**FR-0.2 (P0)** — A factory definition must be portable between topologies with byte-identical files.
A conformance suite (§7.20) asserts identical stage transitions and gate outcomes across executors
for a fixed task set.

### 6.4 The run lifecycle

```
work item (stage S) ─► Conductor selects agent A
        │
        ▼
  resolve config: harness, model tier, runner, tools, secrets, skills, memory scope
        │
        ▼
  Awareness Pack assembly ── budgeted, cited, deterministic-first ──┐
        │                                                           │
        ▼                                                           │
  acquire workspace: checkout, worktree, checkpoint C0  ◄───────────┘
        │
        ▼
  turn loop: model ↔ Tool Registry, ledger-recorded, budget-enforced,
             escalation ladder available, blast radius enforced
        │
        ▼
  self-check: gates for stage S · calibration statement · evidence assembly
        │
        ├── gates fail ──► bounded repair loop ──► re-gate ──► (exhausted) return with findings
        │
        ▼
  commit outputs: branch/diff · evidence bundle · spec delta · memory candidates
        │
        ▼
  ledger seal · cost record · scorer sampling · stage transition decision
```

Every arrow in that diagram is a requirement in §7 and a test in the conformance suite.

---

## 7. Functional requirements

### 7.1 Factory model and sizing (FR-1)

**FR-1.1 (P0)** — A factory is defined by a single definition tree rooted at a directory containing
`factory.yaml`. Its identity is `name`; its addressable handle is `handle`.

**FR-1.2 (P0)** — A factory declares one or more repositories it works in. All work items belong to
exactly one factory.

**FR-1.3 (P0)** — A factory applies **one policy** across all of its intake sources. Repository
groups requiring different policies must be separate factories.

**FR-1.4 (P0)** — Sizing guidance must be enforced by lint, not just documented: `sf lint` warns when
a factory's repositories have no shared release cadence or dependency edges, and when two factories
in the same tree overlap on a repository.

**FR-1.5 (P1)** — Multiple factories may share a definition tree via a workspace file listing factory
roots, so a monorepo of factories is reviewable in one change.

**FR-1.6 (P0)** — A factory must declare its schema version. The loader must reject unknown versions
with a message naming the supported set, and must never partially apply a definition (§7.2).

### 7.2 Definitions as code (FR-2)

**FR-2.1 (P0)** — The complete factory definition is files. The canonical tree:

```
factory.yaml                     # root document
agents/<name>/agent.md           # frontmatter config + prompt body
agents/<name>/skills/<s>/SKILL.md
automations/<name>/automation.md # frontmatter triggers + prompt body
runners/<name>.yaml              # compute profile
scorers/<name>/scorer.md         # frontmatter contract + rubric body
skills/<name>/SKILL.md           # factory-wide skills
specs/<area>/<unit>.md           # living spec units
policy/*.yaml                    # gates, checkpoints, budgets, escalation
memory/policy.yaml               # memory admission, decay, promotion rules
```

Resource names derive from the path. Only `factory.yaml` and at least one agent are required.

**FR-2.2 (P0)** — Every file kind must have a published, machine-readable JSON Schema, generated from
the same parser that validates loads, and served by `sf schema [kind]` without authentication or
network access.

**FR-2.3 (P0)** — Validation must be **whole-tree and atomic**. A definition that fails validation
never partially applies; the running factory continues on its last valid definition.

**FR-2.4 (P0)** — Validation errors must cite file and line, name the offending key, and state the
accepted values. Unresolvable cross-references (an automation naming a missing agent, an agent naming
a missing runner, a scorer naming a missing agent) are validation errors, not runtime errors.

**FR-2.5 (P0)** — `sf validate` must run offline and must be usable as a pre-commit hook and a CI
check. The CI check must annotate the pull request diff.

**FR-2.6 (P0)** — Definition changes must be reviewable as ordinary code changes, with the same
history, diff, blame, revert, and protection rules as application code.

**FR-2.7 (P0)** — A definition must be **loadable in dry-run**: `sf plan` prints the resolved,
fully-inherited configuration for every agent and automation without executing anything.

**FR-2.8 (P1)** — Definitions support environment interpolation from a declared, validated variable
set. Undeclared variables are validation errors. Secret *values* never appear in definitions — only
secret *names* (PR-4).

**FR-2.9 (P1)** — A definition may `extend` a base definition (a shared organisational template),
with a documented, deterministic merge order and a `sf plan --explain` that shows which file supplied
each resolved value.

**FR-2.10 (P0)** — Definition inheritance is: factory defaults → agent → automation override. Maps
(`secrets`, `mcpServers`, `tools`) declared at a lower level **replace** rather than merge.
`sf plan` must show the outcome explicitly.

**FR-2.10a (P0) — Factory-wide grants are defaults, not floors.** *Revised in v2.1.0.* The original
text made them always-apply on top of whatever a level resolved to, which an implementation review
showed defeats narrowing entirely: an agent declaring `secrets: []` got the factory's secrets back.
A level that declares nothing inherits the factory-wide set; a level that declares its own —
**including an empty list** — replaces it. Default-deny (FR-17.2) requires that an explicit empty
declaration results in nothing, because otherwise an agent cannot narrow its own grants and the
narrowing syntax is decorative.

### 7.3 Agents (FR-3)

**FR-3.1 (P0)** — Every factory declares exactly one agent with role `CONDUCTOR`. Zero or more than
one is a validation error.

*Rationale and its limits.* One conductor gives a requester one address and one conversation, and
gives the factory one place where routing decisions are made and audited. That is an interface and
auditability argument, not a proof that a single coordinator is architecturally necessary — a
peer-to-peer or stage-owned routing model is not obviously worse, and has not been tested here. It is
also a concentration of authority over attacker-controllable text, which is why FR-3.3a bounds what
that authority can do. Recorded as OQ-3.

**FR-3.2 (P0)** — Built-in roles: `CONDUCTOR`, `SCOUT`, `ARCHITECT`, `BUILDER`, `CRITIC`, `PROVER`,
`CUSTOM`. Role determines default gates, default skills, default Awareness Pack composition, and
default stage association.

| Role | Stage | Responsibility | Produces |
| --- | --- | --- | --- |
| Conductor | all | Routes work, holds the conversation, asks humans, hands off | Decisions, questions, status, final handoff |
| Scout | Triage | Establishes what is true and how big the change is | Evidence, reproduction, scope, complexity, open questions |
| Architect | Design | Turns requirements into a plan with validation criteria | Spec delta, acceptance criteria, draft change |
| Builder | Build | Makes and validates the change | Code, tests, validation output, visual evidence |
| Critic | Review | Independently checks the finished change | Findings, verdict, re-run validation |
| Prover | Verify | Establishes the change does what was claimed, end to end | Recordings, reproduction proof, evidence bundle |

**FR-3.3 (P0)** — Roles are **responsibilities, not a fixed pipeline**. The Conductor must be able to
skip stages that do not apply, enter partway when context suffices, and return work to an earlier
stage. Skipping is recorded in the ledger with a reason.

**FR-3.3a (P0) — Skip authority is bounded by policy, not by judgement.** The Conductor reads
attacker-controllable text (issue bodies, comments, chat), so unbounded routing authority is an
injection primitive: text that persuades the Conductor to skip Review removes Review. Policy
therefore declares, per work class, which stages are **non-skippable**. Review and Verify are
non-skippable by default. A skip of a non-skippable stage is not a decision the Conductor can make;
it requires a human decision recorded against an identified principal (FR-26.2).

**FR-3.3b (P0) — Routing decisions cite their basis.** Every skip records which signal justified it
and that signal's trust class (FR-6.4b). A skip justified only by `untrusted` input is refused.

**FR-3.4 (P0)** — Each agent independently selects harness, model, runner, executor, tool grants,
secret grants, skills, and memory scope. Unset values inherit from factory defaults (FR-2.10).

**FR-3.5a (P0) — Independence is a ladder, not a binary.** In a single-provider or offline factory,
"different model and harness" may be unsatisfiable. The requirement is therefore the *strongest
available* rung, declared and recorded per factory: (1) different provider; (2) different model
family; (3) different model size or version; (4) same model, different harness; (5) same engine with
an independent deterministic checker carrying the review's blocking weight. Rungs 4 and 5 must be
declared explicitly and are reported on every review verdict, so a reader knows how independent the
review actually was. A factory that can only reach rung 5 is valid and is told so.

**FR-3.5 (P0)** — The Critic must not run on the same model *and* harness as the Builder for the same
work item unless the definition explicitly opts in with `allowSharedBlindSpot: true`. Default
configurations must differ; `sf lint` warns otherwise. Rationale: independent review requires
independent failure modes.

**FR-3.6 (P0)** — Only the Conductor communicates with the requester. Specialists surface questions
*through* the Conductor, which is responsible for asking, waiting, and routing the answer back.

**FR-3.7 (P0)** — For revisions, the Conductor must continue the *existing* conversation with the
same specialist rather than starting a new one, preserving context across passes.

**FR-3.8 (P0)** — Custom agents may be declared for any purpose (documentation, security sweep,
dependency hygiene, release notes, dead-code removal). A custom agent need not participate in the
default lifecycle and may be driven purely by its own automation.

**FR-3.9 (P0)** — An agent's Markdown body is its durable role prompt. It must be composed at runtime
with, and clearly delimited from: the Awareness Pack, applicable skills, policy statements, and the
work item's task text. Precedence and delimiters must be specified and tested.

**FR-3.10 (P1)** — Agents may declare `concurrency` (max simultaneous runs) and `queue` semantics, so
a factory throttles a specific role without throttling the whole fleet.

**FR-3.11a (P0) — Budgets compose upward.** Per-agent budgets do not bound anything, because rework
resets them: a work item that cycles Build → Review → Build ten times spends ten budgets. Three
levels are therefore required and independently enforced: **per run** (FR-3.11), **per work item**
(total across every run it ever causes, including retries, scoring, and self-improvement follow-ups),
and **per factory per period**. Exhaustion at the work-item level parks the item as
`BLOCKED: budget_exceeded` with everything produced so far; exhaustion at the factory level stops
intake and notifies, and never silently degrades quality to fit.

**FR-3.11b (P0) — Assurance is inside the budget, not beside it.** Scoring, benchmarking, pack
assembly, memory policing, and self-improvement runs consume the same budgets and are attributed to
the work item or factory that caused them. A factory must be able to discover that it is spending
more on introspection than on work (metric O-10), which is impossible if assurance is unbudgeted.

**FR-3.11 (P0)** — Agents must declare a **budget**: max wall-clock, max tool calls, max tokens, max
cost. Exceeding a budget ends the run with `budget_exceeded`, a partial evidence bundle, and a
Conductor decision point — never a silent truncation.

**FR-3.12 (P1)** — An agent may declare `fallback` agents: if a run fails with a retryable class, the
Conductor may dispatch the fallback with the accumulated context, recorded as an escalation.

### 7.4 Work items and the stage machine (FR-4)

**FR-4.1 (P0)** — A work item is created at intake and holds a stable identity through completion,
regardless of how many runs, agents, stages, or humans touch it.

**FR-4.2 (P0)** — The **default** stage set is
`INTAKE → TRIAGE → DESIGN → BUILD → REVIEW → VERIFY → HANDOFF → COMPLETE`, with `CANCELLED` and
`BLOCKED` reachable from any stage. Legal transitions are an explicit table, and a transition not in
the table is a defect.

**FR-4.2a (P0) — The stage set is configuration, not architecture.** These eight stages were adopted
by analogy with how teams already describe delivery, not derived from the problems in §2, and this
document should not pretend otherwise. The stage graph is therefore declared in `policy/stages.yaml`
and a factory may define its own, subject to two invariants that *are* derived: every work item has
exactly one current stage, and at least one non-skippable verification stage precedes handoff
(FR-3.3a). Whether the default eight is right at all is OQ-1, and it is expected to change.

**FR-4.3 (P0)** — Every transition records: from, to, actor (agent or human), reason, evidence
references, and timestamp, appended to the ledger.

**FR-4.4 (P0)** — `COMPLETE` means *handed to a human with evidence*, never *merged* or *deployed*.
Merge state is observed and reported, never assumed (NG-1).

**FR-4.5 (P0)** — A reply on the originating thread, issue, or change must continue the existing work
item rather than opening a new one. Repeated deliveries of the same upstream event must be
idempotent, keyed on a stable provider event identity.

**FR-4.6 (P0)** — A work item carries its **source context** (thread, issue, change, permalink)
through every stage, and results must be posted back to that same place.

**FR-4.7 (P0)** — `BLOCKED` requires a machine-readable blocker: `awaiting_human`, `awaiting_ci`,
`missing_credential`, `budget_exceeded`, `conflicting_spec`, `gate_failed_terminal`, or
`external_dependency`. Blockers must carry the exact action needed to unblock.

**FR-4.8 (P0)** — Any human must be able to stop, cancel, re-stage, or reassign a work item at any
time, from the CLI, dashboard, or originating tool.

**FR-4.9 (P1)** — Work items may declare dependencies on other work items; the Conductor must not
advance a dependent item past `DESIGN` while its dependency is unresolved.

**FR-4.10 (P0)** — A work item's full state must be reconstructible from the ledger alone. The
database is a cache (§7.15).

### 7.5 Living Spec and Delta (FR-5)

This is the answer to P4 (spec rot) and the substrate the whole factory reasons against.

**FR-5.1 (P0)** — The Living Spec is a tree of **spec units** under `specs/`. A unit is a Markdown
file with frontmatter and a stable `id` that survives renames and moves.

**FR-5.2 (P0)** — A spec unit declares:

| Field | Meaning |
| --- | --- |
| `id` | Stable identity (never reused, never changed) |
| `title` | Human name |
| `status` | `draft` \| `active` \| `deprecated` \| `retired` |
| `intent` | What must be true, in behavioural terms |
| `acceptance` | Enumerated, individually checkable criteria |
| `constraints` | What must not happen (performance, security, compatibility) |
| `implements` | Code anchors: paths, symbols, or content-addressed ranges |
| `verifies` | Test anchors: test ids/paths that check this unit |
| `supersedes` | Prior unit ids this replaces |
| `provenance` | Where this intent came from: work item, thread, human, commit |
| `confidence` | How well-established this intent is |

**FR-5.3 (P0)** — **No agent edits the spec directly.** All changes arrive as a **Spec Delta**: a
structured, reviewable proposal listing added, modified, superseded, and retired units, each with a
rationale and provenance.

**FR-5.4 (P0)** — A Spec Delta must be independently reviewable *before* the code that implements it.
Design-stage output is a Spec Delta plus a draft change, not code.

**FR-5.5 (P0)** — **Three-way agreement.** For every `active` spec unit the factory must be able to
compute an agreement state over (unit, implementing code, covering test):

| State | Meaning | Default handling |
| --- | --- | --- |
| `agreed` | Anchors resolve; tests exist and pass; acceptance criteria mapped | Healthy |
| `unverified` | Anchors resolve; no covering test | Gate warning; queued as improvement work |
| `drifted` | Code changed under the anchor without a delta | Gate warning; drift report |
| `contradicted` | Test fails, or two active units conflict on the same anchor | Gate failure; blocks Build |
| `orphaned` | Anchors no longer resolve | Drift report; retirement candidate |

**FR-5.6 (P0)** — The Awareness Pack for every run must include the **spec slice** relevant to the
work item: units whose anchors intersect the change surface, plus their constraints, plus any unit in
`contradicted` state touching that surface.

**FR-5.7 (P0)** — A change that alters behaviour covered by an `active` unit without an accompanying
Spec Delta must fail the Review gate. The Critic must state which unit and which criterion.

**FR-5.8 (P0)** — Spec units are **content-addressed for drift**: each `implements` anchor stores a
digest of the anchored range, so drift is detected mechanically rather than by asking a model.

**FR-5.9 (P1)** — `sf spec diff` renders the behavioural difference between two definition revisions:
what the system is now supposed to do that it was not before.

**FR-5.10 (P1)** — `sf spec cover` reports the acceptance-criteria coverage matrix: criteria with no
test, tests with no criterion, and criteria whose tests have never failed (suspicious, per §7.13).

**FR-5.11 (P0)** — Retirement is first-class (PR-8). A unit may be retired with a reason; retired
units leave the active slice but remain in history and remain resolvable by id forever.

**FR-5.12 (P1)** — Specs may be **bootstrapped from an existing codebase**: `sf spec induct` proposes
draft units from code and tests, each marked `draft` with low confidence, requiring human promotion.
This is the on-ramp for a repository that has no spec today.

### 7.6 Memory Fabric (FR-6)

The answer to P5. Memory here is not an append log; it is a governed store with admission control,
policing, evolution, and decay.

**FR-6.1 (P0) — Lanes.** Every memory lives in exactly one lane:

| Lane | Contents | Readable by | Promotion out |
| --- | --- | --- | --- |
| **Working** | Within-run scratch: findings, hypotheses, partial results | The owning run | Ends at run end unless nominated |
| **Candidate** | Nominated but unproven claims (quarantine) | Only agents that opt in, always labelled unverified | Requires corroboration or human confirmation |
| **Canon** | Verified, corroborated, in-force knowledge | All agents in scope | — |
| **Archive** | Superseded or expired memories, retained for audit | Retrieval by explicit query only | Never (re-entry requires re-derivation) |

**FR-6.2 (P0) — Admission control.** A write into Candidate must carry: content, a typed `kind`,
`scope`, `provenance` (run id, transcript span, tool outputs, file+line), `confidence`, `evidence`
references, and a `ttl` or `expires_on`. A write missing any required field is rejected, not stored.

**FR-6.3 (P0) — Typed memory kinds**, each with its own admission rules and default lifetime:
`fact` (about the system), `convention` (how this team does things), `decision` (with rationale),
`failure` (what did not work, and why), `preference` (human-stated), `procedure` (candidate skill,
§7.7), `anchor` (stable pointer into code), `metric` (observed measurement).

**FR-6.4 (P0) — Promotion is earned, never automatic.** Candidate → Canon requires *at least one* of:
(a) **source-independent corroboration** — a different run, on a different model or tool path, whose
claim derives from a *disjoint provenance set*; (b) a passing verification, test, or gate that
directly exercises the claim; (c) explicit human confirmation by an identified principal. The
satisfying evidence is recorded on the memory.

**FR-6.4a (P0) — Corroboration is computed over sources, not over runs.** Two runs that read the same
issue comment, the same file, or the same upstream memory are one observation sampled twice. The
promotion check must intersect the runs' provenance sets and refuse promotion when the intersection
is non-empty. Without this, untrusted text — an issue body, a comment, a dependency's README —
launders into Canon and is then rendered as a *cited convention* in every subsequent Awareness Pack,
which would defeat the structural injection defence in FR-17.4.

**FR-6.4b (P0) — Trust class is carried, not inferred.** Every source, memory, spec unit, evidence
item, and work item carries a `trust` attribute: `verified` (a deterministic check exercised it),
`operator` (it came from the definition or an identified human), `internal` (produced by the factory
from `verified`/`operator` inputs only), or `untrusted` (any content originating outside the
definition, including repository content, issue text, comments, CI output, tool-server descriptions,
and model output derived from any of these). Trust is **monotone downward**: a derived object's trust
is the minimum of its inputs'. No memory whose trust is `untrusted` may enter Canon, and no
`untrusted` item may appear in the `conventions` section of a pack at all.

**FR-6.5 (P0) — Policing.** A continuously running policy pass must:
- detect **contradiction** between memories in the same scope and quarantine both pending resolution;
- detect **staleness** by re-resolving `anchor` memories against current code and demoting misses;
- detect **duplication** by similarity and merge, preserving the union of provenance;
- detect **poisoning**: a memory whose provenance chain traces to a single unverified source that has
  since been contradicted must demote everything derived from it (transitive invalidation).

**FR-6.6 (P0) — Provenance is mandatory and transitive.** Every memory records its sources; every
memory derived from another records the parent. A memory whose entire provenance chain is invalidated
is automatically demoted to Archive with the reason recorded.

**FR-6.7 (P0) — Filtering at retrieval.** Retrieval is scoped, budgeted, and ranked. It must apply, in
order: hard scope filter → lane filter (Canon by default) → contradiction filter (drop quarantined) →
freshness decay → relevance rank → **diversity cap** (no single source may dominate a pack) → budget
truncation. Every returned memory is cited in the Awareness Pack with its lane and confidence.

**FR-6.8 (P0) — Decay and consolidation.** Memories carry a decay function by kind. On decay expiry a
memory is re-validated (cheaply, deterministically where possible) or demoted. Consolidation merges
many specific memories into one general one, retaining links to the specifics.

**FR-6.9 (P0) — Scopes.** `run`, `work-item`, `repository`, `factory`, `team`, `personal`. A memory is
visible only within its scope and to agents explicitly granted it. Cross-scope promotion is an
explicit, audited operation.

**FR-6.10 (P0) — Asynchronous by default.** Memory extraction runs after a run completes; retrieval
runs during Awareness Pack assembly. Neither may block the agent's turn loop, and both must have
hard timeouts after which the run proceeds with what is available (PR-9).

**FR-6.11 (P0) — Auditability.** Every mutation (create, promote, demote, merge, split, expire,
invalidate) is ledger-recorded with actor, reason, and before/after. `sf memory why <id>` prints the
complete provenance tree and lane history.

**FR-6.12 (P0) — Bounded growth.** Each scope declares a budget (count and bytes). On breach, the
policy pass consolidates and archives by lowest value density until under budget, and records what it
dropped. Memory must be able to shrink (PR-8).

**FR-6.13 (P1) — Human-facing controls.** `sf memory review` presents Candidate memories for
confirmation or rejection; rejection records a negative signal that suppresses re-nomination of the
same claim.

**FR-6.14 (P0) — Local storage.** The default backend is embedded and file-backed under the factory's
state directory, with no external service. Vector or external stores are optional adapters behind the
same interface (PR-2).

**FR-6.15 (P0) — Export and portability.** The whole fabric must be exportable to and importable from
a documented, plain-text format. No lock-in.

### 7.7 Skills and the skill lifecycle (FR-7)

The answer to P6. Skills grow *and shrink* under policy.

**FR-7.1 (P0)** — A skill is a directory with a `SKILL.md`: frontmatter (`name`, `description`,
`version`, `status`, `appliesTo`, `owners`, `evals`) plus a Markdown body of instructions, optionally
alongside scripts and templates.

**FR-7.2 (P0)** — Scope by placement: `skills/<name>/` is factory-wide; `agents/<a>/skills/<name>/` is
scoped to that agent. Name collisions resolve most-specific-first, deterministically, and `sf lint`
reports every shadowed skill.

**FR-7.3 (P0) — Lifecycle states**: `draft → trial → active → deprecated → retired`. Every transition
is a definition change, reviewed like code.

| State | Loadable | Counted in selection budget | Notes |
| --- | --- | --- | --- |
| `draft` | Only when named explicitly | No | Under construction |
| `trial` | Yes, for a declared sample of runs | Yes, capped | Must have an attached eval |
| `active` | Yes | Yes | Steady state |
| `deprecated` | Yes, with a warning and a pointer to its replacement | Yes | Scheduled for retirement |
| `retired` | No | No | Preserved in history |

**FR-7.4 (P0) — Promotion.** `draft → trial` requires a description that passes the discoverability
check (FR-7.9) and at least one eval task. `trial → active` requires eval evidence: a measured
improvement on its declared metric over a baseline, at declared repetitions. Promotion without
evidence must be impossible through the normal path and must be loudly recorded when forced.

**FR-7.5 (P0) — Evolution.** A skill may be revised by a proposal carrying: the failing runs that
motivated it, the diff, and a re-run of its eval set before and after. Revisions bump `version`. A
revision that regresses its own eval set must be rejected by the gate.

**FR-7.6 (P0) — Merging.** When two skills' trigger conditions overlap beyond a threshold and their
bodies are substantially similar, the registry must propose a merge: one successor skill, both
predecessors deprecated with pointers, union of evals, and the merged skill must pass the union
before the predecessors retire.

**FR-7.7 (P0) — Splitting.** When a skill's eval results diverge by task class — passing one class and
failing another — the registry must propose a split into narrower skills with sharper descriptions,
each carrying the eval subset it serves. Rationale: oversized skills degrade selection.

**FR-7.8 (P0) — Sunsetting.** A skill must be proposed for retirement when any holds: it has not been
selected in N eligible runs; its eval set has been failing for M consecutive windows; every anchor it
references is orphaned; or it is fully covered by another active skill. Retirement is a reviewed
change, never automatic (PR-7).

**FR-7.9 (P0) — Discoverability quality.** Selection quality is a measured property. The registry must
compute, per skill: selection precision (selected and helped), recall (should have been selected),
and collision rate against sibling descriptions. Skills below threshold are flagged for description
revision before their instructions are blamed.

**FR-7.10 (P0) — Selection budget.** The number of skills offered to an agent per run is bounded and
ranked by expected value for the task, not by alphabetical or filesystem order. The pack records which
skills were offered, which were loaded, and which were used.

**FR-7.11 (P0) — Skills change knowledge, never access** (PR-4). A skill cannot grant a tool, secret,
or scope. `sf lint` must fail on a skill body that implies it can.

**FR-7.12 (P1) — Skill induction.** The factory may propose a new `draft` skill from repeated
successful behaviour observed in the ledger: N runs solving the same task class with a common
procedure. The proposal carries the source runs and a generated eval set.

**FR-7.13 (P0)** — Every non-draft skill declares an owner and a review date. Undated or unowned
skills fail lint.

### 7.8 Runners and execution (FR-8)

**FR-8.1 (P0)** — A runner declares: `description`, `platform` (os, arch, image), `instanceShape`
(cpu, memory, disk), `setupCommands`, `env`, `network` policy, and `timeouts`.

**FR-8.2 (P0)** — Executors: `local` (subprocess sandbox on the operator's machine), `container`
(OCI runtime), `ssh-worker` (owned remote host, outbound-connecting), `cloud` (managed pool).
Executor choice is orthogonal to every other definition file (FR-0.1).

**FR-8.3 (P0)** — Setup commands must be **idempotent and re-runnable**; the runner records their
output as part of the run's evidence, and a setup failure ends the run with `setup_failed` and the
failing command, never with a confusing downstream error.

**FR-8.4 (P0)** — Every run gets an **isolated workspace**: a dedicated worktree or checkout,
never a shared mutable directory. Concurrent runs on one repository must not interfere.

**FR-8.5 (P0)** — Network policy per runner: `none`, `allowlist`, or `open`, defaulting to
`allowlist`. The allowlist is explicit and auditable. `none` must still permit configured model
inference if that endpoint is local.

**FR-8.6 (P0)** — Filesystem policy per runner: the writable set is the workspace plus declared paths.
Writes outside are denied and recorded as violations (§7.12).

**FR-8.7 (P0)** — Local executor must work on Linux and macOS with no privileged daemon required, and
must degrade explicitly: if OS-level sandboxing is unavailable, it says so and requires
`--allow-unsandboxed` rather than silently running unconfined (PR-9).

**FR-8.8 (P1)** — Runners may be pooled and reused across runs with a documented reset guarantee.
Reuse must never leak workspace state or credentials between runs; the reset is tested.

**FR-8.9 (P0)** — Agents and automations select a runner by name; a factory may define many (a large
Linux runner for builds, a macOS runner for platform work, a minimal runner for triage).

**FR-8.10 (P0)** — Resource ceilings are enforced by the executor, not requested politely. Exceeding
cpu, memory, disk, or wall-clock terminates the run with a typed error and a partial evidence bundle.

### 7.9 Awareness Pack (FR-9)

> *The single largest lever on output quality is what the agent knows when it starts.* This section is
> the primary mechanism behind the central bet (§1.1): it is why a small model performs unusually
> well here, and it is the first thing to measure when quality drops.

**FR-9.1 (P0) — Determinism over a captured input snapshot.** Assembly first captures an
**input snapshot**: the repository commit, the definition revision, the memory-store revision, the
ledger sequence number, the skill-registry revision, the wall-clock instant used for every freshness
and decay computation, and the seed. The pack is then a pure function of that snapshot. Given the
same snapshot, assembly must produce a byte-identical pack; the snapshot digest and the pack digest
are both recorded on the run.

Determinism is a property of *replay*, not of two runs at different times: decay (FR-6.8), freshness
(FR-9.9), and retrieval timeouts (FR-6.10) all make a later run legitimately different. Timeouts are
handled by recording the *achieved* result set in the snapshot, so a replay reproduces the truncation
rather than racing it. The conformance assertion is therefore "same snapshot ⟹ same pack", and any
requirement stated as "same inputs at any time" is a defect in this document.

**FR-9.2 (P0)** — Standard sections, each individually budgeted, each omitted-with-reason rather than
silently dropped:

| # | Section | Content |
| --- | --- | --- |
| 1 | **Mission** | The work item, its source context, its acceptance criteria, and the stage's definition of done |
| 2 | **Spec slice** | Active spec units intersecting the change surface, with constraints and any contradicted units (FR-5.6) |
| 3 | **Terrain** | Repository map at the right altitude: the module graph around the change surface, entry points, ownership, build and test topology |
| 4 | **Precedent** | Prior work items touching the same surface: what was tried, what merged, what was reverted, and why |
| 5 | **Hazards** | Failure history for this surface: flaky tests, recent incidents, known-fragile paths, past reverts, past review findings |
| 6 | **Conventions** | The conventions that actually apply here, drawn from Canon memory and repository rules — with citations |
| 7 | **Toolbelt** | The tools available this run, with typed signatures, costs, and worked examples |
| 8 | **Skills** | The ranked, budgeted skill offer (FR-7.10) |
| 9 | **Contract** | The blast-radius contract (§7.12), budget, escalation ladder, and required outputs |
| 10 | **Open questions** | Unresolved questions from prior stages, with who can answer them |

**FR-9.3 (P0) — Deterministic-first (PR-6).** Every section must be built by deterministic tools where
possible: the module graph comes from static analysis, not from a model summarising files; hazards
come from version-control and CI history; precedent comes from the ledger. Model-generated content in
a pack must be labelled as such and must carry its source run.

**FR-9.4 (P0) — Citation.** Every claim in the pack carries a source the agent can follow: a file and
line, a run id, a memory id, a spec unit id, or a commit. Uncited assertions are forbidden.

**FR-9.5 (P0) — Budgeting.** The pack has a total budget and per-section budgets by agent role. When a
section exceeds its budget it is *summarised with a pointer to retrieval*, never truncated mid-item.
The agent must always be able to fetch the full version through a tool.

**FR-9.6 (P0) — Progressive disclosure.** The pack is the *opening* context, not the total. Every
section has a corresponding retrieval tool, so an agent that needs more can ask for exactly more,
mid-run, without a restart.

**FR-9.7 (P0) — Role shaping.** Composition varies by role: Scout weights Terrain, Hazards and
Precedent; Architect weights Spec slice and Conventions; Builder weights Terrain, Toolbelt and
Contract; Critic weights Spec slice, Hazards and the change diff — and the Critic's pack must **not**
include the Builder's reasoning transcript, only its outputs and evidence, so review stays
independent (FR-3.5).

**FR-9.8 (P0) — Pack telemetry.** For each run, record the pack digest, section sizes, what was
retrieved on demand, and what went unused. This is the input to pack tuning and to §11's metrics: an
unused section is waste and a repeatedly-retrieved item belongs in the pack.

**FR-9.9 (P0) — Freshness.** Assembly must reject stale inputs: an index older than the current head,
a spec anchor that no longer resolves, a memory past decay. Stale inputs are refreshed or excluded
with a recorded reason.

**FR-9.10 (P1) — Pack diffing.** `sf pack diff <run-a> <run-b>` shows what the second run knew that
the first did not — the primary debugging tool when two runs on the same task diverge.

**FR-9.11 (P0) — Offline assembly.** Pack assembly must complete with no network access beyond the
configured model endpoint. Every section has a documented offline degradation.

### 7.10 Tool Registry (FR-10)

**FR-10.1 (P0)** — Tools are declared with a typed schema (name, description, typed inputs and
outputs, side-effect class, cost class, idempotency, timeout). Untyped or undocumented tools may not
be exposed to an agent.

**FR-10.2 (P0) — Side-effect classes**: `read` (no mutation), `write` (mutates workspace), `exec`
(runs arbitrary code), `network`, `external` (mutates something outside the workspace — a comment, an
issue, a change). Each class carries distinct permission, audit, and blast-radius rules (§7.12).

**FR-10.3 (P0) — Baseline read tools**, present in every factory, all deterministic: repository
search, symbol lookup and cross-reference, file read with ranges, module and dependency graph, blame
and history, test discovery and topology, spec unit lookup, memory query, ledger query, run and diff
retrieval, and CI status.

**FR-10.4 (P0) — Baseline write and exec tools**: patch application with conflict detection, file
write, test execution with structured results, formatter and linter execution, build execution,
process execution under the runner's policy, and checkpoint/rollback (§7.12).

**FR-10.5 (P0) — Baseline external tools** (permission-gated per agent): comment on the source thread,
open or update a change, update a tracker item, request review, upload evidence.

**FR-10.6 (P0) — Structured results.** Tools return structured data, not prose. Test execution returns
per-test outcomes, durations, and failure detail; search returns ranked, located hits. Agents must
never be asked to parse human-oriented output when a structured form exists (PR-6).

**FR-10.7 (P0) — Grants are explicit.** An agent's tool set is declared in its configuration. There is
no ambient tool. Attempting an ungranted tool is a recorded violation, not an error message the agent
can route around (PR-4).

**FR-10.8 (P0) — External tool servers.** Standard tool-server integration is supported and is subject
to the same declaration, grant, audit, and blast-radius rules as built-in tools. A tool server's own
description text is untrusted input and must never be able to widen a grant.

**FR-10.9 (P0) — Failure semantics.** Tools return typed failures with remediation hints. Timeouts,
truncation, and partial results are explicit fields, never silently swallowed.

**FR-10.10 (P0) — Cost accounting.** Every tool call is ledger-recorded with duration and cost class,
feeding §11's per-stage efficiency metrics.

**FR-10.11 (P1) — Tool synthesis.** Recurring multi-call sequences observed in the ledger may be
proposed as new composite tools — the deterministic counterpart to skill induction (FR-7.12).

### 7.11 Model routing, calibration, and escalation (FR-11)

> This is where "lighter models do wonders" becomes a mechanism rather than a hope.

**FR-11.1 (P0) — Harness abstraction.** The harness interface (prompt assembly, turn loop, tool
dispatch, streaming, cancellation, usage accounting) is implementation-independent. `loom` is the
built-in harness; external harnesses are adapters implementing the same interface.

**FR-11.2 (P0) — Provider independence (PR-10).** Model access is via pluggable providers: hosted
APIs, self-hosted endpoints, and local runtimes. Provider credentials live only at the inference
boundary and are **never** injected into an execution workspace (§7.17).

**FR-11.3 (P0) — Tiers.** A factory declares an ordered ladder of model tiers (for example
`local-small → small → mid → large`), each with cost and capability metadata. Agents declare a
*starting tier*, not a model, unless they deliberately pin one.

**FR-11.4 (P0) — Escalation is evidence-gated.** A run may climb a tier only when a recorded trigger
fires: a gate failed twice with the same signature; the agent's calibrated confidence stayed below
threshold after retrieval; a required output failed schema validation repeatedly; or a
declared complexity signal exceeded threshold. Every escalation records the trigger, the tier before
and after, and the delta in outcome — so the factory learns where escalation actually pays.

**FR-11.5 (P0) — De-escalation.** Where a task class is shown by benchmark to be handled at a lower
tier with equal outcome, the ladder's starting tier must be *lowerable* by proposal, with evidence.
Cost reduction is a first-class improvement, not an afterthought.

**FR-11.6 (P0) — Calibration.** Every agent's final output must include a structured self-assessment:
confidence per acceptance criterion, the evidence supporting each, and explicitly enumerated unknowns.
Confidence without cited evidence must be treated as zero by downstream gates.

**FR-11.7 (P0) — Calibration is scored.** Stated confidence is compared against observed outcomes
(gate results, review findings, post-merge reverts). A miscalibrated agent — confident and wrong, or
unconfident and right — is a defect surfaced in the dashboard and a valid target for self-improvement.

**FR-11.8 (P0) — Structured output contracts.** Every stage declares a schema for its output. Output
is validated; on failure the agent is given the validation error and a bounded number of repair
attempts before escalation. Downstream stages consume validated structures, never free prose.

**FR-11.9a (P0) — Decomposition does not run on the weakest model.** Splitting a task into
individually-verifiable steps is the hardest reasoning in the run, and assigning it to the tier least
able to do it is self-defeating. Decomposition therefore runs, in preference order: from a **skill**
that already encodes the decomposition for this task class; from a **plan produced at Design** by a
higher tier and carried into Build; or, only if neither exists, from one bounded higher-tier call
whose cost is attributed to the run. A small-tier run that decomposes its own task must record that
it did so, because that is the configuration most likely to fail.

**FR-11.9b (P0) — Steps must have real verifiers.** "Verify each step with a deterministic tool" is
only meaningful where a verifier exists. Each step declares its verifier class: `deterministic` (a
test, a type check, a build, a resolved symbol), `heuristic` (a lint or a structural check), or
`none`. A step with `none` may not be treated as verified, and a decomposition consisting mostly of
`none` steps is reported as unscaffolded rather than presented as scaffolded.

**FR-11.9 (P0) — Decomposition for small models.** When the starting tier is below a declared
threshold, the harness must automatically apply small-model scaffolding: decompose the task into
individually-verifiable steps, checkpoint between steps, verify each with a deterministic tool before
proceeding, and keep the working context at or under the tier's effective window. This scaffolding
must be measurable in benchmarks and toggleable per agent.

**FR-11.10 (P0) — Graceful degradation (PR-9).** Provider unavailable, rate limited, or context
exceeded must produce a typed, recorded outcome and a defined fallback (next provider, next tier,
retry with reduced pack) — never a silent truncation or an invented result.

**FR-11.11 (P1) — Deterministic replay.** Given a recorded run, the harness must be able to replay the
tool sequence against recorded responses for debugging, with the model calls stubbed.

**FR-11.12 (P0) — Usage accounting.** Tokens, latency, retries, escalations, and cost per run per
model per stage are recorded, and roll up to the per-change cost metric in §11.

### 7.12 Blast radius, checkpoints, and courage (FR-12)

> Agents are timid because being wrong is expensive. Make being wrong cheap and *verifiably*
> reversible, then tell the agent so, in terms it can check.

**FR-12.1 (P0) — Every run declares a blast-radius contract** in its Awareness Pack: the writable
paths, the permitted side-effect classes, the external actions allowed, the resource ceiling, and the
undo mechanism. The contract is machine-checked by the executor, not merely stated.

**FR-12.2 (P0) — Checkpoints.** The workspace is checkpointed before the run and at every stage
boundary. `rollback(checkpoint)` is a first-class tool available to the agent itself.

**FR-12.3 (P0) — Speculative branches.** An agent may open a *speculative* line of work — try an
approach, evaluate it against gates, and discard or keep it — without leaving traces outside the
workspace. Speculation must be cheap, bounded by budget, and recorded so the discarded approaches
appear in the evidence bundle as considered-and-rejected alternatives.

**FR-12.4 (P0) — External actions are the hard boundary.** `external` side effects (comments, changes,
tracker updates) are irreversible in practice and therefore: never speculative, always
permission-gated, always ledger-recorded before execution, and always attributed.

**FR-12.5 (P0) — Violations are recorded, not just blocked.** An attempt to exceed the contract is
denied *and* recorded as a violation with full context. Repeated violations of the same kind are a
signal for the improvement loop, and a pattern of them is a security event (§7.17).

**FR-12.6 (P0) — The contract is stated to the agent affirmatively.** The pack tells the agent what it
*may* do and what undo costs, not only what is forbidden. The purpose is to license bold approaches
inside a safe envelope, which is the point of PR-5.

**FR-12.7 (P1) — Blast-radius widening requires human approval**, is scoped to a single run, expires,
and is ledger-recorded with the approver.

**FR-12.9 (P0) — The contract covers every output channel, not just the filesystem.** A rolled-back,
budget-killed, or contract-violating run must not leave residue anywhere: memory candidates, spec
deltas, skill proposals, benchmark task submissions, and ledger-visible claims are all **staged** and
are committed only when the run reaches a terminal state that the policy admits. A `contract_violation`
run commits nothing but its violation records. Undo that restores the filesystem while leaving a
memory candidate behind is not undo.

**FR-12.10 (P0) — Violation classes are distinguished.** Ordinary toolchains write outside a
workspace constantly (caches, temporary directories, package metadata), so a single zero-tolerance
counter is a gate that will be disabled within a week. Violations are classified: `benign`
(a declared-tolerable path such as a cache), `blocked` (denied and recorded, run continues), and
`escalating` (an attempt at a grant boundary). Only `escalating` blocks; `blocked` is reported; the
tolerable set is declared in the runner, reviewed like any other grant, and reported in `sf audit`.

**FR-12.8 (P0) — Secrets never enter the workspace unless declared.** A run receives exactly the
secrets its agent declares, mounted for its lifetime, redacted at every output boundary, and
destroyed at run end.

### 7.13 Evals, tests, and gates (FR-13)

**FR-13.1 (P0) — Three distinct assurance mechanisms**, deliberately not conflated:

| Mechanism | Question | Timing | Blocking |
| --- | --- | --- | --- |
| **Gate** | Is this specific work item ready to advance? | Per stage, every run | Yes |
| **Scorer** | Are our runs, in aggregate, meeting a standard? | Sampled, after runs | No |
| **Benchmark** | Is configuration A better than B on fixed tasks? | On demand | No, but gates adoption |

**FR-13.2 (P0) — Gates.** Declared per stage in `policy/`. A gate has an id, a trigger condition, a
check (deterministic command or structured evaluation), a severity (`block` or `warn`), and a
remediation hint. Baseline gates:

| Gate | Stage | Check |
| --- | --- | --- |
| `spec-agreement` | Design, Review | No `contradicted` unit on the change surface (FR-5.5) |
| `delta-present` | Review | Behavioural change on an active unit has an accompanying Spec Delta (FR-5.7) |
| `build-green` | Build | Project build succeeds |
| `tests-pass` | Build, Review | Repository validation passes; results structured |
| `regression-proven` | Build | For a defect fix: the new test fails at the parent commit and passes at the tip |
| `coverage-of-criteria` | Review | Every acceptance criterion maps to a test that exercises it |
| `no-unreviewed-external` | Review | No `external` side effect happened outside the permitted set |
| `evidence-complete` | Review, Verify | Every claim in the summary resolves to an artifact |
| `calibration-present` | all | Structured self-assessment present and schema-valid (FR-11.6) |
| `blast-radius-clean` | all | Zero contract violations |
| `secret-clean` | Build, Review | No secret material in diff, logs, or evidence |

**FR-13.3 (P0) — `regression-proven` is mandatory for defect work.** A fix without a test that
demonstrably fails before it and passes after it does not pass Build.

**FR-13.3a (P0) — The failure at the parent commit must be an assertion about behaviour.** A test
that fails at the parent commit because of an import error, a collection error, a missing fixture, a
syntax error, or any failure raised before the test body executed **does not satisfy the gate**. The
harness classifies the parent-commit failure and accepts only an assertion failure — otherwise
`from mymodule import the_new_function` satisfies the gate trivially, which is exactly the shape a
small model produces by default. The observed failure message must also be recorded, so a reviewer
can see *what* was proven.

**FR-13.3b (P0) — Author independence is measured, not assumed.** The test that proves the regression
is written by the same agent that wrote the fix, so it can encode the same misunderstanding. This
gate therefore bounds a *class* of error, not all of them: it proves the change alters behaviour in
the direction claimed, not that the claim was right. Establishing the claim is the Critic's and the
spec's job (FR-5.7), and this document must not present `regression-proven` as more than it is.

**FR-13.4 (P0) — Gate failures produce structured findings**: which gate, which criterion, the
observed evidence, and the remediation. Findings feed the repair loop and the evidence bundle.

**FR-13.5 (P0) — Bounded repair.** On gate failure the agent gets a bounded number of repair attempts.
Exhaustion returns the work item to the Conductor with findings — never a pass-by-timeout.

**FR-13.6 (P0) — Scorers.** A scorer declares: `name`, `description`, target `agents`, `labels` (each
with a value, score in [0,1], and description), `passingScore`, `samplingRate`, judge `model`, and an
optional `selfImprovement` flag; the body is the rubric. A scorer classifies; it does not grade
numerically, so failures point at one thing.

**FR-13.7 (P0)** — Scoring is sampled, asynchronous, and must never block or influence the run it
scores. Any run may additionally be scored on demand, which replaces that scorer's prior result for
that run. Changing `passingScore` re-renders history but never rewrites recorded classifications.

**FR-13.8 (P0) — Judge integrity.** A scorer's judge must not be the same model *and* harness as the
agent it scores, unless explicitly opted in. Scorers must be checkable against a human-labelled
sample, and their agreement rate reported; a scorer whose agreement with human labels falls below
threshold is flagged as untrustworthy before its verdicts are used to drive change.

**FR-13.9 (P0) — Benchmarks.** A benchmark suite declares the agent under test, a fixed task set with
success criteria, the configurations to compare, the scorers to apply, and repetitions. It reports
pass rate, cost, latency, and quality per configuration with per-task detail, plus a variance estimate.
It must **not** collapse results into one number or declare a winner (PR-7): the operator decides.

**FR-13.10 (P0)** — Benchmark tasks may be created from any completed run, copying its input; success
criteria must be added before the suite runs. Reported benchmark cost must state explicitly what it
does and does not include.

**FR-13.10a (P0) — "No winner" applies to reporting, not to policy.** FR-13.9 forbids the *report*
from collapsing results into one number, because the weighting between pass rate, cost, and latency
is the operator's to choose. FR-13.11, FR-7.4 and FR-11.5 then apply *declared, written-down*
thresholds to decide adoption — which is a policy the operator authored, not a judgement the system
made. Both are consistent only because the weighting lives in the definition; an implementation that
hard-codes a preference violates FR-13.9.

**FR-13.11 (P0) — Adoption policy.** A configuration change that a benchmark shows regressing a
declared metric beyond tolerance must be blocked from adoption by the definition gate, with an
explicit, recorded human override path.

**FR-13.12 (P0) — Evidence bundles.** Every stage completion produces a bundle: structured test
results, diffs, command transcripts, recordings, gate outcomes, scorer results, and the calibration
statement. Bundles are addressable, retained per policy, and attached to the work item and its change.

**FR-13.13 (P1) — Test-suite health.** The factory tracks flakiness, runtime, and *criteria never
observed failing* (FR-5.10), and may propose work items to fix a test suite that cannot catch
regressions. A test that has never failed is unproven, not proven.

**FR-13.14 (P0) — Everything offline.** Gates, scorers, and benchmarks must all run in local mode
against a local model endpoint (PR-2).

### 7.14 Self-improvement loop (FR-14)

**FR-14.1 (P0)** — Self-improvement is opt-in **per scorer**. Enabling it on a scorer authorises the
factory to investigate that scorer's failures and propose fixes.

**FR-14.2 (P0)** — The loop: *cluster* related failures by signature → *diagnose* root cause from the
underlying runs, packs, and ledger → *propose* a minimal change → *validate* the proposal against the
relevant evals or benchmark → *submit* as a reviewed change with its evidence.

**FR-14.3a (P0) — Definition changes are stricter than code changes, not equal to them.** A change
to `policy/`, `scorers/`, gates, grants, secrets, network policy, or the held-out set alters what
every future run is allowed to do, so it cannot carry the same review weight as an application
change. Required: a second human reviewer, a distinct approval group from ordinary code review, no
self-approval by the proposing agent's own identity, and a standing-benchmark run before adoption.
NFR-8.2's "identical review rules" is superseded here: it was wrong.

**FR-14.3b (P0) — Proposing is not writing.** FR-17.6 forbids a run from writing the loaded
definition; FR-14.3 permits a run to *propose* a definition change. These are consistent only because
a proposal is authored in a workspace, submitted through the change path, and applied by the
coordination plane after human approval — never by the run. Any implementation in which an
improvement run's process can cause a definition to take effect is a defect.

**FR-14.3 (P0)** — Proposals may target the factory's **own definition** — a prompt, a skill, an
Awareness Pack weight, a tier assignment, a gate threshold, a runner — as well as application code.
The definition is code and improves by the same route (PR-1).

**FR-14.4 (P0)** — Every proposal carries a *Regressions addressed* section linking the failing runs
and scorer results that motivated it, so a reviewer can trace the change to its evidence.

**FR-14.5 (P0) — Nothing is auto-adopted** (PR-7). Every proposal is a change awaiting human review.

**FR-14.6 (P0) — Anti-thrash.** The loop must not propose changes to the same target more often than a
declared cooling period, must not propose a change already rejected without new evidence, and must
cap open self-improvement proposals per factory.

**FR-14.7 (P0) — Guard against reward hacking.** A proposal that improves a scorer's score without
improving the underlying outcome must be detectable: proposals are validated against a **held-out**
task set the proposing loop cannot read or write, and any proposal that edits a scorer, gate, eval,
threshold, or the held-out set is flagged *self-referential*, requires a second human reviewer, and
may never be validated by the artefact it modifies.

**FR-14.7a (P0) — Grader capture does not require editing a grader.** A proposal that teaches an
agent to *write what the judge rewards* — a summary phrased to match the rubric, a claim shaped to
satisfy `evidence-complete` without adding evidence — captures the grader while touching no scorer
file, so the self-referential flag never fires. Defences, all required:

1. **Outcome anchoring.** Every scorer whose failures drive the loop must have a declared *outcome
   partner* from §11.1 (O-2 revert, O-3 defect escape, O-4 follow-up burden). A proposal that moves
   the scorer without moving, or at minimum not degrading, its outcome partner is refused.
2. **Counter-metric panel.** Every proposal reports cost per merged change, rework rate, human review
   cost, and standing-benchmark pass rate. Degrading any beyond tolerance refuses the proposal.
3. **Rubric-drift detection.** Scorer pass rates that rise while their outcome partners are flat are
   reported as *suspected capture* and suspend that scorer from driving the loop until a human
   re-validates it against a fresh labelled sample.
4. **Loop effectiveness.** If adopted proposals do not move outcome metrics over a window, the loop
   is disabled by default rather than left running.

**FR-14.8 (P0) — Improvement telemetry.** Track proposals opened, adopted, rejected, and reverted, and
the measured effect of adopted ones. A loop whose adopted proposals do not move outcomes is itself a
defect and must show as one.

**FR-14.9 (P1)** — Improvement targets extend to memory policy (admission thresholds, decay rates) and
skill lifecycle thresholds, closing the loop over §7.6 and §7.7.

### 7.15 Observability, ledger, and dashboard (FR-15)

**FR-15.1 (P0) — The Ledger** is the append-only, hash-chained record of every consequential event:
work item transitions, run starts and ends, tool calls, pack digests, gate outcomes, scorer results,
memory mutations, skill lifecycle changes, definition applications, budget events, violations, and
human decisions. Tamper-evident by chaining; verifiable by `sf ledger verify`.

**FR-15.2 (P0)** — All derived state (dashboards, indexes, caches) must be rebuildable from the ledger
alone (FR-4.10). Losing the database must cost time, not history.

**FR-15.3 (P0) — Metrics**, computed over a selectable window:

| Metric | Definition |
| --- | --- |
| Runs | Total runs, broken down by agent, stage, status, source, model, tier |
| Changes opened | Changes created from factory work, counted once, in the period first observed |
| Changes merged | Of changes opened, how many later merged (sourced separately; may lag) |
| Autonomy | Share of merged factory changes needing no human code push before merge |
| Cycle time | Median duration from run start → change → first review → merge, with per-stage medians |
| Cost per change | Median cost of changes opened in the window, decomposable by cost component and by change size |
| Rework rate | Share of work items returning to an earlier stage at least once |
| Gate pass rate | First-attempt pass rate per gate |
| Escalation rate | Share of runs climbing a tier, and the outcome delta when they do |
| Calibration error | Divergence between stated confidence and observed outcome (FR-11.7) |
| Pack efficiency | Share of pack content used, and on-demand retrieval rate (FR-9.8) |
| Memory health | Canon size, Candidate backlog, contradiction rate, invalidation rate |
| Skill health | Selection precision/recall, trial→active conversion, retirement rate |
| Spec health | Agreement-state distribution, drift rate, criteria without coverage |

**FR-15.4 (P0)** — Cost figures must be labelled **estimates** where they derive from recorded usage
rather than provider billing, and must state what they exclude.

**FR-15.5 (P0)** — Metrics that require an integration the factory does not have must be shown as
*unavailable with reason*, never as zero (PR-9). Aggregate run counts include evaluation, benchmark,
and improvement runs, and the dashboard must say so, since a rising run count with flat output can be
measurement activity rather than work.

**FR-15.6 (P0) — Views**: an overview (metrics and trend), an activity board (work items by stage,
filterable, with a *needs attention* flag), a run inspector (transcript, pack, tools, gates, evidence,
cost), a definition view, an evaluation view (scorers, benchmarks, improvement proposals), and a
memory/skill registry view.

**FR-15.7 (P0)** — Live runs must be observable *and steerable*: a human can watch, send a message,
adjust, pause, or stop a run in flight.

**FR-15.8 (P0)** — The dashboard is a **local-first, read-mostly** application served by `sf dash`
from the local ledger with no external dependency. Hosted deployment is the same application behind
authentication.

**FR-15.9 (P0)** — Everything visible in the dashboard is available from the CLI and API in structured
form. No metric is UI-only.

**FR-15.10a (P0) — Retention deletes bodies, never records.** Deleting an evidence body that a sealed
claim resolves to would break INV-6, and truncating the ledger would break INV-8. Retention therefore
removes *content* and leaves a **tombstone**: the id, class, digest, size, and the reason and time of
removal. A claim whose evidence body has expired remains resolvable to a tombstone and is rendered as
"evidence expired", never as unsupported and never as satisfied. The ledger is never truncated; it is
**segmented and sealed** (FR-27.2), with segment digests chained across the boundary so verification
still works over an archived prefix.

**FR-15.10b (P0) — Erasure is by reference.** Where content must be destroyed for legal or privacy
reasons, the referenced body is destroyed and the tombstone records that an erasure occurred, under
whose authority, and when. Erasure never rewrites a ledger entry; it makes what the entry points at
unavailable. This keeps deletion possible in an append-only design, which is otherwise architecturally
impossible (FR-27.3).

**FR-15.10 (P0) — Retention** is configurable per artifact class (transcripts, packs, evidence,
recordings) with a documented default, and enforced by a recorded, auditable pass.

### 7.16 Human checkpoints and policy (FR-16)

**FR-16.1 (P0) — Default checkpoints**, all overridable in `policy/`:

| Checkpoint | Default | Enforced by |
| --- | --- | --- |
| Spec approval | Work passing Design waits for a human to approve the Spec Delta | Workflow policy |
| Question answering | Ambiguity is asked about, not guessed at | Workflow policy |
| Merge | The factory opens changes and hands off; humans merge | Repository permissions |
| Blast-radius widening | Human approval, single run, expiring | Executor |
| Improvement adoption | Human review of every proposal | Definition gate |
| Self-referential change | Stricter review for changes to scorers, gates, or evals | Definition gate |

**FR-16.2 (P0) — Policy is not enforcement (PR-4).** Workflow checkpoints live in policy files and
prompts and are changeable by the team. Access and merge authority live in external permissions.
The distinction must be stated wherever both appear, and `sf lint` must fail on any policy file
claiming to enforce what only an external system can enforce.

**FR-16.3 (P0)** — A checkpoint must be resolvable from wherever the work arrived — the originating
thread, the change, the tracker item, the CLI, or the dashboard — and the resolution must be recorded
with the deciding human's identity.

**FR-16.4 (P0) — Checkpoints must be time-bounded.** An unanswered checkpoint escalates its
notification and eventually parks the work item as `BLOCKED: awaiting_human` rather than holding a
run open and burning budget.

**FR-16.5 (P0) — Attribution.** Every artifact the factory produces externally must be attributable to
the factory, the agent, the model tier, and the work item.

**FR-16.6 (P1) — Autonomy levels.** A factory declares a level per work class — `advisory` (proposes
only), `supervised` (acts with checkpoints, the default), `autonomous-to-change` (opens changes
without spec approval for a declared low-risk class). No level permits merging (NG-1).

**FR-16.7 (P0) — Emergency stop.** A single command halts every run in a factory, revokes in-flight
external actions where revocable, and records the reason.

### 7.17 Security, secrets, and trust boundaries (FR-17)

**FR-17.1 (P0) — Credential classes**, each with its own boundary:

| Class | Used for | Boundary |
| --- | --- | --- |
| Inference credentials | Model provider requests | Inference boundary only; never in a workspace |
| Execution secrets | APIs, registries, tools an agent uses | Explicit per-agent allowlist; default empty |
| Harness auth | External harness adapters | Separate from the agent's secret allowlist |
| Repository identity | Checkout and push | `EXECUTOR` (acting principal) or `CREATOR` (requesting user); declared, not inferred |

**FR-17.2 (P0)** — Default-deny for every grant: tools, secrets, network, filesystem, external actions.

**FR-17.3 (P0) — Redaction is a backstop, not a control.** Known secret values are redacted at every
output boundary (transcripts, logs, evidence, comments), and the design must not depend on it.

**FR-17.4 (P0) — The execution plane is untrusted.** Repository content, tool-server descriptions,
issue text, comments, CI output, and model output are all untrusted input. None may widen a grant,
alter policy, or change a gate. Prompt-injection resistance is a *structural* property here: grants
live in configuration the execution plane cannot write.

**FR-17.5 (P0) — Containment by authority, not by taint tracking.** Untrusted content is delivered
inside labelled, delimited regions, and delimiters are escaped in content so they cannot be forged.
But the harness must **not** claim to trace influence through a language model: paraphrase, encoding,
splitting, and influence-without-copying all defeat string-level taint tracking, and a control that
can be defeated by rewording is not a control.

What is enforced instead is *authority*, which is a property of configuration rather than of text:

| Boundary | Rule |
| --- | --- |
| Secrets | A run receives exactly the secrets its agent declares. No text in any region can add one. |
| Definition and policy files | Not writable from an execution workspace, under any circumstance (FR-17.6). |
| Grants (tools, scopes, network, external actions) | Resolved from configuration before the run starts and immutable for its duration. |
| Irreversible external actions | Require an authorisation that the run cannot mint: either a standing grant in the definition, or a human decision recorded against an identified principal (FR-26.2). |

**FR-17.5a (P0) — Untrusted-origin decisions are declared, not detected.** Where a run's *only*
justification for a boundary-crossing action traces to an untrusted region — the action was not asked
for by the work item, is not covered by the agent's standing grants, and is not derived from a
`verified` or `operator` source — the harness escalates to a human. This is a coarse, honest control:
it will over-escalate and it will miss laundering through paraphrase, and both properties are stated
here so no reader mistakes it for a solved problem. The strong guarantee is the table above; this is
the weaker layer on top of it.

**FR-17.6 (P0) — Definition files are protected.** A run may propose a change to the factory
definition only via the normal change path; direct writes to the loaded definition from inside an
execution workspace are denied and recorded as violations.

**FR-17.7 (P0) — Audit.** `sf audit` produces the complete reachability report: every agent, its
tools, secrets, network policy, filesystem policy, external actions, model providers, and data
egress paths — from the definition, without running anything.

**FR-17.8 (P0) — Data locality.** The operator chooses independently where execution runs, where
inference happens, and where run data persists. Local mode keeps all three on one machine. Every
non-local flow must be enumerated by `sf audit`.

**FR-17.9 (P0) — Supply chain.** Runner images are pinned by digest; skill and definition changes are
reviewed; tool-server endpoints are declared and pinned. Unpinned references fail lint.

**FR-17.10 (P1)** — Secret access is per-run, time-bounded, ledger-recorded, and rotation-aware, with
an alert on a run requesting a secret it has never used.

**FR-17.11 (P0)** — A violation-rate threshold triggers a security event: notification, and optionally
an automatic pause of the offending agent.

### 7.18 Intake and integrations (FR-18)

**FR-18.1 (P0) — Intake sources**: git host events, chat, issue trackers, generic signed webhooks,
schedules, direct CLI runs, the Factory MCP (§7.19), and monitoring signals.

**FR-18.2 (P0) — Adapter contract.** Every integration implements one interface: authenticate,
subscribe, normalise an event into a typed factory event, resolve identity, post a reply to the
originating context, and report health. Adding an integration must not touch orchestration code.

**FR-18.3 (P0) — Automations** bind triggers to an agent and a prompt. A trigger declares `provider`,
`event`, an optional `filter`, and for schedules a cron expression or descriptor, interpreted in UTC.

**FR-18.4 (P0) — Filter semantics**, specified once and applied identically everywhere: every declared
key must match (AND); within a key, any listed value matches (OR); an omitted key matches everything;
and keys support `in`/`not_in` forms. One event may match several automations and each match starts
its own run; overlapping automations must be reported by lint.

**FR-18.5 (P0) — Filters gate *starting work*, never *access*** (PR-4). Access comes from what was
authorised on the provider. Tightening a filter must never be presented as a security control, and the
documentation and lint must both say so.

**FR-18.6 (P0) — Author trust.** For sources where an event author need not be a factory member, the
automation must be able to filter by author, membership, branch, or label, and the default templates
must set a restrictive filter rather than an open one.

**FR-18.7 (P0) — Idempotency.** Every adapter supplies a stable event identity; redelivery must not
duplicate work (FR-4.5).

**FR-18.8 (P0) — Reply in place.** Results, questions, and status go back to the originating context.

**FR-18.9 (P0) — Health and degradation.** Adapter health is visible; an unhealthy adapter parks
affected work items as `BLOCKED` with the reason rather than dropping events (PR-9).

**FR-18.10 (P0) — Local intake parity.** Every capability reachable through an integration must also
be reachable through `sf`, so a fully local factory loses no functionality, only convenience (PR-2).

**FR-18.11 (P0) — Git host baseline.** The reference git-host adapter supports at minimum: issue
created/labelled/assigned/mentioned; change opened/closed/merged/labelled/assigned/mentioned/ready/
reopened/synchronised; review requested/submitted; push; and check-suite and workflow completion —
with filters on repository, branch, base branch, path, label, author, assignee, mentioned user or
team, reviewer, review state, workflow, and conclusion.

**FR-18.12 (P0) — Chat baseline.** Mentions, direct messages, channel messages, reaction-triggered
intake, and threaded replies, with filters on conversation, author or member, keyword, emoji, and
reacted-message author.

**FR-18.13 (P0) — Tracker baseline.** Issue created/labelled/assigned/state-changed, comment created,
and agent-session events, with filters on team, project, label, state, assignee, and mentioned user.
At most one tracker per factory, since two trackers means two sources of truth for one work item.

**FR-18.14 (P1) — Signal baseline.** Monitoring and error-tracking signals become work items with the
signal's fingerprint as deduplication key, so a recurring alert extends one work item.

### 7.19 Factory MCP and local/remote handoff (FR-19)

**FR-19.1 (P0)** — The factory exposes a tool-server interface so any tool-capable coding agent can
work with it in both directions: send work in, and take work out.

**FR-19.2 (P0) — Tool surface**: list factories; list, search, and get work items; get a work item with
local setup guidance; message the Conductor; read the conversation; send work in or hand work back;
list notification routes; complete a work item; fetch and validate definition files.

**FR-19.3 (P0) — One record.** Work continued locally lands on the *same* work item with the same
history. There is no second identity.

**FR-19.4 (P0) — The server never modifies the caller's files.** It returns setup guidance — an
isolated worktree, the branch, the context — and the caller's own agent executes it.

**FR-19.5a (P0) — Irreversible actions need a lease even when work items do not.** Not locking a
work item is a deliberate choice, but it must not extend to `external` effects: two actors finishing
concurrently must not both open a change, both comment, or both update a tracker. Every external
action on a work item takes a short, renewable lease keyed on (work item, action class). A second
actor is told who holds it and what they are doing, rather than racing them.

**FR-19.5 (P0) — Concurrency honesty.** Picking work up does not claim, lock, or pause it. The tool
surface must expose active runs and make announcing the pickup a one-call operation, and the docs must
warn plainly about duplicate work.

**FR-19.6 (P0) — Handing back** requires a pushed branch or change reference plus a note of what
changed, what was validated, and what remains. Unpushed work is invisible to the factory and the tool
must say so.

**FR-19.7 (P0) — Notifications** are best-effort by design and must be described as such; a route is
chosen when sending work in.

**FR-19.8 (P0) — Local mode.** In local mode the same interface is served over a local socket, so the
operator's own local agent gets the identical surface with no network (PR-2).

**FR-19.9 (P1)** — The server publishes its own usage guidance and schemas, so a calling agent picks up
the correct workflow without operator instruction.

### 7.20 Local-first runtime (FR-20)

> The modification that makes this project distinct: **the factory runs on one machine, offline,
> with no account.**

**FR-20.1 (P0)** — `sf init` creates a complete, valid factory definition in a repository, with
sensible defaults, in one command, requiring no external service.

**FR-20.2 (P0)** — `sf work "<request>"` runs a full work item locally end to end: intake → stages →
branch, evidence bundle, spec delta, and memory candidates — with no network beyond the configured
model endpoint.

**FR-20.3 (P0)** — Local mode supports a **local model endpoint** as a first-class provider, and the
default tier ladder must include a local-small tier so a laptop-only configuration is a supported
configuration, not a hack.

**FR-20.4 (P0) — Storage** is embedded and file-backed under `.factory/` (ledger, memory, indexes,
evidence, run state), documented, inspectable, and portable.

**FR-20.5 (P0) — Parity conformance suite.** A published suite asserts that a fixed set of work items
produces identical stage transitions, gate outcomes, and evidence structure across `local`,
`container`, and `ssh-worker` executors. Divergence is a release blocker (FR-0.2).

**FR-20.6 (P0) — No phone-home.** A local factory makes no network call the operator did not configure.
`sf audit --egress` enumerates every possible outbound destination from the definition.

**FR-20.7 (P0) — Resource courtesy.** Local mode respects declared ceilings on cpu, memory, disk, and
concurrency, defaulting to a share of the machine rather than all of it, and must be interruptible.

**FR-20.8 (P0) — Promotion path.** A local factory becomes a shared one by changing executor and
storage settings only. No agent, skill, spec, scorer, or automation file changes.

**FR-20.9 (P1) — Local queue.** A local factory may run a background worker draining a local queue, so
a solo operator gets fleet behaviour on one machine.

**FR-20.10 (P0) — Offline degradation is explicit.** Every capability requiring network states its
offline behaviour: unavailable-with-reason, or a documented local substitute.

### 7.21 API, SDK, and CLI (FR-21)

**FR-21.1 (P0)** — The CLI is the complete surface. Anything the dashboard or API can do, `sf` can do.

**FR-21.2 (P0) — Command families**: `init`, `validate`, `lint`, `plan`, `schema`; `work`, `run`,
`item`, `stage`, `cancel`; `agent`, `skill`, `spec`, `memory`, `scorer`, `bench`, `gate`; `ledger`,
`metrics`, `dash`, `audit`; `serve` (tool server), `worker`, `export`, `import`.

**FR-21.3 (P0)** — Every command supports `--json` for machine-readable output, non-zero exit codes on
failure, and `--dry-run` where it mutates.

**FR-21.4 (P0) — The API mirrors the CLI**, is versioned, documented by a generated OpenAPI document,
and returns typed errors with a stable code, a human message, and a remediation hint.

**FR-21.5 (P0) — Typed error catalogue** covering at least: authentication required, not authorised,
invalid request, not found, conflict, budget exceeded, insufficient resources, environment setup
failed, agent process failed, integration not configured, integration disabled, external
authentication required, feature not available, infrastructure timeout, operation not supported,
resource unavailable, content policy violation, and internal error. Each documents cause and
remediation.

**FR-21.6 (P1) — SDK** in at least one language, generated from the same schemas, with streaming run
observation.

**FR-21.7 (P0) — Local auth.** In local mode the API binds to loopback and uses a file-based token;
it must not be reachable off-host by default.

### 7.22 Evidence, recording, and verification (FR-22)

**FR-22.1 (P0)** — Verification is a **stage**, not a side effect. The Prover role establishes that a
change does what was claimed, independently of the Builder's assertions.

**FR-22.2 (P0) — Evidence classes**: structured test results; command transcripts; diffs; terminal
recordings; screen or browser recordings for user-facing changes; performance measurements; and gate
and scorer outcomes.

**FR-22.3 (P0) — Screen and browser verification is optional but first-class.** Where enabled, an agent
may drive a browser or desktop session to reproduce an issue or demonstrate a change, and the session
is recorded and attached. Where unavailable, the gate degrades to requiring an explicit statement that
visual evidence is absent (PR-9) — never to silence.

**FR-22.4 (P0) — Evidence must be reviewable in the tool where the human already is**: attached to the
change and linked from the originating thread, with an attachment mode the operator controls
(inline, linked, or off).

**FR-22.5 (P0) — Evidence is addressable and immutable** once sealed, retained per policy (FR-15.10),
and redacted at capture (FR-17.3).

**FR-22.6 (P0) — Claims resolve to evidence.** The `evidence-complete` gate fails any summary
containing a claim with no corresponding artifact. "I ran the tests" without structured results is a
gate failure.

**FR-22.7 (P1) — Recording robustness.** Truncated or failed recordings are reported as such with
retry guidance, never presented as successful evidence.

---

### 7.23 Repository onboarding (FR-23)

> Added in v2.0.0. Almost every gate in §7.13 depends on a repository having runnable validation,
> and §10 previously disposed of that with "or the factory helps create it". That conjunction hid
> the largest single scope item in the document.

**FR-23.1 (P0) — Readiness assessment.** `sf onboard` reports, for a repository: whether a build
command exists and succeeds, whether a test command exists and succeeds, test runtime, flake
estimate from repeated runs, coverage of the change surfaces the factory will touch, and which gates
are therefore enforceable. Its output is the honest answer to "can this factory work here yet?"

**FR-23.2 (P0) — Degraded mode is explicit and named.** A repository with no runnable tests may still
be worked, in `advisory` mode: `tests-pass` and `regression-proven` report `unenforceable` rather
than `pass`, every change carries that label, and the dashboard reports the share of work done under
degradation. A gate that cannot run is never silently satisfied.

**FR-23.3 (P0) — Validation bootstrapping is ordinary work.** Where validation is missing, the
factory proposes it as normal work items (add a test harness, add a smoke test for this surface),
subject to the same review as any change. It is not a hidden capability of the platform.

**FR-23.4 (P0) — Index lifecycle.** The repository index that the Awareness Pack, symbol tools, and
spec anchors all rest on has a specified lifecycle: cold build (with a progress and cost estimate),
incremental invalidation on change, a storage budget, an explicit staleness signal, and defined
behaviour when absent — degrade to on-demand analysis and mark packs `degraded`, never silently serve
stale results.

**FR-23.5 (P1) — Monorepo and multi-language.** Onboarding detects project boundaries, per-project
build and test commands, and the mapping from a change surface to the subset of validation that
covers it, so a change in one package does not run the whole repository's suite.

**FR-23.6 (P0) — Time to first useful run is measured**, not asserted, and reported per repository.

### 7.24 Versioning, state, and replay integrity (FR-24)

**FR-24.1 (P0) — Runs pin their definition.** Every run records the definition revision, the resolved
configuration digest, the harness version, the provider's resolved model version string, the sampling
parameters, and the pack snapshot digest. Without these, replay (FR-11.11), benchmark comparison
(FR-13.9), and improvement traceability (FR-14.4) are unsound the moment a prompt is edited — and the
data cannot be recovered retroactively.

**FR-24.2 (P0) — A work item pins one definition revision** for its duration by default, so a
mid-flight definition change does not make a work item's stages mutually incomparable. Re-pinning is
explicit and recorded.

**FR-24.3 (P0) — On-disk state migration.** Every persisted store (ledger, memory, index, evidence,
run state) declares a format version. Upgrades run forward migrations with a dry-run mode and a
mandatory pre-migration backup; a downgrade policy is stated for each store.

**FR-24.4 (P0) — Component version compatibility.** Coordinator, worker, dashboard, and CLI declare a
compatibility range. A worker outside the coordinator's range refuses work with a clear message
rather than producing subtly different results.

**FR-24.5 (P0) — Base drift and merge conflicts.** A change whose base has moved must be re-validated
against the new base before handoff; a conflicted change is either resolved by a run under the normal
gates or parked as `BLOCKED: conflict`, never handed off unresolved.

### 7.25 Identity, authorisation, and separation of duties (FR-25)

> Added in v2.0.0. The baseline said "a human" must approve, override, widen, force-promote, and
> emergency-stop, and never said which human.

**FR-25.1 (P0) — Principals.** Every actor is a principal with a stable id: a person, an agent, an
automation, or the coordination plane. Provider identities (git host, chat, tracker) map to principals
explicitly; an unmapped identity may trigger intake but may not make a decision.

**FR-25.2 (P0) — Capability model.** Authority is granted per capability, not per person-in-general:
`approve_spec`, `answer_question`, `widen_blast_radius`, `force_promote_skill`, `adopt_definition_change`,
`approve_self_referential_change`, `emergency_stop`, `erase_data`, `override_gate`. Each is granted to
named principals or groups in the definition.

**FR-25.3 (P0) — Separation of duties.** The principal who proposes a definition change may not be the
sole approver. A self-referential change (FR-14.7) requires two approvers from a distinct group.

**FR-25.4 (P0) — Every decision is attributed** to a principal, with the capability exercised, the
evidence shown to them at the time, and the time. A decision without attribution is not a decision.

**FR-25.5 (P0) — Live steering is a decision channel.** The ability to message or steer a run in
flight (FR-15.7) can supply the human decision that FR-17.5a defers to, so it is authenticated,
capability-checked, and ledger-recorded exactly like any other decision. An unauthenticated steering
channel is a privilege-escalation path.

**FR-25.6 (P0) — Repository identity is scoped.** Where runs act as a requesting user (`CREATOR`), the
credential is scoped to the minimum needed to check out and push — never to merge, never to
administer. NG-1 ("agents never merge") is otherwise a stated intention with nothing behind it.

### 7.26 Cost control, scheduling, and backpressure (FR-26)

**FR-26.1 (P0) — Aggregate spend caps** at factory and organisation level, per period, with a
declared behaviour on approach (warn), at (stop intake), and over (halt and notify).

**FR-26.2 (P0) — Admission control and scheduling.** A backlog needs a scheduling model: priority,
fairness across sources, starvation prevention, and a bound on concurrent work items. Absent this, one
noisy source consumes the factory.

**FR-26.3 (P0) — Intake backpressure and circuit breaking.** A signal storm — a failing deploy
emitting thousands of alerts — must not convert directly into unbounded spend. Per-source rate limits,
deduplication by fingerprint, and a circuit breaker that parks a source and notifies are required.

**FR-26.4 (P0) — Client-side provider control.** Concurrency limits, backoff, and fair queuing across
agents, rather than discovering provider limits by being rate-limited (FR-11.10 handles the reactive
case; this handles the proactive one).

**FR-26.5 (P0) — Cost attribution.** Every unit of spend is attributed to a work item, an agent, a
stage, and a cause (primary work, retry, scoring, benchmark, improvement), so O-9 and O-10 are
computable rather than estimated.

### 7.27 Data governance and lifecycle (FR-27)

**FR-27.1 (P0) — Data classification.** Every persisted class (transcripts, packs, evidence,
recordings, memory, ledger) declares whether it can contain repository content, personal data, or
credentials, and its retention default.

**FR-27.2 (P0) — Ledger segmentation.** The ledger is segmented, sealed, and archivable, with segment
digests chained across boundaries so verification works over an archived prefix. Bounded growth
(NFR-3.2) is otherwise a claim with no mechanism.

**FR-27.3 (P0) — Erasure and legal hold.** Erasure by reference (FR-15.10b), a legal-hold flag that
suspends retention for named subjects, and an erasure report. Append-only plus permanent archive
otherwise makes compliance architecturally impossible.

**FR-27.4 (P0) — Personal data in transcripts.** Transcripts may contain personal data from issues and
chat. Redaction at capture (FR-17.3), configurable retention, and export/erasure per subject.

**FR-27.5 (P0) — Provenance of generated code.** Changes record that they were machine-authored, which
model tier produced them, and under which definition revision — for attribution, licence review, and
post-hoc analysis.

**FR-27.6 (P1) — Dependency and licence checks** on changes that add dependencies, reported as
findings rather than silently merged.

### 7.28 Operations (FR-28)

**FR-28.1 (P0) — Installation.** Documented prerequisites, supported OS and runtime versions, an
offline install path, and a post-install verification command (`sf doctor`) that reports what works
and what does not.

**FR-28.2 (P0) — Upgrade.** A documented upgrade path per component, with a compatibility statement
and a rollback procedure.

**FR-28.3 (P0) — Backup.** A defined *complete, consistent backup*: ledger, memory, evidence, index,
run state, and definition revision, with a consistency guarantee across them and a documented
restore procedure.

**FR-28.4 (P0) — Disaster recovery.** Stated RPO and RTO, a rebuild-from-ledger procedure, and
defined behaviour on ledger loss and on partial loss.

**FR-28.5 (P0) — Run leases and orphan recovery.** Every run holds a heartbeat lease. A lease that
expires marks the run `infrastructure_timeout`, releases its workspace, and notifies — so an executor
that dies does not leave a permanently "running" work item.

**FR-28.6 (P0) — Workspace garbage collection.** Worktrees, checkouts, caches, and evidence staging
are reclaimed on a schedule and on disk pressure, with a floor that protects sealed evidence.

**FR-28.7 (P0) — Disk-pressure behaviour.** Defined and tested: the coordination plane refuses new
runs and continues serving reads before the disk fills, because a partial write during a chained
ledger append is this design's worst corruption mode.

**FR-28.8 (P0) — Health and alerting.** Component health, queue depth, lease expiry rate, provider
error rate, and budget consumption are exported and alertable.

**FR-28.9 (P0) — Log rotation and observability hygiene**, with secret redaction applied to logs.

### 7.29 Conversation and context lifecycle (FR-29)

**FR-29.1 (P0) — Bounded conversation state.** FR-3.7 requires continuing a specialist's existing
conversation across revisions; on a multi-pass work item that conversation exceeds any context
window. The harness maintains conversation state explicitly: a durable structured summary plus
retrievable full history, compacted deterministically (HARNESS.md §6.4), with the compaction recorded.

**FR-29.2 (P0) — Continuation is a resumption, not a replay.** Resuming a conversation restores the
structured state and the pack for the current stage, not the entire prior transcript.

**FR-29.3 (P0) — Cross-run state is auditable.** What one run carried into the next is inspectable, so
"context was lost" is a diagnosable claim rather than a guess.

### 7.30 Project deliverables (FR-30)

**FR-30.1 (P0)** — `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, a maintainers list, and a stated decision
process.

**FR-30.2 (P0)** — `SECURITY.md` with a vulnerability disclosure process and a supported-versions
statement. This project handles credentials and executes untrusted content; shipping without one is
not acceptable.

**FR-30.3 (P0)** — A quickstart, a tutorial, and a CI-tested reference factory definition that a
reader can copy and run.

**FR-30.4 (P0)** — Generated reference documentation for every schema, gate, tool, error code, and CLI
command, produced from the same definitions that validate (NFR-4.3).

**FR-30.5 (P1)** — A published threat model and a description of the trust boundaries in §6.2.

## 8. Non-functional requirements

### 8.1 Reliability

- **NFR-1.1 (P0)** — No lost work items. Every accepted intake event either produces a work item or a
  recorded rejection with a reason.
- **NFR-1.2 (P0)** — Crash safety: a coordinator restart resumes in-flight work items from the ledger
  without duplicating external side effects. External actions are recorded before execution and are
  idempotency-keyed.
- **NFR-1.3 (P0)** — Bounded everything: every loop (repair, escalation, retry, improvement) has a
  declared bound. Unbounded loops are defects.
- **NFR-1.4 (P0)** — Partial failure is a first-class outcome: a run that fails still produces a
  ledger record, a partial evidence bundle, and a typed reason.

### 8.2 Performance

- **NFR-2.1 (P0)** — Awareness Pack assembly completes within a declared budget (target: under 10s for
  a repository of 100k files with a warm index) and reports its own timing.
- **NFR-2.2 (P0)** — Deterministic tools return within their declared timeout or a typed timeout error.
- **NFR-2.3 (P1)** — Repository indexing is incremental; a change to one file does not force a full
  re-index.
- **NFR-2.4 (P0)** — The CLI starts in under 300ms for non-executing commands.

### 8.3 Scalability

- **NFR-3.1 (P0)** — A factory handles at least 10 concurrent runs on a workstation-class machine and
  scales horizontally by adding executors, not by changing the definition.
- **NFR-3.2 (P0)** — Ledger and memory growth are bounded by retention and consolidation policy
  (FR-15.10, FR-6.12), and both must be measured in the dashboard.

### 8.4 Usability

- **NFR-4.1 (P0)** — Time to first useful run on a fresh repository: under 10 minutes, including
  `sf init`.
- **NFR-4.2 (P0)** — Every error names what failed, why, and the next action.
- **NFR-4.3 (P0)** — Documentation is generated from the same schemas that validate, so it cannot drift.

### 8.5 Maintainability and testability

- **NFR-5.1 (P0)** — Subsystems are separable: spec, memory, skills, evals, harness, orchestration, and
  execution are independently testable with no hidden coupling.
- **NFR-5.2 (P0)** — Every model interaction is behind an interface that is stubbable, so the entire
  factory is testable without a model.
- **NFR-5.3 (P0)** — Deterministic core: identical inputs produce identical non-model outputs.
- **NFR-5.4 (P0)** — Test coverage gate on the core packages, and a conformance suite (FR-20.5) in CI.

### 8.6 Compatibility and portability

- **NFR-6.1 (P0)** — Linux and macOS support; Windows via container executor.
- **NFR-6.2 (P0)** — No dependency on a specific model, harness, git host, tracker, or chat tool for
  core operation (PR-10).
- **NFR-6.3 (P0)** — All persisted state is in documented, plain-text or open formats with export and
  import (FR-6.15, FR-21.2).

### 8.7 Accessibility and internationalisation

- **NFR-7.1 (P1)** — Dashboard meets WCAG 2.2 AA.
- **NFR-7.2 (P1)** — All user-facing strings are externalised; no locale assumptions in parsing.

### 8.8 Governance

- **NFR-8.1 (P0)** — Apache-2.0 licensed, with a NOTICE file and dependency licence reporting in CI.
- **NFR-8.2 (P0)** — Definition changes and application changes follow identical review rules.

---

### 8.9 Operability

- **NFR-9.1 (P0)** — A complete, consistent backup is definable, takeable while running, and
  restorable, with a documented consistency guarantee across ledger, memory, evidence, and state.
- **NFR-9.2 (P0)** — Stated RPO and RTO, with a tested rebuild-from-ledger procedure.
- **NFR-9.3 (P0)** — Defined and tested disk-pressure behaviour that protects the coordination plane
  before the disk fills.
- **NFR-9.4 (P0)** — Every long-running component exposes health, queue depth, and lease state.

### 8.10 Security posture

- **NFR-10.1 (P0)** — Default-deny for every grant class, verified by a test that a fresh factory
  reaches nothing it was not explicitly given.
- **NFR-10.2 (P0)** — Every dependency and runner image is pinned by digest; unpinned references fail
  lint.
- **NFR-10.3 (P0)** — A published threat model, kept current with the trust boundaries in §6.2.
- **NFR-10.4 (P0)** — Detective and preventive controls are labelled as such wherever a risk is
  mitigated, so a reader is never sold detection as prevention.

### 8.11 Fairness and access

- **NFR-11.1 (P1)** — The system must be usable by a team with one machine, no CI, and a local model.
  Every requirement that assumes otherwise declares its degradation.
- **NFR-11.2 (P1)** — Agent-facing and user-facing output must not assume English-language
  repositories, comments, or issue text.
- **NFR-11.3 (P1)** — Nothing in the design may require a paid hosted service to reach a correct,
  if slower, result.

### 8.12 Project governance

- **NFR-12.1 (P0)** — Contributing guide, code of conduct, maintainers, and decision process.
- **NFR-12.2 (P0)** — Security policy and vulnerability disclosure process.
- **NFR-12.3 (P0)** — A CI-tested reference example that the quickstart uses verbatim.
- **NFR-12.4 (P0)** — Dependency licence reporting in CI, compatible with Apache-2.0 distribution.

## 9. Data model

### 9.1 Core entities

| Entity | Key fields | Notes |
| --- | --- | --- |
| `Factory` | id, name, handle, schemaVersion, repositories, defaults | From `factory.yaml` |
| `Agent` | name, role, harness, tier/model, runner, tools, secrets, skills, memory scope, budget | From `agents/` |
| `Automation` | name, enabled, agent, triggers[], filters, prompt, overrides | From `automations/` |
| `Runner` | name, platform, shape, setup, network, filesystem, timeouts | From `runners/` |
| `Scorer` | name, agents[], labels[], passingScore, samplingRate, judge, selfImprovement, rubric | From `scorers/` |
| `Skill` | name, version, status, scope, description, owners, evals, body | From `skills/` |
| `SpecUnit` | id, title, status, intent, acceptance[], constraints[], implements[], verifies[], supersedes[], provenance, confidence | From `specs/` |
| `SpecDelta` | id, workItem, changes[], rationale, provenance, reviewState | Proposed change |
| `WorkItem` | id, factory, title, source context, stage, blocker, dependencies[], createdBy, timestamps | Runtime |
| `Run` | id, workItem, agent, stage, tier, runner, packDigest, status, usage, cost, timestamps | Runtime |
| `Memory` | id, lane, kind, scope, content, provenance[], confidence, evidence[], ttl, decay, links[] | Runtime |
| `Evidence` | id, run, class, digest, location, sealed, retention | Runtime |
| `GateResult` | id, run, gate, severity, outcome, findings[] | Runtime |
| `ScoreResult` | id, run, scorer, label, score, reasoning, judge | Runtime |
| `LedgerEntry` | seq, ts, actor, type, subject, payload, prevHash, hash | Append-only |

### 9.2 Invariants

- **INV-1** — Exactly one `CONDUCTOR` agent per factory.
- **INV-2** — Every `Run` belongs to exactly one `WorkItem`; every `WorkItem` to exactly one `Factory`.
- **INV-3** — Every `Memory` in `Canon` has at least one satisfied promotion criterion recorded.
- **INV-4** — Every `SpecUnit` id is globally unique and never reused, including after retirement.
- **INV-5** — Every ledger entry's `prevHash` matches its predecessor's `hash`.
- **INV-6** — Every claim in a stage summary resolves to an `Evidence` row (enforced by
  `evidence-complete`).
- **INV-7** — No `Run` holds a secret it did not declare.
- **INV-8** — Derived state is reconstructible from the ledger.

---

## 10. Constraints and assumptions

**Assumptions.** Operators have a git repository and a model endpoint (local or remote). Repositories
have some form of runnable validation, or the factory helps create it. Humans remain in the loop at
the checkpoints of §7.16.

**Constraints.** No dependence on any hosted service for core operation. No stored model weights. No
requirement for privileged daemons in local mode. All persisted state open-format.

---

## 11. Success metrics and acceptance criteria

> **Revised in v2.0.0.** The baseline version of this section was rejected in review on three
> counts: it stopped measuring at merge, its targets had denominators the system itself controls,
> and its acceptance test was built so that the criteria most likely to fail could not falsify the
> thesis. What follows is the replacement, not an amendment.

### 11.0 Measurement principles

- **MP-1 — Measure past the finish line.** Merge is not an outcome. Every quality metric here has a
  post-merge partner (revert, follow-up fix, defect escape, incident attribution), and the
  post-merge partner is the one that decides whether the factory is working.
- **MP-2 — No metric whose denominator the system chooses.** Where the factory controls what enters
  a ratio, the ratio is reported against an externally-fixed denominator as well.
- **MP-3 — Every headline metric ships with a named counter-metric** that would degrade if the
  headline were being gamed. Reporting the headline without the counter is a defect.
- **MP-4 — Name the gaming strategy.** Each metric below states how it could be moved without
  improving reality. If we cannot state that, we do not understand the metric.
- **MP-5 — Human cost is measured, never assumed.** The time humans spend reviewing, answering, and
  correcting factory output is a first-class measured quantity, not a hoped-for saving.

### 11.1 Outcome metrics

| # | Metric | Definition | Counter-metric | How it could be gamed |
| --- | --- | --- | --- | --- |
| O-1 | Delivered change rate | Factory-originated changes merged per week | Revert rate (O-2) | Ship trivia; split one change into many |
| O-2 | Revert rate | Share of merged factory changes reverted or hot-fixed within 30 days | Delivered change rate | Avoid touching anything consequential |
| O-3 | Defect escape rate | Defects attributable to a factory change, per merged change, at 30/90 days | Cycle time | Same as O-2 |
| O-4 | Follow-up burden | Human commits on a factory branch after handoff, per merged change | Autonomy (O-5) | Push the work into the next work item |
| O-5 | Autonomy | Share of merged factory changes with zero human code commits | Revert rate, follow-up burden | Restrict the factory to trivial work |
| O-6 | Human review cost | Median reviewer minutes per factory change, and the ratio to a matched human-authored change | Delivered change rate | Attach less evidence; reviewers rubber-stamp |
| O-7 | Rubber-stamp rate | Share of factory changes approved faster than a floor derived from diff size | Human review cost | — (this *is* the counter to O-6) |
| O-8 | Rework rate | Share of work items returning to an earlier stage more than once | Cycle time | Loosen gates |
| O-9 | Cost per merged change | Total attributable spend ÷ merged changes, **including** assurance, memory, scoring, benchmarking and retry overhead | Revert rate | Exclude overhead from the numerator |
| O-10 | Assurance overhead share | Share of total spend consumed by scoring, benchmarking, self-improvement and pack assembly | Delivered change rate | Turn assurance off |
| O-11 | Queue position of the constraint | Where work waits longest: intake, factory stages, human checkpoints, or review | — | — |

**FR-15.13 (P0)** — O-11 is mandatory and reported first. It is the metric that can tell the project
its own premise is wrong: if the constraint sits at human review or decision latency rather than at
change production, then improving O-1 makes delivery *worse*, and the roadmap must change rather
than the target.

**FR-15.14 (P0)** — O-2, O-3 and O-4 require observation of the repository *after* merge. Where that
observation is unavailable (an offline factory with no post-merge feedback), these metrics are
reported `unavailable`, and every quality claim in the dashboard is labelled *unvalidated* until they
exist. A factory that cannot see its own outcomes must say so rather than report autonomy as quality.

**FR-15.15 (P0) — Evidence audit.** The "no silent false pass" claim is made testable by a standing
procedure: each window, a fixed random sample (default 20) of gate-passing changes is independently
re-checked by a human against the same criteria. The disagreement rate is published. A target of zero
is not claimed; the *measured* rate is.

### 11.2 The central-bet experiment

The claim in §1.1 is a hypothesis. This is its protocol. It is written to be able to fail.

**Pre-registration (FR-13.17, P0).** Before any trial runs, a registration document is committed
containing: the task corpus and its selection rule, the conditions, the primary and secondary
outcomes, the analysis plan, the effect sizes considered meaningful, the multiplicity correction, the
stopping rule, and the results that would falsify the hypothesis. It is committed to the repository
and is not editable after the first trial. Post-hoc changes are recorded as amendments and reported
as such alongside the results.

**Schedule.** The experiment runs at **M3**, not M8. Running it after every design decision is sunk
converts it from a test into a ceremony. It re-runs at M6 and M8; the M3 result is allowed to change
the design.

**Corpus.** At least 120 tasks (not 40 — see power, below), drawn by a stated mechanical rule from
real work items across at least five repositories and at least four task classes (defect fix, small
feature, refactor, and test/infrastructure work), with difficulty stratified by an *a priori*
measure (diff size and file fan-out of the human-authored resolution) fixed before conditions are
assigned. A **held-out third** is sealed and never used for tuning.

**Snapshot isolation (FR-13.18, P0).** Each task runs against a repository snapshot at the parent
commit of its real resolution, with memory, ledger precedent, and spec state reconstructed *as of
that commit*. Without this, the Precedent and Hazards sections replay the answer, and the experiment
measures retrieval of a known resolution rather than capability. Any condition that cannot be
snapshot-isolated is excluded from the primary analysis.

**Conditions.** Every condition receives the **same attempt budget, the same repair budget, and the
same acceptance oracle**. Any difference in attempts is a confound, not a treatment.

| # | Condition | Harness | Tier | Purpose |
| --- | --- | --- | --- | --- |
| A | Competent baseline | A capable off-the-shelf single-agent setup with repository access and its own tooling | Large | The honest comparison, not a strawman |
| B | Competent baseline | Same | Small | Tier effect at constant harness |
| C | Full factory harness | This system | Small | The treatment |
| D | Full factory harness | This system | Large | Headroom |
| E | Ablations of C | C minus one of {Awareness Pack, gates, skills, memory, scaffolding} | Small | Which parts earn their place |

**Blinding.** Outcome adjudication for any non-deterministic criterion is done by raters blind to
condition. Deterministic outcomes (tests pass at the tip) need no blinding and are preferred for the
primary outcome precisely for that reason.

**Power.** With five primary comparisons, α = 0.05 Holm-corrected, 80% power, and a minimum
meaningful effect of 10 percentage points on pass rate, the corpus and repetition count must be
justified by an explicit calculation in the registration. Repetitions on the same task are **not**
independent samples and are modelled as such (task as a random effect).

**Outcomes.**

| ID | Outcome | Statement |
| --- | --- | --- |
| AC-1 | Primary | C's pass rate exceeds A's by at least the registered effect size |
| AC-2 | Primary | C's fully-loaded cost per *passing* task — including assurance, retries, scoring and pack assembly — is below A's |
| AC-3 | Primary | C's pass rate exceeds B's by at least the registered effect size (the harness, not the tier, is doing the work) |
| AC-4 | Primary | Each ablation in E reduces C's pass rate by at least the registered effect size |
| AC-5 | Primary | C's post-hoc calibration error, measured as the gap between stated per-criterion confidence and observed outcome, is no worse than A's |
| AC-6 | Secondary | C's advantage holds on the sealed held-out third |
| AC-7 | Secondary | C's human review cost per accepted change is no worse than A's |

**Falsification (explicit).** **Any** failed primary outcome falsifies the central bet as stated. No
criterion is exempt. In particular:

- AC-3 failing means the tier, not the harness, explains the result — the project's premise is wrong.
- AC-4 failing for a subsystem means **that subsystem must be removed**, not retained for
  plausibility. This applies to the Awareness Pack, memory, skills, gates, and scaffolding equally.
- AC-2 failing means the harness is a cost multiplier, and the local-first case weakens accordingly.
- AC-5 failing means the calibration machinery produces confident wrongness, which is worse than no
  calibration at all.

A falsified result is published in this repository alongside the design change it forces.

**Known threats to validity, recorded in advance.** Corpus contamination (tasks may appear in model
training data — mitigated by recency filtering and by reporting contamination-suspect tasks
separately); operator curation confound (condition C's advantage depends on the state of *this*
factory's memory, skills and spec — that state is snapshotted, published, and reported as part of the
result, because "the harness" is not separable from what has been put into it); and oracle
weakness (a repository's own tests may accept a wrong change — hence AC-6 and the O-2/O-3 partners).

### 11.3 Subsystem acceptance

| Subsystem | Acceptance |
| --- | --- |
| Living Spec | Injected drift is detected within one run; a behavioural change with no delta fails Review on a labelled corpus, with recall reported rather than asserted |
| Memory | A contradictory memory is quarantined before it reaches a pack; an invalidated source demotes its descendants; corroboration by two runs reading the *same source* is refused; a scope stays within budget under a 10k-write soak |
| Skills | A skill regressing its evals cannot be promoted; overlapping skills produce a merge proposal; the merge/split pair does not oscillate over 20 cycles on a fixed corpus |
| Evals | `regression-proven` is not satisfiable by an import or collection error at the parent commit; a scorer below human-agreement threshold cannot drive adoption |
| Harness | Identical inputs and an identical input snapshot produce an identical pack digest; recorded runs replay deterministically with stubbed models |
| Local-first | The conformance suite passes identically across executors; `sf audit --egress` enumerates definition-declared destinations and explicitly reports setup-command and allowlist egress as *unverified by static analysis* |
| Security | No definition file is writable from an execution workspace; a decision authored solely from untrusted input at a grant boundary requires a human decision |

## 12. Rollout plan

| Milestone | Contents | Exit criterion |
| --- | --- | --- |
| **M0 — Foundation** | Definition schema, loader, validator, ledger, CLI skeleton, CI | `sf validate` and `sf plan` on a real definition; ledger verifies |
| **M1 — Local run** | Loom harness, Tool Registry, local executor, Awareness Pack v1, one-agent factory | `sf work` produces a branch and evidence bundle offline |
| **M2 — The fleet** | Conductor, all specialist roles, stage machine, work items, checkpoints | AN-1 end to end, locally |
| **M3 — Assurance** | Gates, evidence bundles, scorers, benchmarks | `regression-proven` and `evidence-complete` enforced; benchmark reports |
| **M4 — Knowledge** | Living Spec + Delta, Memory Fabric, Skill lifecycle | Three-way agreement computed; memory promotion and policing live |
| **M5 — Connected** | Git host, chat, tracker adapters; automations and filters; Factory MCP | AN-1 driven from a real event; work handed to a local agent and back |
| **M6 — Improvement** | Self-improvement loop, dashboard, metrics | A proposal traced from scorer failure to adopted change |
| **M7 — Scale-out** | Container, ssh-worker, cloud executors; conformance suite | FR-20.5 parity green in CI |
| **M8 — Hardening** | Audit, redaction, injection containment, retention, error catalogue | §11.2 acceptance test run and published |

Each milestone ships with tests, documentation generated from schemas, and a CI gate.

---

## 13. Risks and open questions

> **Revised in v2.0.0.** The baseline register listed detective controls as mitigations for
> preventive risks, and its open questions were all tuning questions that threatened no decision
> already made. Both are corrected. Each mitigation below is labelled **[P]** preventive (it stops
> the thing happening) or **[D]** detective (it tells you afterwards). A CRITICAL risk whose only
> mitigation is **[D]** is not mitigated.

### 13.1 Risks

| ID | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| R-1 | Plausible-but-wrong changes pass review | Critical | **[P]** `regression-proven` with parent-failure classification (FR-13.3a); **[P]** independence ladder (FR-3.5a); **[D]** calibration scoring (FR-11.7); **[D]** post-merge revert and defect-escape metrics (O-2, O-3). Honest statement: the preventive controls bound a class of error, not all of it — O-2/O-3 are how we learn what got through |
| R-2 | Memory poisoning propagates | Critical | **[P]** trust classes with monotone downward propagation (FR-6.4b); **[P]** source-disjoint corroboration (FR-6.4a); **[P]** `untrusted` barred from Canon and from `conventions`; **[D]** transitive invalidation (FR-6.6) |
| R-3 | Reward hacking / grader capture | Critical | **[P]** held-out isolation and self-referential flagging (FR-14.7); **[P]** outcome anchoring — a scorer that cannot name an outcome partner cannot drive the loop (FR-14.7a); **[D]** rubric-drift detection; **[D]** loop-effectiveness metric |
| R-4 | Prompt injection via repository or issue content | Critical | **[P]** grants resolved from configuration the execution plane cannot write (FR-17.5 table); **[P]** definition files unwritable from a workspace (FR-17.6); **[P]** non-skippable stages (FR-3.3a); **[D]** untrusted-origin escalation (FR-17.5a), which is explicitly incomplete |
| R-5 | Cost blowout | High | **[P]** three-level budgets, run/work-item/factory (FR-3.11a); **[P]** assurance inside the budget (FR-3.11b); **[P]** intake backpressure (FR-26.3); **[D]** O-9, O-10 |
| R-6 | Spec becomes bureaucracy | Medium | **[P]** deltas only where behaviour changes; **[P]** induction on-ramp (FR-5.12); **[D]** time-in-Design metric |
| R-7 | Skill sprawl degrades selection | Medium | **[P]** bounded offer (FR-7.10); **[P]** merge/split/sunset with anti-oscillation; **[D]** selection precision |
| R-8 | Local mode becomes second-class | High | **[P]** parity conformance suite as a release blocker (FR-20.5); **[D]** topology-split metrics |
| R-9 | Scorer drift drives bad change | High | **[P]** agreement threshold before a scorer may drive the loop (FR-13.8); **[P]** outcome anchoring (FR-14.7a). Honest statement: FR-13.8's human labelling does not scale to many scorers, so the number of loop-driving scorers is deliberately capped |
| R-10 | Operator disables the safeguards within a week | High | **[P]** violation classes so ordinary toolchain noise does not trip a gate (FR-12.10); **[P]** time-bounded checkpoints (FR-16.4); **[D]** measured share of overridden gates — an override rate above threshold is treated as a design defect, not user error |
| R-11 | Vendor coupling creeps in | Medium | **[P]** adapter contract (FR-18.2); **[D]** `sf audit` |
| R-12 | Evidence theatre: bundles grow, trust does not | Medium | **[P]** claims must resolve to artifacts (FR-22.6); **[D]** O-6 human review cost and O-7 rubber-stamp rate. If O-6 rises while O-2 does not fall, the evidence is costing more than it is worth |
| R-13 | **The bottleneck is not change production** | Critical to the premise | **[D]** O-11 constraint position, reported first. If the constraint is review or decision latency, the roadmap changes. No preventive control exists, because this is a fact about the world, not a failure of the system |
| R-14 | **Reviewer overload**: the factory shifts work from writing to reviewing | High | **[P]** per-factory cap on concurrent open changes awaiting review; **[D]** O-6, O-7. A factory that outruns its reviewers has made things worse |
| R-15 | Junior engineers lose the learning path that writing first drafts provides | Medium | **[D]** none available in-product. Recorded here because it is a real cost of adoption that the product cannot measure and should not pretend to |
| R-16 | Ledger or state corruption under disk pressure | High | **[P]** disk-pressure behaviour (FR-28.7); **[P]** segmentation (FR-27.2); **[D]** `sf ledger verify` |
| R-17 | Orphaned runs and unbounded disk growth | High | **[P]** leases (FR-28.5); **[P]** workspace GC (FR-28.6) |
| R-18 | Deletion is architecturally impossible in an append-only design | High | **[P]** tombstones and erasure-by-reference designed in from the start (FR-15.10a, FR-27.3). Retrofitting this would require rewriting every store |

### 13.2 Open questions

Rewritten so that each threatens a decision already made, rather than tuning a decision already
taken. A question that cannot change anything is not an open question.

| ID | Question | What it could overturn | Needed by |
| --- | --- | --- | --- |
| OQ-1 | Is a **fixed stage machine** right at all, or should stages be a per-factory declared graph? | FR-4.2, the whole §7.4 model. The eight stages were adopted by analogy, not derived from §2 | M2 |
| OQ-2 | Does **role specialisation** beat one well-equipped agent with the same tools and pack? | §7.3 entirely. If not, five roles are five times the prompt maintenance for nothing | M3, via §11.2 condition E |
| OQ-3 | Is **one conductor** an architectural necessity or an interface convenience? | FR-3.1, INV-1 | M2 |
| OQ-4 | Is the bottleneck change production, or review and decision latency? | §11.1's entire orientation, and the roadmap | M3, via O-11 |
| OQ-5 | Does **memory** improve outcomes enough to justify its poisoning risk? | §7.6 entirely; AC-4's memory ablation is the test | M3 |
| OQ-6 | Can a scorer's **human-agreement** requirement scale past a handful of scorers, and if not, what is the cap? | FR-13.8, R-9 | M3 |
| OQ-7 | Does **evidence** reduce review time or increase it? | FR-22, R-12; O-6 against a matched baseline is the test | M4 |
| OQ-8 | Is **calibration** self-reportable at all, or is measured post-hoc accuracy the only usable signal? | FR-11.6, AC-5 | M3 |
| OQ-9 | What is the honest **corpus contamination** rate for §11.2, and does it invalidate the result? | §11.2's validity. Must be answered *before* the experiment, not at it | M3 |
| OQ-10 | Should third-party harness adapters exist, given they falsify pack determinism and replay? | FR-11.1, NFR-5.3 | M5 |

## 14. Traceability

| Problem | Addressed by |
| --- | --- |
| P1 laptop agents don't compound | §7.6 Memory, §7.7 Skills, §7.14 Self-improvement, §7.15 Ledger |
| P2 no ROI answer | §7.13 Evals, §7.15 Metrics, §11 |
| P3 accidental governance | §7.17 Security, FR-17.7 audit, §7.2 definitions as code |
| P4 spec rot | §7.5 Living Spec + Delta |
| P5 memory as liability | §7.6 Memory Fabric |
| P6 skill accretion | §7.7 Skill lifecycle |
| P7 frontier model as a substitute for engineering | §7.9 Awareness, §7.10 Tools, §7.11 Routing, §11.2 |
| P8 rented black box | §7.2 definitions as code, §7.20 local-first, Apache-2.0 |

---

## Appendix A — Requirement index

Functional families: FR-0 topology · FR-1 factory model · FR-2 definitions as code · FR-3 agents ·
FR-4 work items · FR-5 living spec · FR-6 memory · FR-7 skills · FR-8 runners · FR-9 awareness ·
FR-10 tools · FR-11 routing and calibration · FR-12 blast radius · FR-13 evals and gates ·
FR-14 self-improvement · FR-15 observability · FR-16 checkpoints · FR-17 security · FR-18 intake ·
FR-19 factory tool server · FR-20 local-first · FR-21 API/CLI · FR-22 evidence ·
**FR-23 repository onboarding · FR-24 versioning and replay integrity · FR-25 identity and
authorisation · FR-26 cost, scheduling and backpressure · FR-27 data governance ·
FR-28 operations · FR-29 conversation lifecycle · FR-30 project deliverables**.

Non-functional families: NFR-1 reliability · NFR-2 performance · NFR-3 scalability · NFR-4 usability ·
NFR-5 maintainability · NFR-6 portability · NFR-7 accessibility · NFR-8 governance ·
**NFR-9 operability · NFR-10 security posture · NFR-11 fairness and access ·
NFR-12 project governance**.

## Appendix B — Change log

| Version | Date | Change |
| --- | --- | --- |
| 1.0.0 | 2026-08-31 | Baseline. Written before adversarial, completeness, and bias review. |
| 2.0.0 | 2026-08-31 | Revised against 251 review findings. Section 11 replaced outright; twelve mechanism-level corrections; eight new requirement families; four new non-functional families; risk register relabelled preventive/detective; open questions rewritten to threaten decisions rather than tune them. See Appendix C. |
| 2.1.0 | 2026-08-31 | Revised against an adversarial review of the *implementation* (88 findings, 11 critical). FR-2.10a reverses factory-wide grants from floors to defaults, because always-apply defeated narrowing. See Appendix D. |

## Appendix C — Review dispositions

Three independent reviews were run against v1.0.0 and are kept in [`reviews/`](reviews/):
**62** adversarial findings (12 CRITICAL), **117** completeness gaps (31 BLOCKING), and **72** bias
findings. The reports are retained unedited, including the findings this revision *declined* to act
on, because a review record that only preserves the accepted findings is not a review record.

### C.1 Accepted and fixed in v2.0.0

| Finding | What was wrong | Resolution |
| --- | --- | --- |
| AR-01, AR-02, AR-13–AR-17, BR-22–BR-30 | The acceptance test could not fail: strawman controls, unequal attempt budgets, a corpus that leaked the answers, and exemptions for the criteria most likely to fail | §11.2 replaced: pre-registration, snapshot isolation, a competent baseline, equal budgets, power analysis, blinding, a held-out third, and explicit falsification with no exemptions. Moved from M8 to M3 |
| BR-41, BR-14, BR-30 | Measurement stopped at merge; the bottleneck assumption was never stated; denominators were system-controlled | §11.1 rebuilt around post-merge outcomes (O-2, O-3, O-4), constraint position (O-11), and counter-metrics on every headline (MP-1…MP-5) |
| AR-03 | Corroboration was defined over runs, so two runs reading one planted issue laundered untrusted text into Canon | FR-6.4a: corroboration intersects provenance sets. FR-6.4b: an explicit trust class on every object, monotone downward, with `untrusted` barred from Canon |
| AR-04 | FR-17.5 required taint-tracking through a language model | FR-17.5 rewritten around *authority* — grants resolved from configuration the execution plane cannot write — with FR-17.5a stated as the weaker, admittedly incomplete layer |
| AR-05 | Grader capture needs no edit to a grader, so the self-referential flag never fires | FR-14.7a: outcome anchoring, a counter-metric panel, rubric-drift detection, and loop-effectiveness gating |
| AR-06 | FR-17.6 and FR-14.3 contradicted each other; NFR-8.2 made definition changes *equal* to code changes | FR-14.3a (stricter review, second reviewer, no self-approval) and FR-14.3b (proposing is not writing). NFR-8.2 explicitly superseded |
| AR-07, BR-16 | `regression-proven` was satisfied by an import error at the parent commit | FR-13.3a: the parent failure must be an assertion failure; FR-13.3b states plainly what the gate does *not* prove |
| AR-08 | Rollback restored the filesystem and left memory candidates, spec deltas and skill proposals behind | FR-12.9: every output channel is staged and committed only at an admitted terminal state |
| AR-10 | Unbounded stage-skip authority in the one agent that reads attacker-controlled text | FR-3.3a non-skippable stages; FR-3.3b routing decisions cite their basis and its trust class |
| AR-11, AR-12 | `CREATOR` identity could carry merge scope; live steering had no identity model | FR-25.6 scoped repository identity; FR-25.5 makes steering an authenticated, capability-checked decision channel |
| AR-18 | Determinism was asserted while decay, timeouts and freshness made it impossible | FR-9.1 rewritten as determinism over a captured input snapshot, with achieved results recorded |
| AR-29, AR-31 | Budgets were per-run only; assurance was unbudgeted | FR-3.11a three-level budgets; FR-3.11b assurance inside the budget |
| AR-34, FR-15.11/12, FR-27.3 | Retention deleted what INV-6 and INV-8 promised was reconstructible; erasure was impossible | FR-15.10a tombstones; FR-15.10b erasure by reference; FR-27.2 ledger segmentation |
| AR-37 | FR-13.9 forbade declaring a winner while three other requirements declared one | FR-13.10a: "no winner" governs reporting; adoption applies operator-authored thresholds |
| AR-39 | Review independence was unsatisfiable in a single-provider factory | FR-3.5a: an independence ladder, with the achieved rung reported on every verdict |
| AR-41 | No lease meant duplicated irreversible external actions | FR-19.5a: leases on external actions, not on work items |
| AR-27 | Decomposition — the hardest step — was assigned to the weakest model | FR-11.9a preference order; FR-11.9b verifier classes, with `none` never counted as verified |
| AR-45 | A zero-tolerance violation gate over events ordinary toolchains generate constantly | FR-12.10 violation classes; only `escalating` blocks |
| BR-01, BR-02, BR-03 | The stage machine, the single conductor and the role set were adopted by analogy and then protected as invariants | FR-4.2a makes the stage graph configuration; FR-3.1 states the rationale *and its limits*; OQ-1, OQ-2 and OQ-3 put all three up for falsification |
| BR-11 | §2.3 asserted the thesis as its own premise | Rewritten to separate two observations from one hypothesis |
| BR-50, BR-63, BR-13 | The reviewer who inherits the output had no persona, no protection and no measured cost | Persona U6; metrics O-6 and O-7; risks R-14 and R-15 |
| Completeness FR-23…FR-30, NFR-9…NFR-12 | Onboarding, replay integrity, identity, cost control, data governance, operations, conversation lifecycle and project deliverables were entirely absent | Eight new functional families and four new non-functional families |

### C.2 Accepted, deferred with a reason

| Finding | Why not now |
| --- | --- |
| AR-19 (contradicted-state deadlock), AR-20 (reformat false drift), AR-22 (contradiction vs. merge), AR-23 (Canon collapse), AR-24 (low-frequency skill trap), AR-26 (merge/split oscillation) | All are algorithm-level corrections to §7.5–§7.7. They are specified in [`harness/living-spec.md`](harness/living-spec.md), [`harness/memory.md`](harness/memory.md) and [`harness/skills.md`](harness/skills.md) and will be reconciled into the PRD once the implementations settle at M4. Tracked, not dismissed |
| AR-25, BR-18 (metrics needing an unavailable oracle) | Selection recall, criteria-never-failing, and similar quantities have no ground truth. They are retained but must be reported as *estimates with a stated derivation*, never as measurements. Full resolution deferred to M6 |
| AR-30 (NFR-2.1/3.1/8.4 jointly infeasible) | Requires measurement on a real repository before the numbers can be set honestly. The current figures are marked as targets to be re-derived at M3 |
| AR-36, R-9 (human-agreement validation does not scale) | Resolved by capping the number of loop-driving scorers rather than by scaling labelling. The cap is OQ-6 |
| AR-51 (cross-repository migration has no home) | JTBD-4 is genuinely unsupported by the single-source-context work-item model. Recorded as unsupported in v2.0.0 rather than papered over; a multi-repository work item is a design change, not a parameter |
| BR-15 (verifiable ≠ correct) | Accepted as a permanent limitation and stated as one in FR-13.3b and R-1, rather than resolved. The honest position is that gates bound classes of error and O-2/O-3 measure what escapes |

### C.3 Declined

| Finding | Why |
| --- | --- |
| BR-04 (one policy per factory is a multi-tenant artifact) | Partly right — the *lint* was imported thinking — but a single policy boundary is what makes `sf audit` answerable. Retained, with the lint downgraded to advisory |
| AR-57 (UTC-only schedules) | Correct but minor; scheduled for M5 with timezone-aware cron. Not a v1 blocker |
| BR-09 / OQ-10 (third-party harness adapters falsify determinism) | The tension is real and is now recorded as OQ-10 rather than removed. Portability is a stated principle (PR-10) and paying for it in replay fidelity is a trade we are making knowingly |

### C.4 What the reviews did not cover

No review examined the harness specifications in [`harness/`](harness), which were written after the
PRD baseline and carry several of the corrections above. They are due for the same treatment. Nor did
any review examine the implementation. Both are gaps in this record, stated here rather than left to
be noticed.

## Appendix D — Implementation review dispositions

After the subsystems were built, an adversarial review ran against the *code* rather than
the document: **88 findings — 11 critical, 45 major, 32 minor** — each reproduced against
running code. The report is kept unedited at [`reviews/code-review.md`](reviews/code-review.md).

It found what design review structurally cannot: places where the implementation did not do
what the document says. Several defeated a control that reads as enforced elsewhere, which
is the worst category — a reader of `sf audit`, or of this PRD, would have believed a
guarantee that did not exist.

### D.1 Critical findings, all fixed

| Finding | The gap between document and code |
| --- | --- |
| C1 | FR-3.3a says review is non-skippable. The skip check measured from `BLOCKED`, which has no position in the stage order, so parking a work item and resuming it walked past review with an empty skip list — two ordinary calls, no human approval. |
| C2 | FR-6.4b says untrusted content may never enter Canon. Memory consolidation clustered across lanes and never read `trust`, so an untrusted candidate could archive a Canon memory and take its place. |
| C3 | FR-17.3 says secrets are redacted at every output boundary. The redaction function was called from nowhere. |
| C4 | FR-12.10's violation classes distinguish a real attempt from cache noise. The tolerated-path check ran on the unresolved path, so one `..` made a write outside the contract read as benign. |
| C5 | FR-5.5's agreement states depend on a unit's invariants. `apply_delta` used a copy that skips validation, so a re-anchor to nothing left an active unit anchorless — which then reported `agreed` and permanently satisfied the gate. |
| C6 | FR-13.2 says a gate that cannot run is an error. A stage with no declared gate set ran zero gates and reported clean, including `HANDOFF` — the last point anything could be caught. |
| C7 | Run isolation. Tool-registry violations were cumulative and unscoped, so one run's violation terminated every later run sharing the registry. |
| C8 | NFR-1.2 promises crash safety. A torn append made both append-only logs permanently unreadable, and therefore permanently unwritable. |
| C9 | FR-8.5's network policy. `allowlist` was the schema default, was reported by `sf audit` as a control, and was enforced nowhere. |
| C10 | FR-15.10b's erasure-by-reference. `erase()` appended a tombstone and left the content in place. |
| C11 | FR-6.5's contradiction detection. The tokenizer's length filter removed `"no"`, so contradictions phrased with it were invisible. |

### D.2 What this changed in the document

- **FR-2.10a** (this revision) reverses factory-wide grants from floors to defaults.
- **FR-13.3a** gains a companion: an assertion whose whole subject is a name's existence is
  not a behavioural failure, so `assert hasattr(module, "new_function")` does not satisfy
  `regression-proven` — the same bypass an import error gives, one keystroke further on.
- **FR-4.2a**'s stage order is now explicit data rather than derived from the transition
  table's key order. A security control must not change behaviour with a dict literal's
  ordering.

### D.3 Findings that were real with the wrong trigger

Two findings named a genuine hole and reproduced it with a case the code already handled.
Both are recorded here rather than quietly re-scoped, because a fix that answers a different
question from the one asked is how a review record stops meaning anything.

| Finding | What the report said | What was actually true |
| --- | --- | --- |
| M38 | A graph declaring `non_skippable={TRIAGE}` with a legal `INTAKE → BUILD → REVIEW → HANDOFF` path validates clean while enforcing nothing. | That path is refused at runtime: skipping is measured against the declared *order*, and `TRIAGE` lies between `INTAKE` and `BUILD` in it. But the check `validate_graph` performed really was weaker than the message it printed. The hole it does leave is the one C1 exposed — a declared order placing `HANDOFF` *before* the non-skippable stage leaves an edge that skips nothing, passes the skip rule, and reaches a human unverified. The fix checks reachability through edges the skip rule would permit, not through the transition table alone. |
| M39 | `load()` returns a partial tree, so cross-reference checks run against a tree missing the files that failed. | True, and the partiality is deliberate: `sf validate` exists to report every problem at once, and raising on the first unparseable file would report one. The defect was that nothing marked the tree as partial, so the checks invented phantoms. `Definition.unloaded` now names what failed and `validate()` drops the findings that merely follow from it. |

### D.4 Disposition

All 88 findings are fixed: 11 critical, 45 major, 26 minor, and the 12 test-quality items.
Each behavioural fix carries a regression test in
[`tests/test_review_regressions.py`](../tests/test_review_regressions.py), and each of those
was run against the unfixed code first and confirmed to fail on its own assertion — the
same `regression-proven` discipline FR-13.3a imposes on work the factory does.

Four findings changed a requirement rather than only the code:

| Finding | Requirement change |
| --- | --- |
| M18 | **FR-2.10a**: factory-wide secrets and MCP servers are defaults an agent may replace, not floors it inherits. Always-apply defeated the narrowing the field exists to express. |
| M35 | **FR-3.11a**: turns are the fifth budget dimension. Exhausting them is a budget outcome, not a verdict on the work, and must not feed the repair ladder. |
| N11 | **FR-11.3**: `scaffoldBelow` is `scaffoldAtOrBelow`. The threshold was always inclusive and the name read as exclusive, and the two readings disagree exactly where it matters — the lowest tier is the one that needs scaffolding, which is the whole premise in §2.3. |
| M37 | **FR-13.3a** gains a companion clause: an assertion whose subject is a name's existence is not a behavioural failure. |

### D.5 The general lesson

Nine of the eleven critical findings were **a control that existed and was not wired in**,
not a control nobody thought of. The document was right and the code did not implement it.
The pattern holds down the severity scale: `redact()` called from nowhere (C3), a network
policy reported by `sf audit` and enforced nowhere (C9), `unused_effects` returning its
input unchanged (M19), a proxy strip that could not strip anything (T4). A control that is
present, documented, and inert is worse than an absent one, because a reader of `sf audit`
or of this document believes a guarantee that does not exist.

Two consequences for how this project works:

1. The conformance suites in §11.3 are executable tests, not prose. A prose conformance
   statement is exactly the artifact that let these findings survive.
2. The review record keeps what it declined and what it got wrong. §D.3 records two
   findings whose stated trigger did not reproduce; the reports in
   [`reviews/`](reviews/) are unedited, including the findings not acted on. A record that
   preserves only the accepted findings is not a record.

The second lesson is narrower and sharper: **six of these bugs were held in place by a
test that asserted them** (T1, T2, T3, T4, T7, T9). A test can encode a defect as
confidently as code can, and a suite of 500 green tests is not evidence that 500 properties
hold — it is evidence that 500 assertions match the code, which is a different claim.
