# Adversarial Code Review 2 — identity, economics, governance

| Field | Value |
| --- | --- |
| Under review | `identity/{principals,duties,checkpoints,loading}.py`, `economics/{spend,scheduling}.py`, `governance/{classification,retention,segments}.py`, `definition/validate.py::_check_principals`, `orchestrator/workitem.py::{advance,cancel,_requires}`, `tests/test_{identity,economics,governance}.py` |
| Review type | Hostile. The modules are an authorisation layer, a cost control, and a compliance mechanism; each is assumed broken until it survives a reproduction. |
| Method | Every finding was reproduced against the installed package from the repository root. Command and verbatim output are given. Nothing is reported that was not run. |
| Findings | 4 CRITICAL · 8 MAJOR · 8 MINOR (20) · 6 tests that assert the wrong thing |
| Baseline | `96 passed` — `python3 -m pytest tests/test_identity.py tests/test_economics.py tests/test_governance.py -q`. Every finding below is present in a green tree. |

**Reading rule.** CRITICAL means the control does not do the thing it is named for, or reports a
state it did not establish. MAJOR means it fails on a reachable path or asserts something untrue to
an operator. MINOR means it is wrong and cheap to fix before it becomes one of the other two.

---

## Findings index

| ID | File:line | Finding | Severity | Verified |
| --- | --- | --- | --- | --- |
| I1 | `orchestrator/workitem.py:423` | An approval with an empty `subject` authorises every work item, forever — a permanent skip-REVIEW / cancel-anything token, and `Directory.authorise` mints one on request | CRITICAL | yes |
| I2 | `identity/*` (whole package) | The authorisation layer has no caller. No code path opens or resolves a checkpoint, calls `approve()`, or produces a `Decision` that reaches `advance`/`cancel` | CRITICAL | yes |
| I3 | `governance/retention.py:219,260` | `sweep` and `erase` report `expired` / `erased` / `acted` / `complete` when no callback was supplied and nothing was destroyed | CRITICAL | yes |
| I4 | `governance/segments.py:157` | `Manifest.verify()` establishes nothing about the ledger: the last segment is unprotected, a rewritten sealed range verifies clean, and `seal()` seals an already-broken chain | CRITICAL | yes |
| I5 | `orchestrator/workitem.py:409` | `_requires` never consults a `Directory`; a `Decision` naming an unknown or inactive principal, with no rationale, authorises a cancellation | MAJOR | yes |
| I6 | `orchestrator/workitem.py:328,397` | The approving `Decision` is discarded. `Transition` has no field for it, so the refusal text "the approval is recorded against their identity" is false | MAJOR | yes |
| I7 | `identity/duties.py:137` | FR-25.3's "two approvers from a distinct group" is checked against the proposer only, never between approvers: two people from one team satisfy it | MAJOR | yes |
| I8 | `definition/validate.py:475` | The duplicate-provider-identity check lives only in `Directory.add`. `sf validate` reports clean; `sf principals` dies with an uncaught `ValueError` | MAJOR | yes |
| I9 | `definition/validate.py:540` | Only 3 of the 6 capabilities in `ANSWERED_BY` are checked for holders; the other 3 raise `ValueError` out of `CheckpointBook.open` at run time | MAJOR | yes |
| I10 | `economics/scheduling.py:67,160` | Fingerprint deduplication (FR-26.3) is never invoked: `FactoryEvent.fingerprint` is never populated and `fingerprint_of` has no caller in `src/` | MAJOR | yes |
| I11 | `economics/scheduling.py:61` | Ageing has no floor: a 13-hour-old LOW item outranks a brand-new URGENT incident, and a queue of them starves it | MAJOR | yes |
| I12 | `economics/spend.py:145` | `overhead_fraction` is presented as measured; only 2 of the 7 `Cause` values are ever emitted, so it can only ever mean "share of spend on runs that repaired" | MAJOR | yes |
| I13 | `identity/principals.py:209` | `resolve_identity` ignores `active`: deactivating a principal does not revoke their intake trust | MINOR | yes |
| I14 | `identity/duties.py:107,155` | Self-approval and the group check are string comparisons against `proposer_id`; a proposer recorded by provider handle self-approves, and `_shares_group` fails open on an unknown id | MINOR | yes |
| I15 | `identity/principals.py:204,216` | `add` stores identities verbatim, `resolve_identity` lower-cases the lookup: a directly-built `Directory` loses identities, and the ambiguity guard is defeated by case | MINOR | yes |
| I16 | `identity/principals.py:179,193` | `Directory` documents that "nothing here mutates at run time"; `add` is public and grants capabilities mid-run | MINOR | yes |
| I17 | `identity/principals.py:80` | `PERSON_ONLY` omits `emergency_stop` and `answer_question`, so an automation can emergency-stop and an agent can clear a `QUESTION` human checkpoint | MINOR | yes |
| I18 | `economics/scheduling.py:99` | At default limits, 33 events in one instant park a source for an hour; sustained, that is a permanent intake outage for ~33 events/hour of effort | MINOR | yes |
| I19 | `identity/checkpoints.py:194` | The `REMINDED` escalation is silently skipped when the sweeper was not running between the two deadlines; the item parks with no reminder ever sent | MINOR | yes |
| I20 | `identity/duties.py:41` | `ApprovalRequest.definition_change` is accepted and never read by anything | MINOR | yes |

---

# CRITICAL

## I1 — An approval with an empty subject is a permanent token for every work item

**`orchestrator/workitem.py:423`, `identity/principals.py:226`**

`_requires` guards both human-authority paths in the stage machine — skipping a non-skippable
stage (`advance`) and cancelling work (`cancel`). Its subject check is:

```python
if approval.subject not in ("", subject):
    return (f"the approval names {approval.subject!r}, not {subject!r}; an approval is for "
            "one decision, not a token to reuse")
```

The empty string is an accepted value, and it means *any subject*. `Directory.authorise` places no
constraint on `subject` at all, so a real holder can be issued exactly this decision through the
supported API and it becomes a standing authority over every work item in the factory, with no
expiry and no single-use property.

**Reproduction**

```
python3 -c "
from software_factory.identity import Directory, Principal, PrincipalKind, Capability, Decision
d = Directory([Principal(id='amaya', kind=PrincipalKind.PERSON, capabilities=frozenset({Capability.SKIP_STAGE}))])
dec = d.authorise('amaya', Capability.SKIP_STAGE, subject='', rationale='blanket approval for the sprint')
print(type(dec).__name__, repr(dec))
"
```
```
Decision Decision(principal_id='amaya', capability=<Capability.SKIP_STAGE: 'skip_stage'>, subject='', rationale='blanket approval for the sprint', at=datetime.datetime(2026, 8, 31, 19, 35, 6, 730870, tzinfo=datetime.timezone.utc), evidence_shown=(), channel='cli')
```

```
cd /home/user/software-factory && python3 -c "
from software_factory.orchestrator import StageMachine
from software_factory.orchestrator.workitem import SourceContext, WorkItem
from software_factory.definition.models import Stage
from software_factory.identity import Capability, Decision
m = StageMachine()
def item(i):
    return WorkItem(id=i, factory='f', title='t', request='r',
                    source=SourceContext(provider='cli', kind='direct', ref='local'), stage=Stage.TRIAGE)
wildcard = Decision(principal_id='nobody-at-all', capability=Capability.SKIP_STAGE, subject='', rationale='x')
for wid in ('wi-1','wi-2','wi-3'):
    r = m.advance(item(wid), Stage.HANDOFF, actor='conductor', reason='looks done', approval=wildcard)
    print(wid, type(r).__name__, getattr(r,'skipped',None))
"
```
```
wi-1 Transition (<Stage.DESIGN: 'DESIGN'>, <Stage.BUILD: 'BUILD'>, <Stage.REVIEW: 'REVIEW'>, <Stage.VERIFY: 'VERIFY'>)
wi-2 Transition (<Stage.DESIGN: 'DESIGN'>, <Stage.BUILD: 'BUILD'>, <Stage.REVIEW: 'REVIEW'>, <Stage.VERIFY: 'VERIFY'>)
wi-3 Transition (<Stage.DESIGN: 'DESIGN'>, <Stage.BUILD: 'BUILD'>, <Stage.REVIEW: 'REVIEW'>, <Stage.VERIFY: 'VERIFY'>)
```

**Why it matters.** `DEFAULT_NON_SKIPPABLE` is `{REVIEW}` and the module docstring names the exact
threat: "the conductor reads attacker-controllable text, so unbounded routing authority is an
injection primitive". One empty-subject decision — the kind a hurried operator would produce for a
"blanket approval" — restores that unbounded authority permanently, across every work item, and
`test_an_approval_for_one_work_item_does_not_authorise_another` passes throughout because it only
tests a *mismatched* subject, never an absent one.

**Fix.** Remove `""` from the accepted set in `_requires` — require an exact subject match. Reject an
empty `subject` in `Directory.authorise` the same way an empty `rationale` is rejected. If a
scope-wide authority is genuinely wanted, model it as its own capability with its own name, so it is
visible in `sf principals` rather than hiding inside a subject field.

---

## I2 — The authorisation layer has no caller

**`identity/checkpoints.py`, `identity/duties.py`, `identity/principals.py:226`**

Every enforcement point in `identity/` is unreachable from any running code path.

**Reproduction**

```
cd /home/user/software-factory && for sym in CheckpointBook ApprovalState ApprovalRequest "approve(" Retention "ConcurrencyLimiter" "Scheduler(" ; do
  echo "--- $sym ---"
  grep -rn --include=*.py "$sym" src/ | grep -v "src/software_factory/identity/\|src/software_factory/economics/\|src/software_factory/governance/" | head -4
done
```
```
--- CheckpointBook ---
--- ApprovalState ---
--- ApprovalRequest ---
--- approve( ---
--- Retention ---
--- ConcurrencyLimiter ---
--- Scheduler( ---
```

```
cd /home/user/software-factory && grep -rn "authorise(" --include=*.py src/
```
```
src/software_factory/identity/principals.py:226:    def authorise(
src/software_factory/identity/duties.py:125:    decision = directory.authorise(
src/software_factory/identity/checkpoints.py:178:        decision = self.directory.authorise(
```

`Directory.authorise` — the only thing that can mint a legitimate `Decision` — is called from exactly
two places, both inside `identity/`, and neither of those two has a caller either. The only consumers
of a `Decision` are `StageMachine.advance` and `StageMachine.cancel`, and the coordinator calls
`advance` without one:

```
cd /home/user/software-factory && sed -n 225,227p src/software_factory/orchestrator/coordinator.py
```
```
                moved = self.machine.advance(
                    item, stage, actor="coordinator", reason=f"entering {stage.value}"
                )
```

There is also no CLI surface. `checkpoints.py:168` tells a user to run `sf checkpoints`; the command
does not exist, and neither does any retention or erasure command:

```
cd /home/user/software-factory && python3 -m software_factory.cli --help 2>&1 | grep -c checkpoint
cd /home/user/software-factory && python3 -m software_factory.cli govern --help 2>&1 | grep -A6 "Commands"
```
```
0
╭─ Commands ───────────────────────────────────────────────────────────────────╮
│ classes  What each persisted class can contain, how long it is kept, and     │
│          why.                                                                │
│ seal     Seal complete ledger segments so an archived prefix stays           │
│          verifiable.                                                         │
│ verify   Verify the segment chain, which works over an archived prefix.      │
╰──────────────────────────────────────────────────────────────────────────────╯
```

`CapState.accepts_new_work` and `CapState.continues_running_work` — the entire behavioural half of
the spend cap — are referenced only by tests:

```
cd /home/user/software-factory && grep -rn --include=*.py "accepts_new_work\|continues_running_work" src/ tests/
```
```
src/software_factory/economics/spend.py:59:    def accepts_new_work(self) -> bool:
src/software_factory/economics/spend.py:63:    def continues_running_work(self) -> bool:
tests/test_economics.py:61:    assert state.accepts_new_work
tests/test_economics.py:72:    assert not state.accepts_new_work
tests/test_economics.py:73:    assert state.continues_running_work
tests/test_economics.py:82:    assert not state.accepts_new_work
tests/test_economics.py:83:    assert not state.continues_running_work
```

**Why it matters.** A factory running `sf work` today enforces zero human checkpoints, zero
separation of duties, zero retention, zero concurrency bound, and zero spend cap. The spend cap is a
*report*; nothing consults it. `sf principals` describes itself as "the security answer to who can
approve, override, widen, or stop?" — the answer it prints is not consulted by anything that
approves, overrides, widens, or stops. This is precisely the class the previous review found nine
times: a control that exists and is not called. The difference is that here it is not one control, it
is the whole layer.

**Fix.** This is a wiring task, not a redesign. In priority order: (1) the coordinator must construct
a `CheckpointBook` from the loaded directory and open a checkpoint at each of the FR-16.1 points;
(2) intake must consult `CapState.accepts_new_work` before admitting and the coordinator must consult
`continues_running_work` before starting a stage; (3) `sf checkpoints list|resolve` and
`sf govern sweep|erase` need to exist, or the remediation strings that name them must be corrected;
(4) `Pipeline`/`Coordinator` must hold the `Scheduler` and `ConcurrencyLimiter` rather than leaving
them constructible-but-unconstructed. Until then, no documentation or CLI text should describe any of
these as enforced.

---

## I3 — Retention and erasure report success without acting

**`governance/retention.py:219-221, 260-262, 99-101, 129-133`**

Both destructive operations take their destructor as an *optional* callback and append to the
success list whether or not it was supplied:

```python
if tombstone is not None:
    tombstone(artifact)
report.expired.append(artifact.id)     # unconditional
...
if destroy is not None:
    destroy(artifact)
report.erased.append(artifact.id)      # unconditional
```

`SweepReport.acted` is `bool(self.expired)` and `ErasureReport.complete` is `not self.blocked_by_hold`.
So a sweep that deleted nothing reports `acted: True`, and an erasure that destroyed nothing produces
a signed-looking receipt reading `complete: true, erased: ["t1"]`.

**Reproduction**

```
cd /home/user/software-factory && python3 -c "
from software_factory.governance import Retention, Artifact, DataClass
from software_factory.memory.records import utc_now
from datetime import timedelta
r = Retention()
old = Artifact(id='t1', data_class=DataClass.TRANSCRIPT, created_at=utc_now()-timedelta(days=400))
rep = r.sweep([old])   # no tombstone callable supplied
print('expired:', rep.expired); print('acted  :', rep.acted)
print('artifact still live? tombstoned =', old.tombstoned)
print('as_dict:', rep.as_dict())
"
```
```
expired: ['t1']
acted  : True
artifact still live? tombstoned = False
as_dict: {'expired': ['t1'], 'held': [], 'alreadyTombstoned': []}
```

```
cd /home/user/software-factory && python3 -c "
from software_factory.governance import Retention, Artifact, DataClass
from software_factory.memory.records import utc_now
r = Retention()
a = Artifact(id='t1', data_class=DataClass.TRANSCRIPT, created_at=utc_now(), subjects=frozenset({'amaya'}))
rep = r.erase('amaya', [a], requested_by='human:dpo')   # no destroy callable
print('erased   :', rep.erased); print('complete :', rep.complete); print('as_dict  :', rep.as_dict())
rep2 = r.erase('amaya', [], requested_by='human:dpo')
print('empty request -> complete:', rep2.complete, 'erased:', rep2.erased)
"
```
```
erased   : ['t1']
complete : True
as_dict  : {'subject': 'amaya', 'requestedBy': 'human:dpo', 'at': '2026-08-31T19:35:40.919681+00:00', 'complete': True, 'erased': ['t1'], 'unerasable': [], 'blockedByHold': []}
empty request -> complete: True erased: []
```

Note the second line of the second run: an erasure request over an *empty* artifact list also
reports `complete: True`. `Retention` never enumerates anything; it reports completeness over
whatever list it was handed, and calls that an answer.

A related defect on the same path: `erase` does not check `Artifact.tombstoned`, so a body that
retention already destroyed is destroyed again and reported as freshly erased:

```
cd /home/user/software-factory && python3 -c "
from software_factory.governance import Retention, Artifact, DataClass
from software_factory.memory.records import utc_now
r = Retention()
gone = Artifact(id='t1', data_class=DataClass.TRANSCRIPT, created_at=utc_now(), subjects=frozenset({'amaya'}), tombstoned=True)
destroyed=[]
rep = r.erase('amaya', [gone], requested_by='human:dpo', destroy=lambda a: destroyed.append(a.id))
print('re-destroyed:', destroyed, '| report.erased:', rep.erased)
"
```
```
re-destroyed: ['t1'] | report.erased: ['t1']
```

**Why it matters.** The module's own docstring says "A subject-erasure request whose answer is
'probably everything' is not an answer." The report is worse than that: it is a positive assertion of
deletion that nothing established. `ErasureReport.as_dict` is shaped to be handed to a data subject
or a regulator. Combined with I2 (no caller supplies a destructor because there is no caller), the
first production use of this API is overwhelmingly likely to be the one that omits the callback.

**Fix.** Make `tombstone` and `destroy` required parameters. If an audit-only mode is wanted, make it
explicit — `dry_run: bool` — and have the report carry it, so `acted` and `complete` are `False` by
construction on a dry run. Add an `examined: int` field to both reports so "complete" is qualified by
what was actually inspected. Skip already-tombstoned artifacts in `erase` as `sweep` already does.

---

## I4 — `Manifest.verify()` establishes nothing about the ledger

**`governance/segments.py:157-189, 192-231`**

`verify()` walks `self.segments` and checks three things: index continuity, `first_seq` continuity,
and that each segment's stored `prev_segment_digest` equals the *recomputed* digest of the segment
before it. It never opens the ledger. Three consequences, all reproduced:

1. The final segment is chained to by nothing, so any field in it can be rewritten undetected.
2. `last_hash` is never compared to any entry, so a sealed range can be rewritten in the ledger and
   the manifest still verifies.
3. `seal()` never calls `Ledger.verify()`, so an already-broken chain can be sealed and thereby
   acquire a "verifiable" manifest.

**Reproduction**

```
cd /home/user/software-factory && python3 - <<'PY'
import tempfile, json
from pathlib import Path
from software_factory.governance import Manifest, seal
from software_factory.ledger import EntryType, Ledger

tmp = Path(tempfile.mkdtemp())
led = Ledger(tmp/'ledger.jsonl')
for i in range(25):
    led.append(EntryType.RUN_STARTED, actor='worker', subject=f'run-{i}')
man = Manifest(path=tmp/'segments.jsonl')
sealed = seal(led, man, size=10)
print('sealed:', [(s.index, s.first_seq, s.last_seq, s.last_hash[:8]) for s in sealed])
man.verify(); print('baseline manifest.verify(): OK')

last = man.segments[-1]
man.segments[-1] = type(last)(index=last.index, first_seq=last.first_seq, last_seq=last.last_seq,
                              last_hash='f'*64,
                              prev_segment_digest=last.prev_segment_digest,
                              entry_count=last.entry_count, sealed_at=last.sealed_at)
man.verify(); print('after tampering with the LAST segment: verify() still OK')

man2 = Manifest.load(tmp/'segments.jsonl')
lines = (tmp/'ledger.jsonl').read_text().splitlines()
rec = json.loads(lines[4]); rec['subject'] = 'run-4-REWRITTEN'
lines[4] = json.dumps(rec, sort_keys=True, separators=(',',':'))
(tmp/'ledger.jsonl').write_text('\n'.join(lines)+'\n')
man2.verify(); print('after rewriting sealed ledger entry 5: manifest.verify() OK  <-- segment 0 covers it')
try:
    Ledger(tmp/'ledger.jsonl').verify(); print('ledger.verify(): OK')
except Exception as e:
    print('ledger.verify():', type(e).__name__, str(e)[:70])
PY
```
```
sealed: [(0, 1, 10, '65e1d9c7'), (1, 11, 20, 'bc319411')]
baseline manifest.verify(): OK
after tampering with the LAST segment: verify() still OK
after rewriting sealed ledger entry 5: manifest.verify() OK  <-- segment 0 covers it
ledger.verify(): LedgerError /tmp/tmpn8hthd1h/ledger.jsonl: content hash mismatch at entry 5
```

```
cd /home/user/software-factory && python3 - <<'PY'
import tempfile, json
from pathlib import Path
from software_factory.governance import Manifest, seal
from software_factory.ledger import EntryType, Ledger
tmp = Path(tempfile.mkdtemp())
led = Ledger(tmp/'ledger.jsonl')
for i in range(12):
    led.append(EntryType.RUN_STARTED, actor='worker', subject=f'run-{i}')
lines = (tmp/'ledger.jsonl').read_text().splitlines()
rec = json.loads(lines[2]); rec['subject'] = 'run-2-REWRITTEN'
lines[2] = json.dumps(rec, sort_keys=True, separators=(',',':'))
(tmp/'ledger.jsonl').write_text('\n'.join(lines)+'\n')
try:
    Ledger(tmp/'ledger.jsonl').verify()
except Exception as e:
    print('ledger is already broken:', type(e).__name__, str(e)[:60])
man = Manifest(path=tmp/'segments.jsonl')
s = seal(Ledger(tmp/'ledger.jsonl'), man, size=10)
print('seal() sealed the broken range anyway:', [(x.index, x.first_seq, x.last_seq) for x in s])
man.verify(); print('manifest.verify(): OK -- the tampered range is now "sealed and verifiable"')
PY
```
```
ledger is already broken: LedgerError /tmp/tmpv70c_ecf/ledger.jsonl: content hash mismatch at entr
seal() sealed the broken range anyway: [(0, 1, 10)]
manifest.verify(): OK -- the tampered range is now "sealed and verifiable"
```

**Why it matters.** `sf govern verify` calls only `manifest.verify()` (`cli.py:1349-1351`) and prints
`ok`. The module docstring claims "a factory can archive segment 0 to cold storage and still prove
that segment 1 was not rewritten" — there is no function anywhere in the codebase that compares a
segment's `last_hash` to a ledger entry, so that proof is never performed. `seal()` is worse than
neutral here: sealing a tampered ledger *launders* it, because the segment now records the tampered
hash as the sealed truth and any later comparison would be against the forgery.

**Fix.** Three changes. (a) `seal()` must call `ledger.verify()` (or verify the window it is about to
seal) before writing a segment; a broken chain must refuse to seal. (b) Add
`verify_against(ledger)` that recomputes the chain over each unarchived segment's range and compares
to `last_hash`, and make `sf govern verify` call it when the entries are present, reporting
"chain-only" explicitly when they are not. (c) The tip digest needs an anchor outside the manifest —
append a `SEGMENT_SEALED` entry to the ledger carrying the new segment's digest, so the manifest and
the ledger each commit to the other and neither can be rewritten alone.

---

# MAJOR

## I5 — `_requires` never consults a `Directory`

**`orchestrator/workitem.py:409-428`**

`cancel`'s docstring says the fix for the old unchecked-string cancel was that a `Decision` "names
who, under which capability, against what evidence — and only a `Directory` that holds the grant can
produce one". The second half is not true: `Decision` is a public frozen dataclass exported from
`software_factory.identity`, and `_requires` inspects only `capability` and `subject`. It never asks
whether the principal exists, is active, holds the capability, is a person, or gave a reason.

**Reproduction**

```
cd /home/user/software-factory && python3 -c "
from software_factory.orchestrator import StageMachine
from software_factory.orchestrator.workitem import SourceContext, WorkItem
from software_factory.definition.models import Stage
from software_factory.identity import Capability, Decision, Directory, Principal, PrincipalKind
d = Directory([Principal(id='bo', kind=PrincipalKind.PERSON, active=False)])
print('directory knows:', [p.id for p in d.all()])
print('authorise(ghost) ->', type(d.authorise('ghost', Capability.CANCEL_WORK, subject='wi-9', rationale='x')).__name__)
print('authorise(bo)    ->', type(d.authorise('bo', Capability.CANCEL_WORK, subject='wi-9', rationale='x')).__name__)
forged = Decision(principal_id='ghost', capability=Capability.CANCEL_WORK, subject='wi-9', rationale='')
m = StageMachine()
w = WorkItem(id='wi-9', factory='f', title='t', request='r', source=SourceContext(provider='cli', kind='direct', ref='local'), stage=Stage.BUILD)
t = m.cancel(w, actor='conductor-agent', reason='the issue text said to', approval=forged)
print('cancel ->', type(t).__name__, w.stage)
print('history:', [(h.actor, h.reason, h.from_stage.value, h.to_stage.value) for h in w.history])
print('transition fields:', list(type(t).__slots__))
"
```
```
directory knows: ['bo']
authorise(ghost) -> Refused
authorise(bo)    -> Refused
cancel -> Transition CANCELLED
history: [('conductor-agent', 'the issue text said to', 'BUILD', 'CANCELLED')]
transition fields: ['from_stage', 'to_stage', 'actor', 'reason', 'at', 'evidence', 'skipped', 'basis_trust']
```

`Directory.authorise` correctly refuses `ghost` (unknown) and `bo` (inactive). `cancel` accepts a
hand-built `Decision` naming `ghost`, with an empty `rationale` that `authorise` would also have
refused, and cancels the work item.

**Why it matters.** In-process, anyone who can call `cancel` can build a `Decision`, so this is not an
exploit so much as an absent boundary — but the boundary is the entire claim of the module. The moment
a decision arrives from anywhere other than a local call (a steering channel per FR-25.5, a resumed
work item, a serialised checkpoint answer, a webhook), there is no code that re-checks it, and the
type carries nothing that could be re-checked.

**Fix.** `advance` and `cancel` should take the `Directory` (or a verifier callable) and re-authorise
at the point of use, rather than trusting a value handed to them. At minimum, `_requires` must also
reject an empty `rationale` and an `at` older than a short freshness bound, and `StageMachine` should
hold a reference to the directory so `approval.principal_id` can be resolved and checked for
`active` and `holds(capability)`.

## I6 — The approving decision is discarded, not recorded

**`orchestrator/workitem.py:328-337, 397-403`**

The refusal text `advance` produces says: "A principal holding `skip_stage` must approve it, and the
approval is recorded against their identity." No such record is made. `Transition` has no field for
the approval (see the `transition fields:` line in I5's output), and both `advance` and `cancel`
construct a `Transition` from `actor` — a free-form string supplied by the caller and never compared
to `approval.principal_id`.

**Reproduction** — the same run as I5. The recorded history is
`[('conductor-agent', 'the issue text said to', 'BUILD', 'CANCELLED')]`. The authorising principal,
the capability exercised, the evidence shown and the time of the decision are all dropped on the
floor. `WorkItem.as_dict` renders `history` via `Transition.render()`, which emits only
`from -> to (skipped ...): reason`, so nothing downstream can recover it either.

**Why it matters.** FR-25.4 is "a decision without attribution is not a decision", and `Decision`
was built to carry that attribution. The one place a `Decision` is consumed throws it away, and the
ledger entry the coordinator writes (`WORK_ITEM_TRANSITION`, `actor="coordinator"`) records the
coordinator as the actor for a transition a human authorised. An auditor reading the ledger after a
REVIEW skip sees an agent's name and no evidence a person was involved.

**Fix.** Add `approval: Decision | None = None` to `Transition`, populate it in both `advance` and
`cancel`, include `approval.as_dict()` in `Transition.render`/`WorkItem.as_dict`, and reject the
transition when `actor` is set and does not equal `approval.principal_id`.

## I7 — Separation of duties does not require distinct groups between approvers

**`identity/duties.py:136-152`**

FR-25.3, quoted verbatim from `docs/PRD.md:1656`: "A self-referential change (FR-14.7) requires two
approvers from a distinct group." `validate.py:533-535` restates it: "FR-25.3 needs two distinct
approvers from distinct groups for a self-referential change." `approve()` compares each approver
against the *proposer* only. Two approvers who share a group with each other satisfy the rule.

**Reproduction**

```
cd /home/user/software-factory && python3 - <<'PY'
from software_factory.identity import (ApprovalRequest, ApprovalState, Capability, Directory,
                                       Principal, PrincipalKind, approve)
def person(i, *caps, groups=()):
    return Principal(id=i, kind=PrincipalKind.PERSON, groups=frozenset(groups), capabilities=frozenset(caps))
C = Capability.APPROVE_SELF_REFERENTIAL_CHANGE
d = Directory([person('amaya', C, groups=('maintainers',)),
               person('bo', C, groups=('platform',)),
               person('cass', C, groups=('platform',))])
st = ApprovalState(request=ApprovalRequest(subject='scorers/tests-actually-run', proposer_id='amaya', self_referential=True))
st = approve(d, st, principal_id='bo', rationale='reads fine')
st = approve(d, st, principal_id='cass', rationale='reads fine to me too')
print('satisfied:', st.satisfied, '| approvers:', [x.principal_id for x in st.approvals],
      '| their groups:', [sorted(d.get(x.principal_id).groups) for x in st.approvals])
PY
```
```
satisfied: True | approvers: ['bo', 'cass'] | their groups: [['platform'], ['platform']]
```

**Why it matters.** The stated failure mode is capture, not carelessness. Two members of one team
approving a change to what counts as success is exactly the correlated judgement the second approver
exists to break. `validate.py` even warns an operator to "Grant it to a second principal in a
different group" — the configuration is checked for a property the runtime does not enforce, which is
the worst of both: the operator is told the property holds and it does not.

**Fix.** In `approve()`, when `request.self_referential`, run `_shares_group` between the candidate
and *every existing approver* as well as the proposer, and refuse on any overlap.

## I8 — Duplicate provider identities: `sf validate` clean, `sf principals` crashes

**`definition/validate.py:475-573`, `identity/principals.py:197-204`**

`Directory.add` raises `ValueError` when one provider identity maps to two principals — correctly,
since "an ambiguous identity cannot attribute a decision". `_check_principals` does not perform that
check, so the condition passes validation and detonates at the point of use.

**Reproduction**

```
cd /home/user/software-factory && python3 - <<'PY'
import tempfile, subprocess, sys
from pathlib import Path
from software_factory.scaffold import init_factory
tmp = Path(tempfile.mkdtemp())
init_factory(tmp, name='reference', owner='amaya', repo='service')
(tmp/'principals'/'mallory.yaml').write_text(
    "id: mallory\nkind: person\nidentities:\n  - git-host:amaya\ncapabilities:\n  - approve_spec\n", encoding='utf-8')
r = subprocess.run([sys.executable,'-m','software_factory.cli','validate',str(tmp)],capture_output=True,text=True)
print('sf validate rc=', r.returncode); print(r.stdout[-300:])
r = subprocess.run([sys.executable,'-m','software_factory.cli','principals',str(tmp)],capture_output=True,text=True)
print('sf principals rc=', r.returncode); print((r.stderr or r.stdout)[-400:])
PY
```
```
sf validate rc= 0
clean — /tmp/tmpzenjkod9

sf principals rc= 1
us identity cannot   │
│       attribute a decision"                                                  │
│   203 │   │   │   │   )                                                      │
╰──────────────────────────────────────────────────────────────────────────────╯
ValueError: provider identity 'git-host:amaya' maps to both 'amaya' and 
'mallory'; an ambiguous identity cannot attribute a decision
```

**Why it matters.** `_check_principals`'s whole purpose is to meet the problem "against the file and
the line, which is a better place for a reader to meet the problem than a traceback"
(`loading.py:22-26`). For this specific error it does the opposite. Worse, the same broken definition
reaches `intake/loading.py:58`, so a factory in this state fails to start intake at all — with a
traceback rather than a validation error naming the two files.

**Fix.** Add the identity collision check to `_check_principals`: build a `dict[str, str]` of
identity → principal id while walking `definition.principals` and emit
`principal.ambiguous_identity` (ERROR) with the path of the second file, mirroring the message
`Directory.add` already writes.

## I9 — Only half the checkpoint capabilities are checked for holders

**`definition/validate.py:540`**

`ANSWERED_BY` maps six checkpoint kinds to six capabilities. `_check_principals` warns about three:
`approve_spec`, `answer_question`, `adopt_definition_change`. `CheckpointBook.open` raises
`ValueError` for any kind whose capability has no holder, so the remaining three produce an
uncaught exception at run time in a factory that validated clean.

**Reproduction**

```
cd /home/user/software-factory && python3 - <<'PY'
import tempfile
from pathlib import Path
from software_factory.scaffold import init_factory
from software_factory.definition.loader import load, load_strict
from software_factory.definition.validate import validate
from software_factory.identity.loading import directory_from
from software_factory.identity import CheckpointBook, Checkpoint, CheckpointKind, Capability, ANSWERED_BY

tmp = Path(tempfile.mkdtemp())
init_factory(tmp, name='reference', owner='amaya', repo='service')
for p in (tmp/'principals').glob('*.yaml'):
    t = p.read_text()
    p.write_text('\n'.join(l for l in t.splitlines() if 'override_gate' not in l) + '\n')
definition, report = load(tmp)
validate(definition, report)
print('validation errors  :', [i.code for i in report.errors])
print('validation warnings:', [i.code for i in report.warnings])
book = CheckpointBook(directory=directory_from(load_strict(tmp)))
print('holders(OVERRIDE_GATE):', [p.id for p in book.directory.holders(Capability.OVERRIDE_GATE)])
try:
    book.open(Checkpoint(id='cp', kind=CheckpointKind.GATE_OVERRIDE, work_item_id='wi-1',
                         question='override the failing gate?', asked_by='builder'))
except ValueError as e:
    print('opening a GATE_OVERRIDE checkpoint ->', type(e).__name__, str(e)[:90])
warned = {"approve_spec", "answer_question", "adopt_definition_change"}
print('checkpoint capabilities NOT covered by validate:', sorted(c.value for c in ANSWERED_BY.values() if c.value not in warned))
PY
```
```
validation errors  : []
validation warnings: []
holders(OVERRIDE_GATE): []
opening a GATE_OVERRIDE checkpoint -> ValueError no principal holds override_gate, so a gate_override checkpoint could never be cleared; gr
checkpoint capabilities NOT covered by validate: ['approve_self_referential_change', 'override_gate', 'widen_blast_radius']
```

**Why it matters.** The comment above the loop states the intent — "Warn where the factory is one
checkpoint away from a stall it cannot clear" — and then hard-codes a subset that omits the three
highest-authority checkpoints in the system. `override_gate` and `widen_blast_radius` are the two
whose absence most needs an operator's attention.

**Fix.** Iterate `ANSWERED_BY.values()` instead of a literal tuple, so the warning set is derived
from the same map the checkpoints route through and cannot drift from it. Consider raising the
severity to ERROR for `override_gate`, since `CheckpointBook.open` treats it as fatal anyway.

## I10 — Fingerprint deduplication is never invoked

**`economics/scheduling.py:67-77, 160-171`**

FR-26.3 requires "per-source rate limits, deduplication by fingerprint, and a circuit breaker".
`Backpressure.admit` gates its dedupe on `if item.fingerprint:`. Nothing in `src/` ever produces a
non-empty fingerprint.

**Reproduction**

```
cd /home/user/software-factory && grep -rn "fingerprint_of" --include=*.py src/ tests/
cd /home/user/software-factory && grep -rn "fingerprint" --include=*.py src/software_factory/intake/
```
```
src/software_factory/economics/__init__.py:13:    fingerprint_of,
src/software_factory/economics/__init__.py:40:    "fingerprint_of",
src/software_factory/economics/scheduling.py:67:def fingerprint_of(*parts: str) -> str:
tests/test_intake.py:440:    from software_factory.economics import fingerprint_of
tests/test_intake.py:442:    assert fingerprint_of("deploy failed", "payments") != fingerprint_of("deploy failedpayments")
tests/test_economics.py:28:    fingerprint_of,
tests/test_economics.py:170:    print_ = fingerprint_of("deploy failed", "payments", "prod")
tests/test_economics.py:180:    assert fingerprint_of("Deploy Failed ", "PAYMENTS") == fingerprint_of(
tests/test_economics.py:183:    assert fingerprint_of("deploy failed", "payments") != fingerprint_of("deploy failed", "search")
src/software_factory/intake/events.py:90:    fingerprint: str = ""
src/software_factory/intake/events.py:108:            "fingerprint": self.fingerprint,
src/software_factory/intake/pipeline.py:164:                fingerprint=event.fingerprint,
src/software_factory/intake/pipeline.py:198:                        fingerprint=event.fingerprint,
```

`fingerprint_of` has no caller outside tests. `FactoryEvent.fingerprint` has a default of `""` and no
adapter or constructor in `src/` sets it. `Pipeline` therefore always passes `fingerprint=""` to
`Backpressure.admit`, and the dedupe branch never executes.

**Why it matters.** The module docstring names the failure this defends against: "A failing deploy
emits thousands of alerts; each one looks like a legitimate work item." Without the fingerprint, the
only thing standing between a signal storm and spend is the rate limiter — and the rate limiter's
response to a storm is to trip the breaker and park the source, which is the outcome the ordering
comment explicitly says must be avoided ("a storm of *identical* alerts trip the breaker and park a
source over work that was never real"). The stated policy ordering is correct and inert.

**Fix.** Populate `FactoryEvent.fingerprint` in the adapters from the stable content of the signal
(alert rule id + service + environment for monitoring; error signature for tracking) using
`fingerprint_of`, and make `Backpressure.admit` distinguish "no fingerprint offered" from "fingerprint
offered and unseen" — a source class that is expected to repeat should be refused rather than admitted
when it presents no fingerprint.

## I11 — Ageing has no floor: an aged routine item outranks a fresh incident

**`economics/scheduling.py:48, 61-64`**

`effective_priority` is `float(priority) - waited_hours * 0.25`, unbounded below. `LOW` is 3 and
`URGENT` is 0, so a LOW item that has waited 12 hours ties a brand-new URGENT one and beats it at 13.

**Reproduction**

```
cd /home/user/software-factory && python3 - <<'PY'
from datetime import timedelta
from software_factory.economics import Scheduler, Queued, Priority
from software_factory.memory.records import utc_now
now = utc_now()
s = Scheduler()
for i in range(5):
    s.enqueue(Queued(id=f'routine-{i}', source='tracker', priority=Priority.LOW, queued_at=now - timedelta(hours=13)))
s.enqueue(Queued(id='INCIDENT', source='tracker', priority=Priority.URGENT, queued_at=now))
print('effective priorities:',
      round(Queued(id='x', source='t', priority=Priority.LOW, queued_at=now-timedelta(hours=13)).effective_priority(now), 3),
      'vs URGENT', Queued(id='y', source='t', priority=Priority.URGENT, queued_at=now).effective_priority(now))
print('drain order:', [q.id for q in s.drain(6, now=now)])
PY
```
```
effective priorities: -0.25 vs URGENT 0.0
drain order: ['routine-0', 'routine-1', 'routine-2', 'routine-3', 'routine-4', 'INCIDENT']
```

**Why it matters.** Overnight is longer than 13 hours. Any factory with a routine backlog will start
each morning with every aged LOW item ranked above an incident filed that minute, and there is no
bound: a hundred aged items delay the incident by a hundred slots. The comment claims a gradient is
chosen over a timeout so "an operator watching the queue sees an inexplicable reordering" is avoided,
but an urgent incident sorted below yesterday's chores is exactly that.

**Fix.** Clamp the ageing bonus so it cannot cross a priority band: `max(float(priority) - waited *
AGEING_PER_HOUR, float(priority) - 1.0)` lifts an item by at most one band, which prevents starvation
without inverting the declared order. If crossing bands is genuinely wanted, floor the result at
`float(Priority.URGENT)` so nothing can ever outrank a fresh urgent item.

## I12 — `overhead_fraction` is presented as measured and cannot be

**`economics/spend.py:33-46, 145-156`**

`Cause` declares seven categories and `overhead_fraction` is documented as "retries, repairs,
scoring, benchmarking and improvement are all real costs of running a factory, and a factory spending
most of its money on them is a factory doing something wrong". Two of the seven are ever emitted, by
one call site, at one charge per stage-run:

```
cd /home/user/software-factory && grep -rn '"cause"' --include=*.py src/ ; grep -rn "MODEL_CALLED" --include=*.py src/ | grep -v cli.py
```
```
src/software_factory/cli.py:1404:        raw_cause = str(payload.get("cause", Cause.PRIMARY.value))
src/software_factory/orchestrator/coordinator.py:392:                "cause": "repair" if run.repair_attempts else "primary",
src/software_factory/orchestrator/coordinator.py:379:            EntryType.MODEL_CALLED,
src/software_factory/observability/metrics.py:373:        if entry.type is EntryType.MODEL_CALLED:
src/software_factory/observability/views.py:159:        if e.type is EntryType.MODEL_CALLED
src/software_factory/ledger/log.py:269:        `MODEL_CALLED` mean a busy factory writes thousands of entries a day, so at 100k an
src/software_factory/ledger/entry.py:33:    MODEL_CALLED = "model.called"
```

There is one emitter, and it attributes a run's *entire* spend to `repair` if the run repaired at all
and to `primary` otherwise. `retry`, `scoring`, `benchmark`, `improvement` and `onboarding` never
appear. So `overheadFraction` in `sf spend --json` does not mean "the share not spent on primary
work"; it means "the share of spend belonging to runs that repaired at least once", which both
over-counts (a repaired run's primary work is booked as overhead) and under-counts (all scoring and
benchmarking is booked as primary).

The window also has no upper bound, so a charge dated in the future is counted as current spend:

```
cd /home/user/software-factory && python3 -c "
from datetime import timedelta
from software_factory.economics import Ledgerless, SpendCap, Charge, Cause
from software_factory.memory.records import utc_now
now = utc_now()
acc = Ledgerless(SpendCap(scope='f', limit_units=100, period=timedelta(days=1)))
c = Charge(units=90, work_item_id='wi', agent='a', stage='s', cause=Cause.PRIMARY, at=now + timedelta(days=30))
r = acc.report([c], now=now)
print('future-dated charge counted:', r.spent, r.state)
"
```
```
future-dated charge counted: 90.0 warning
```

**Why it matters.** FR-26.5's justification is that "we spent £400 today" cannot be separated into
work, retries, scoring, and benchmarking, "and those four have completely different answers to 'is
this a problem'". The separation is offered by the type and not produced by the system, so the number
an operator reads is a differently-defined quantity wearing the name of the one they asked for. The
emitting code is outside this review's scope; the claim is made by `spend.py`.

**Fix.** Either emit the missing causes (scorers, benchmarks and the improvement loop each need their
own `MODEL_CALLED` entry with the right `cause`) or narrow `overhead_fraction`'s docstring and rename
it to what it measures. Add a `SpendReport.unattributed: float` (or `priced: int` / `unpriced: int`)
so the report states how much spend it could not attribute rather than implying there was none. Filter
the window at both ends: `start <= c.at <= now`.

---

# MINOR

## I13 — `resolve_identity` ignores `active`

**`identity/principals.py:209-217`**

`holders()` filters on `Principal.holds`, which checks `active`. `resolve_identity` does not, and it
is the function the intake known-author gate consults.

```
cd /home/user/software-factory && python3 -c "
from software_factory.identity import Directory, Principal, PrincipalKind, Capability
d = Directory([Principal(id='amaya', kind=PrincipalKind.PERSON, active=False, capabilities=frozenset(), identities=frozenset({'git-host:amaya-r'}))])
p = d.resolve_identity('git-host', 'amaya-r')
print('resolve_identity ->', p.id if p else None, '| active =', p.active if p else None)
print('holders(APPROVE_SPEC):', [x.id for x in d.holders(Capability.APPROVE_SPEC)])
"
```
```
resolve_identity -> amaya | active = False
holders(APPROVE_SPEC): []
```

`Pipeline._author_check` (`intake/pipeline.py:225-239`) treats "not None" as "known author", so
deactivating a departed maintainer does not stop their handle from triggering automations that
require a known author. **Fix:** either filter inactive principals in `resolve_identity`, or return
them and make every caller check `active` — the first is safer because the second has to be
remembered at each site.

## I14 — Self-approval and the group check are raw string comparisons

**`identity/duties.py:107, 155-166`**

`approve()` refuses self-approval by `principal_id == request.proposer_id`, and `_shares_group`
returns `None` (no conflict) whenever either id is not in the directory. A proposer recorded by
anything other than their exact principal id — a provider handle, for instance — defeats both.

```
cd /home/user/software-factory && python3 - <<'PY'
from software_factory.identity import (ApprovalRequest, ApprovalState, Capability, Directory,
                                       Principal, PrincipalKind, approve)
def person(i, *caps, groups=()):
    return Principal(id=i, kind=PrincipalKind.PERSON, groups=frozenset(groups), capabilities=frozenset(caps))
C = Capability.APPROVE_SELF_REFERENTIAL_CHANGE
d = Directory([person('amaya', C, groups=('maintainers',))])
st = ApprovalState(request=ApprovalRequest(subject='scorers/tests-actually-run',
                                           proposer_id='git-host:amaya-r', self_referential=True))
r = approve(d, st, principal_id='amaya', rationale='my own change, and I like it')
print('amaya approving her own proposal ->', type(r).__name__, [x.principal_id for x in getattr(r, 'approvals', ())])
PY
```
```
amaya approving her own proposal -> ApprovalState ['amaya']
```

**Fix.** Resolve `proposer_id` through the directory at `ApprovalRequest` construction (accepting
either a principal id or a `provider:handle`) and store the resolved principal id. Make
`_shares_group` refuse rather than pass when either principal cannot be resolved — an unresolvable
proposer is a reason not to approve, not a reason to skip the check.

## I15 — Identity case normalisation is done in the loader, not in `Directory`

**`identity/principals.py:197-204, 216`**

`add` stores `principal.identities` verbatim; `resolve_identity` lower-cases the lookup key.
`loading.directory_from` happens to lower-case on the way in, so the file-loaded path is consistent —
but a `Directory` built in code is not, and the duplicate-identity guard compares un-normalised
strings.

```
cd /home/user/software-factory && python3 - <<'PY'
from software_factory.identity import Directory, Principal, PrincipalKind
def p(i, ident): return Principal(id=i, kind=PrincipalKind.PERSON, identities=frozenset({ident}))
d = Directory([p('amaya', 'git-host:Amaya-R')])
print('(a) resolve("git-host","Amaya-R") ->', d.resolve_identity('git-host', 'Amaya-R'))
print('(a) resolve("git-host","amaya-r") ->', d.resolve_identity('git-host', 'amaya-r'))
d2 = Directory([p('amaya', 'git-host:amaya'), p('mallory', 'git-host:AMAYA')])
print('(b) added with no "maps to both" error:', [x.id for x in d2.all()])
print('(b) resolve("git-host","AMAYA") ->', d2.resolve_identity('git-host','AMAYA').id, " <- mallory's own handle resolves to amaya")
PY
```
```
(a) resolve("git-host","Amaya-R") -> None
(a) resolve("git-host","amaya-r") -> None
(b) added with no "maps to both" error: ['amaya', 'mallory']
(b) resolve("git-host","AMAYA") -> amaya  <- mallory's own handle resolves to amaya
```

In (a) the identity is unreachable by any spelling. In (b) the ambiguity guard is silently bypassed
and one principal's declared handle resolves to a different principal. **Fix:** normalise in
`Directory.add` (`identity.lower()`) rather than in the loader, so every construction path shares one
rule and the collision check compares the same strings the lookup uses.

## I16 — `Directory` documents an immutability it does not implement

**`identity/principals.py:179-204`**

"Nothing here mutates at run time: a factory that can grant itself a capability mid-run has no
capability model." `add` is public, and `CheckpointBook.directory` is a plain attribute.

```
cd /home/user/software-factory && python3 -c "
from software_factory.identity import (Capability, Checkpoint, CheckpointBook, CheckpointKind, Directory, Principal, PrincipalKind)
book = CheckpointBook(directory=Directory([Principal(id='amaya', kind=PrincipalKind.PERSON, capabilities=frozenset({Capability.APPROVE_SPEC}))]))
book.open(Checkpoint(id='cp-2', kind=CheckpointKind.SPEC_APPROVAL, work_item_id='wi-1', question='?', asked_by='architect'))
book.directory.add(Principal(id='new-guy', kind=PrincipalKind.PERSON, capabilities=frozenset({Capability.APPROVE_SPEC})))
print(book.resolve('cp-2', principal_id='new-guy', answer='approved'))
"
```
```
Decision(principal_id='new-guy', capability=<Capability.APPROVE_SPEC: 'approve_spec'>, subject='spec_approval:wi-1', rationale='approved', at=..., evidence_shown=(), channel='cli')
```

**Fix.** Make `add` private (`_add`, used only by the constructor and `loading`), or add a `seal()`
that flips a flag `add` checks. The docstring is the design intent; the class should enforce it.

## I17 — `PERSON_ONLY` omits capabilities the PRD reserves to people

**`identity/principals.py:80-92`**

The module docstring: "the baseline said 'a human' must approve, override, widen, force-promote and
**emergency-stop**". `PERSON_ONLY` has four of those five. `ANSWER_QUESTION` is also absent, and
`CheckpointKind.QUESTION` is one of the FR-16.1 default *human* checkpoints.

```
cd /home/user/software-factory && python3 -c "
from software_factory.identity import (Capability, PERSON_ONLY, Checkpoint, CheckpointBook, CheckpointKind, Directory, Principal, PrincipalKind)
print('not person-only:', sorted(c.value for c in Capability if c not in PERSON_ONLY))
agent = Principal(id='conductor', kind=PrincipalKind.AGENT, capabilities=frozenset({Capability.ANSWER_QUESTION}))
book = CheckpointBook(directory=Directory([agent]))
book.open(Checkpoint(id='cp-1', kind=CheckpointKind.QUESTION, work_item_id='wi-1', question='Drop the legacy column?', asked_by='builder'))
print('routable_to:', book.routable_to('cp-1'))
d = book.resolve('cp-1', principal_id='conductor', answer='yes, drop it')
print('resolved by an agent ->', d.principal_id, '| status:', book.checkpoints['cp-1'].status)
"
```
```
not person-only: ['answer_question', 'emergency_stop', 'steer_run']
routable_to: ['conductor']
resolved by an agent -> conductor | status: resolved
```

An agent principal holding `answer_question` is routed the human checkpoint and clears it. The
`emergency_stop` gap is lower risk (an automation that can stop things is a safe direction) but it
contradicts the module's own sentence and the PRD's. **Fix:** add `emergency_stop` to `PERSON_ONLY`
if the PRD sentence is meant, or amend the docstring. For `answer_question`, split the checkpoint: a
question a person must answer and a question an agent may answer are different checkpoints and should
not share one capability.

## I18 — The breaker is cheap to trip and expensive to survive

**`economics/scheduling.py:95-105, 174-199`**

At the defaults `Pipeline` uses, 33 events park a source for an hour.

```
cd /home/user/software-factory && python3 - <<'PY'
from datetime import timedelta
from collections import Counter
from software_factory.economics import Backpressure, Queued, Admitted
from software_factory.memory.records import utc_now
gate = Backpressure()
print('defaults:', gate.limits)
now = utc_now()
codes = []
for i in range(34):
    r = gate.admit(Queued(id=f'e{i}', source='git-host:acme/payments', queued_at=now), now=now)
    codes.append(type(r).__name__ if isinstance(r, Admitted) else r.code)
print(Counter(codes))
st = gate.state_for('git-host:acme/payments')
print('parked_until - now =', st.parked_until - now)
later = gate.admit(Queued(id='real-bug', source='git-host:acme/payments', queued_at=now+timedelta(minutes=20)), now=now+timedelta(minutes=20))
print('legitimate item 20 min later ->', later.code, later.retry_after)
PY
```
```
defaults: SourceLimits(max_per_window=30, window=datetime.timedelta(seconds=600), breaker_trips=3, breaker_cooldown=datetime.timedelta(seconds=3600))
Counter({'Admitted': 30, 'intake.rate_limited': 2, 'intake.breaker_tripped': 1, 'intake.source_parked': 1})
parked_until - now = 1:00:00
```
```
legitimate item 20 min later -> intake.source_parked 0:40:00
```

`Pipeline` sources are `f"{provider}:{origin.ref}"`, so anyone who can file 33 events against a
repository can suppress that repository's intake for an hour, repeatably, at ~33 events/hour of
effort. With I10 (no fingerprints) there is nothing that distinguishes a storm from a busy hour.
**Fix:** the breaker should require sustained pressure across *distinct windows* rather than three
consecutive rejections inside one, and reopening should be gradual (admit at a reduced rate first)
rather than a binary park. Reject-without-parking should also be the response when the source has a
history of producing real work.

## I19 — The reminder escalation is skipped when the sweeper was not running

**`identity/checkpoints.py:96-109, 194-208`**

`due_state` returns the terminal state for the elapsed time, so a sweeper that missed the reminder
window jumps straight to `PARKED` and no `REMINDED` change is ever returned — the reminder is never
sent.

```
cd /home/user/software-factory && python3 -c "
from datetime import timedelta
from software_factory.identity import Capability, Checkpoint, CheckpointBook, CheckpointKind, Directory, Principal, PrincipalKind
from software_factory.memory.records import utc_now
book = CheckpointBook(directory=Directory([Principal(id='amaya', kind=PrincipalKind.PERSON, capabilities=frozenset({Capability.APPROVE_SPEC}))]))
opened = utc_now()
book.open(Checkpoint(id='cp-1', kind=CheckpointKind.SPEC_APPROVAL, work_item_id='wi-1', question='?', asked_by='architect', opened_at=opened))
print('sweep at +49h:', book.sweep(now=opened + timedelta(hours=49)))
"
```
```
sweep at +49h: [('cp-1', <CheckpointStatus.PARKED: 'parked'>)]
```

`due_state`'s docstring says it is computed "so a process that was not running when a deadline passed
does not miss it" — it does not miss the *deadline*, it silently drops the *escalation*. FR-16.4 is
"escalates and then parks". **Fix:** `sweep` should emit every stage the checkpoint passed through
since its last recorded status, not just the final one, so the reminder is still sent alongside the
park.

## I20 — `ApprovalRequest.definition_change` is accepted and ignored

**`identity/duties.py:41, 43-55`**

```
cd /home/user/software-factory && python3 -c "
from software_factory.identity import ApprovalRequest
a = ApprovalRequest(subject='factory/agents/builder.md', proposer_id='amaya', definition_change=True)
b = ApprovalRequest(subject='factory/agents/builder.md', proposer_id='amaya', definition_change=False)
print('True  ->', a.required_approvals, a.capability)
print('False ->', b.required_approvals, b.capability)
print('identical:', (a.required_approvals, a.capability) == (b.required_approvals, b.capability))
" ; grep -rn "definition_change" src/software_factory/identity/
```
```
True  -> 1 adopt_definition_change
False -> 1 adopt_definition_change
identical: True
src/software_factory/identity/principals.py:64:    ADOPT_DEFINITION_CHANGE = "adopt_definition_change"
src/software_factory/identity/duties.py:41:    definition_change: bool = False
```

(The only hit in `identity/` is the field declaration itself; `principals.py:64` is the enum member,
matched as a substring.)

The field is declared and read by nothing — `required_approvals` and `capability` branch only on
`self_referential`, so a request that is *not* a definition change is still required to be approved
under `adopt_definition_change`. `tests/test_identity.py:219` sets it, which is why it looks live.
**Fix:** delete the field, or make `capability` return `None`/raise for a request that is neither a
definition change nor self-referential, so the type cannot express a request nobody can classify.

---

# Tests that assert the wrong thing

These are the most dangerous findings, because each one converts a defect into a documented
guarantee. All six pass today.

**T1 — `tests/test_governance.py:111` `test_an_artifact_past_its_retention_expires`.** Calls
`retention.sweep([old])` with no `tombstone` callable and then asserts `report.acted`. Nothing was
tombstoned; `old.tombstoned` is still `False`. The test's name says an artifact expired and its final
assertion says the sweep acted, and neither happened. This encodes I3 as correct behaviour.

**T2 — `tests/test_governance.py:277` `test_an_erasure_report_renders_every_outcome`.** Calls
`retention.erase(...)` with no `destroy` callable and asserts `body["complete"] is True` and
`body["erased"] == ["t1"]`. It asserts, as the specified rendering of an erasure report, a receipt
for a deletion that did not occur. This is the second half of I3, blessed.

**T3 — `tests/test_governance.py:334` `test_altering_an_earlier_segment_breaks_every_later_digest`.**
The name promises "every later digest". The test tampers with `segments[0]` in a two-segment manifest
and asserts the *second* segment's chain check fires. It proves only that a non-final segment is
protected. Tampering with `segments[-1]` — the one case the chain cannot cover — passes `verify()`
silently (I4) and no test covers it. A reader takes the name as the guarantee.

**T4 — `tests/test_economics.py:64` `test_at_the_cap_intake_stops_and_running_work_finishes`.** The
name asserts a system behaviour. The body asserts two boolean properties on an enum member. Nothing
in `src/` reads either property (I2), so intake does not stop at the cap and running work is not
governed by it. The test passes and the sentence it is named for is false.

**T5 — `tests/test_economics.py:311` `test_a_recently_queued_low_item_still_waits`,** docstring "The
fix must not invert priority outright." It compares a *fresh* LOW against a *fresh* HIGH, which is the
one configuration in which ageing cannot invert anything, because both ageing terms are zero. It
provides no coverage of the bound it claims to defend, and the ageing term does invert priority after
12 hours (I11).

**T6 — `tests/test_identity.py:595` `test_an_approval_for_one_work_item_does_not_authorise_another`,**
docstring "An approval is for one decision, not a token to reuse." It tests only a *mismatched*
subject. The empty subject — which `_requires` accepts as a wildcard for every work item, and which
`Directory.authorise` will mint (I1) — is untested, so the test's stated guarantee is the opposite of
the code's actual behaviour on the case that matters.

**Also noted, not a test defect but worth removing:** `tests/test_identity.py:580` asserts
`assert Blocker` — a truthiness check on an imported enum class, which can never fail. The comment
beside it ("the blocker enum is what a caller records next") reads like a check and is not one.

---

# What I checked and found sound

- **`Principal.__post_init__`'s `PERSON_ONLY` guard.** An agent or automation genuinely cannot be
  constructed holding `approve_spec` and friends; `dataclasses.replace` re-runs the validator. The
  gap is which capabilities are in the set (I17), not the enforcement.
- **`Directory.authorise` itself.** Unknown, inactive, missing-capability and empty-rationale are all
  refused with distinct codes and useful remediation, and the refusal names the holders. It is the
  one control in the reviewed set that is both correct and complete — it simply has almost no callers
  and its output is not re-checked by the one consumer that exists.
- **`duties.approve`'s duplicate-approval check.** Counting one principal's approval twice is
  correctly refused; the ordering (self → duplicate → capability → group) is sound and the group
  check returns before the approval is appended, so a refused approval is never recorded.
- **`digest_parts` / `fingerprint_of` construction.** Length-prefixing is injective; I could not
  construct a collision between distinct part sequences. The function is correct — it is just never
  called (I10).
- **`Classification.__post_init__`.** Both invariants (personal data ⇒ erasable, credentials ⇒
  redacted at capture) fire, and `DEFAULT_CLASSIFICATION` satisfies them for all eight classes.
  `classes_holding` and `expires_at_age` behave as documented.
- **Legal hold semantics in `Retention`.** The hold check genuinely precedes the expiry check in both
  `sweep` and `erase`; holds cover artifacts created after placement; lifting is non-destructive and
  restores expiry; the duplicate-hold-id guard works; `Retention.__init__` copies both the holds list
  and the classification dict, so a caller's list cannot be mutated through it.
- **`Backpressure` ordering and window arithmetic.** Dedupe genuinely precedes rate limiting, a
  duplicate does not consume a rate-limit slot, the window rolls correctly, `consecutive_trips`
  resets on a successful admission, the cooldown reopens without human action, and one source's park
  does not affect another. The defects are the unused fingerprint (I10) and the trip economics (I18),
  not the state machine.
- **`ConcurrencyLimiter`.** Global and per-agent bounds are both enforced, `release` is idempotent,
  and a limit below one is refused. (Acquiring the same `run_id` twice for one agent consumes one
  slot rather than two — a caller bug, not reachable from anything in `src/`.)
- **`Scheduler` round-robin.** Sources genuinely take turns, priority orders within a source, and
  `depth_by_source`/`__len__` agree with the queues. The ageing term is the defect (I11). (Two items
  with the same id in *different* sources are both accepted and both drained; the duplicate guard is
  per-source. Not reachable from `Pipeline`, whose ids embed the source.)
- **`SpendCap` threshold ordering and `state_for` boundaries.** `>=` at each threshold is right, the
  ordering invariant is enforced at construction, a zero cap is refused, and negative charges are
  refused at construction. `Ledgerless.report`'s four-way attribution sums correctly.
- **`Segment.digest` and `Manifest` round-tripping.** The digest covers every identifying field
  including `prev_segment_digest`, `from_dict`/`as_dict` round-trip losslessly, `seal` only seals
  complete segments and resumes correctly from `sealed_through`, and `_check_contiguous` runs before
  the segment is appended. The defect is what `verify()` does not check (I4), not how a segment is
  built.
- **`WorkItem.returned_to_earlier_stage` and `DEFAULT_ORDER`.** The documented correction holds:
  `BLOCKED` resumes are not counted as rework, and the order is explicit rather than derived from the
  transition table's key order.
- **`StageMachine.advance`'s other three refusals.** Terminal items, illegal transitions, and an
  untrusted basis are all refused before anything mutates, and `skipped_between` correctly measures
  from `parked_at` when leaving `BLOCKED` — the "park it, then hand it off" bypass is genuinely closed.
- **`_check_principals`' unknown-capability and person-only checks.** Both fire with the file path and
  the `capabilities` key, and `loading.directory_from` drops unrecognised names rather than raising.
  I specifically tested whether `name in set(Capability)` mis-filters on `StrEnum` hashing — it does
  not; members hash by value and every declared capability survives the filter.

**Scope not covered.** I did not review `intake/`, `orchestrator/coordinator.py`, `harness/`, or the
CLI except where a reviewed module's behaviour is only observable through them (I2, I8, I10, I12).
`memory/records.utc_now` was taken as returning an aware UTC datetime, which it does; naive datetimes
passed into `Charge.at`, `Artifact.created_at` or `Checkpoint.opened_at` raise `TypeError` on
comparison rather than silently mis-ordering, which is the safe direction.
