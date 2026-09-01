# payments factory

This directory is the complete definition of a software factory. Everything the factory
does is described by these files, so a change to its behaviour is a change you can
review, diff, and revert.

```
factory.yaml            the root document: repositories, ladder, defaults
agents/<name>/agent.md  one agent: frontmatter config, Markdown body as its role prompt
automations/            what starts work, and the filters that decide which events
runners/                the compute a run executes on
scorers/                sampling classifiers over completed runs
skills/                 versioned procedures agents can load
policy/                 stages, gates, budgets, memory policy
```

## Try it

```bash
sf validate .      # structure and cross-references
sf lint .          # advisory checks
sf plan .          # the fully resolved configuration for every agent
sf audit .         # what each agent can reach, and where data can go
```

## Two things worth knowing

**Instructions never grant access.** An agent's prompt says what to do. What it can
*reach* comes from its grants. Editing a prompt can never widen what an agent is able
to do, which is why prompts are safe to iterate on.

**Policy is not enforcement.** The checkpoints in `policy/` are your team's workflow.
Merge authority lives in your repository's branch protection, not here.
