# Reference

Generated from the definitions themselves, by
[`scripts/generate_reference.py`](../../scripts/generate_reference.py).

Nothing here is written by hand. NFR-4.3 and FR-30.4 ask for reference documentation
*produced from* the same definitions the validator uses, and the reason is drift: a page
written alongside the code and describing a gate that was later renamed reads perfectly and
sends every reader to a name that does not exist. There is no way to notice that by reading
it. Generating the page turns the drift into a diff, and CI turns the diff into a failure.

| Page | Source of truth |
| --- | --- |
| [Gates](gates.md) | `evals.gates.BASELINE_GATES` |
| [Stages](stages.md) | `orchestrator.DEFAULT_TRANSITIONS`, `DEFAULT_ORDER`, `DEFAULT_NON_SKIPPABLE` |
| [Tools](tools.md) | `runtime.tools.BUILTIN_TOOL_EFFECTS` |
| [Capabilities](capabilities.md) | `identity.principals.Capability`, `PERSON_ONLY` |
| [Data classes](data-classes.md) | `governance.DEFAULT_CLASSIFICATION` |
| [Error codes](errors.md) | `errors.ErrorCode` |
| [CLI](cli.md) | the `typer` app itself |

Schemas are not duplicated here: `sf schema <kind>` emits the JSON Schema for every document
type, and a copy in Markdown would be a second thing to keep true.

To regenerate:

```
python scripts/generate_reference.py
```

To check without writing, which is what CI does:

```
python scripts/generate_reference.py --check
```
