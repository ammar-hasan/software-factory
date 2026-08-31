# Adversarial Review 2 — Observability, Harness Conversation, Recording, Executors, Egress

| Field | Value |
| --- | --- |
| Scope | `observability/{metrics,views,dash}.py`, `harness/conversation.py`, `evals/recording.py`, `runtime/executors.py`, `definition/egress.py`, the new ledger-writing code in `orchestrator/coordinator.py`, and the six matching test modules |
| Method | Every finding reproduced by running the real code from `/home/user/software-factory`. Exact commands and their output are inline. Nothing is reported on reading alone except where explicitly labelled. |
| Findings | 3 CRITICAL · 14 MAJOR · 10 MINOR (27 total) · 15 tests that assert the wrong thing or pass for a reason other than their name |
| Date | 2026-08-31 |

**Reading rule.** CRITICAL means the system states something untrue that a person would act on, or leaks a
secret. MAJOR means a declared control does not exist, or a number is wrong in a way that flatters.
MINOR means it is wrong but bounded.

**Note on a moving tree.** Other sessions were editing this repository during the review. `egress.py` was
rewritten and committed mid-review as `5e51c59` ("real model providers, resolved model egress, a precise
offline guard"), which upgraded `_from_model_tiers` to resolve providers to hosts. Both egress findings
below were re-run against `5e51c59` after that landed and still reproduce; their line numbers are from
`5e51c59`. Every other file in scope is unmodified at `b032d7e`/`5e51c59`, and all other findings were
reproduced against that state.

---

## Findings index

| ID | Location | Finding | Severity | Verified |
| --- | --- | --- | --- | --- |
| O1 | `observability/metrics.py:347` | `changes_opened` reports an available `0` on an empty ledger — the exact PR-9 failure the module's own docstring forbids, for a metric that needs the same git-host adapter `changes_merged` declares | CRITICAL | yes |
| O2 | `observability/metrics.py:296-307` | `gate_pass_rate` de-duplicates on `(work item, gate name)` and ignores the stage, so a gate that passes at BUILD and **fails at REVIEW** is reported as a 100% pass rate | CRITICAL | yes |
| O3 | `runtime/executors.py:182` | The container executor puts secret **values** on the `docker run` command line, world-readable in `ps` — the precise thing the comment above it says it avoids. A test asserts this as correct | CRITICAL | yes |
| O4 | `runtime/executors.py:109,311` | `ContainerExecutor` constructs on the *presence* of a docker binary, not on a working daemon. On this machine it constructs and then reports every run as the caller's command failing | MAJOR | yes |
| O5 | `metrics.py:356,378` vs `coordinator.py:249` | `changes_opened` and `cost_per_change` fold on a `to == "HANDOFF"` transition the coordinator never writes. The cost estimate never computes on real data | MAJOR | yes |
| O6 | `coordinator.py:377-400` | With no ladder declared, `MODEL_CALLED` records a synthetic tier's `model: "local-model"` and `costUnits: 0.0` — a model name and a cost the run did not establish | MAJOR | yes |
| O7 | `metrics.py:257-267`, `coordinator.py:337` | The work/evaluation/benchmark/improvement split is dead: nothing anywhere writes a `purpose` other than `"work"`, so `measurementShare` is structurally always 0 | MAJOR | yes |
| O8 | `definition/egress.py:191` | A factory declaring a hosted model (or a third-party harness) on `agentDefaults` with no ladder is reported `offlineCapable: true` with an empty destination list — a silent omission, not an indeterminate entry | MAJOR | yes |
| O9 | `definition/egress.py:238-240` | MCP servers are flattened into one dict by name, so three distinct declared tool servers are reported as one | MAJOR | yes |
| O10 | `observability/dash.py:52` | `?days=abc` and `?days=99999999` crash the request with an unhandled traceback; `?days=-1` returns HTTP 200 with an inverted window and every metric silently empty | MAJOR | yes |
| O11 | `observability/dash.py:117-120` | The CSP has no `connect-src`, so `default-src 'none'` blocks the page's own `fetch` — the dashboard never loads data — while `script-src 'unsafe-inline'` permits injected inline handlers | MAJOR | header yes; browser enforcement not executed |
| O12 | `observability/dash.py:226` | Stored HTML injection: hostile **model output** reaches a ledger payload and is concatenated into `innerHTML` unescaped | MAJOR | yes (end to end) |
| O13 | `runtime/executors.py:249-265` | `SshWorkerExecutor` silently drops `policy.environment()`, including declared secrets. The remote command runs with none of the run's environment | MAJOR | yes |
| O14 | `runtime/executors.py:137,252` | Both new executors bypass `LocalExecutor`'s cwd-writability guard: `cwd=/etc` is refused locally and accepted by container and ssh | MAJOR | yes |
| O15 | `runtime/executors.py:70` | `ContainerImage` accepts a bare image name (`ubuntu`, `python`) as "pinned"; it resolves to `:latest` at runtime | MAJOR | yes |
| O16 | `harness/conversation.py:177-228` | "Bounded state" bounds the note *count* and never the note *size*. A single 2 MB note is carried into every later run and `compact` returns "nothing to do" | MAJOR | yes |
| O17 | `coordinator.py:613-616` | `DECISION`, `ATTEMPT`, `CONSTRAINT` and `ARTIFACT` notes are never produced: no stage schema asks the model for `decisions` or `attempted`. `ATTEMPT`'s largest-budget protection guards a kind nothing creates | MAJOR | yes |
| O18 | `metrics.py:232-240` | Nothing computes `changes_merged`, `autonomy` or `cycle_time_to_merge`. Declaring `--integration git-host` makes the three rows vanish from the dashboard instead of producing numbers | MINOR | yes |
| O19 | `coordinator.py:610` | `transcript_refs` accumulates duplicate work-item ids, not transcript references, and no transcript is ever persisted — FR-29.1's "retrievable full history" does not exist | MINOR | yes |
| O20 | `metrics.py:318-324` | `escalation_rate` divides distinct escalated **work items** by the count of **runs**. Three runs that all escalated report 33% | MINOR | yes |
| O21 | `coordinator.py:565` | The diff evidence digest is `str(hash(diff))` — `PYTHONHASHSEED`-dependent, different in every process | MINOR | yes |
| O22 | `conversation.py:201` | A caller-supplied partial budget silently uses a magic `10` for the kinds it omits rather than `KIND_BUDGET` | MINOR | yes |
| O23 | `coordinator.py:777-788` | `_as_texts` turns a `null` list entry into a carried note whose text is the literal string `"None"`, and drops a dict-shaped field with no record | MINOR | yes |
| O24 | `conversation.py:151` | The carried-state digest ignores the stage a note came from, so two different carried states digest identically | MINOR | yes |
| O25 | `coordinator.py:593`, `recording.py:190` | `expects_visual`'s `user_facing` parameter is hardcoded `True` by its only production caller; `RecordingPolicy.terminal_always` is read nowhere | MINOR | yes |
| O26 | `runtime/executors.py:241` | An ssh worker's refusal of `network: allowlist` says "the local executor cannot enforce…", naming the wrong component | MINOR | yes |
| O27 | `metrics.py:135-137` | `measurement_share` returns `0.0` for zero runs, which `dash.py` renders as "0% measurement" for a factory that has never run | MINOR | yes |

---

## CRITICAL

### O1 — `changes_opened` is an available zero for something nothing established

**File:** `src/software_factory/observability/metrics.py:347-360`

**What the code does.** `_changes_opened` counts distinct work items with a `WORK_ITEM_TRANSITION` whose
payload `to` is `"HANDOFF"`, and returns `Measure(name="changes_opened", value=float(len(opened)), ...)`
unconditionally. There is no `insufficient` branch and `changes_opened` is not in `REQUIRES_INTEGRATION`
(`metrics.py:201-205`), which lists `changes_merged`, `autonomy` and `cycle_time_to_merge` as needing
`git-host`.

**Reproduction.**

```
$ python3 -c "
from software_factory.observability import compute
for m in compute([]).measures: print(' ', m.render())"
   gate_pass_rate: [insufficient_data] no gates ran in this window
   escalation_rate: [insufficient_data] no runs in this window
   rework_rate: [insufficient_data] no work items moved in this window
   cost_per_change: [insufficient_data] no work item both incurred cost and reached handoff in this window
   changes_opened: 0 changes
   autonomy: [unavailable] no git-host adapter is configured, so this cannot be observed; reporting zero here would read as a factory that produces none
   changes_merged: [unavailable] no git-host adapter is configured, so this cannot be observed; reporting zero here would read as a factory that produces none
   cycle_time_to_merge: [unavailable] no git-host adapter is configured, so this cannot be observed; reporting zero here would read as a factory that produces none
```

The same holds after a real three-stage coordinator run that passed every gate
(`scratchpad/r/run_real.py`, full script retained): `changes_opened: 0 changes | availability=available |
value=0.0`, alongside `changes_merged: [unavailable] … reporting zero here would read as a factory that
produces none`.

The zero propagates into `views._trend`, whose docstring promises the opposite:

```
--- overview() trend ---
{"runs": 3, "gate_pass_rate": null, ..., "changes_opened": 0.0, "changes_merged": null, ...}
```

`changes_opened` gets a hard trend of `0.0` — "no change" — because it was AVAILABLE-0 in both windows.

**Why it matters.** This is the single failure the module's own docstring names, in the one metric that was
not guarded: *"'Changes merged: 0' reads as a factory that merges nothing."* Opening a change requires the
same git-host adapter merging one does; without an adapter the factory cannot know whether a change was
opened. A factory that has never run reports `changes opened: 0` as an established fact, sitting one row
above three metrics that correctly refuse to guess. A reader sees three honest refusals and one number and
concludes the number is real.

**Fix.** Add `"changes_opened": "git-host"` to `REQUIRES_INTEGRATION` and delete `_changes_opened` from the
unconditional list, or — if the intent is genuinely "work items that reached handoff" — rename it
(`work_items_handed_off`), return `insufficient(...)` when no `WORK_ITEM_TRANSITION` entries exist at all,
and state in the unit that it counts stage transitions and not changes on a git host.

---

### O2 — a gate failure at REVIEW is reported as a 100% pass rate

**File:** `src/software_factory/observability/metrics.py:290-315`

**What the code does.** `_gate_pass_rate` keys its "first attempt only" de-duplication on
`(entry.subject, payload["gate"])`. `entry.subject` is the *work item id* for every gate the coordinator
writes (`coordinator.py:421-427`), and the stage is in the payload but is not part of the key. So a gate
name evaluated at more than one stage is treated as a retry of the first evaluation and every later
evaluation is discarded — including its outcome.

**Reproduction.** Exactly the entry shape the coordinator writes:

```
$ python3 - <<'PY'
import tempfile; from pathlib import Path
from software_factory.ledger import Ledger, EntryType
from software_factory.observability import compute
l = Ledger(Path(tempfile.mkdtemp())/"l.jsonl")
l.append(EntryType.GATE_EVALUATED, actor="builder", subject="wi-1",
         payload={"stage": "BUILD", "gate": "secret-clean", "outcome": "pass"})
l.append(EntryType.GATE_EVALUATED, actor="critic", subject="wi-1",
         payload={"stage": "REVIEW", "gate": "secret-clean", "outcome": "fail"})
m = compute(l.read()).measure("gate_pass_rate")
print("gate_pass_rate ->", m.render(), "| sample:", m.sample)
PY
gate_pass_rate -> gate_pass_rate: 1 share | sample: 1
```

This is not a synthetic shape. A real run of the reference scaffold discards six of seventeen evaluations
(`scratchpad/r/gates.py`):

```
stage    gate                       outcome   counted?
TRIAGE   calibration-present        pass      yes
TRIAGE   blast-radius-clean         pass      yes
BUILD    calibration-present        pass      NO - discarded as a retry
BUILD    blast-radius-clean         pass      NO - discarded as a retry
BUILD    secret-clean               pass      yes
...
REVIEW   secret-clean               pass      NO - discarded as a retry
REVIEW   tests-pass                 unenforceable NO - discarded as a retry
...
gate_pass_rate: gate_pass_rate: 0.8182 share sample: 11 of 17 evaluations
```

**Why it matters.** `secret-clean` at REVIEW inspects a different diff from `secret-clean` at BUILD. It is
not a retry; it is a different check of different content. The fold silently drops its outcome, and it drops
it in the flattering direction: the *first* evaluation is the one that runs against the smallest diff and is
most likely to pass. A gate that catches a leaked credential at review contributes nothing to the number the
dashboard prints. `blast-radius-clean` and `calibration-present` are discarded at two stages out of three in
every ordinary run.

**Fix.** Key on `(subject, stage, gate)`. The coordinator already writes `stage` into the payload; the fold
just does not read it. If "first attempt" is meant to survive *repair loops within a stage*, add an explicit
attempt counter to the payload and key on that — the stage is not a retry.

---

### O3 — the container executor puts secret values on the host command line, and a test blesses it

**File:** `src/software_factory/runtime/executors.py:178-182`, test at `tests/test_executors.py:116-128`

**What the code does.**

```python
for name, value in sorted(self.policy.environment().items()):
    # Values are passed through the environment rather than the command line: a
    # secret on a command line is visible in the host's process list to anyone who
    # can run `ps`, and redaction at capture does not reach that.
    args += ["--env", f"{name}={value}"]
```

The comment describes a control the code does not implement. `--env NAME=VALUE` places the value in the
`docker` client's own argv. `SandboxPolicy.environment()` merges `self.secrets` verbatim
(`runtime/executor.py:133`), so declared secret values land there.

**Reproduction.**

```
$ python3 - <<'PY'
import tempfile; from pathlib import Path
from software_factory.runtime.executor import SandboxPolicy
from software_factory.runtime.executors import ContainerExecutor, ContainerImage
pol = SandboxPolicy(workspace=Path(tempfile.mkdtemp()), wall_clock_s=20,
                    secrets={"SF_TOKEN": "sk-live-supersecret"})
ex = ContainerExecutor(pol, ContainerImage("ghcr.io/acme/builder:1.0"), runtime="/usr/bin/docker")
w = ex._wrap(["pytest"], cwd=None)
print(w)
print("secret in argv:", any("sk-live-supersecret" in p for p in w))
PY
['/usr/bin/docker', 'run', '--rm', '--user', '1000:1000', '--security-opt', 'no-new-privileges',
 '--cap-drop', 'ALL', '--memory', '4096m', '--pids-limit', '512', '--volume', '/tmp/…:/tmp/…',
 '--workdir', '/tmp/…', '--network', 'none', '--env', 'HOME=/tmp/…', '--env', 'PATH=…',
 '--env', 'PWD=/tmp/…', '--env', 'SF_TOKEN=sk-live-supersecret', 'ghcr.io/acme/builder:1.0', 'pytest']
secret in argv: True
```

That argv is handed to `subprocess.Popen` by the inner `LocalExecutor` (`scratchpad/r/secret_ps.py` patches
`Popen` to capture the final vector):

```
final argv handed to the OS:
  ['/bin/sh', '-c', 'ulimit -t 900; ulimit -v 4194304; ulimit -c 0; exec "$@"', 'sh',
   '/bin/echo', 'run', '--rm', …, '--env', 'SF_TOKEN=sk-live-supersecret', 'ghcr.io/acme/builder:1.0', 'pytest']
secret in that argv: True
```

and process argv is world-readable while the process lives:

```
$ python3 -c "
import os, subprocess, time; from pathlib import Path
p = subprocess.Popen(['python3','-c','import time;time.sleep(2)','--env','SF_TOKEN=sk-live-supersecret'])
time.sleep(0.3)
print('mode:', oct(os.stat(f'/proc/{p.pid}/cmdline').st_mode)[-3:])
print(subprocess.run(['ps','-ww','-p',str(p.pid),'-o','args='],capture_output=True,text=True).stdout.strip())"
mode: 444
python3 -c import time;time.sleep(2) --env SF_TOKEN=sk-live-supersecret
```

**Why it matters.** Every unprivileged local user, every other container sharing the host PID namespace, and
every process-listing tool sees the run's credentials. `LocalExecutor._redact` strips secret values from
captured output, so the project's one secret-hygiene control is applied at exactly the place the secret does
not appear, and not at the place it does. `sf audit` would report the container executor as the *stronger*
isolation choice while it is the only executor that publishes the secret.

The test that should catch this asserts the bug:

```python
def test_secrets_are_passed_by_environment_not_on_the_command_line(tmp_path: Path) -> None:
    """A secret on a command line is visible in the host's process list to anyone who
    can run `ps`, and redaction at capture does not reach that."""
    ...
    assert "--env" in wrapped
    assert any(part.startswith("SF_TOKEN=") for part in wrapped)
```

The second assertion is the defect, stated as the requirement.

**Fix.** Pass `--env NAME` (no value) so the runtime inherits the variable from the client process's
environment, which `LocalExecutor` already sets via `env=self.policy.environment()`; or write an env-file to
a mode-0600 path in the workspace and pass `--env-file`. Then invert the test: assert that no argv element
contains a secret *value*, iterating `policy.secrets.values()`.

---

## MAJOR

### O4 — the container executor treats a docker binary as a container runtime

**File:** `src/software_factory/runtime/executors.py:109-118` and `:311-316`

**What the code does.** `_detect_runtime()` returns the first of `docker`/`podman` found by `shutil.which`.
`__init__` refuses only when that is `None`. A binary with no reachable daemon passes.

**Reproduction** — on this machine, which has `/usr/bin/docker` and no daemon:

```
$ python3 - <<'PY'
import tempfile, subprocess; from pathlib import Path
from software_factory.runtime import executors as m
from software_factory.runtime.executor import SandboxPolicy
print("_detect_runtime() ->", m._detect_runtime())
print("docker info exit  ->", subprocess.run(["docker","info"],capture_output=True).returncode)
ex = m.ContainerExecutor(SandboxPolicy(workspace=Path(tempfile.mkdtemp()), wall_clock_s=10),
                         m.ContainerImage("ghcr.io/acme/b:1.0"))
r = ex.run(["echo","hello"])
print("run ->", r.exit_code, "| command", r.command, "| stderr", repr(r.stderr[:90]))
PY
_detect_runtime() -> /usr/bin/docker
docker info exit  -> 1
constructed with no explicit runtime: /usr/bin/docker
run result -> exit 1 | command ('echo', 'hello') | stderr 'failed to connect to the docker API at unix:///var/run/docker.sock; check if the path is c'
```

**Why it matters.** The module's opening docstring says each executor "either provides what it claims or
raises at construction with a remediation". This one constructs and then reports the *caller's* command as
having failed — `ContainerExecutor.run` deliberately rewrites `command=tuple(command)` (`:142`), so a gate
reading the `CommandResult` sees `echo hello` exiting 1, not "there is no container runtime". A run that
never executed anywhere is indistinguishable from a run whose command failed. Nothing in the result says the
isolation the factory declared was never applied.

The project already knows the right check and put it in the wrong file. `tests/test_parity.py:50-70` probes
`docker info` and its docstring says the alternative is *"precisely the 'presence is not capability' mistake
the whole project keeps finding elsewhere"* — while the code it is testing makes exactly that mistake.

**Fix.** Move `test_parity._working_runtime`'s probe into `_detect_runtime`, and validate an explicitly
passed `runtime=` the same way. Raise `ExecutorError` at construction naming the daemon, not the command.

---

### O5 — the cost estimate folds on a transition the coordinator never writes

**Files:** `observability/metrics.py:356` and `:378`, against `orchestrator/coordinator.py:245-260`

**What the code does.** Both `_changes_opened` and `_cost_per_change` select
`entry.type is WORK_ITEM_TRANSITION and entry.payload.get("to") == "HANDOFF"`. The coordinator writes
exactly **one** `WORK_ITEM_TRANSITION` per `run()`, in the `finally` block, with `to` set to whatever stage
the item happens to be in at the end. `_default_path` (`:265-275`) never includes `Stage.HANDOFF`.

**Reproduction** — a full three-stage run of the reference scaffold (`scratchpad/r/run_real.py`):

```
stages: ['TRIAGE', 'BUILD', 'REVIEW'] final stage: REVIEW
--- ledger entry types written ---
Counter({'gate.evaluated': 17, 'pack.assembled': 3, 'run.started': 3, 'model.called': 3,
         'run.finished': 3, 'work_item.created': 1, 'work_item.transition': 1})
--- WORK_ITEM_TRANSITION payloads ---
{"backwards": false, "blocker": null, "stage": "REVIEW", "to": "REVIEW", "workClass": "chore"}
--- metrics report ---
  cost_per_change: [insufficient_data] no work item both incurred cost and reached handoff in this window
  changes_opened: 0 changes
```

Three `MODEL_CALLED` entries carrying `costUnits` were written and none of them can ever be attributed.

**Why it matters.** `cost_per_change` is the metric FR-15.4 exists for, and on real data it is permanently
`insufficient_data`. Its own error text — "no work item both incurred cost and reached handoff" — reads as an
observation about the factory's throughput when it is a statement about a key nobody writes. Separately,
only one transition per `run()` reaches the ledger, so the intermediate stage moves (TRIAGE→BUILD→REVIEW) are
never recorded at all: FR-15.2's "all derived state is rebuildable from the ledger" is false for the stage
machine, and `payload["stage"]` and `payload["to"]` are the same value, so the record cannot answer "where
did it come from" either.

**Fix.** Emit a `WORK_ITEM_TRANSITION` per `StageMachine.advance` with `from` and `to`, at the point the
transition happens, and let the `finally` block record only the terminal state. Then confirm that something
in the system actually moves an item to `HANDOFF`, or change the metric to fold on the stage that exists.

---

### O6 — `MODEL_CALLED` records a model name and a cost the run did not establish

**File:** `src/software_factory/orchestrator/coordinator.py:317-322` and `:377-400`

**What the code does.** `FactoryDocument` makes `ladder` optional and requires `agentDefaults` to declare
exactly one of `model`, `tier` or `harness`. When there is no ladder, `_run_stage` builds
`self._synthetic_ladder()` — a single hardcoded tier `local-small` / `provider: local` / `model:
local-model` / no prices — and `MODEL_CALLED` then reports `active_tier.model` and
`round(run.spend.cost_units, 6)` from it. `agentDefaults.model` is never consulted.

**Reproduction** (`scratchpad/r/no_ladder.py`, a scaffold with the ladder removed and
`agentDefaults: {model: claude-opus-4-20250514}`):

```
ladder: None | agentDefaults.model: claude-opus-4-20250514
MODEL_CALLED payload: {"stage": "TRIAGE", "tier": "local-small", "model": "local-model", "costUnits": 0.0, "inputTokens": 150}
MODEL_CALLED payload: {"stage": "BUILD",  "tier": "local-small", "model": "local-model", "costUnits": 0.0, "inputTokens": 150}
MODEL_CALLED payload: {"stage": "REVIEW", "tier": "local-small", "model": "local-model", "costUnits": 0.0, "inputTokens": 150}
```

And when such zeros *do* get attributed, they are presented as an estimate:

```
$ python3 - <<'PY'   # three model calls at the scaffold's declared prices (zero), three handoffs
...
cost_per_change: cost_per_change: 0 cost units (estimate)
  estimate flag: True | value: 0.0 | sample: 3
  excludes: ('provider billing adjustments', 'local compute and electricity', 'human review time',
             'runs that had not reached handoff when the window closed')
PY
```

**Why it matters.** The ledger is the system of record for spend, and here it asserts a model name that
appears nowhere in the definition and a cost of zero for a run that called a hosted model. The comment above
the write says the entry exists so the economics layer does not "report a factory running for free" — and
this is the path on which it does exactly that. The `excludes` tuple lists four things the estimate leaves
out and does not list the one that produced the zero: *the ladder declares no prices*. A zero that means
"nobody configured a price" is rendered identically to a zero that means "this was free".

**Fix.** Refuse to synthesise a ladder — a definition with no ladder and a declared `model`/`harness` should
raise with a remediation, in the same spirit as `cloud_executor`. Failing that, mark the synthetic tier and
record `model: null, costUnits: null, reason: "no ladder declared"`, and make `_cost_per_change` return
`insufficient` when every contributing tier priced at zero, with "no tier declares a price" as the reason.

---

### O7 — the run-count split can only ever say "work"

**Files:** `observability/metrics.py:249-287`, `orchestrator/coordinator.py:329-340`

**What the code does.** `_count_runs` switches on `payload["purpose"]` into work / evaluation / benchmark /
improvement. The only writer of `RUN_STARTED` in the entire source tree hardcodes `"purpose": "work"`.

**Reproduction.**

```
$ grep -rn "RUN_STARTED" --include=*.py src/ | grep -v ledger/entry.py
src/software_factory/orchestrator/coordinator.py:330:            EntryType.RUN_STARTED,
src/software_factory/observability/metrics.py:257:        if entry.type is EntryType.RUN_STARTED:
$ grep -n '"purpose"' src/software_factory/orchestrator/coordinator.py
337:                "purpose": "work",
```

and from the real run:

```
runs.as_dict(): {"total": 3, "work": 3, "evaluation": 0, "benchmark": 0, "improvement": 0,
                 "measurementShare": 0.0, …,
                 "note": "Run counts include evaluation, benchmark and improvement runs. A rising
                          total with flat output can be measurement activity rather than work."}
```

**Why it matters.** FR-15.5's requirement is the split, and `dash.py:202` renders
`${Math.round(r.measurementShare * 100)}% measurement` next to the total. That cell is structurally always
"0% measurement", printed beside a note explaining why the reader should care about the number. If the
evaluation, benchmark or improvement subsystems ever run through the coordinator, their runs will be counted
as work — the mis-attribution is not merely absent, it is pre-committed in the wrong direction.

**Fix.** Make `purpose` a `Coordinator.run` parameter (defaulting to `work`), pass it from the eval /
benchmark / improvement entry points, and have `_count_runs` treat an unrecognised purpose as its own bucket
rather than folding it into `work` — a `case _: work += 1` default means a typo in a caller inflates the
output figure.

---

### O8 — a factory that calls a hosted model on every run is reported as offline-capable

**File:** `src/software_factory/definition/egress.py:166-234`, specifically `if ladder is None: return []`
at `:191`

**What the code does.** `_from_model_tiers` reads `definition.factory.ladder` only. `FactoryDocument`
permits — and `_defaults_declare_a_model` *requires* — `agentDefaults` to name a `model` or a `harness`
instead, with no ladder at all. Neither is enumerated, and neither is marked indeterminate.

**Reproduction** (`scratchpad/r/egress.py`, re-run against the current working tree):

```
=== A. agentDefaults declares a hosted MODEL directly, no ladder ===
  agentDefaults.model = claude-opus-4-20250514  ladder = None
  offlineCapable: True  destinations: []

=== B. agentDefaults declares a third-party HARNESS, no ladder ===
  agentDefaults.harness = type='claude-code' model='claude-opus-4-20250514' … auth=HarnessAuth(source=managedSecret, secret_name='anthropic-key')
  offlineCapable: True  destinations: []
```

**Why it matters.** This is the dangerous case the module's docstring names: *"an egress report that
silently omits what it cannot see is worse than none, because it reads as a complete list."* Case B is the
worst version — the definition declares a managed *secret* for a hosted harness, which is unambiguous
evidence of an outbound call, and `sf audit --egress` answers `offlineCapable: true` with an empty list. The
recent upgrade to `_from_model_tiers` (resolving providers to hosts through the provider registry) made the
ladder path more precise and left the two non-ladder paths at zero.

**Fix.** Enumerate `factory.agent_defaults` and every agent's and automation's `execution` block: a `model`
resolves through the same provider registry when its provider can be inferred and is `INDETERMINATE`
otherwise; a `harness` is `INDETERMINATE` with "a third-party harness reaches whatever endpoint it is
configured with, which is not in this definition" and, when `auth.source is managedSecret`, that is
`IMPLIED` egress at minimum.

---

### O9 — MCP destinations collapse when two of them share a name

**File:** `src/software_factory/definition/egress.py:236-240`

**What the code does.**

```python
servers: dict[str, Any] = dict(definition.factory.mcp_servers)
for agent in definition.agents.values():
    servers.update(agent.definition.execution.mcp_servers or {})
```

The report is built from a dict keyed by the *local alias*, so a factory-level server and an agent-level
server sharing a key, or two agents each naming their own server `tools`, resolve to one entry. `mcpServers`
is a per-scope alias; there is no requirement that two agents mean the same server by it.

**Reproduction** (`scratchpad/r/egress3.py`):

```
declared, distinct destinations:
   factory   : {'tools': McpServerRef(id='acme-shared-tool-server', …)}
   agent architect : {'tools': McpServerRef(id='architect-only-server', …)}
   agent builder   : {'tools': McpServerRef(id='builder-only-server', …)}

enumerate_egress reports:
    tool server 'builder-only-server' (not determinable)  [mcp 'tools'] — referenced by id; …
```

Three declared destinations, one reported. Two vanish with no indeterminate marker and no note.

**Why it matters.** Same failure mode as O8 and the one the module exists to prevent: the operator reads a
one-line list and believes it is complete. The `source` field says `mcp 'tools'`, which is also wrong — it
names an alias that three different scopes bind differently, so an operator following the report to change
the destination edits the wrong file.

**Fix.** Iterate scopes rather than merging them: build a list of `(scope, alias, server)` triples over
`factory.mcp_servers` and each agent's and automation's, de-duplicate on the *server identity*
(`id`, or `(command, args)`), and set `source` to the scope that declared it.

---

### O10 — `?days=` is unvalidated: two values crash the request, one returns an inverted window

**File:** `src/software_factory/observability/dash.py:52-53`

**Reproduction** (`scratchpad/r/dash_probe.py` — server on port 0 in a thread, hit with `urllib`):

```
/api/overview                          -> 200 window={'start': '2026-08-24T…', 'end': '2026-08-31T…'} runs=1
/api/overview?days=abc                 -> RemoteDisconnected 'Remote end closed connection without response'
/api/overview?days=-1                  -> 200 window={'start': '2026-09-01T19:36:50+00:00', 'end': '2026-08-31T19:36:50+00:00'} runs=0
/api/overview?days=99999999            -> RemoteDisconnected 'Remote end closed connection without response'
/api/overview?days=0                   -> 200 window={'start': '2026-08-31T19:36:50…', 'end': '2026-08-31T19:36:50…'} runs=0
/api/overview?days=1e9                 -> RemoteDisconnected 'Remote end closed connection without response'
```

with, on the server's terminal:

```
ValueError: invalid literal for int() with base 10: 'abc'
  File "…/observability/dash.py", line 52, in payload
OverflowError: date value out of range
  File "…/observability/metrics.py", line 168, in last
```

**Why it matters.** Two failures, one worse than the other. The crashes dump a traceback into the terminal
the operator is watching — the exact outcome `log_message` was overridden to prevent (`dash.py:107-109`) —
and return no response at all, so the page shows nothing and says nothing. The `days=-1` case is the serious
one: HTTP 200, a window whose `start` is *after* its `end`, `Window.contains` therefore false for every
entry, and a fully populated factory rendered as `runs=0`, `changes_opened: 0 changes`, everything else
`insufficient_data`. The window is in the payload, so a careful reader could catch it; the dashboard does
not render the window anywhere.

**Fix.** Parse with a guard: `try: days = int(raw) except ValueError: return {"error":"days.invalid", …}`,
clamp to `1 <= days <= 3650`, and return a structured error for anything outside it. Wrap `do_GET`'s body in
`try/except Exception` returning a 500 with a JSON error rather than dropping the connection.

---

### O11 — the CSP blocks the dashboard's own fetch and permits injected inline handlers

**File:** `src/software_factory/observability/dash.py:117-120`

**What the code does.**

```
Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'
```

**Reproduction** of the header (`scratchpad/r/dash_probe.py`):

```
CSP header: default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'
```

**Verification status.** The header is verified. The browser consequence is reasoned from the CSP Level 3
fetch-directive fallback rules, not executed — there is no browser on this machine (`which chromium firefox
google-chrome` → nothing) and I did not want to claim a behaviour I had not run.

**Why it matters.** Two problems in one header.

1. There is no `connect-src`, so it falls back to `default-src 'none'`, which blocks `fetch()`. The page's
   only data path is `await fetch('/api/' + view)` (`dash.py:223`). Under an enforcing browser the page
   renders `loading…` and never replaces it. The dashboard's entire client is inert, and nothing in the test
   suite would notice — `test_the_dashboard_serves_a_page_with_no_external_resources` checks the HTML
   contains no external URLs and never checks the page can reach its own API.
2. `script-src 'unsafe-inline'` is the one directive that would have contained O12. Inline event handlers on
   injected elements are governed by `script-src`, and `'unsafe-inline'` permits them. The header is written
   as though its job is to stop *external* loading — which the page does not do anyway — and it disables the
   one protection that matters against content the page renders from the ledger.

**Fix.** `default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; script-src 'sha256-…';
base-uri 'none'; form-action 'none'`. Compute the script hash at import time from `INDEX_HTML` (a few lines
of `hashlib`) so the inline script keeps working and injected handlers do not. Add a test that asserts
`connect-src` is present and that `script-src` does not contain `'unsafe-inline'`.

---

### O12 — model output reaches the dashboard's `innerHTML` unescaped

**File:** `src/software_factory/observability/dash.py:226` (and `:187-193`, `:212-216`)

**What the code does.** The run view does
`content.innerHTML = '<pre>' + JSON.stringify(data, null, 2) + '</pre>'`. `JSON.stringify` escapes JSON
metacharacters, not HTML ones, so `<`, `>` and `/` pass through. `measureRow` and `renderActivity`
interpolate `m.name`, `m.reason`, `w.id`, `w.title` and `w.why` into template literals the same way.

**Reproduction** — end to end, hostile *model output* through a real coordinator run
(`scratchpad/r/xss_e2e.py`); the payload is placed in the stub model's `calibration.unknowns`, which
`coordinator.py:413` writes verbatim into the `RUN_FINISHED` payload:

```
$ python3 scratchpad/r/xss_e2e.py
payload is in the ledger file: True
payload is served by /api/run: True
payload survives into the innerHTML string: True
```

with `PAYLOAD = "</pre><img src=x onerror=alert(1)>"`. The rendered string is:

```
"agent": "</pre><img src=x onerror=alert(1)>"
```

— the `</pre>` closes the container and the `<img>` is parsed as an element with an inline handler, which
the CSP in O11 explicitly permits.

**Why it matters.** The ledger's payloads are full of text from outside the trust boundary: model output
(`unknowns`, `reason`), work-item titles and request bodies from intake, and command stderr. `run_inspector`
returns whole payloads by design (`views.py:165`). The dashboard is loopback-only, so the attacker needs the
operator to open the run view for a poisoned run — which is precisely what an operator does when a run looks
wrong. Script running on that origin can read the entire ledger through the same API and, with
`connect-src` fixed, exfiltrate it.

Today this is latent because the CSP also blocks the fetch (O11). Fixing O11 without fixing this activates
it.

**Fix.** Build the DOM with `textContent` / `document.createElement`, or add a one-line `esc()` that replaces
`& < > " '` and apply it to every interpolation. For the run view, `pre.textContent =
JSON.stringify(data, null, 2)` is a strictly smaller change than the current line.

---

### O13 — the ssh worker silently drops the run's environment, secrets included

**File:** `src/software_factory/runtime/executors.py:249-265`

**What the code does.** `run()` builds `ssh <options> <host> -- "cd <cwd> && <command>"` and hands it to the
inner `LocalExecutor`, which sets `env=self.policy.environment()` on the **ssh client**. OpenSSH forwards no
environment by default, `options` contains no `SendEnv`/`SetEnv`, and no assignment is prefixed to the remote
command. The remote command therefore runs with the worker's login environment, not the run's.

**Reproduction** (`scratchpad/r/ssh_probe.py`):

```
policy.environment() the run is supposed to see:
    HOME = /tmp/tmpihaxjsft
    PATH = /root/.local/bin:…
    PWD = /tmp/tmpihaxjsft
    SF_TOKEN = sk-live-supersecret

argv run() builds: ['/usr/bin/ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                    'worker.internal', '--', "cd '/srv/factory' && 'printenv' 'SF_TOKEN'"]

any SendEnv/SetEnv in the ssh options: False
```

The remote command string carries no environment. Four variables the policy guarantees — including the
declared secret and the constructed `PATH` — do not exist on the worker.

**Why it matters.** The module's thesis is "either provides what it claims or refuses; the one thing none of
them does is quietly do something else". This quietly does something else, in the direction that produces
confusing failures: a command that reads a declared secret gets an empty variable and fails with an
authentication error attributed to the credential rather than to the executor. `SandboxPolicy.environment()`
also strips proxy variables under `network: none` — a control that cannot apply here at all since the remote
environment is untouched. FR-20.5's parity requirement is broken in the least visible way possible: same
definition, same command, different environment.

**Fix.** Either prefix the remote command with `env -i NAME=… NAME=…` built from
`policy.environment()` with each value `_shell_quote`d (note this reintroduces O3's `ps` exposure on the
*worker*, so prefer writing the environment to a mode-0600 file over the connection and sourcing it), or
refuse at construction when `policy.secrets` is non-empty, with a remediation saying the worker's
environment is the operator's to configure. Silently dropping it is the one option the module forbids.

---

### O14 — both new executors bypass the cwd guard the local executor enforces

**File:** `src/software_factory/runtime/executors.py:137-138` and `:252`

**What the code does.** `LocalExecutor.run` refuses a `cwd` outside the policy's writable paths
(`runtime/executor.py:222-228`). `ContainerExecutor.run` passes the caller's `cwd` to `_wrap` for
`--workdir` and then calls the inner executor with `cwd=self.policy.workspace`, so the guard is evaluated
against the workspace and never against the value it is guarding. `SshWorkerExecutor.run` does the same via
`remote_cwd = str(cwd)`.

**Reproduction** (`scratchpad/r/cwd_guard.py`):

```
local   : refused -> /etc is outside the run's writable paths
container: --workdir = /etc  (no refusal; inner check runs against policy.workspace instead)
ssh      : remote command = cd '/etc' && 'pytest'  (no refusal)
```

**Why it matters.** This is the review's recurring pattern — a control that exists and is not called on the
value it protects. For the ssh worker it means a command runs in an arbitrary directory on a real machine
the factory does not confine. For the container the blast radius is smaller (only the workspace is mounted)
but the parity claim is still false: the same call is an `ExecutorError` on one executor and a normal run on
the other, so a gate or tool that relies on the refusal behaves differently depending on where the run
executes.

Note also `SshWorkerExecutor` uses a **local** path as a **remote** working directory. Nothing maps
`policy.workspace` to `remote_workspace`, and nothing transfers the workspace to the worker at all.

**Fix.** Call `self.policy.is_writable(cwd)` in both `run` methods before wrapping, raising the same
`ExecutorError` with the same message. For ssh, additionally translate a workspace-relative path onto
`remote_workspace` rather than passing the local absolute path through.

---

### O15 — `ContainerImage` accepts a bare image name as "pinned"

**File:** `src/software_factory/runtime/executors.py:66-75`

**What the code does.**

```python
tag = self.reference.rpartition(":")[2]
if not tag or tag == "latest" or "/" in tag:
    raise ValueError(…)
```

For a reference with no `:`, `rpartition` returns `('', '', 'ubuntu')`, so `tag` is the image name itself:
non-empty, not `latest`, no slash. It passes. The check catches `ghcr.io/acme/builder` only because the
would-be tag contains a slash — an accident of that particular shape, not the property being tested.

**Reproduction.**

```
$ python3 - <<'PY'
from software_factory.runtime.executors import ContainerImage
for c in ["ubuntu","python","node","alpine","myregistry.test:5000/app:1.2","ghcr.io/acme/builder","busybox:latest"]:
    try:
        ContainerImage(c); print("ACCEPTED as pinned:", repr(c))
    except ValueError:
        print("refused           :", repr(c))
PY
ACCEPTED as pinned: 'ubuntu'
ACCEPTED as pinned: 'python'
ACCEPTED as pinned: 'node'
ACCEPTED as pinned: 'alpine'
ACCEPTED as pinned: 'myregistry.test:5000/app:1.2'
refused           : 'ghcr.io/acme/builder'
refused           : 'ghcr.io/acme/builder:latest'
refused           : 'busybox:latest'
```

**Why it matters.** `ContainerImage("ubuntu")` is `ubuntu:latest` to every container runtime — the case the
class was written to refuse, stated in its own docstring: *"`latest` is a different image on different days,
so a run that reproduces today and not tomorrow has no bug to find."* FR-24's replay integrity depends on
this check and the check tests the wrong string. `test_an_unpinned_image_is_refused` passes because both of
its examples happen to contain a slash.

Related, and worth a look: `test_parity.PARITY_IMAGE = "python:3.12-slim"` is annotated *"Pinned, like every
image this project accepts"*. `3.12-slim` is a floating tag that changes with every patch release.

**Fix.** Parse properly: split the reference on the last `/` first; a tag exists only if the remaining
component contains a `:`. Require a digest, or a tag matching something version-shaped, and reject a bare
name explicitly. Add `ContainerImage("ubuntu")` and `ContainerImage("registry.local:5000/app")` to the test.

---

### O16 — "bounded state" bounds the note count and never the note size

**File:** `src/software_factory/harness/conversation.py:111-122` and `:177-228`

**What the code does.** `KIND_BUDGET` caps the *number* of notes per kind. `compact` drops notes only when a
kind exceeds its count budget, and returns `None` — "nothing needed dropping" — otherwise. Note text is
never measured or truncated, and it comes straight from model output via `_as_texts`.

**Reproduction** (`scratchpad/r/conv.py`):

```
=== 1. bounded state? one hostile note, 2 MB of text ===
compaction record: None
notes after compaction: 2
rendered summary size (chars): 2000075
```

**Why it matters.** The module's opening docstring justifies its entire existence with *"On a work item that
takes five passes, that conversation exceeds any context window — so 'continue the conversation' cannot mean
'send it all again'."* A context window is measured in tokens, not notes; the budget is denominated in the
one unit that does not bind. `ConversationState.render()` is what travels into the next run's prompt, and
its size is set by the model, unbounded, with `compact` reporting that nothing needed doing. The maximum
carried state under the current budget is 55 notes of arbitrary length.

**Fix.** Add a per-note character/token cap (truncate with an explicit `[… n characters elided]` marker, the
way `LocalExecutor._cap` already does for command output) and a total-summary cap that triggers compaction
independently of the count. Record the size reduction on the `Compaction` record so it is auditable.

---

### O17 — four of the five note kinds are never created

**File:** `src/software_factory/orchestrator/coordinator.py:613-623` against `STAGE_SCHEMAS` at `:78-116`

**What the code does.** `_carry_forward` builds `DECISION` notes from `output["decisions"]` and `ATTEMPT`
notes from `output["attempted"]`. No stage schema contains either key, so the model is never asked for them
and a schema-conformant response never carries them. `NoteKind.CONSTRAINT` and `NoteKind.ARTIFACT` are not
constructed anywhere in `src/`.

**Reproduction** (`scratchpad/r/conv_real.py`):

```
keys any stage schema asks the model for:
  TRIAGE   required=['findings', 'scope', 'calibration'] properties=['calibration','findings','open_questions','scope']
  DESIGN   required=['plan', 'acceptance', 'calibration'] properties=['acceptance','calibration','plan']
  BUILD    required=['summary', 'claims', 'calibration'] properties=['calibration','claims','summary']
  REVIEW   required=['verdict', 'findings', 'calibration'] properties=['calibration','findings','verdict']
  'decisions' requested anywhere: False
  'attempted' requested anywhere: False

conversations after a real run:
  ('wi_…', 'scout'):   notes=1 kinds={'open_question': 1}
  ('wi_…', 'builder'): notes=1 kinds={'open_question': 1}
  ('wi_…', 'critic'):  notes=1 kinds={'open_question': 1}
```

```
$ grep -rn "NoteKind\." --include=*.py src/ | grep -v conversation.py
coordinator.py:614:  Note(kind=NoteKind.DECISION, …)
coordinator.py:616:  Note(kind=NoteKind.ATTEMPT, …)
coordinator.py:622:  Note(kind=NoteKind.OPEN_QUESTION, …)
```

**Why it matters.** The carried state that FR-3.7 and FR-29 are about consists, in the assembled system, of
calibration unknowns and nothing else. `NoteKind.ATTEMPT` — *"The most valuable kind and the most often
lost: without it the next run tries it again and reaches the same wall"* — is given the largest budget in
`KIND_BUDGET` and is never created, so the next run does try it again. The subsystem is fully built, fully
tested, and connected to two model output keys that nothing requests.

**Fix.** Add `decisions` and `attempted` (arrays of strings) to the BUILD and REVIEW schemas, and add
`constraints` and `artifacts` if those kinds are meant to exist. Then add a coordinator-level test that runs
a stage with a stub emitting them and asserts the notes land — the existing conversation tests construct
`Note` objects directly and so cannot see this gap.

---

## MINOR

### O18 — three metrics have no implementation; configuring the integration deletes their rows

**File:** `src/software_factory/observability/metrics.py:224-242`

`compute` builds five measures and then appends `unavailable(...)` entries *only for integrations that are
missing*. Nothing computes `changes_merged`, `autonomy` or `cycle_time_to_merge` when `git-host` is present.

```
$ python3 -c "
from software_factory.observability import compute
print('without git-host:', [m.name for m in compute([], integrations=frozenset()).measures])
print('with    git-host:', [m.name for m in compute([], integrations=frozenset({'git-host'})).measures])"
without git-host: ['gate_pass_rate','escalation_rate','rework_rate','cost_per_change','changes_opened','autonomy','changes_merged','cycle_time_to_merge']
with    git-host: ['gate_pass_rate','escalation_rate','rework_rate','cost_per_change','changes_opened']
```

An operator who does the thing the reason text tells them to do — configure a git-host adapter — watches the
three metrics disappear from the dashboard entirely. `test_configuring_the_integration_removes_the_unavailable_entry`
asserts this as the desired behaviour. Fix: emit these measures unconditionally, as
`unavailable(... "no adapter")` when absent and as `insufficient(... "the adapter is configured but this
build does not yet fold merge data")` when present, so the row never silently vanishes.

### O19 — `transcript_refs` holds duplicate work-item ids and no transcript exists

`coordinator.py:610` appends `item.id` on every stage. After two runs of the same agent on one item:

```
after another REVIEW run by the same agent:
  notes: 2 transcript_refs: ['wi_42b9e6795361', 'wi_42b9e6795361']
  render first line: Carried from 2 previous run(s):
```

`Resumption.previous_runs` — the FR-29.3 audit record — is a list containing the same id twice, identifying
nothing. Worse, `RunResult.transcript` (`harness/loop.py:154`) is never persisted: `grep -rn "transcript"
src/` finds no writer. The docstring's *"the history stays addressable"* and *"the full transcripts remain
retrievable"* (rendered to the agent at `conversation.py:171-173`) describe something that does not exist.
Fix: give each run a distinct id, write the transcript to the ledger or an artifact store, and store the
reference that was actually written — or delete the claim from the rendered summary.

### O20 — `escalation_rate` divides work items by runs

`metrics.py:319` counts `len({e.subject for e in entries if ESCALATION})` — distinct work items — over
`runs.total`, which counts `RUN_STARTED` entries (one per stage).

```
one work item, 3 runs, 3 escalations -> escalation_rate: escalation_rate: 0.3333 share sample(runs): 3
```

Every run escalated; the metric says 33%. Fix: count escalation entries (or distinct escalating runs) in the
numerator, and say in the unit which denominator is meant.

### O21 — the diff evidence digest is process-local

`coordinator.py:565` uses `digest=str(hash(diff))`.

```
$ for i in 1 2 3; do python3 -c "print(hash('diff --git a/x b/x\n+line\n'))"; done
-7752930422172383911
-4581290844993497000
2107006711025150057
```

Two runs producing an identical diff record different digests, and a digest recorded yesterday cannot be
recomputed today, so the evidence item cannot be checked against the artifact it names. `ledger/entry.py:73-85`
already documents `PYTHONHASHSEED` as precisely this hazard and refuses to use `str()` for it. Fix: use
`digest_parts(diff)`, which the module already imports elsewhere.

### O22 — a partial budget silently substitutes a magic number

`conversation.py:201` is `limit = budget.get(kind, 10)`, so a caller passing `{DECISION: 3}` gets a budget of
10 for the other four kinds rather than `KIND_BUDGET`'s declared 15/10/10/8.

```
KIND_BUDGET[ATTEMPT] = 15  attempts kept: 10
```

`test_every_note_kind_has_a_budget`'s docstring — *"A kind with no budget silently takes the default, which
is a policy nobody wrote"* — names this exact hazard and then checks only that `KIND_BUDGET` is complete.
Fix: `limit = budget.get(kind, KIND_BUDGET[kind])`, or merge the caller's overrides onto `KIND_BUDGET`.

### O23 — `_as_texts` manufactures a note that says "None", and drops a dict without a record

`coordinator.py:777-788`:

```
  {'a': 'decided x'}             -> []
  None                           -> []
  42                             -> []
  [{'t': 'decided y'}]           -> ["{'t': 'decided y'}"]
  ['ok', None, {'k': 1}]         -> ['ok', 'None', "{'k': 1}"]
  [[1, 2], 'z']                  -> ['[1, 2]', 'z']
```

A `null` entry becomes a carried decision whose text is the string `"None"`, and a Python `repr` leaks into a
summary the next run reads as prose. A dict-shaped field is dropped with no note and no ledger record, so a
model that returns `{"decisions": {"1": "…"}}` loses its decisions silently. Fix: skip non-string entries
rather than `str()`-ing them; when a field is present but unusable, record one `NoteKind` note or a ledger
entry saying so — the module's whole thesis is that a silent drop and a model that said nothing must not
look the same.

### O24 — the carried-state digest ignores the stage

`conversation.py:151` digests `f"{kind}:{text}:{run_id}"` only.

```
different stage, same digest: True 636c749d8717d57a068468e9 636c749d8717d57a068468e9
```

`Resumption.carried_digest` exists so that *"two runs claiming the same carried state and digesting
differently were handed different things"*; the converse — the same digest meaning the same state — does not
hold across stages. (The `:`-joining is also un-length-prefixed, which `digests.py`'s own docstring calls out
as the collision class to avoid; `digest_parts` is injective *between* parts but this construction is not
injective *within* one.) Fix: pass each field as its own part to `digest_parts` and include `stage`.

### O25 — `user_facing` is hardcoded, and `terminal_always` is read nowhere

`coordinator.py:593` calls `self.recording_policy.expects_visual(item.work_class.value, user_facing=True)`.
The only parameter distinguishing user-facing work from the rest is supplied as a constant by the only
production caller, so every feature and defect is treated as user-facing:

```
feature        REVIEW  -> NotRecorded | Visual evidence is absent. …
defect         REVIEW  -> NotRecorded | Visual evidence is absent. …
chore          REVIEW  -> None
```

A backend-only defect gets an evidence claim asserting missing visual evidence it never needed. Separately,
`RecordingPolicy.terminal_always` (*"Terminal recording is cheap and available everywhere, so it is the
floor"*) is never read: `grep -rn "terminal_always" src/ tests/` returns only its definition. The declared
floor is not enforced anywhere. Fix: derive `user_facing` from the work item (surfaces touched, or a field on
`WorkItem`), and either wire `terminal_always` into `_bundle` — a `NotRecorded(TERMINAL,
UNSUPPORTED_EXECUTOR)` when no recorder exists — or delete the field.

### O26 — the ssh worker's allowlist refusal names the local executor

`SshWorkerExecutor.__init__` refuses `network: none` itself but leaves `network: allowlist` to the inner
`LocalExecutor`, whose message is written for a different component:

```
ALLOWLIST refusal message: the local executor cannot enforce a per-host network allowlist
```

An operator debugging an ssh-worker configuration is told to look at the local executor, and the remediation
they get points them at the container executor for per-host filtering, which O-scope aside is also not true
(`ContainerExecutor` refuses `allowlist` too). Fix: refuse `allowlist` explicitly in `SshWorkerExecutor`
with a message naming the ssh worker.

### O27 — an undefined ratio rendered as a definite zero

`RunCounts.measurement_share` returns `0.0` when `total == 0`, and `dash.py:202` prints
`${Math.round(r.measurementShare * 100)}% measurement`. A factory with no runs displays "0% measurement",
which is a claim about a distribution that has no members. Given O7, this cell is always "0%" regardless.
Fix: return `None` for an empty total and render "—" for it.

---

## Tests that assert the wrong thing

Fifteen. The first four are the dangerous ones — they state a defect as the requirement, or they stand in
for a check nobody else performs.

1. **`tests/test_executors.py:116` `test_secrets_are_passed_by_environment_not_on_the_command_line`.** The
   name and docstring say secrets must not appear on the command line; the assertions require that they do
   (`any(part.startswith("SF_TOKEN=") for part in wrapped)`). See O3. This test will actively resist the fix.

2. **`tests/test_metrics.py:160` `test_configuring_the_integration_removes_the_unavailable_entry`.**
   `assert report.measure("changes_merged") is None` — the test's subject is a metric vanishing from the
   dashboard when the operator configures the integration it needs, and it asserts that as correct. See O18.

3. **`tests/test_metrics.py:171` `test_the_gate_pass_rate_counts_first_attempts_only`.** Its fixture uses
   two evaluations of `tests-pass` on `wi-1` with no `stage` key, so the only case it exercises is the one
   where de-duplication is right. The cross-stage case — where the same key discards a genuine failure at a
   later stage — is never constructed, and the test is green while O2 stands. A test named "first attempts
   only" that never sees a second stage is not testing the rule it names.

4. **`tests/test_executors.py:36` `test_an_unpinned_image_is_refused`.** Both examples
   (`…/builder:latest`, `…/builder`) contain a `/`, which is what the check actually keys on. A bare
   `ubuntu` — the commonest unpinned reference there is — passes the validator and the test would not
   notice. See O15.

5. **`tests/test_metrics.py:93` `test_run_counts_separate_work_from_measurement`.** Synthesises
   `purpose="benchmark"` and `purpose="improvement"` entries that no writer in the system produces. It
   proves the fold can split, not that the split ever happens; the requirement it cites (FR-15.5) is
   unmet. See O7.

6. **`tests/test_metrics.py:215` `test_changes_opened_counts_a_work_item_once`** and
   **`:231` `test_cost_per_change_is_a_median_and_says_it_is_an_estimate`.** Both synthesise
   `transition(..., "HANDOFF")` entries the coordinator never writes. Both pass; neither metric ever
   computes on real data. See O5.

7. **`tests/test_parity.py:127` `test_every_executor_reports_the_command_the_caller_asked_for`.** Asserts
   `"command=tuple(command)" in inspect.getsource(...)`. A source-text substring, not a behaviour. The
   string appearing inside a comment would satisfy it, and a `run` that later overwrote `command` would
   too. Its own docstring concedes: "Checked structurally here; the behavioural suite proves it end to
   end" — and the behavioural suite is skipped.

8. **`tests/test_parity.py:158` `test_redaction_and_capping_are_shared_not_reimplemented`.** Same shape:
   `"_redact" not in source`. It would pass for an executor that reimplemented redaction under a different
   name, and it does pass for `SshWorkerExecutor`, which composes `LocalExecutor` and then discards the
   policy environment entirely (O13) — a divergence far larger than a duplicated `_redact`.

9. **`tests/test_parity.py:256` `test_the_suite_states_what_it_could_not_verify`.** The body builds a list of
   string literals and asserts `all(reason.strip() for reason in unverified)`. It cannot fail under any
   condition, reports nothing to CI, and is the only place the ssh-worker's total absence from behavioural
   testing is "documented". This is the C9 shape the module's docstring warns about, inside the test
   written to prevent it.

10. **`tests/test_parity.py:120` `test_every_executor_carries_the_same_policy`.** Asserts
    `executor.policy.workspace == tmp_path` for all three. For `SshWorkerExecutor` the commands actually run
    in `remote_workspace="/srv/factory"`, which the assertion never looks at. The test's docstring — "an
    executor that held a different one would make the audit describe a run that did not happen" — describes
    exactly what is true of the ssh worker, and the assertion passes.

11. **`tests/test_executors.py:67` `test_a_container_executor_finds_a_runtime_when_one_exists`.**
    Monkeypatches `_detect_runtime` to return a literal path, so the real function — the one that confuses
    presence with capability (O4) — is never executed by any test.

12. **`tests/test_dashboard.py:225` `test_the_dashboard_serves_a_page_with_no_external_resources`.** Checks
    the HTML for `http://`, `cdn`, `<script src`. It never checks that the page can reach its own API, which
    the CSP it also serves forbids (O11). The suite has no test that the client can load data at all.

13. **`tests/test_conversation.py:206` `test_every_note_kind_has_a_budget`.** Docstring: "A kind with no
    budget silently takes the default, which is a policy nobody wrote." It then checks only that
    `KIND_BUDGET` has all five keys — never the `budget.get(kind, 10)` path where that silent default is
    actually taken (O22).

14. **`tests/test_conversation.py:149` `test_compaction_is_deterministic`.** Calls the same function twice in
    one process. Determinism across processes (the property a replay needs) is untested, as is determinism
    of the thing that actually varies — note size (O16).

15. **`tests/test_executors.py:214` `test_every_executor_satisfies_one_protocol`** and
    **`test_parity.py:112`.** `isinstance(x, Executor)` on a `runtime_checkable` Protocol checks for the
    presence of attribute names only, not signatures:

    ```
    $ python3 -c "
    from software_factory.runtime.executors import Executor
    class X:
        policy = None
        def run(self, *a, **k): return 'nope'
    print(isinstance(X(), Executor))"
    True
    ```

    An object whose `run` takes different arguments and returns a string satisfies it.

### What `tests/test_parity.py` actually proves when it runs

Three of its seven tests skip here. Of the four that run, **none calls `run()` on any executor.** What they
establish is: three objects have a `policy` attribute and a `run` attribute (item 15 above); their
`policy.workspace` fields are equal (item 10); one dataclass has eight field names; and two source files
contain two substrings (items 7, 8). No command is executed by any executor in this module. The
`ssh-worker` executor has no behavioural test in the repository at all, skipped or otherwise — its absence
is "reported" by the tautology in item 9.

So when the suite is green on a machine with a working docker daemon it proves local≡container for two
commands and an exit code; on a machine without one, it proves nothing about behaviour whatsoever, while
`pytest -q` prints `99 passed, 3 skipped` — a result that reads as a parity suite that ran.

**Fix.** Make the structural suite exercise `run()` against a stub runtime: a fake `docker` script on PATH
that `exec`s its trailing arguments, and a fake `ssh` script that strips options and runs the remote command
locally. That turns every structural test into a behavioural one — same command, same `CommandResult` shape,
same environment, same cwd refusal — with no daemon and no worker. It would have caught O3, O13 and O14.

---

## What I checked and found sound

- **`_shell_quote` (`executors.py:319`).** 323 adversarial inputs — quotes, backslashes, `$()`, backticks,
  newlines, tabs, globs, `--`, and 300 random strings drawn from shell metacharacters — round-tripped
  through `/bin/sh -c 'printf %s <quoted>'` with zero mismatches, and a deliberate
  `x'; touch /tmp/PWNED; echo '` argument produced the literal string and no file. The POSIX
  single-quote-with-`'"'"'`-escape is correctly implemented.
- **No path traversal and no write path in the dashboard.** The handler touches no filesystem path derived
  from the URL; `/api/../../../etc/passwd` and `/api/%2e%2e/…` fall through to `view.unknown` and
  `/../etc/passwd` returns 404. Only `do_GET` is defined, so `POST`/`PUT`/`DELETE`/`PATCH` all return 501.
  The server binds `127.0.0.1` by default.
- **The container executor's refusals and its argument construction.** `network: allowlist` is refused at
  construction with a remediation; `network: none` maps to `--network none`; `--cap-drop ALL`,
  `--security-opt no-new-privileges`, `--pids-limit`, `--memory` and `--user 1000:1000` are all present; the
  argv is a list handed to `Popen` with no shell, so there is no injection path through the image reference
  or the command.
- **`cloud_executor`** refuses with a distinct error type and a remediation naming the alternatives, exactly
  as the module claims.
- **`Measure.__post_init__` invariants.** An unavailable measure carrying a value, an unavailable measure
  with no reason, and an estimate with no exclusions all raise at construction. The type makes O1's failure
  impossible for any metric that goes through `unavailable()` — which is why O1 is a gap in *which* metrics
  use it, not in the mechanism.
- **`views._trend`** correctly returns `None` rather than `0` for any metric unavailable in either window.
  (Its only leak is O1's available-zero, which is upstream.)
- **`digest_parts` (`digests.py`)** is length-prefixed and injective across parts.
- **`compact` preserves insertion order** and keeps the most recent notes per kind, as documented, and
  returns `None` rather than recording a no-op — verified against the existing tests and by inspection of
  the `id()`-keyed ordering, which is safe because every kept note is still held by `state.notes`.
- **The recording subsystem's "absence is an artifact" path works end to end.** From a real `Coordinator`,
  `feature`/`defect` at `REVIEW`/`VERIFY` produce a `NotRecorded` whose evidence item is added to the bundle
  and whose statement ("Visual evidence is absent. No browser recording (no recorder is configured for
  review): not enabled. …") is attached as a claim. `chore` and `refactor` correctly produce nothing.
  `Recording.__post_init__` refuses a truncation with no reason, and `EvidenceItem.truncated` carries
  through.
- **Ledger integrity around the new writes.** Every payload the coordinator writes is JSON-serialisable
  (including `blocker: null` and `StrEnum` values), and the entries hash-chain and verify.
- **`ContainerImage` does reject `:latest`** and slash-suffixed unpinned references — O15 is about the third
  shape, not these two.

---

## Not verified

- **O11's browser behaviour.** The header is reproduced above; that `connect-src` falls back to
  `default-src 'none'` and blocks `fetch` is the CSP Level 3 fallback rule, not something I executed — this
  machine has no browser (`which chromium chromium-browser google-chrome firefox` → nothing).
- **`ssh <host> -- <command>` (`executors.py:262`).** OpenSSH's `ssh(1)` does not document `--` between the
  destination and the command. Whether the client consumes it or forwards it to the remote shell (where
  `-- cd '/srv' && cmd` would fail) could not be tested: there is no `ssh` binary on this machine
  (`shutil.which("ssh")` is `None`, which is also why every test in `test_executors.py` and
  `test_parity.py` passes a literal `/usr/bin/ssh` that does not exist). Flagging it as worth one manual
  check against a real worker, not as a finding.
