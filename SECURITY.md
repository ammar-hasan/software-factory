# Security Policy

This project executes untrusted content and handles credentials. That is not incidental —
it is what a software factory does — so the security posture is part of the product, not
a wrapper around it.

## Reporting a vulnerability

Report privately through
[GitHub's private vulnerability reporting](https://github.com/ammar-hasan/software-factory/security/advisories/new).
Please do not open a public issue for a security problem.

Include: what you found, how to reproduce it, what an attacker gains, and any suggested
fix. We will acknowledge within 5 working days, give an assessment within 10, and keep
you updated until it is resolved. We will credit you unless you ask us not to.

## Supported versions

Pre-1.0. Only the latest release receives fixes. This will change at 1.0.

## Threat model

The design assumes the **execution plane is hostile** (PRD §6.2). Repository content,
issue and comment text, CI output, tool-server descriptions, dependency metadata, and
model output derived from any of these are all untrusted input.

### What is structurally prevented

| Property | Mechanism |
| --- | --- |
| No text can widen an agent's access | Grants resolve from configuration before a run starts and are immutable during it (`harness/tools.py`) |
| Definition files are not writable from a workspace | The execution plane never has write access to the loaded definition |
| Untrusted content cannot become a cited convention | `untrusted` trust class is barred from the canon memory lane, and corroboration is computed over provenance sets, so reading one planted comment twice is one observation |
| Routing cannot be talked out of review | Non-skippable stages need a human decision; a transition justified only by untrusted input is refused |
| Secrets stay out of the workspace unless declared | Per-agent allowlist, default empty |
| Skills cannot grant capability | Lint fails on a skill body claiming to |

### What is *not* prevented, stated plainly

- **Taint tracking through a model.** We do not claim to trace attacker influence through
  a language model. Paraphrase, encoding, splitting, and influence-without-copying all
  defeat string-level taint tracking, and a control defeated by rewording is not a
  control. The guarantee is the grant table above; the untrusted-origin escalation on top
  of it will over-escalate and will miss laundering, and both are stated in FR-17.5a.
- **A model doing something wrong within its grants.** Gates bound classes of error, not
  all of them.
- **A malicious operator.** Someone who can edit the definition can change what agents
  reach. That is what definition review and separation of duties are for.
- **Supply chain beyond pinning.** We pin images and dependencies by digest. We do not
  audit what those digests contain.

### Reporting scope

In scope: anything that widens an agent's access without a definition change, any path
from untrusted content to a grant boundary, secret leakage into transcripts, evidence or
memory, ledger tampering that verification would miss, and any way to make a gate pass
without satisfying it.

Out of scope: a model producing a bad change within its grants (that is a quality problem
— open an issue), and vulnerabilities in a dependency without a path through this code.

## Running it safely

- Start with the default: deny egress, empty secret allowlist, review non-skippable.
- Run `sf audit` and read it. It reports what each agent can reach, and explicitly names
  the egress it *cannot* verify statically — setup commands can reach the network, and
  no static analysis of a shell command is complete.
- Give repository identities the minimum: checkout and push, never merge, never admin.
- Treat a rising violation rate as a security signal, not as noise.
