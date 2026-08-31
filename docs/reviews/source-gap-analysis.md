# Source Gap Analysis — Announcement Video vs. PRD v2.0.0 and Harness Specs

| Field | Value |
| --- | --- |
| Document | Gap analysis |
| Source | `.research/video/0WBk4ai8y1A.transcript.txt` — an ~8:56 product announcement for a commercial software-factory product ("the reference product") |
| Compared against | [`docs/PRD.md`](../PRD.md) v2.0.0 and [`docs/harness/`](../harness/) (HARNESS.md, awareness.md, memory.md, living-spec.md, skills.md, evals.md) |
| Method | Three passes: extract every claim, classify against our requirements, recommend paste-ready requirements |
| Status | Analysis only — no requirement in this document is adopted until it lands in the PRD through the ordinary change path |

> **Naming.** The source names a vendor, its products, and three third-party services. None of those
> names appear here; they are referred to as *the reference product*, *the git host*, *the tracker*,
> *chat*, *the screen-recording product*, and *third-party harnesses*. Quotes are shortened and
> bracketed where a brand fell inside them. This is deliberate: the repository must contain no brand
> references.

> **Provenance caveat.** `.research/video/meta.txt` describes a different recording (a different video
> id, a 47:10 duration, and a different title) from the 8:56 transcript this analysis was asked to
> read. The transcript file is treated as authoritative throughout, per the task's rules; the
> metadata was used only to confirm that the material is a product announcement. If the longer
> recording exists, it is a second source and this analysis does not cover it.

---

## Summary

**80 distinct items extracted.**

| Classification | Count | Meaning |
| --- | --- | --- |
| COVERED | 44 | An existing requirement already demands it |
| PARTIAL | 22 | We cover part of it; the missing part is named per item |
| ABSENT | 8 | Nothing in our documents corresponds |
| DIVERGENT | 5 | We do something deliberately different |
| Out of scope | 1 | Commercial-offer claim, not a capability |

**Rank distribution over the 30 actionable items (ABSENT + PARTIAL):** HIGH 11 · MEDIUM 16 · LOW 3.

The five DIVERGENT items are not ranked; each is discussed in §4 with a statement of whether our
rejection stands.

### The eight ABSENT items, in importance order

1. **Computer use as a declared tool class** (V25) — the capability is promised in FR-22.3 and has no tool, grant, effect class, or contract anywhere in the Tool Registry.
2. **Sub-agent delegation and the delegation tree** (V55) — our `Run` has no parent, and no view answers "which agents served this request, and what did each cost".
3. **Recording post-production for reviewer legibility** (V48) — click emphasis, keyboard callouts, dead-spot removal. We require recordings; we say nothing about making one cheap to watch.
4. **Human review comments as a self-improvement input** (V68) — FR-14.2 clusters scorer failures only. The reviewer's complaint is a failure mode no rubric encoded yet.
5. **Incidental discoveries become their own work items** (V30) — a run that finds an unrelated defect can neither file it nor spawn a sibling item.
6. **Repository-tailored spec templates** (V37) — spec units have a schema, but nothing declares the *shape* of a design document per work class or repository.
7. **Conversation mining for improvement signal** (V69) — "hidden gems in all the conversations", which our trust model constrains but does not forbid.
8. **Media as a research input** (V26) — an agent consulting a recorded video or an API-less UI to establish a fact.

### Full item table

| # | Item | Class | Rank | One-line gap |
| --- | --- | --- | --- | --- |
| V1 | Cloud-hosted factories teams build for themselves | DIVERGENT | note | We are local-first; cloud is a topology (§6.3), not the product |
| V2 | Mixed models/harnesses defeat monitoring of access, cost, ROI | COVERED | — | §2.1 P2, P3; personas U3, U4 |
| V3 | Per-lifecycle-step model and cost tuning | COVERED | — | FR-3.4, FR-11.3, FR-2.10 |
| V4 | Central management of tool servers, secrets, skills | COVERED | — | FR-2.1, FR-10.8, FR-17.1, FR-17.2 |
| V5 | Dashboard: team velocity and cost over time | COVERED | — | FR-15.3, FR-15.6 |
| V6 | Automations as the vehicle for per-step configuration | COVERED | — | FR-18.3, FR-2.10 |
| V7 | Connect a repository, factory running in minutes | PARTIAL | MEDIUM | NFR-4.1 sets 10 minutes; nothing generates a repository-aware fleet |
| V8 | A provisioned suite of agents, one per lifecycle step | PARTIAL | HIGH | FR-2.1 requires only one agent; `sf init` may produce an empty factory |
| V9 | Triage agent researching incoming issues | COVERED | — | FR-3.2 (Scout) |
| V10 | Spec-writing agent for larger features | COVERED | — | FR-3.2 (Architect), FR-5.4 |
| V11 | Implementation agent that verifies with computer use | PARTIAL | HIGH | FR-22.3 permits it; no tool exists and verification is a separate stage |
| V12 | Code review agent | COVERED | — | FR-3.2 (Critic), FR-3.5, FR-3.5a |
| V13 | A foreman orchestrating the chain | COVERED | — | FR-3.1, FR-3.3 |
| V14 | All infrastructure written as code | COVERED | — | PR-1, FR-2.1, FR-2.6 |
| V15 | One central file: repositories, secrets, tool servers | COVERED | — | FR-1.2, FR-2.1, FR-2.8 |
| V16 | Per-agent config: behaviour, model, skills | COVERED | — | FR-3.4, FR-3.9 |
| V17 | Skills are how agents reach third-party integrations | DIVERGENT | note | FR-7.11: skills change knowledge, never access |
| V18 | Tracker mention starts spec writing | COVERED | — | FR-18.13, FR-18.3 |
| V19 | Git-host mention drives PR review and comments | COVERED | — | FR-18.11 |
| V20 | Chat mention starts agents | COVERED | — | FR-18.12 |
| V21 | Multiple factories, one per team | PARTIAL | MEDIUM | FR-1.5 is a workspace file only; no cross-factory reporting or audit |
| V22 | Per-factory scoping of permissions, skills, tool servers | COVERED | — | FR-1.3, FR-17.2, FR-17.7 |
| V23 | A search-optimisation factory over blog content | DIVERGENT | note | NG-4; FR-23.2 advisory mode is the nearest thing we have |
| V24 | A marketing factory over landing page and brand assets | DIVERGENT | note | Same as V23 |
| V25 | Computer use as a general agent capability | **ABSENT** | HIGH | No `ui` tool class, effect class, grant, or session contract |
| V26 | Agent watches recorded video as research | **ABSENT** | MEDIUM | No media ingestion path of any kind |
| V27 | One factory spanning several core repositories | COVERED | — | FR-1.2 |
| V28 | Third-party coding harnesses supported | COVERED | — | FR-11.1, OQ-10 |
| V29 | Foreman routes a request to triage for investigation | COVERED | — | FR-3.3, FR-3.3a |
| V30 | Triage files a tracker issue for a discovered defect | **ABSENT** | HIGH | No discovery type, no sibling work item, no agent-authored tracker item |
| V31 | Connect your own task boards for team alignment | PARTIAL | MEDIUM | FR-18.8 posts results; state between results is not mirrored |
| V32 | Back-and-forth with the triage agent directly | DIVERGENT | note | FR-3.6 routes everything through the Conductor |
| V33 | Triage hands off to spec writing for larger features | COVERED | — | FR-3.3, FR-4.2 |
| V34 | Spec tailored to the repositories worked in | PARTIAL | MEDIUM | FR-9.2 gives the Architect terrain; nothing shapes the output document |
| V35 | Specs written as code you can review | COVERED | — | FR-5.1, FR-5.3, PR-1 |
| V36 | Tech spec generated as a pull request | PARTIAL | MEDIUM | FR-5.4 requires reviewable, never says where |
| V37 | Spec from a detailed repository-tailored template | **ABSENT** | MEDIUM | No template concept for design output |
| V38 | Spec details changes plus testing and validation strategy | PARTIAL | MEDIUM | Spec units carry `acceptance`/`verifies`; no validation-strategy section |
| V39 | Spec approval gates implementation | COVERED | — | FR-16.1 |
| V40 | Runs backed by sessions joinable from any tooling | PARTIAL | MEDIUM | Steering is dashboard-first; no session attach contract |
| V41 | Live session view with follow-up from web UI or terminal | PARTIAL | MEDIUM | FR-15.7 is dashboard; FR-21.1's "CLI is complete" is not met |
| V42 | Factory tool server for sessions, config, anything else | COVERED | — | FR-19.1, FR-19.2 |
| V43 | Tool server resumes sessions, talks to the foreman | COVERED | — | FR-19.2, FR-3.7, FR-29.2 |
| V44 | Tool server updates factory configuration | PARTIAL | MEDIUM | FR-19.2 fetches and validates; no propose-change tool |
| V45 | Tool server explains the work that needs doing | COVERED | — | FR-19.2 |
| V46 | PR with changes detailed and validation steps | COVERED | — | FR-13.12, FR-22.4 |
| V47 | PR carries a computer-use recording of the change | PARTIAL | HIGH | FR-22.3 optional-but-first-class; the producing capability is absent (V25) |
| V48 | Recording post-production: clicks, callouts, dead spots | **ABSENT** | HIGH | Nothing addresses making evidence cheap to watch |
| V49 | Ask the agent about its decisions while reviewing | PARTIAL | HIGH | Plumbing exists (FR-4.5, FR-18.8); post-handoff explanation does not |
| V50 | The agent side by side during PR review | PARTIAL | MEDIUM | Same gap as V49, stated as a workflow claim |
| V51 | Bird's-eye view of conversations across all channels | PARTIAL | MEDIUM | FR-15.6's board is by stage, not by thread |
| V52 | Activity feed showing where agents are in each step | COVERED | — | FR-15.6 |
| V53 | All runs across the team, with request-origin callouts | COVERED | — | FR-15.3, FR-4.6 |
| V54 | Artifacts (images, videos) reviewable from the run list | PARTIAL | LOW | Evidence is addressable; no cross-run visual browse |
| V55 | Breakdown of sub-agents used, showing delegation strategy | **ABSENT** | HIGH | No parent/child run relation in the data model or any view |
| V56 | The harness does automatic orchestration | COVERED | — | FR-3.3, FR-4.2a |
| V57 | Per-conversation agent trace | PARTIAL | LOW | Ledger holds it; no view assembles it per conversation |
| V58 | Cost of every agent run | COVERED | — | FR-11.12, FR-15.3, FR-26.5 |
| V59 | Web UI view of models, secrets, tool servers, instructions | COVERED | — | FR-15.6, FR-2.7 |
| V60 | Automations view: tracker mentions, DMs vs channels | COVERED | — | FR-15.6, FR-18.12 |
| V61 | Vendor helps monitor and improve the factory over time | COVERED | — | FR-14.1, FR-14.8 |
| V62 | Scores: an agent that judges other agents | COVERED | — | FR-13.1, FR-13.6 |
| V63 | A code-quality score over all implementation runs | COVERED | — | FR-13.6 |
| V64 | Operator-defined scoring criteria and pass threshold | COVERED | — | FR-13.6 |
| V65 | Score history with the cost of scoring | PARTIAL | MEDIUM | O-10 is aggregate; no per-scorer attribution surfaced |
| V66 | Pass/fail plus the scorer's comments | COVERED | — | FR-13.6, `ScoreResult` (§9.1) |
| V67 | Self-improvement over scoring output and all runs | COVERED | — | FR-14.2 |
| V68 | Improvement from complaints left on PR reviews | **ABSENT** | HIGH | Human review findings are not a declared loop input |
| V69 | "Hidden gems" mined from all the team's conversations | **ABSENT** | MEDIUM | No conversation-mining path; trust model constrains how |
| V70 | Cost per PR over time, to check optimisations worked | PARTIAL | MEDIUM | O-9 exists; the series is not annotated with definition revisions |
| V71 | Velocity: PRs opened, total runs | COVERED | — | FR-15.3 |
| V72 | "Any other analytics you may need" | PARTIAL | LOW | `ledger query` exists; no saved/custom metric surface |
| V73 | Benchmarks over tasks representing the team's real work | COVERED | — | FR-13.9, FR-13.10 |
| V74 | Scores over benchmark tasks with different models *and context* | PARTIAL | HIGH | Configurations vary harness/tier/runner/scaffolds — not the pack |
| V75 | Benchmarks drive per-step model routing | PARTIAL | HIGH | FR-11.5 accepts a proposal; nothing produces one |
| V76 | Open and flexible: any agents, any models | COVERED | — | PR-10, NFR-6.2, FR-11.2 |
| V77 | Hosted or self-hosted, full data sovereignty | COVERED | — | §6.3, FR-17.8, FR-20.6 |
| V78 | Infrastructure owned as code, easy to tweak | COVERED | — | PR-1, FR-2.6, FR-14.3 |
| V79 | Connectors to any trigger, plus REST API and SDK | COVERED | — | FR-18.1, FR-18.2, FR-21.4, FR-21.6 |
| V80 | Early access, waitlist, usage credits | out of scope | — | Commercial offer, not a capability |

---

## Pass 1 — Extract

Every distinct capability, workflow step, component, integration, metric, UI surface, configuration
concept and claim the transcript mentions, with the supporting phrase. Ordered as the video presents
them.

### Framing and the stated problem

**V1 — Cloud-based factories that teams build for themselves.**
> "flexible infrastructure to help teams build cloud-based software factories of their own"

**V2 — Heterogeneous models and harnesses across a team defeat monitoring of security access, cost, and return on investment.**
> "letting team members use different models and harnesses across the tool chain makes it hard to monitor security access, cost, and return on investment"

**V3 — Per-lifecycle-step tuning of models and costs.**
> "fine-tune the models and costs that are used for each step in the development life cycle"

**V4 — A central place to manage tool servers, secret access, skills, and the rest of the environment.**
> "adding a central place to manage [tool servers], secret access, skills, and everything else in the environment"

**V5 — A dashboard giving a bird's-eye view of team velocity and cost, to track factory efficiency over time.**
> "a dashboard to give a bird's-eye view on team velocity and cost to track the efficiency of a factory over time"

**V6 — Automations are the mechanism that delivers the above.**
> "Using automations in a software factory can help address this by letting the team..."

### Onboarding and the agent suite

**V7 — Connect a repository; a factory is running in a few minutes.**
> "Connect your repository, and you can have a factory spun up in a few minutes."

**V8 — Provisioning sets you up with a suite of agents, one per lifecycle step.**
> "this will set you up with a suite of agents for each step in the development life cycle"

**V9 — A triage agent that researches issues as they arrive.**
> "Starting with triage to research issues as they come in"

**V10 — A spec-writing agent for larger features.**
> "spec writing for larger features"

**V11 — An implementation agent that writes code, with verification by computer use.**
> "implementation to write the code with verification using computer use"

**V12 — A code-review agent.**
> "code review on the back end"

**V13 — A factory foreman that orchestrates all the agents through the chain.**
> "a factory foreman that will orchestrate all of these agents through the chain"

### Definition as code

**V14 — All the infrastructure is written as code.**
> "all this infrastructure is written as code"

**V15 — A centralized file managing every repository in the factory, its secrets, and the available tool servers.**
> "a centralized file to manage all of the repositories in your factory, secrets and [tool] servers available"

**V16 — A per-agent config file declaring behaviour, model, and skills.**
> "for each of our agents, we have a config file to show how that agent should behave, the model that it should be using, and the set of skills that it has access to"

**V17 — Skills are the mechanism by which agents work with third-party integrations.**
> "the set of skills that it has access to to work with your third-party integrations"

### Intake and multiple factories

**V18 — Tag the factory in on the task board to start writing a spec.**
> "you can tag it in on your task board ... to start writing out a spec for a feature"

**V19 — Tag the factory in on the git host for PR review flows and leaving comments.**
> "tag in your factory on [the git host] for PR review flows and leaving comments"

**V20 — Tag agents in from chat; stated as the favourite workflow.**
> "our favorite workflow, tagging in agents from [chat]"

**V21 — Multiple factories, one per team.**
> "we actually have multiple factories that we run for each of our teams"

**V22 — Each factory has different permissions, skills, and tool servers, so capabilities are scoped appropriately.**
> "different permissions, skills, [tool] servers, so the capabilities of every factory are scoped appropriately"

**V23 — A search-optimisation factory for blog content.**
> "we use for optimizing our blog post for search engines"

**V24 — A marketing factory for the landing page and brand assets.**
> "a marketing factory that we use for updating our landing page and brand assets"

**V25 — Computer use as a general agent capability: the agent drives a UI, reviews screenshots, and navigates with a keyboard.** (Stated across three places: verification, video-watching, and the PR recording.)
> "with computer use, the agent can watch [recorded] videos for us" · "while the agent is reviewing screenshots" · "while it's navigating with the keyboard"

**V26 — The agent watches recorded videos on the team's behalf.**
> "we also discovered by accident that with computer use, the agent can watch [recorded] videos for us"

**V27 — A product-building factory with access to several core repositories.**
> "This agent has access to our core repositories for the [terminal], the server, and the web UI."

**V28 — Third-party coding harnesses are supported inside the system.**
> "We support [two third-party] harnesses inside of the system, by the way."

### The worked example

**V29 — The foreman routed the request to the triage agent for investigation.**
> "that foreman routed our request to the triage agent to do a bit of investigation"

**V30 — Triage discovered a defect and filed a tracker issue for visibility to the rest of the team.**
> "The agent discovered that it wasn't implemented correctly, so it fired a [tracker] issue for visibility to the rest of the team."

**V31 — Connect your own task boards so the whole team is aligned on what agents are doing.**
> "you can connect your own task boards to make sure the whole team is aligned on what agents are doing"

**V32 — Back-and-forth with the triage agent to understand the scope of the problem.**
> "after a bit of back and forth with the triage agent to understand the scope of the problem"

**V33 — Triage then moves the work along to spec writing, for larger features.**
> "then moved it along to spec writing. This is for larger features"

**V34 — The spec is a detailed plan tailored to the repositories being worked in.**
> "where you want to write out a detailed plan tailored to the repositories that you're working in"

**V35 — Specs are written as code you can review.**
> "these specs get written as code that you can review"

**V36 — The tech spec is generated as a pull request.**
> "we have a tech spec that was generated as a pull request"

**V37 — A tech spec is a plan based on a more detailed template tailored to your repository.**
> "you can think of tech specs like a plan based on a more detailed template tailored to your repository"

**V38 — The spec details all changes needed, along with testing and validation strategies.**
> "This details all of the changes that need to be made, along with testing and validation strategies."

**V39 — Once a spec is approved, work moves to implementation.**
> "once a spec gets approved, we can move on to implementation"

**V40 — Every agent run is backed by a session that is easy to join from whatever tooling you use.**
> "all agent runs are backed by a session that are easy to join from whatever tooling that you're using"

**V41 — View the live session that implemented the change, with follow-up from the web UI or the terminal.**
> "view the live session that implemented this change with the opportunity to follow up from the web UI, from the [vendor] terminal"

**V42 — A factory tool server for interacting with sessions, config, and anything else from your own coding harness.**
> "you can use the factory [tool server] to interact with sessions, config, and anything else from your favorite coding harness"

**V43 — The tool server resumes sessions and talks to the foreman.**
> "useful not only for resuming sessions and interacting with the foreman"

**V44 — The tool server updates the factory configuration.**
> "but also for updating your factory configuration"

**V45 — The tool server explains the work that needs to get done.**
> "or understanding the work that needs to get done"

**V46 — The output is a pull request ready for review, with changes detailed and validation steps.**
> "we get a pull request that's ready for review. With all the changes detailed, validation steps"

**V47 — The PR carries a computer-use recording demonstrating the change.**
> "and even a recording using computer use. Here we can see the agent demonstrating that it added the picker"

**V48 — Recordings are post-produced: cursor clicks highlighted, dead spots edited out, keyboard callouts shown.**
> "We'll also highlight cursor clicks, we'll edit out dead spots while the agent is reviewing screenshots, and we'll also show callouts while it's navigating with the keyboard."

**V49 — In review you can comment on the git host or ask the agent about the decisions it made, in chat.**
> "you're free to leave comments ... or ask the agent about decisions that it made directly on [chat]"

**V50 — Having the agent side by side while you review a PR.**
> "It's really helpful to have the agent side by side while you're reviewing a PR."

### The dashboard

**V51 — A bird's-eye view of every conversation happening across chat, tracker issues, and anywhere else work is tracked.**
> "you can get a bird's-eye view of all of these conversations that are happening across [chat], across [tracker] issues, anywhere else that you're tracking work"

**V52 — An activity feed showing where agents are at in each step: triage, planning, building, reviewing.**
> "In the activity feed, we can see where agents are at in each step of the process"

**V53 — All agent runs across the team, with callouts for the origin of each request.**
> "all of the agent runs across our team with callouts for the origin of those requests"

**V54 — Artifacts reviewable from the run list: images or videos taken as part of a PR.**
> "whether there's any artifacts that we can review like images or videos taken as part of a PR"

**V55 — A breakdown of sub-agents used, to see the delegation strategy.**
> "a breakdown of sub agents used to see the delegation strategy"

**V56 — Claim: the harness is fully capable of automatic orchestration.**
> "[The] harness is fully capable of automatic orchestration, by the way."

**V57 — The agents used for a given chat conversation, as a trace: triage, a spec step, implementation.**
> "the agents that were used for that [chat] conversation here with the triage agent, a spec step, and implementation"

**V58 — The cost of every agent run, for full visibility on token spend.**
> "review the cost of every agent run for full visibility on where our tokens are going"

**V59 — Agents and automations configured as code but viewable in the web UI: models used at each step, secrets, tool servers, additional instructions.**
> "configured as code, but you can also view them from the web UI to review the models that are used at each step, secrets, [tool servers], and additional instructions"

**V60 — An automations view showing what connects agents to the outside world: tracker mentions, chat DMs versus channels.**
> "automations that connect those agents to the outside world, like mentions ... [DMs] versus ... channels"

### Assurance and improvement

**V61 — The vendor helps not only set up the factory but monitor and improve it over time.**
> "Not only do we help you set up the factory, but we also help you monitor and improve that factory over time."

**V62 — Scores: a special type of agent that views the output and quality of the other agents.**
> "scores, which are a special type of agent that will view the output and the quality of the other agents in the system"

**V63 — A code-quality score reviewing all implementation agent runs, judging on a rubric.**
> "we have a score for code quality that reviews all of our implementation agent runs and judges based on a rubric"

**V64 — Operator-defined scoring criteria and pass threshold, to fine-tune what success and failure look like.**
> "You can also define your scoring criteria, your [pass] threshold to really fine-tune what success and failure look like."

**V65 — Past score runs shown with a cost breakdown of what scoring itself costs.**
> "a few of those past runs here with the cost breakdown for what these scores are costing us"

**V66 — Whether each agent passed, and what comments the scorer had about that performance.**
> "whether each of these agents passed the sniff test and what comments the agent might have about that performance"

**V67 — Self-improvement: an agent reviewing all the scoring output and all the runs, suggesting insights and changes.**
> "an agent reviewing all of that scoring output and all of the runs that your team puts through this system and suggesting insights and changes"

**V68 — A concrete example: the improvement agent addressed complaints left on PR reviews about code comments, and proposed a change to a skill.**
> "This is addressing complaints that we left on PR reviews about code comments and suggest a change to the skill"

**V69 — "Hidden gems" pulled out of all the conversations the team has.**
> "pulling out hidden gems in all the conversations that you have to make meaningful change"

### Analytics and benchmarks

**V70 — Cost per PR over time, to check whether shipping skill changes actually reduces it.**
> "looking at our cost per PR to understand as we ship changes to our skills and optimize our setup, is it truly decreasing the cost of the pull requests"

**V71 — Velocity tracking: number of PRs opened, total runs across the system.**
> "velocity tracking, looking at the number of PRs opened, the total runs across the system"

**V72 — "Any other analytics you may need access to."**
> "and any other analytics you may need access to"

**V73 — Benchmarks over a task set representing the work the team actually does.**
> "benchmarks, which let you take a set of tasks that represent the work that you do on your team"

**V74 — Scores run against those tasks with different models *and context*.**
> "runs these scores against those tasks with different models and context"

**V75 — The result configures effective routing to the right model for each step — "a personalized model benchmark".**
> "So you can set up effective routing to the right models for each step in your factory. It's like a personalized model benchmark just for your setup."

### Closing claims

**V76 — Open and flexible: any agents, any models.**
> "It's open and flexible, letting you use any agents or any models for the job"

**V77 — Host with the vendor or self-host, with full data sovereignty.**
> "with options to either host to us or self-host, and full data sovereignty"

**V78 — Because the infrastructure is owned as code, it is easy to tweak and improve over time.**
> "because the infrastructure is owned as code, it's easy to tweak and improve over time"

**V79 — Connectors to any trigger, plus a REST API and an SDK.**
> "with connectors to any trigger, [chat, git host, tracker], and REST API and SDK"

**V80 — Early access, a waitlist, and usage credits for qualified teams.**
> "It's in early access, so join the waitlist"

---

## Pass 2 — Map

Only the items that are not plainly COVERED are discussed at length. COVERED items are listed first,
compactly, with their citations; a reader who wants to challenge one has the requirement id.

### 2.1 COVERED (44)

| # | Requirement that already demands it |
| --- | --- |
| V2 | §2.1 P2 ("no one can answer is this worth it"), P3 ("governance is accidental"), P7; personas U3, U4; JTBD-8 |
| V3 | FR-3.4 (each agent selects harness, model, runner, tools, secrets, skills); FR-11.3 (tiers, starting tier per agent); FR-2.10 (inheritance) |
| V4 | FR-2.1 (canonical tree); FR-10.8 (external tool servers under the same grant rules); FR-17.1 (credential classes); FR-17.2 (default-deny); FR-17.7 (`sf audit` reachability report) |
| V5 | FR-15.3 (metrics table incl. cost per change, cycle time, autonomy); FR-15.6 (overview with trend) |
| V6 | FR-18.3 (automations bind trigger → agent → prompt); FR-2.10 (automation-level override of model and grants) |
| V9 | FR-3.2 (Scout: "establishes what is true and how big the change is") |
| V10 | FR-3.2 (Architect); FR-5.4 (Design output is a Spec Delta plus a draft change) |
| V12 | FR-3.2 (Critic); FR-3.5 and FR-3.5a (independence, and the ladder when it is unsatisfiable) |
| V13 | FR-3.1 (exactly one Conductor); FR-3.3 (roles are responsibilities, not a fixed pipeline) |
| V14 | PR-1; FR-2.1; FR-2.6 (reviewable as ordinary code changes) |
| V15 | FR-1.2 (a factory declares its repositories); FR-2.1 (`factory.yaml` root document); FR-2.8 (secret *names*, never values) |
| V16 | FR-3.4; FR-3.9 (the agent's Markdown body is its durable role prompt) |
| V18 | FR-18.13 (tracker baseline: issue created/labelled/assigned/state-changed, mentions); FR-18.3 |
| V19 | FR-18.11 (git-host baseline: change opened/mentioned/review requested, and filters) |
| V20 | FR-18.12 (chat baseline: mentions, DMs, channel messages, reaction intake, threaded replies) |
| V22 | FR-1.3 (one policy per factory); FR-17.2; FR-17.7 |
| V27 | FR-1.2 (a factory declares one or more repositories) |
| V28 | FR-11.1 (harness abstraction; external harnesses are adapters); recorded as a live tension in OQ-10 |
| V29 | FR-3.3 (Conductor may skip, enter partway, return work); FR-3.3a (bounded by policy) |
| V33 | FR-3.3; FR-4.2 (stage machine) |
| V35 | FR-5.1 (spec units are Markdown files under `specs/`); FR-5.3 (all changes arrive as a reviewable Delta); PR-1 |
| V39 | FR-16.1 (spec approval is a default checkpoint) |
| V42 | FR-19.1 and FR-19.2 (list/search/get work items, message the Conductor, read the conversation, fetch and validate definition files) |
| V43 | FR-19.2; FR-3.7 (continue the existing conversation); FR-29.2 (continuation is a resumption) |
| V45 | FR-19.2 ("get a work item with local setup guidance") |
| V46 | FR-13.12 (evidence bundles); FR-22.4 (evidence reviewable in the tool where the human already is) |
| V52 | FR-15.6 (activity board: work items by stage, filterable, with a *needs attention* flag) |
| V53 | FR-15.3 (runs broken down by agent, stage, status, **source**, model, tier); FR-4.6 (source context carried through every stage) |
| V56 | FR-3.3; FR-4.2a (the stage graph is configuration) |
| V58 | FR-11.12 (usage accounting per run per model per stage); FR-15.3; FR-26.5 (cost attribution incl. cause) |
| V59 | FR-15.6 (definition view); FR-2.7 (`sf plan` prints the fully-inherited resolved configuration) |
| V60 | FR-15.6; FR-18.12 (DM versus channel are distinct filterable events); FR-18.3 |
| V61 | FR-14.1 (self-improvement opt-in per scorer); FR-14.8 (improvement telemetry) |
| V62 | FR-13.1 (three distinct mechanisms); FR-13.6 (scorer declaration) |
| V63 | FR-13.6 (`agents` targeting, rubric body); evals.md E-14 (classification, not grading) |
| V64 | FR-13.6 (`labels`, `passingScore`, `samplingRate`) |
| V66 | FR-13.6; `ScoreResult { label, score, reasoning, judge }` (§9.1) |
| V67 | FR-14.2 (cluster → diagnose → propose → validate → submit) |
| V71 | FR-15.3 ("Changes opened", "Runs") |
| V73 | FR-13.9 (fixed task set, configurations, repetitions); FR-13.10 (tasks from completed runs) |
| V76 | PR-10; NFR-6.2; FR-11.2 (pluggable providers) |
| V77 | §6.3 deployment topologies; FR-17.8 (data locality); FR-20.6 (no phone-home) |
| V78 | PR-1; FR-2.6; FR-14.3 (proposals may target the factory's own definition) |
| V79 | FR-18.1 (intake sources), FR-18.2 (adapter contract); FR-21.4 (versioned API with generated OpenAPI); FR-21.6 (SDK) |

Two of these deserve a note rather than a bare tick.

**V56 (automatic orchestration).** The reference product presents automatic orchestration as a
capability claim. We present the same behaviour (FR-3.3) *and* bound it (FR-3.3a, FR-3.3b), because
the orchestrator reads attacker-controllable text. That is a strictly stronger position and does not
need changing.

**V63/V64 (scorers).** Our FR-13.6 is a near-exact superset of what the video describes, including
`samplingRate` and the `selfImprovement` opt-in the video implies but never states. We additionally
require judge integrity and human-agreement calibration (FR-13.8, evals.md E-20), which the video
does not mention at all.

### 2.2 PARTIAL (22) — with the missing part named

**V7 — Connect a repository, factory in minutes.** We have NFR-4.1 (under 10 minutes to first useful
run), FR-20.1 (`sf init` creates a complete, valid definition), and FR-23.1 (`sf onboard` readiness
assessment). *Missing:* nothing connects the two. `sf onboard` reports what is enforceable; `sf init`
writes defaults. No requirement says the second consumes the first, so a generated factory does not
reflect the repository it was generated for.

**V8 — A suite of agents for each lifecycle step, out of the box.** FR-3.2 defines six roles;
FR-30.3 requires a CI-tested reference definition. *Missing:* FR-2.1 says "Only `factory.yaml` and at
least one agent are required", and no requirement obliges `sf init` to emit the fleet. As written, a
conformant implementation satisfies NFR-4.1 with a single-agent factory, which is precisely the
"single agent wrapped in a webhook" §1 rejects.

**V11 — Implementation with verification by computer use.** FR-22.3 makes screen and browser
verification "optional but first-class". *Missing:* two things. (a) No tool implements it — see V25.
(b) The video verifies inside implementation; we verify in a separate Prover stage (FR-22.1). That
part is a deliberate divergence and ours is stronger (an independent actor establishes the claim), so
only (a) needs fixing.

**V21 — Multiple factories per team.** FR-1.5 (P1) permits a workspace file listing factory roots.
*Missing:* every reporting and audit surface is scoped to one factory. FR-15.3, FR-15.6, FR-17.7 and
`sf metrics` have no cross-factory mode, so an organisation that follows our own FR-1.3 advice (one
policy per factory ⇒ several factories) cannot answer "what did agents cost us this month" in one
place. The video shows exactly that view.

**V31 — Connect your own task boards for team alignment.** FR-18.13 (tracker baseline) and FR-18.8
(reply in place) cover intake and results. *Missing:* state between those two moments. Nothing
mirrors stage, blocker, assignee, or change link back to the tracker item, so the team's board shows
"agent mentioned" and then, much later, "agent replied".

**V34 — A spec tailored to the repositories worked in.** FR-9.2 sections 3 (Terrain) and 6
(Conventions) give the Architect the material. *Missing:* nothing shapes the *output*. Tailoring is
left entirely to the model, which is the pattern PR-6 exists to prevent.

**V36 — Tech spec generated as a pull request.** FR-5.4 requires the Delta be independently
reviewable before the code, and FR-16.1 makes approval a checkpoint. *Missing:* the delivery
mechanism. A Delta reviewable only in the dashboard is a second review queue with different
notifications, different history, and no blame.

**V38 — Testing and validation strategies inside the spec.** Spec units carry `acceptance` and
`verifies` (FR-5.2), and gates check coverage at Review (FR-13.2 `coverage-of-criteria`). *Missing:*
a *forward-looking* validation plan at Design time — how each criterion will be exercised, and
whether visual evidence is required — which is what makes the Build stage's job checkable in advance.

**V40 — Runs backed by joinable sessions.** FR-15.7 (observable and steerable), FR-19.2 (message the
Conductor), FR-29 (conversation lifecycle). *Missing:* a session identity and a join contract. Our
`Run` is an execution record; the video's session is a thing a human attaches to from several places.
FR-25.5 already recognises steering as a decision channel, so the authorisation half exists; the
attach half does not.

**V41 — Follow up from the web UI or the terminal.** FR-15.7 is written for the dashboard.
*Missing:* the CLI half, despite FR-21.1's claim that "the CLI is the complete surface". As written,
FR-21.1 and FR-15.7 are inconsistent.

**V44 — Update factory configuration through the tool server.** FR-19.2 lists "fetch and validate
definition files". *Missing:* a propose-change operation. This is not an oversight to fix carelessly:
FR-17.6 forbids a run writing the loaded definition and FR-14.3b explains why proposing is not
writing. The tool belongs on the proposal path, not the write path.

**V47 — A computer-use recording on the PR.** FR-22.3 permits it and FR-22.4 attaches it.
*Missing:* the producing capability (V25) and the legibility work (V48).

**V49 / V50 — Ask the agent about its decisions while reviewing.** FR-4.5 makes a reply continue the
work item, FR-18.8 replies in place, FR-15.6 gives a run inspector. *Missing:* the specific case of a
*completed* work item. After HANDOFF (FR-4.4) nothing requires the factory to answer questions about
what it did, and the Critic's own pack deliberately excludes the Builder's reasoning (awareness.md
§7), so the material for an answer is not routinely retained on the path a reviewer would use. This
is the single most common human interaction with a machine-authored change, and today it either
restarts work or goes unanswered.

**V51 — A bird's-eye view of conversations across channels.** FR-15.6 gives an activity board of work
items by stage. *Missing:* the thread as an organising unit. A checkpoint parked in a chat thread
(FR-16.4) is visible on our board only as `BLOCKED: awaiting_human`, not as "this conversation is
waiting on you".

**V54 — Artifacts browsable from the run list.** Evidence is addressable (evals.md E-7) and appears
in the run inspector (FR-15.6). *Missing:* a cross-run visual view. LOW: a convenience.

**V57 — Per-conversation agent trace.** The ledger records every dispatch (FR-15.1). *Missing:* a
view that assembles "which agents served this thread". Subsumed by the delegation-tree
recommendation.

**V65 — The cost of scoring itself.** FR-3.11b puts assurance inside the budget, FR-26.5 attributes
spend by cause (`scoring`, `benchmark`, `improvement`), and O-10 reports assurance overhead share.
*Missing:* per-scorer resolution. We can tell an operator that 22% of spend is introspection; we
cannot tell them *which rubric* to retire. This is exactly the level of concreteness the video shows.

**V70 — Cost per PR over time.** O-9 (cost per merged change, fully loaded) and FR-14.8 (measured
effect of adopted proposals) exist. *Missing:* the join between them on the dashboard — the cost
series annotated with the definition revisions adopted in the window, which is the form in which the
question "did our skill change work" is actually asked.

**V72 — Any other analytics.** FR-21.2 gives `ledger query` and FR-15.9 forbids UI-only metrics.
*Missing:* a saved, versioned, operator-authored metric. Today an extra metric is a code change.

**V74 — Benchmarks over different models *and context*.** FR-13.9 and evals.md's `Benchmark` record
vary `harness`, `model/tier`, `runner`, `scaffolds`. *Missing:* the pack. This is the sharpest gap in
the whole analysis relative to our own thesis: §1.1 stakes the project on the harness — chiefly the
Awareness Pack — and awareness.md §4 admits its section weights are "starting values, not settled
ones" and a "first-class self-improvement target". The benchmark that would settle them cannot
currently express them as a variable.

**V75 — Benchmarks drive per-step routing.** FR-11.5 permits lowering a starting tier "by proposal,
with evidence" and FR-13.11 gates adoption on benchmark evidence. *Missing:* the producer. Nothing
turns a benchmark result into a routing proposal, so the loop closes only if a human transcribes the
table into the definition by hand — which, in practice, means it does not close.

### 2.3 ABSENT (8)

**V25 — Computer use as a declared tool class. (HIGH)**
FR-22.3 says an agent "may drive a browser or desktop session". Nothing in FR-10 or HARNESS.md §4.3
provides a tool that can. There is no `ui`/browser effect class in FR-10.2's five classes, no grant
name, no blast-radius clause for navigation and authentication (FR-12.1 covers paths, effects,
external actions, ceilings — not origins), and no determinism or recording contract. A capability
with no descriptor cannot be granted (FR-10.7), audited (FR-17.7), or budgeted (FR-3.11). FR-22.3 is
therefore currently unimplementable as written, and the gap is a security gap as much as a feature
gap: a browser is simultaneously a network egress path, an external-action surface, and an untrusted
input source, and none of the three is addressed.

**V26 — Media as a research input. (MEDIUM)**
Nothing in our documents ingests audio or video. Teams keep real decisions in recordings; the
transcript's own example (an agent watching a recorded video) is a research act, not verification.
Note the trust consequence: anything so retrieved is `untrusted` under FR-6.4b and can never reach
Canon or the `conventions` section, which constrains the design but does not forbid it.

**V30 — Incidental discoveries become their own work items. (HIGH)**
Our work item holds one source context from intake to handoff (FR-4.6), and FR-10.5's external tools
allow "update a tracker item" but not create one. A Scout that finds an unrelated defect therefore
has three options, none of which we specify: widen the current item (scope creep), record it in
memory where no human will see it, or drop it. The video's third option — a linked sibling item,
filed where the team looks — is the right one and we do not have it. This also connects to a
documented hole: C.2 records that cross-repository migration (JTBD-4) is unsupported because of this
same single-source-context model.

**V37 — Repository-tailored spec templates. (MEDIUM)**
FR-5.2 defines a spec unit's *fields*. Nothing defines the *shape of a design document* per work
class or repository. The reference product treats this as a template artifact the operator owns; we
treat it as something the Architect's prompt produces, which is unreviewable, undiffable, and
inconsistent between runs.

**V48 — Recording post-production for reviewer legibility. (HIGH)**
FR-22.2 lists recordings as an evidence class and FR-22.7 handles truncation. Nothing addresses
whether a recording is *worth watching*. This lands directly on two of our own recorded risks: R-12
("evidence theatre: bundles grow, trust does not") and O-6 (human review cost, whose stated gaming
strategy is "attach less evidence; reviewers rubber-stamp"). A raw capture of an agent pausing to
read a screenshot is evidence that costs the reviewer more than the diff. The reference product's
answer — click emphasis, keyboard callouts, dead-spot removal — is a concrete mechanism for making
FR-22 pay, and we have no equivalent.

**V55 — Sub-agent delegation and the delegation tree. (HIGH)**
Our `Run` entity (§9.1) has no parent. Our model has exactly one level of delegation, at the
orchestration layer: Conductor dispatches specialist. Nothing describes an agent dispatching a
sub-run, and nothing forbids it either — which is the problem, because an unspecified capability that
implementations will build anyway is unbudgeted spend (FR-3.11a counts per-run and per-work-item
budgets with no notion of a child), an unaudited widening of the effective agent set (FR-17.7's
report enumerates agents from the definition), and invisible in every view. The video's
"breakdown of sub agents used to see the delegation strategy" is the diagnostic that makes multi-agent
runs legible, and we cannot render it because we do not record the edge.

**V68 — Human review findings as an improvement input. (HIGH)**
FR-14.2's loop begins "cluster related failures by signature", and FR-14.1 makes the loop opt-in
*per scorer* — so the only admissible input is a failure a scorer already knew how to look for. A
human's comment on a factory-authored change is the opposite: evidence of a failure mode no rubric
encoded yet. It is also the only signal grounded in the reviewer's own cost (O-6, O-7, R-14). The
video's worked example is precisely this — complaints about code comments on PR reviews becoming a
skill revision — and our loop as specified cannot produce it.

**V69 — Conversation mining. (MEDIUM)**
The broader form of V68: mining chat threads, issue discussions, and review conversations for
improvement signal. Genuinely absent, and genuinely dangerous under our own model — FR-6.4b classes
all of it `untrusted`, and FR-6.4a exists because two runs reading the same comment are one
observation. Worth building with those constraints attached rather than dismissing; see the
recommendation, which quotes rather than promotes.

### 2.4 DIVERGENT (5)

**V1 — Cloud-based factories.** The reference product is cloud-first with self-hosting as an option
("either host to us or self-host"). We are local-first: PR-2 makes local the reference implementation
and §6.3 makes cloud one of five topologies. **Different, and our rejection stands** for the reasons
in P8 — but the video is a useful reminder that our position costs us the "few minutes to a running
factory" story (V7, V8), which is a real adoption property and is why those two items are ranked as
they are. The right response is to make the local path fast, not to move the control plane.

**V17 — Skills as the integration mechanism.** The video says an agent's skills are what let it "work
with your third-party integrations". Our FR-7.11 says the opposite in the strongest terms: skills
change knowledge, never access; `sf lint` fails a skill body that implies otherwise. Access comes
from tool grants (FR-10.7) and adapters (FR-18.2). **Our rejection stands unchanged** — PR-4 is the
whole reason prompt injection cannot widen a grant here — but we should read the video's phrasing as
a UX observation: operators *think* in terms of "this agent can use the tracker", and our
documentation should present the grant and the skill as one configured capability even though they
are enforced separately.

**V23 / V24 — Non-code factories (search optimisation over blog content, marketing over landing pages
and brand assets).** NG-4 declares we are "not a general workflow engine" and are opinionated about
the software delivery lifecycle. Almost every gate in FR-13.2 assumes a repository with runnable
validation. **Our rejection mostly stands**, with an honest qualification: FR-23.2's advisory mode
already makes a repository with no runnable tests a supported, labelled configuration, so a content
factory is *expressible* today — it would simply run with `tests-pass` and `regression-proven` marked
`unenforceable`. That is a reasonable place to leave it. What we should not do is chase generality:
the video's marketing factory is impressive as a demo and would cost us the three-way agreement
(FR-5.5) and `regression-proven` (FR-13.3) that the rest of the design is built on.

**V32 — Direct back-and-forth with a specialist agent.** The video's user converses with the triage
agent to establish scope. FR-3.6 says only the Conductor communicates with the requester, and
specialists surface questions *through* it. **Our rejection stands as an auditability property but is
too strong as a user-experience rule.** FR-3.1's own rationale admits the single-conductor model is
"an interface and auditability argument", recorded as OQ-3. A relay — the human addresses a named
specialist, the Conductor carries it to that specialist's existing conversation (FR-3.7) and records
both — preserves everything FR-3.6 was protecting while removing a real friction. Recommended below
as FR-3.15 (P2).

---

## Pass 3 — Recommendations

Paste-ready requirements in the PRD's voice, slotted into existing families where one fits and into
one new family where none does. Numbers are checked against Appendix A and against every requirement
in §7; none collides, including the retired `FR-15.11`/`FR-15.12` referenced in Appendix C.

### HIGH — materially changes what we should build

#### New family: FR-31 Computer use and interactive surfaces

> Proposed as a family rather than as clauses inside FR-10 and FR-22 because it spans the Tool
> Registry, the blast-radius contract, the evidence model, and the trust model, and because FR-22.3
> currently promises a capability that no other requirement makes buildable.

**FR-31.1 (P1) — Computer use is a declared tool class, not an aspiration.** The Tool Registry gains
a `ui` side-effect class and a baseline tool set — open, navigate, click, type, key, screenshot, and
read-accessibility-tree — each with typed input and output schemas, a timeout, a cost class, and at
least one worked example (FR-10.1), granted per agent like any other tool (FR-10.7). *Rationale:*
FR-22.3 says an agent "may drive a browser or desktop session" and nothing in FR-10 or the harness
spec can do it; a capability with no descriptor cannot be granted, audited, or budgeted.

**FR-31.2 (P1) — A UI session is inside the blast-radius contract.** The contract declares the
origins a session may reach, whether it may authenticate, and which execution secrets it may use —
never inference credentials (FR-17.1). Navigation outside the declared origins is a `blocked`
violation (FR-12.10); an attempt to authenticate without the grant is `escalating`. *Rationale:* a
browser is simultaneously a network egress path, an external-action surface, and an untrusted input
source, and treating it as ordinary `exec` silently widens every row of FR-17.5's table.

**FR-31.3 (P1) — Every UI session is recorded, and its action log is structured.** A session emits a
`screen_recording` evidence item *and* a machine-readable action log — each navigation, click, key,
and assertion with its timestamp — so a reviewer or a gate can read what happened without watching
it. An unrecorded UI session may not satisfy any gate. *Rationale:* PR-3; a video that only a human
can interpret is not evidence a gate can consume.

**FR-31.4 (P1) — Everything a UI session observes is `untrusted`.** Page text, screenshots, and
accessibility trees enter under FR-6.4b's `untrusted` class, may never reach Canon or a pack's
`conventions` section, and are cited by session id and timestamp. *Rationale:* rendering a hostile
page into an agent's context is a prompt-injection channel with a wider surface than issue text,
which FR-17.4 already treats as untrusted.

**FR-31.5 (P2) — UI sessions serve research as well as verification.** An agent may be granted a
read-only session — no writes, no authentication, no external actions — to consult a source with no
API. *Rationale:* teams keep decisions in places without APIs; the alternative is a human
transcribing them, which is the work the factory exists to remove.

#### FR-22 Evidence, recording, and verification

**FR-22.8 (P1) — Recordings are produced for a reviewer, not for an archive.** A screen or terminal
recording attached to a change is post-processed by a declared, deterministic recipe: pointer and
click emphasis, keyboard actions rendered as on-screen callouts, and idle spans above a threshold
removed. The removed spans are listed in the evidence item with their durations. *Rationale:* R-12
says evidence only pays if it costs the reviewer less than reading the diff, and O-6 measures whether
it does; an unedited capture of an agent pausing to read a screenshot fails that test by
construction.

**FR-22.9 (P1) — Edited evidence retains its original.** The unedited capture is retained under the
same retention class (FR-15.10), is addressable from the edited item, and the edit recipe and its
version are recorded. *Rationale:* editing evidence without keeping the original is tampering,
however well-intentioned, and INV-6 requires a claim to resolve to what actually happened.

**FR-22.10 (P2) — Evidence legibility is measured, not assumed.** Per evidence class, record whether
reviewers opened it and the review time for changes with and without it, feeding O-6 and OQ-7.
*Rationale:* OQ-7 asks whether evidence reduces review time or increases it; per-class data is how
that question gets answered rather than argued.

#### FR-3 Agents

**FR-3.13 (P1) — Delegation inside a run is declared and recorded.** An agent may dispatch a bounded
sub-run to another agent only where its configuration declares the delegable set. Each sub-run is a
first-class `Run` with its own Awareness Pack, budget, contract, and ledger entries, carrying
`parent_run` and the dispatching turn. Undeclared delegation is a validation error, and an attempt at
runtime is a violation (FR-12.5). *Rationale:* implementations will build delegation whether or not
the design admits it; undeclared, it is unbudgeted spend, an unaudited widening of the effective
agent set (FR-17.7), and invisible in every view.

**FR-3.14 (P1) — A sub-run inherits the intersection, never the union.** A sub-run's tool grants,
secrets, network policy, writable paths, and trust class are the intersection of its own
configuration and its parent's; its budget draws from the parent's remaining work-item budget
(FR-3.11a); its depth is bounded by policy. *Rationale:* without this, delegation is a
privilege-escalation primitive — the cheapest way past a grant is to ask a differently-configured
agent to do it.

**FR-3.15 (P2) — A specialist may be addressed, through the Conductor.** A human may direct a
question at a named specialist from any surface; the Conductor relays it into that specialist's
existing conversation (FR-3.7), returns the answer to the originating context, and records both hops.
*Rationale:* FR-3.6 exists for auditability and one address, not to make it impossible to ask the
triage agent what it found; the relay preserves both properties.

#### FR-4 Work items and the stage machine

**FR-4.11 (P1) — Incidental discoveries become their own work items.** A run that establishes a
defect, a risk, or a spec contradiction outside its own scope emits a typed `discovery` carrying its
evidence and trust class. The Conductor opens a new work item linked to the discovering run rather
than widening the current one, and the discovery appears in the originating item's evidence bundle
either way. *Rationale:* today the alternatives are scope creep, a memory nobody reads, or silence,
and a factory that cannot report what it noticed will not be trusted with what it noticed.

**FR-4.12 (P1) — A discovery may be published, under a permission gate.** Where a tracker is
configured and the automation grants it, a discovery is filed as a tracker item attributed to the
factory (FR-16.5), carrying its evidence and a link back, taking an external-action lease
(FR-19.5a) and deduplicated by fingerprint (FR-26.3). A discovery derived solely from `untrusted`
input requires a human decision before publication (FR-17.5a). *Rationale:* the point of a discovery
is that someone other than the requester sees it; the gate is there because filing issues is an
irreversible external action and a spam vector.

#### FR-14 Self-improvement loop

**FR-14.10 (P0) — Human review findings are a first-class improvement input.** Comments a human
leaves on a factory-authored change, and commits a human pushes onto a factory branch after handoff,
are ingested as typed improvement signals, clustered by surface and finding class alongside scorer
failures (FR-14.2). The loop's admissible inputs are therefore scorer failures, gate findings, human
review comments, and human corrections — not scorer failures alone. *Rationale:* FR-14.1 makes the
loop opt-in per scorer, so today it can only see failure modes a rubric already encoded. The
reviewer's complaint is evidence of one that none did, and it is the only signal grounded in the cost
O-6 and O-4 measure.

**FR-14.11 (P1) — Review-derived proposals quote their source and carry its trust class.** A proposal
motivated by human input quotes the comments verbatim with permalinks, marks the derived claim
`operator` where the author is an identified principal (FR-25.1) and `untrusted` otherwise
(FR-6.4b), and may not turn a comment into a skill, convention, or Canon memory without the ordinary
promotion evidence (FR-7.4, FR-6.4, FR-6.4a). *Rationale:* mining conversations is the exact path by
which attacker-controllable text reaches an agent's standing instructions; the value is real and so
is the risk, and the defence is provenance, not restraint.

**FR-14.12 (P2) — Improvement-signal coverage is reported.** The dashboard reports the share of
adopted proposals originating from scorers, gate findings, human review, and human corrections.
*Rationale:* a loop fed only by its own scorers is measuring itself, and FR-14.7a's rubric-drift
defence is much weaker when no independent signal source exists to compare against.

#### FR-13 Evals, tests, and gates

**FR-13.15 (P1) — Context composition is a benchmark dimension.** A benchmark configuration may vary
Awareness Pack composition — per-section weights, total budget, retrieval depth, and skill-offer size
— as well as harness, tier, runner, and scaffolds, and the report attributes outcome differences to
the dimension varied. *Rationale:* §1.1 stakes the project on the pack, and awareness.md §4 states
its weights are "starting values, not settled ones" and a first-class improvement target. The
instrument that would settle them cannot currently express them.

#### FR-11 Model routing, calibration, and escalation

**FR-11.13 (P1) — A benchmark emits a routing recommendation.** From a completed benchmark the
factory produces a proposed starting-tier assignment per (stage, task class), with the pass rate,
cost, latency and variance behind each, submitted as an ordinary reviewable definition change
(FR-14.5) and subject to FR-13.10a — the thresholds are the operator's, written in the definition,
not the system's. *Rationale:* FR-11.5 permits de-escalation "by proposal, with evidence" and nothing
produces the proposal; a benchmark whose results must be transcribed by hand is a benchmark that gets
run once.

#### FR-15 Observability, ledger, and dashboard

**FR-15.16 (P1) — The run inspector renders the delegation tree.** For any work item, and for any
originating conversation, show the parent/child run tree with each node's agent, role, tier, status,
duration, tokens and cost, and the dispatching turn. *Rationale:* "which agents served this request
and what did each cost" is the first question asked of a multi-agent run, and FR-15.3's per-agent
aggregate cannot answer it for a single item.

**FR-15.17 (P1) — A sealed run can be interrogated after handoff.** From the change, the originating
thread, or the dashboard, a human may ask why a completed run did what it did. The Conductor answers
from that run's pack, ledger, gate results, evidence and calibration statement, citing each
(FR-9.4), without reopening the work item or re-running the agent; the exchange is recorded and the
answer is marked as reconstructed rather than remembered. *Rationale:* FR-4.5 rightly makes a reply
continue the work item, which is correct for new instructions and wrong for "explain yourself" —
review-time questions are the most common human interaction with a machine-authored change, and today
they either restart work or go unanswered.

### MEDIUM — worth adding

**FR-23.7 (P1) — `sf init` scaffolds a working fleet, not an empty tree.** Initialisation consumes
the onboarding assessment (FR-23.1) and generates a Conductor, the specialist agents for the stages
the repository can actually support, automations for whichever integrations are configured, one
starting scorer, and a standing benchmark (evals.md E-30) — as ordinary reviewable files with the
detected build and test commands filled in. *Rationale:* FR-2.1 requires only `factory.yaml` and one
agent, so NFR-4.1's "ten minutes to first useful run" is satisfiable today by an empty factory.

**FR-23.8 (P2) — Scaffolding states what it assumed.** Every generated file records the detected
project layout, test topology and conventions it was derived from, and every assumption is written
into the file where a human can correct it. *Rationale:* generated configuration that hides its
reasoning is configuration nobody edits.

**FR-5.13 (P1) — Design output is templated per work class.** A factory declares spec and design
templates under `specs/_templates/`, selected by work class, which the Architect fills. Required
sections: the change list, the acceptance criteria, the testing and validation strategy, and — where
the change is user-facing — the visual verification plan (FR-31, FR-22.3). *Rationale:* "a detailed
plan tailored to the repository" is a template problem, not a prompt problem; a template is
reviewable and diffable, and an implicit structure inside a prompt is neither.

**FR-5.14 (P1) — A Spec Delta is delivered where the team already reviews things.** Where a git host
is configured, the Delta is opened as a change, so approval (FR-16.1) happens with the team's
ordinary review tooling, history, and notifications. Where none is configured, the local review path
(`sf spec review`) is the documented equivalent (PR-2). *Rationale:* FR-5.4 requires the Delta be
independently reviewable and never says where; a Delta reviewable only in the dashboard is a second
review queue.

**FR-18.15 (P1) — Work-item state is mirrored to the tracker.** Where a tracker is configured, the
factory maintains the tracker item's state, current stage, blocker, and links to the change and
evidence for the life of the work item, one-directionally, with the ledger remaining authoritative
(FR-4.10). Mirroring failures degrade per FR-18.9 and never block a stage. *Rationale:* FR-18.8 posts
results but leaves everything between intake and handoff invisible, so the team's board silently
diverges from what the factory is doing — which is the alignment problem the tracker was adopted to
solve.

**FR-19.10 (P1) — The tool server can propose a definition change.** The surface gains a
`propose_definition_change` operation that validates the edit whole-tree (FR-2.3), opens it through
the ordinary change path, and returns the proposal reference. It never applies a change (FR-14.3b,
FR-17.6), and a proposal touching `policy/`, scorers, gates, grants or secrets carries the stricter
review of FR-14.3a. *Rationale:* an operator working inside their own coding agent should be able to
fix a prompt or a tier without leaving it, and a proposal path is the only form of that which does
not breach FR-17.6.

**FR-21.8 (P1) — `sf attach <run>` joins a live run from the terminal.** Attaching streams the run's
turns, tool calls and evidence, and offers the same steering actions as the dashboard (FR-15.7),
authenticated and capability-checked as a decision channel (FR-25.5). *Rationale:* FR-21.1 states the
CLI is the complete surface; live observation and steering is currently dashboard-only, which makes
that statement false as written.

**FR-15.18 (P2) — Assurance cost resolves to the thing that caused it.** Beyond O-10's aggregate
share, the dashboard attributes assurance spend to the individual scorer, benchmark suite,
improvement proposal, and automation responsible. *Rationale:* an aggregate tells an operator that
introspection is expensive; it does not tell them which rubric to retire.

**FR-15.19 (P1) — A conversation view sits beside the work-item board.** The dashboard offers a
thread-centric view spanning every configured integration: each conversation, its work item, its
stage, and whether it is waiting on a human. *Rationale:* FR-15.6's board is organised by our stage
machine; the team's mental model is the thread they are in, and a time-bounded checkpoint (FR-16.4)
parked in a thread nobody opens is invisible today.

**FR-15.23 (P1) — The cost series is annotated with definition revisions.** Cost per merged change
(O-9) is rendered with the definition revisions adopted in the window marked on it, and every adopted
improvement proposal reports its measured before/after effect on that series (FR-14.8).
*Rationale:* "did our skill change actually make changes cheaper" is the question that justifies the
whole improvement loop; an unannotated trend line cannot answer it.

**FR-10.12 (P2) — Media ingestion is a deterministic tool where one exists.** `media.transcribe`
returns a timestamped transcript and `media.frames` returns sampled frames for an audio or video
artifact in the workspace or evidence store; results are admitted as `untrusted` (FR-6.4b) and cited
by artifact id and timestamp. *Rationale:* PR-6 — where a deterministic path to the content exists it
must be used, and it is cheaper and far more auditable than driving a media player through a UI
session (FR-31.5).

**FR-1.7 (P2) — A workspace of factories reports as one.** Where a workspace file declares several
factories (FR-1.5), `sf metrics`, `sf audit` and the dashboard must be able to report across all of
them with per-factory attribution preserved, and without implying any cross-factory data access.
*Rationale:* FR-1.3 pushes teams toward several factories; nothing then lets them answer "what did
this cost us" or "what can our agents reach" in one place, which are the two questions FR-1.3's
separation was supposed to make easier.

### LOW — mention only

**FR-15.20 (P2) — Saved queries are first class.** Any metric view is expressible as a stored,
versioned query over the ledger, living in the definition and exposed identically in CLI, API and
dashboard (FR-15.9). *Rationale:* no fixed metric set survives contact with a real team, and without
this "one more metric" is a code change.

**FR-15.21 (P2) — Visual evidence is browsable across runs.** The dashboard lists image and recording
evidence over a window, filterable by work item, agent, and gate outcome. *Rationale:* a reviewer
scanning what the factory showed them this week should not open runs one at a time.

**FR-15.22 (P2) — Per-conversation traces are a rendering of FR-15.16.** No separate mechanism; the
delegation tree keyed by originating conversation satisfies it.

---

## 4. What the video shows that we deliberately reject

Restated compactly, with a verdict on each.

| Video approach | Our position | Does the rejection stand? |
| --- | --- | --- |
| Cloud-hosted control plane, self-hosting as an option | Local-first; cloud is one topology (§6.3, PR-2) | **Yes.** P8 is the founding grievance. But it costs us the "running in a few minutes" story, which is why FR-23.7 is ranked HIGH rather than dismissed |
| Skills carry integration capability | Skills change knowledge, never access (FR-7.11, PR-4) | **Yes, unchanged.** This is the mechanism that makes prompt injection unable to widen a grant. Adopt the *vocabulary* lesson only: present grant and skill together in docs, enforce them separately |
| Factories for marketing, brand assets, search optimisation | Opinionated about the software delivery lifecycle (NG-4) | **Mostly.** FR-23.2's advisory mode already makes a non-code repository expressible with `tests-pass` and `regression-proven` marked `unenforceable`. Generalising further would cost us three-way agreement (FR-5.5) and `regression-proven` (FR-13.3), which the rest of the design rests on |
| Direct conversation with a specialist agent | Only the Conductor talks to the requester (FR-3.6) | **Partly.** The auditability property must stay; the user-experience rule is too strong. FR-3.15 relays instead — and FR-3.1's own rationale already concedes this is an interface argument (OQ-3) |
| Verification folded into implementation, via computer use | Verification is a separate stage with an independent actor (FR-22.1, FR-3.5a) | **Yes.** A builder demonstrating its own change is exactly the shared-blind-spot problem FR-3.5 exists to prevent. Adopt the *tool* (FR-31), not the placement |
| No merge boundary discussed | The factory never merges (NG-1, FR-25.6) | **Yes.** The video is silent on merge authority; our position is unaffected |

## 5. What the video does not show that we require

Recorded because a gap analysis that only runs one way misreads its own result. The transcript
contains no mention of: a living specification or any spec-drift mechanism; governed memory of any
kind (no lanes, provenance, promotion, decay, or poisoning containment); gates as blocking checks;
`regression-proven` or any equivalent; calibration or confidence; blast-radius contracts, checkpoints,
or rollback; budgets at any level; trust classes or prompt-injection containment; a hash-chained
ledger or replay; offline operation; retention, erasure, or legal hold; separation of duties; or any
post-merge outcome metric (the video's quality signals stop at the scorer). Its assurance story is
scorers plus benchmarks plus a self-improvement loop — the three mechanisms our FR-13 and FR-14
already specify, with our human-agreement calibration (FR-13.8), held-out isolation (FR-14.7) and
grader-capture defences (FR-14.7a) added on top.

The honest summary: the reference product is materially ahead of us on **surfaces** — the delegation
view, the legible recording, the conversation-centric dashboard, the discovery filed where the team
looks, and the improvement loop that listens to humans — and materially behind on **guarantees**. The
eight ABSENT items are almost all surface items, and that is the finding: our design has been written
by people thinking about correctness, and the gaps are where a reviewer, a manager, or an operator
has to *see* something.
