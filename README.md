# Software Factory

> A software factory takes in requests — bugs, feature asks, support escalations, alerts —
> and a coordinated fleet of specialist agents works them into a stream of reviewable,
> mergeable changes instead of a growing backlog.

**Software Factory** is an open, **local-first** implementation of that idea. It runs the
same way on a laptop, on a self-hosted box, or in the cloud, with the same definition
files, the same agent harness, and the same guarantees. No control plane you don't own.

Licensed under [Apache-2.0](LICENSE).

---

## Why this one is different

Most agent platforms treat the model as the product and the scaffolding as plumbing. This
inverts that. The bet is:

> **A modest model inside an excellent harness beats a frontier model inside a poor one.**

That is a hypothesis, not a slogan, and [§11.2 of the PRD](docs/PRD.md) is the
pre-registered experiment written to falsify it. Every subsystem below exists because it
is one of the mechanisms that claim depends on.

| | What the agent gets |
| --- | --- |
| **Awareness** | A budgeted, cited, deterministically-assembled context pack: the spec slice that governs the change, the shape of the code, what was tried here before, what breaks around here. It retrieves rather than remembers. |
| **Tools** | A typed registry where deterministic tools replace guesswork. Anything computable *is* computed; the model is spent on judgement. |
| **Confidence** | Calibration scored against outcomes. Confidence with no cited evidence is rewritten to zero before anything downstream reads it. |
| **Courage** | A machine-checked blast-radius contract, stated affirmatively. An agent that doesn't know undo is free picks the timid approach every time. |
| **Quality** | Gates that block, and evidence that resolves claims rather than accompanying them. |

## The five subsystems

Each has a design document and a test matrix it satisfies.

| Subsystem | Design | The idea in one line |
| --- | --- | --- |
| **Living Spec + Delta** | [`living-spec.md`](docs/harness/living-spec.md) | Intent that can block a build, because drift is a digest comparison rather than a model's opinion. |
| **Memory Fabric** | [`memory.md`](docs/harness/memory.md) | Four lanes with earned promotion, transitive poisoning containment, and an eight-stage retrieval filter. |
| **Skill lifecycle** | [`skills.md`](docs/harness/skills.md) | Promote, evolve, merge, split, sunset — all evidence-gated, because a library that only grows becomes a junk drawer. |
| **Evals + gates** | [`evals.md`](docs/harness/evals.md) | Gates block one run, scorers sample many, benchmarks compare configurations. Conflating them is how assurance becomes theatre. |
| **Loom harness** | [`HARNESS.md`](docs/harness/HARNESS.md) | Packs, tools, contracts, and an escalation ladder that requires a recorded trigger. |

## Quick start

```bash
pip install -e ".[dev]"

sf init myfactory --name payments --owner acme --repo payments-service
sf validate myfactory     # structure and cross-references
sf lint myfactory         # advisory checks
sf plan myfactory         # the resolved configuration for every agent
sf audit myfactory        # what each agent can reach, and where data can go
```

`sf init` writes a definition that validates and lints with **zero warnings**, offline,
with no account. CI checks that on every run, because a scaffold that emits warnings
teaches every new user that warnings are normal.

### What's in a definition

```
factory.yaml               repositories, tier ladder, execution defaults
agents/<name>/agent.md     frontmatter config, Markdown body as the role prompt
automations/               what starts work, and the filters that decide which events
runners/                   the compute a run executes on
scorers/                   sampling classifiers over completed runs
skills/                    versioned procedures agents can load
policy/                    stages, gates, budgets, memory policy
```

Everything the factory does is described by these files, so changing its behaviour is a
change you can review, diff, and revert — including changes the factory proposes to
itself.

## Status

Early construction, and honest about it. What exists and is tested:

| | |
| --- | --- |
| Definition layer | Loader, whole-tree atomic validation, cross-file lint, inheritance resolution, JSON Schema export |
| Ledger | Append-only, hash-chained, tamper-evident, with `sf ledger verify` |
| Living Spec | Content-addressed anchors, five agreement states, deltas with impact reports |
| Memory Fabric | Admission control, source-disjoint promotion, policy pass, retrieval pipeline |
| Skill registry | Selection quality metrics and all five lifecycle operations |
| Assurance | Baseline gates including `regression-proven`, evidence bundles, scorers, benchmarks |
| Harness | Pack assembly, typed tool registry with grant enforcement, blast radius, routing ladder |
| Orchestrator | Work items, stage machine with bounded routing authority |

Not built yet: the executors, the turn loop against real providers, the integrations, and
the dashboard. See the [milestone plan](docs/PRD.md).

## Reviews

The design was reviewed adversarially before it was implemented, and the reports are kept
unedited in [`docs/reviews/`](docs/reviews) — including the findings we **declined** to
act on, because a review record that preserves only the accepted findings is not a review
record.

Three findings changed the design materially:

- `regression-proven` was satisfiable by an import error at the parent commit — the one-line
  bypass that a small model produces by default.
- Memory corroboration was defined over *runs*, so two agents reading the same planted
  issue comment laundered untrusted text into canon.
- The acceptance experiment had strawman controls and exempted exactly the criteria most
  likely to fail.

[Appendix C of the PRD](docs/PRD.md) maps every finding to its disposition.

## Decisions

The choices that would be expensive to reverse are recorded in
[`docs/adr/`](docs/adr), each with the condition that would change our mind. A decision
with no falsification condition is a preference.

## Development

```bash
ruff format . && ruff check . && mypy && pytest
python scripts/run_offline_tests.py   # the whole suite, with the network denied
```

All four must be clean. See [CONTRIBUTING.md](CONTRIBUTING.md) for the standing positions
you'd otherwise discover in review, and [SECURITY.md](SECURITY.md) for the threat model —
including what it does **not** defend against.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
