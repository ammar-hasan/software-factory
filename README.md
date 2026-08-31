# Software Factory

> A software factory takes in requests — bugs, feature asks, support escalations, alerts — and a
> coordinated fleet of specialist agents works them into a stream of reviewable, mergeable changes
> instead of a growing backlog.

**Software Factory** is an open, **local-first** implementation of that idea. It runs the same way on
a laptop, on a self-hosted box, or in the cloud, with the same definition files, the same agent
harness, and the same guarantees. No control plane you don't own. No vendor in the loop.

Licensed under [Apache-2.0](LICENSE).

---

## Why this one is different

Most agent platforms treat the model as the product and the scaffolding as plumbing. This project
inverts that. The bet here is:

> **A modest model inside an excellent harness beats a frontier model inside a poor one.**

So the harness is the product. Every agent that runs in this factory gets, by construction:

| Capability | What the agent gets |
| --- | --- |
| **Awareness** | A budgeted, assembled context pack: the living spec slice for its work, the repo's real structure, prior attempts at the same problem, the failure history of the code it's touching, and the conventions that apply — retrieved, not remembered. |
| **Tools** | A typed tool registry where deterministic tools replace guesswork: the harness computes what can be computed and reserves the model for what actually needs judgment. |
| **Confidence** | Explicit calibration. Agents state confidence with evidence, and the harness routes low-confidence work to verification instead of letting it through. |
| **Courage** | Cheap, total undo. Every run is a checkpointed, isolated workspace, so a bold approach costs a rollback, not an incident. Agents are told, and can verify, that the blast radius is bounded. |
| **Quality** | Verification is not advice. Gates run before a change is presentable, and the evidence is attached to the work item. |

The result is designed so that **lighter, cheaper models do master-class work** here — and escalate to
heavier models only where the harness proves escalation is warranted.

## The five subsystems

1. **Living Spec + Delta** — the spec is a versioned artifact in the repo, not a chat message. Every
   change to intent is a reviewable *delta* with provenance, and the spec, code, and tests are held
   in a checked three-way agreement.
2. **Self-regulating memory** — memory that organizes, polices, evolves, and filters itself:
   admission control on write, contradiction detection, decay and consolidation, provenance on every
   claim, and a quarantine lane for anything unverified.
3. **Evals + tests** — scorers as code, benchmark suites, regression gates, and a promotion policy
   that will not adopt a configuration change without evidence.
4. **Skill lifecycle** — skills are promoted from observed behaviour, evolved against evals, and
   merged, split, or sunset by policy rather than by accretion.
5. **Orchestration** — work items move through intake, triage, planning, building, reviewing, and
   verification, with humans holding the checkpoints that matter.

## Status

Early construction. See [`docs/PRD.md`](docs/PRD.md) for the full product requirements, and
[`docs/harness/`](docs/harness) for the harness specification.

## Quick start

```bash
pip install -e ".[dev]"
sf --help
```

## Contributing

Everything about this factory is defined as code, including the factory itself. Changes to the
factory's own definition go through the same review as changes to application code.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
