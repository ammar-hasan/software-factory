# 3. Grants come from configuration, never from text

**Status:** Accepted · **Date:** 2026-08-31

## Context

Agents in this system read text an attacker can write: issue bodies, review comments,
chat messages, file contents, dependency metadata, tool-server descriptions, and the
output of other models that read all of the above.

The tempting defence is to trace attacker influence through the model — mark untrusted
regions, and refuse tool calls whose arguments came from one. The PRD's first draft said
exactly that. It is not implementable: paraphrase, encoding, splitting, and
influence-without-copying all defeat string-level taint tracking, and a control that is
defeated by rewording is not a control.

## Decision

Authority is a property of *configuration*, not of text.

- An agent's tools, effect classes, secrets, network policy, and external actions are
  resolved from the definition before the run starts and are immutable for its duration.
- The execution plane cannot write the definition, so nothing a run does can widen what
  the next turn may reach.
- An ungranted call is refused and recorded as a violation. An ungranted *effect* is
  recorded as escalating, because asking for `exec` when only `read` was granted targets
  a capability boundary rather than mis-typing a name.
- Skills change what an agent knows, never what it can reach; a skill body claiming
  otherwise fails lint.

On top of that sits a weaker layer: where a run's only justification for a
boundary-crossing action traces to untrusted input, it escalates to a human. We document
that this layer over-escalates and misses laundering, so nobody mistakes it for the
guarantee.

## Consequences

Some legitimate work needs a human to widen a grant, and that is friction we accept. In
exchange, "what can this factory reach?" is answerable from the definition alone, without
running anything — which is what `sf audit` does.

## What would change our mind

A method for bounding attacker influence through a model that holds under paraphrase and
encoding. We are not aware of one.
