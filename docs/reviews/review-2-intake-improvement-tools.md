# Adversarial review 2 — intake, improvement loop, factory tools

Scope: `intake/{events,adapters,pipeline,loading}.py`, `improvement/{clustering,loop}.py`,
`factory_tools/{leases,server}.py`, `digests.py`, and their tests.

Baseline: `pytest tests/test_intake.py tests/test_improvement.py tests/test_factory_tools.py`
→ **97 passed**. Every finding below reproduces against that same tree; no source file was
modified. Reproduction scripts were run from `/home/user/software-factory`.

Scripts live in `$SCRATCH`, where
`SCRATCH=/tmp/claude-0/-home-user-software-factory/09c0eec8-47d0-539f-9cfe-1f7a54d66f58/scratchpad`;
each section below abbreviates that prefix as `.../scratchpad`. Commands used:

| script | run as | covers |
|---|---|---|
| `n1_dedupe_order.py` | `python3 $SCRATCH/n1_dedupe_order.py` | N2 |
| `n2_filters.py` | `python3 $SCRATCH/n2_filters.py` | N5, N6, N21 |
| `n5_authortrust.py` | `python3 $SCRATCH/n5_authortrust.py` | N1 |
| `mkfactory.py` | `python3 $SCRATCH/mkfactory.py $D` then `python3 -m software_factory.cli lint $D` / `... intake $D ...` | N1, N6 (end to end) |
| `n6_split.py` | `python3 $SCRATCH/n6_split.py` | N7, N8 |
| `n7_thrash.py` | `python3 $SCRATCH/n7_thrash.py` | N3, N12 |
| `n8_drift.py` | `python3 $SCRATCH/n8_drift.py` | N13 |
| `n9_wiring.py` | `python3 $SCRATCH/n9_wiring.py` | N4 |
| `n10_leases.py` | `python3 $SCRATCH/n10_leases.py` | N11, N20 |
| `n11_identity.py` | `python3 $SCRATCH/n11_identity.py` | N17, N18, digest injectivity |
| `n12_backpressure.py` | `python3 $SCRATCH/n12_backpressure.py` | N9, N10, N14 |
| `n13_tests.py` | `python3 $SCRATCH/n13_tests.py` | N16, T1, T5, and N3's bare-failure lever |
| `n14_ordering.py` | `python3 $SCRATCH/n14_ordering.py` | T3 |
| `n15_misc.py` | `python3 $SCRATCH/n15_misc.py` | N19, N21 |
| `n16_submit.py` | `python3 $SCRATCH/n16_submit.py` | N15 |

Three findings are CRITICAL. The dominant pattern from the previous review repeats and is
worse here than last time: **eight of the nine functions that implement the improvement
loop's stated safety properties are never called from any non-test module**, and the two
intake controls with live consequences (`Registry.check`, `overlapping_keys`) are not called
either. A second pattern is new: several guards run *before* the thing they are supposed to
protect, so the guard fires on events the system then refuses for an unrelated reason and
can never see again.

## Summary

| id | file:line | one-line description | severity | verified |
|----|-----------|----------------------|----------|----------|
| N1 | `intake/loading.py:47,69` | The documented author-trust opt-out `authorTrust: any` is left in the match filter, so the automation fires **only** for events that carry an attacker-chosen attribute — and fires with the author check disabled | CRITICAL | yes |
| N2 | `intake/pipeline.py:127` | The deduplicator records an event before health/backpressure can refuse it, so every refused event is permanently unretryable; FR-18.9's "park rather than drop" is false | CRITICAL | yes |
| N3 | `improvement/loop.py:333`, `clustering.py:146-152` | Rejected-signature suppression is keyed on a cluster signature that changes with input order; and `settle()` erases a rejection outright. A rejected proposal returns with no new evidence | CRITICAL | yes |
| N4 | `improvement/loop.py:208,235,258,349,385,437,442` | `may_propose`, `check_effectiveness`, `detect_drift`, `suspend_for_drift`, `submit`, `settle`, `disable` are never called outside tests. The four stated defences are unenforced | MAJOR | yes |
| N5 | `intake/events.py:160-167` | Any dict operand without `in`/`not_in` — a typo, an unsupported operator, `{}` — makes the key match **every** event | MAJOR | yes |
| N6 | `intake/events.py:192-208`, `definition/validate.py:691` | `overlapping_keys` reports no overlap for `not_in` filters (a filter does not overlap itself) *and* is never called; lint skips every filtered trigger, so two identical filters produce no warning | MAJOR | yes |
| N7 | `improvement/clustering.py:169,174` | `_split` keys its text analysis by `run_id`; two failures from one run collide and a cluster can hold members with Jaccard 0.0 | MAJOR | yes |
| N8 | `improvement/clustering.py:170-181` | `_split` is documented as single-link but is greedy first-fit; the clustering, and the cluster signature, depend on input order | MAJOR | yes |
| N9 | `intake/pipeline.py:163` | Backpressure's source key is `provider:origin.ref` — a per-item reply address — so the per-source rate limit and circuit breaker never bind across a repository | MAJOR | yes |
| N10 | `intake/pipeline.py:160-181` | Events that match no automation still consume the rate limit; 33 junk events park a source for an hour | MAJOR | yes |
| N11 | `factory_tools/server.py:253`, `leases.py:135` | `actor` is an unauthenticated string, so the handoff lease is bypassed by naming the current holder | MAJOR | yes |
| N12 | `improvement/loop.py:397-407` | `settle()` defaults `outcome_effect` to `None`, so reverting a harmful adoption erases its measurement from the effectiveness average | MAJOR | yes |
| N13 | `improvement/loop.py:223` | `detect_drift` returns `None` whenever `scorer_delta <= 0`, so a flat scorer over a collapsing outcome is never flagged; being stateless, a sub-tolerance gap never fires however long it runs | MAJOR | yes |
| N14 | `intake/adapters.py:207-215` | `Registry.check()` is never called in `src/`, so `last_health` is always empty, `accepts()` always returns `True`, and the provider-unavailable branch is dead | MAJOR | yes |
| N15 | `improvement/loop.py:349-382,413-434` | `submit()` accepts a `ProposalVerdict` and discards `requires_second_reviewer` and `reason` | MAJOR | yes |
| N16 | `factory_tools/server.py:396-401` | `factory.hand_back`'s published schema marks a payload as complete that the handler always refuses | MAJOR | yes |
| N17 | `intake/events.py:121` | `event_identity` strips each part, so identifiers differing only in surrounding whitespace produce the same event id | MINOR | yes |
| N18 | `identity/principals.py:197-204,216` | `Directory.add` stores identities verbatim while `resolve_identity` lowercases the lookup; a mixed-case identity never resolves | MINOR | yes |
| N19 | `intake/pipeline.py:64` | `Automation.selects` compares `provider`/`event` with `!=` while `matches()` folds case on the same two keys | MINOR | yes |
| N20 | `factory_tools/leases.py:156-185` | `renew` discards the original TTL and inherits an expired holder's `intent`; `release` cannot check expiry; no ceiling on TTL | MINOR | yes |
| N21 | `intake/events.py:184-189` | "Case-insensitive" is `str.lower()`, not `casefold()`, with no unicode normalisation | MINOR | yes |

Nothing in `improvement/` writes to disk. FR-14.3b holds — see *What I checked and found sound*.

---

## N1 — CRITICAL — `authorTrust: any` turns the automation into an attacker-chosen trigger

**Files:** `src/software_factory/intake/loading.py:42,47,62-69`; `src/software_factory/intake/events.py:142-157`

### What the code does

`_accepts_anyone` reads the key `authorTrust` out of the *trigger filter* to decide
`require_known_author`:

```python
filter=dict(trigger.filter),                                   # loading.py:42
require_known_author=not _accepts_anyone(dict(trigger.filter)) # loading.py:47
...
return str(trigger_filter.get("authorTrust", "")).lower() == "any"   # loading.py:69
```

It reads the key but does not remove it. The same dict is then handed to `matches()`, which
treats every declared key as a required attribute match (`events.py:142-146`), and
`_event_value` has no special case for `authorTrust`, so it looks it up in
`event.attributes` (`events.py:157`).

So `authorTrust: any` does two things at once: it disables the author check, **and** it adds
a requirement that the incoming event carry an attribute literally named `authorTrust` with
the value `any`. The automation is inert for real traffic and live for anything that sets
that attribute — and `attributes` is exactly where adapters put provider-supplied facts
("Adapters put provider-specific facts here", `events.py:70-74`), and where the CLI's
`--attribute` flag writes directly (`cli.py:917-933`).

### Reproduction

Unit level (`/tmp/.../scratchpad/n5_authortrust.py`) builds a real `Definition` with one
trigger `filter: {authorTrust: any}` and runs `automations_from`:

```
automation           : public-triage
filter kept as-is    : {'authorTrust': 'any', 'label': 'bug'}
require_known_author : False  <- the opt-out was honoured

selects a normal labelled issue          : False
selects one carrying authorTrust=any     : True
```

End to end, through the shipped CLI, against a valid factory tree containing

```yaml
triggers:
  - provider: git-host
    event: issue.opened
    filter:
      authorTrust: any
```

```console
$ python3 -m software_factory.cli intake $D --provider git-host --event issue.opened \
      --ref "acme/payments#43" --author mallory
ignored — no automation matched. Most events are not for this factory; this is not an error.

$ python3 -m software_factory.cli intake $D --provider git-host --event issue.opened \
      --ref "acme/payments#44" --author mallory -a authorTrust=any
starts public-bugs → agent conductor
```

`mallory` is not in `principals/`. The second call starts a conductor run on attacker text.

### Why it matters

The one control an operator uses to say "this automation accepts strangers" (a) breaks the
automation for every legitimate event and (b) hands the decision of whether the automation
fires to whoever produces the event. The failure is silent: `sf lint` and `sf validate`
report nothing (`Trigger.filter` is `dict[str, Any]`), and the operator sees "no automation
matched", which the CLI explicitly tells them "is not an error".

### Suggested fix

`authorTrust` is policy, not a filter predicate. Lift it out of `Trigger.filter` into a
declared field on `Trigger` (e.g. `authorTrust: known | any`, defaulting to `known`), and
have `validate` reject any filter key in a reserved namespace. If it must stay inside
`filter` for schema-compatibility reasons, pop it before constructing the `Automation`:

```python
declared_filter = dict(trigger.filter)
accepts_anyone = str(declared_filter.pop("authorTrust", "")).lower() == "any"
... filter=declared_filter, require_known_author=not accepts_anyone
```

and add a validator error for any unknown reserved key so a typo cannot become a predicate.

---

## N2 — CRITICAL — the deduplicator records an event before anything can refuse it

**Files:** `src/software_factory/intake/pipeline.py:125-177`; `src/software_factory/intake/adapters.py:148-155`

### What the code does

`Deduplicator.seen` is a mutating read: it records the id and returns `False`
(`adapters.py:148-155`). `Pipeline.receive` calls it first (`pipeline.py:127`), before the
health check (`:141`) and before backpressure (`:160`). Both of those refuse with a
remediation that tells the caller to retry — "Restore the adapter and resume", "Wait for the
window to roll" — but the id is already in the dedupe set for 24 hours, so the retry is
refused as `intake.redelivered` and no work ever starts.

The module docstring states the opposite intent: "an event from an unavailable provider
parks rather than starts (FR-18.9)... parking a work item you cannot reply to is worse than
not starting it."

### Reproduction

`/tmp/.../scratchpad/n1_dedupe_order.py`:

```
A1 while unavailable : Refused intake.provider_unavailable parks_work= True
A2 after recovery   : Refused intake.redelivered
B1 first            : Started
B2 second           : intake.rate_limited | Wait for the window to roll, or raise the source's limit deliberately.
B3 retry after wait : Refused intake.redelivered

RESULT: an event refused by health or backpressure is already recorded as seen; its retry
can never start work.
```

Case A registers an `UNAVAILABLE` adapter, receives the event (correctly refused with
`parks_work=True`), restores the adapter to `HEALTHY`, and redelivers the same event. Case B
rate-limits an event, waits past the window (`now + 20 minutes`), and redelivers.

### Why it matters

`parks_work=True` is a promise that the work will be picked up when the provider recovers.
Nothing in the codebase consumes that flag (see N4/N14), and even if something did, the
event that would recreate the work is now permanently suppressed. Combined with N10 — 33
events that match nothing park a source for an hour — an attacker gets a durable intake
blackout: every genuine event arriving during the park is refused, recorded as seen, and
dead for 24 hours even after the park lifts.

### Suggested fix

Split "have I seen this" from "record that I accepted this". Make `seen()` a pure query and
add an explicit `accept(event, now=...)` called only on the paths that produce `Started` or
`Ignored` — i.e. after every refusal point:

```python
if self.deduplicator.already_accepted(event, now=now):   # pure
    return [Refused(... "intake.redelivered" ...)]
... health, backpressure, matching ...
self.deduplicator.accept(event, now=now)                 # only once we are keeping it
```

Secondary: `_expire` rebuilds the whole dict on every call (`adapters.py:157-159`), which is
O(n) per event on the attacker-facing surface. A deque of `(id, at)` plus a set, popped from
the front, is O(1) amortised.

---

## N3 — CRITICAL — a rejected proposal returns with no new evidence

**Files:** `src/software_factory/improvement/loop.py:333-344,385-410`; `src/software_factory/improvement/clustering.py:146-155,186-190`

### What the code does

The anti-thrash rule is keyed on `cluster.signature` (`loop.py:333`). `cluster_failures`
mints that key as `signature` for the first split group and `f"{signature}.{index}"` for the
rest (`clustering.py:151`) — where `index` is the enumeration order of `_split`'s output, and
`_split`'s output order is the order the failures arrived in (N8), further permuted by
`groups.sort(key=len, reverse=True)` whenever any detail-free failure is present
(`clustering.py:189`). The identifier the suppression rule depends on is therefore not a
property of the failures; it is a property of how the ledger happened to be read.

Separately, `settle()` (`loop.py:385-410`) rewrites a record's `status` to anything the
caller passes, with no capability check and no evidence, so a `REJECTED` record can simply
be moved back to `OPEN` and it disappears from `rejected_signatures()`.

### Reproduction

`/tmp/.../scratchpad/n7_thrash.py` — the *same three failures* under two read orders:

```
Monday   : enc cluster signature = d546ec3f5f0cc3b9
           may_propose again -> loop.already_rejected
Tuesday  : enc cluster signature = d546ec3f5f0cc3b9.1  (same three failures: ['e0', 'e1', 'e2'] )
           may_propose again -> None
  -> the identical rejected case is re-proposable, with no new evidence at all.

== settle() re-writes status with no authorisation and no evidence ==
  rejected_signatures before: ['d546ec3f5f0cc3b9']
  after settle(p1, OPEN)    : []
  may_propose(enc, monday sig) -> None
```

And the same relabelling from a single detail-free failure arriving
(`/tmp/.../scratchpad/n13_tests.py` case C):

```
  without a bare failure: {'c3b9.1': ['t','t','t','t'], '0cc3b9': ['e','e','e']}
  with one bare failure : {'0cc3b9': ['t','t','t','t','b'], 'c3b9.1': ['e','e','e']}
  -> adding a single failure with no detail text re-labels both clusters.
```

### Why it matters

FR-14.6's stated property — "a proposal already rejected must not return without *new*
evidence, or 'no' costs the reviewer the same effort every week" — does not hold. It is
defeated by nothing more adversarial than a different ledger read order or one extra failure
with an empty `detail`, and the loop does not need to know it is doing it. There is also
`new_evidence: bool = False` (`loop.py:263`), which no code computes: it is a claim the
caller asserts about itself.

### Suggested fix

1. Make the cluster key content-addressed, not positional. Derive the split-group suffix
   from the group's own members, e.g.
   `digest_parts(*sorted(f.run_id for f in group))`, so the same members always carry the
   same key regardless of arrival order.
2. Give `settle()` the same treatment as every other state change in this codebase: take a
   `Decision` from `Directory.authorise(..., Capability.ADOPT_DEFINITION_CHANGE)` and refuse
   transitions out of `REJECTED` outright.
3. Replace the `new_evidence` boolean with the evidence itself: pass the cluster's
   `run_ids` and require that they are not a subset of the rejected record's `evidence`.

---

## N4 — MAJOR — the improvement loop's four stated defences are never invoked

**File:** `src/software_factory/improvement/loop.py`

The module docstring names four failure modes and says this module is "everything around
them". Every one of the functions that implements them is dead outside the test suite. An
AST scan of every `ast.Call` node in `src/` and `tests/` (`/tmp/.../scratchpad/n9_wiring.py`):

```
function               called in src/                                called in tests/
may_propose            -- NEVER --                                   10
check_effectiveness    -- NEVER --                                   4
detect_drift           -- NEVER --                                   5
suspend_for_drift      -- NEVER --                                   1
telemetry              src/software_factory/improvement/loop.py:244  3
submit                 -- NEVER --                                   5
settle                 -- NEVER --                                   3
disable                -- NEVER --                                   1
overlapping_keys       -- NEVER --                                   3
cluster_failures       src/software_factory/cli.py:1083              6
matches                src/software_factory/intake/pipeline.py:66    14
release                -- NEVER --                                   4
renew                  -- NEVER --                                   0
```

`telemetry`'s only production caller is `check_effectiveness`, which is itself dead. The
single production entry point into `improvement/` is `sf improve`
(`cli.py:1059-1083`), which calls `cluster_failures` and prints. Nothing consults the
cooling period, the open-proposal cap, the rejected-signature list, the scorer suspension
list, or the effectiveness check; nothing ever calls `disable`.

`LeaseBook.renew` is not called by anything at all, tests included — which is why its two
defects (N20) have never been observed.

**Why it matters.** This is the same finding shape as last review's nine-of-eleven. The
module's prose asserts these gates as properties of the system; they are properties of a
library nobody has connected. Anyone wiring up the propose step later will find `may_propose`
optional — it returns a value, it does not raise, and `submit` does not consult it.

**Suggested fix.** Give the loop one entry point that cannot be bypassed: a `propose()` that
calls `may_propose` itself and returns `Refused | ProposalRecord`, and make `submit`/`_record`
private to it. Add a test asserting `submit` is unreachable without a passing `may_propose`.
Wire `check_effectiveness` → `disable` and `detect_drift` → `suspend_for_drift` into whatever
runs the loop on a schedule, and until that exists, say in the docstring that these are
uncalled rather than describing them as active defences.

---

## N5 — MAJOR — an unrecognised filter operator matches everything

**File:** `src/software_factory/intake/events.py:160-167`

```python
def _key_matches(expected, actual):
    if isinstance(expected, dict):
        if "in" in expected and not _any_of(expected["in"], actual):
            return False
        return not ("not_in" in expected and _any_of(expected["not_in"], actual))
    return _any_of(expected, actual)
```

A dict containing neither key falls straight through to `return not (False)` → `True`.

### Reproduction

`/tmp/.../scratchpad/n2_filters.py`:

```
== N2: a dict operand with no recognised operator is a no-op key ==
  filter {'branch': {'notIn': ['main']}} -> matches: True
  filter {'branch': {'not-in': ['main']}} -> matches: True
  filter {'branch': {'NOT_IN': ['main']}} -> matches: True
  filter {'branch': {'eq': 'release'}} -> matches: True
  filter {'branch': {}} -> matches: True
  filter {'branch': {'in': []}} -> matches: False
```

### Why it matters

`Trigger.filter` is `dict[str, Any]` and `validate` does not inspect its shape, so a
camelCase typo, a YAML style the author assumed was supported, or an operator borrowed from
another config language silently converts a restrictive filter into an open one. FR-18.6
says the default must be restrictive; here the *misconfigured* case is maximally permissive,
which is the wrong direction for the surface that reads attacker text. Note the asymmetry:
the one form that fails closed (`{"in": []}`) is the one a human is least likely to write.

### Suggested fix

Refuse what you do not understand:

```python
if isinstance(expected, dict):
    unknown = set(expected) - {"in", "not_in"}
    if unknown:
        raise ValueError(f"unsupported filter operator(s): {sorted(unknown)}")
```

and validate filter shape at load time so the error names the file and the line rather than
appearing at match time.

---

## N6 — MAJOR — `overlapping_keys` is unsound, and lint never calls it

**Files:** `src/software_factory/intake/events.py:192-208`; `src/software_factory/definition/validate.py:686-708`

`_as_filter_values` reads only `spec.get("in", [])` from a dict (`events.py:205-208`), so a
`not_in` filter contributes an empty value set and the intersection at `:200` is empty →
`return False`, "no overlap". The docstring claims the function is "conservative on purpose:
it reports a possible overlap rather than proving one". It is the opposite: it under-reports.

Meanwhile lint's `_check_automation_overlap` does not call it at all — it skips every
filtered trigger with `if trigger.filter: continue` (`validate.py:691`) and only reports
unfiltered duplicates.

### Reproduction

`/tmp/.../scratchpad/n2_filters.py`:

```
  filter A = {'branch': {'not_in': ['main']}}
  filter B = {'branch': 'feature/x'}
  A matches an event on branch feature/x: True
  B matches an event on branch feature/x: True
  overlapping_keys(A, B) = False   <- claims they cannot collide
  overlapping_keys(A, A) = False   <- a filter does not even overlap itself
```

And through the shipped lint, on a valid factory containing two automations whose triggers
carry the *identical* filter `{label: bug}`:

```console
$ python3 -m software_factory.cli lint $D
warn  .  no principals are declared, so no human checkpoint can be cleared
0 error(s), 1 warning(s)

$ python3 -m software_factory.cli intake $D --provider git-host --event issue.labelled \
      --ref "acme/payments#42" --author mallory -a label=bug
refused intake.unknown_author: ... and 'triage-a' requires a known author
refused intake.unknown_author: ... and 'triage-b' requires a known author
```

Both automations selected the one event. Lint reported nothing.

### Why it matters

FR-18.4 requires overlapping automations to be reported, and the code comment says a missed
report "costs every matching event twice" — every matching event does cost twice, at double
the token spend, and neither the function written for the job nor the lint check that exists
will say so.

### Suggested fix

Call `overlapping_keys` from `_check_automation_overlap` for filtered triggers on the same
`(provider, event)`, and make it sound: a `not_in`-only spec is an open set, so it should
return `True` (possible overlap) unless the other side's values are a subset of the excluded
set. Model each key as `(allowed: set | ALL, excluded: set)` and test
`(allow_l - excl_l) & (allow_r - excl_r)` non-empty, treating `ALL` as the universe.

---

## N7 — MAJOR — `_split` keys its text analysis by `run_id`, so a cluster can hold unrelated failures

**File:** `src/software_factory/improvement/clustering.py:169,174`

```python
analysed = {f.run_id: analyse(f.detail) for f in detailed}
...
jaccard_of(analysed[failure.run_id].tokens, analysed[other.run_id].tokens)
```

`run_id` is not unique per failure. `Failure` carries `run_id` *and* `work_item_id` *and*
`gate` — one run failing several gates is the ordinary case, and `sf improve` builds
`run_id=str(entry.payload.get("run", entry.subject))` from the ledger (`cli.py:1067`), so
every gate failure in one run shares it. Last write wins, and the loser is compared using
someone else's text.

### Reproduction

`/tmp/.../scratchpad/n6_split.py`:

```
jaccard(ENC, TMO) = 0.0

== A: `analysed` is keyed by run_id, so two failures from one run collide ==
  group: [('r1','UnicodeDecodeError'), ('r1','the migration step'), ('t0','the migration step'),
          ('t1','the migration step'), ('t2','the migration step')]
  -> the ENC failure was placed using the TMO failure's tokens (last write wins).
```

The `UnicodeDecodeError` failure and the four timeout failures share **zero** tokens
(Jaccard 0.0) and are in one cluster — precisely the "cluster whose members share nothing
but vocabulary" the module docstring says it exists to prevent, achieved without even
sharing vocabulary.

### Why it matters

The cluster is the input to a diagnosis run. A cluster containing an unrelated failure
produces a diagnosis that covers a cause that is not there, and the resulting proposal is
"fitted to one instance" plus noise — and it carries the contaminated cluster's signature
into the anti-thrash bookkeeping.

### Suggested fix

Key on the failure itself, not on one of its fields:

```python
analysed = {id(f): analyse(f.detail) for f in detailed}
```

or better, compute the analysis once into a parallel list and index by position. Consider
also giving `Failure` a real identity (`digest_parts(run_id, gate, scorer, detail)`) since
`run_id` is used as one in several places here.

---

## N8 — MAJOR — `_split` is not single-link, and the result depends on input order

**File:** `src/software_factory/improvement/clustering.py:158-190`

The docstring says "Single-link over detail similarity". The implementation is greedy
first-fit: each failure joins the first existing group containing any member within
threshold, and groups are never merged. Single-link is the transitive closure of the
similarity relation and is order-independent; this is neither.

### Reproduction

`/tmp/.../scratchpad/n6_split.py`, with A~B = 0.33, B~C = 0.33, A~C = 0.00 at threshold 0.25:

```
  order A,B,C -> [['a', 'b', 'c']]
  order A,C,B -> [['a', 'b'], ['c']]
```

and the consequence for identity:

```
  cluster_failures(enc + tmo): {'d546ec3f5f0cc3b9': ['e0','e1','e2'], 'd546ec3f5f0cc3b9.1': ['t0','t1','t2']}
  cluster_failures(tmo + enc): {'d546ec3f5f0cc3b9': ['t0','t1','t2'], 'd546ec3f5f0cc3b9.1': ['e0','e1','e2']}
```

### Why it matters

Beyond N3 (the signature instability that defeats anti-thrash): "the same failures produce
the same clusters" is the minimum property a clusterer has to have for a cooling period, a
diagnosis, or a telemetry series keyed on a signature to mean anything. `cluster_failures`
also silently loses failures — a signature with four members whose details are pairwise
dissimilar splits into four groups of one and every one is dropped at `min_size`, so the
four-failure pattern disappears entirely rather than being reported coarsely.

It cannot blow up or loop: `_split` is O(n²) in the worst case with a fixed iteration count
and no back-edges. That much is fine.

### Suggested fix

Implement the documented algorithm — union-find over the pairs above threshold — which is
both order-independent and actually single-link:

```python
parent = list(range(len(detailed)))
# find/union over every pair with jaccard >= threshold, then group by root
```

Then sort the resulting groups by a content-derived key before assigning signatures, and
report the dropped sub-threshold groups rather than discarding them silently.

---

## N9 — MAJOR — backpressure is keyed on a per-item reply address

**File:** `src/software_factory/intake/pipeline.py:163`

```python
source=f"{event.provider.value}:{event.origin.ref}",
```

`Origin.ref` is documented as "where a reply goes" (`events.py:49-59`) and in every example
in this repository it is per-item: `acme/payments#42`. `SourceLimits` defaults to 30 items
per 10 minutes per source, with a breaker at 3 trips. Keyed this way, "source" means "this
one issue".

### Reproduction

`/tmp/.../scratchpad/n12_backpressure.py`:

```
== A: 200 issues on one repository -- each is its own 'source' ==
  outcomes: {'Started': 200}  (default limit is 30 per source per 10 minutes)
```

### Why it matters

FR-26.3's stated failure mode is "one source consumes the factory" and "a failing deploy
emits thousands of alerts; each one looks like a legitimate work item". Both of those arrive
under many refs. The rate limit and the circuit breaker cannot see them. The protection
binds only on the one shape it is not needed for — repeated events against a single issue —
where fingerprint dedupe already applies.

### Suggested fix

Give `Origin` a coarse source key distinct from the reply address (repository slug, chat
channel, tracker project, alert source) and use it for `Queued.source`; keep `ref` for
replies. If adding a field is undesirable, derive it: split `ref` on the provider's item
separator (`#`, `/`) and key on the container.

---

## N10 — MAJOR — events that match nothing still spend the rate limit and trip the breaker

**File:** `src/software_factory/intake/pipeline.py:160-181`

Backpressure runs at `:160`; matching runs at `:179`. An event that matches no automation
has already been admitted and counted. The pipeline docstring defends the order — "by now
the event is real, new, and from a working provider, which is exactly what a rate limit is
meant to measure" — but "real" here means "well-formed", not "work".

### Reproduction

`/tmp/.../scratchpad/n12_backpressure.py` — 33 events carrying `label=not-a-bug` against an
automation filtered on `label=bug`, i.e. 33 events that would each have been `Ignored`:

```
  junk event 30: intake.rate_limited
  junk event 31: intake.rate_limited
  junk event 32: intake.breaker_tripped
  a real, matching bug report now: Refused intake.source_parked
   source 'git-host:acme/payments#1' is parked until 2026-08-31T20:41:45.106564+00:00
  -> 33 events that would have been `Ignored` parked the source for an hour.
```

### Why it matters

Anyone who can produce events — comment on an issue, post in a channel, fire a webhook — can
park intake for a source for an hour without ever touching an automation, and the events
that arrive during the park are additionally lost forever via N2. The comment at
`scheduling.py:116-121` gets the principle exactly right for the fingerprint case
("a duplicate is not evidence of load") and then does not apply it to the unmatched case,
which is the same argument.

### Suggested fix

Match first, admit second: compute `selected` before calling `backpressure.admit`, return
`Ignored` when it is empty, and only spend a slot on events that will start work. This also
makes the rate limit measure the thing it is named for.

---

## N11 — MAJOR — the handoff lease is bypassed by naming the current holder

**Files:** `src/software_factory/factory_tools/server.py:209-283`; `src/software_factory/factory_tools/leases.py:117-154`

`hand_back` takes `actor: str` straight from the tool call (`server.py:400` marks it
required; nothing authenticates it) and passes it as `holder` (`server.py:254`). `acquire`
refuses only when `existing.holder != holder` (`leases.py:135`) — and re-acquiring your own
lease renews it by design.

### Reproduction

`/tmp/.../scratchpad/n10_leases.py`:

```
== A: `holder` is an unauthenticated string, so the handoff lease is bypassed ==
  amaya hands back      : True
  bo hands back as 'bo' : handoff.leased
  bo hands back as      : True <- claiming actor='amaya'
  handoffs recorded     : 2
```

### Why it matters

`test_two_actors_cannot_both_hand_the_same_item_back` asserts FR-19.5a's "two handoffs is
two visible artifacts" and passes only because the second actor volunteered a different
name. The module docstring is honest that a lease "is advisory about intent, not about
permission" — but the tool surface presents `handoff.leased` as a refusal that prevents a
duplicate external effect, and it does not. The same string also identifies the holder in
every `describe()` shown to other actors, so an impersonated lease misreports who is acting.

Two further gaps on the same path: `hand_back` never releases the lease, so the second
legitimate handoff is blocked for the full TTL; and after the TTL the lease is free, so the
duplicate handoff is available to anyone five minutes later.

### Suggested fix

Take the actor from the transport's authenticated identity, not from the payload — bind it
when the tool call is dispatched and drop `actor` from the published schema. Failing that,
resolve `actor` through `Directory.resolve_identity` and refuse an unmapped one for any
`external=True` tool, and release the HANDOFF lease on the accepted path so it is not held
for five minutes after the work is done.

---

## N12 — MAJOR — reverting a harmful adoption erases it from the effectiveness average

**File:** `src/software_factory/improvement/loop.py:385-410`

`settle` rebuilds the record with `outcome_effect=outcome_effect`, whose default is `None`
(`:390,406`). Any subsequent settle that does not restate the measurement wipes it.
`telemetry` counts only `ADOPTED` records with a non-`None` effect (`:196-197`), so a
reverted proposal contributes nothing.

### Reproduction

`/tmp/.../scratchpad/n7_thrash.py`:

```
  measured effects       : [-0.4, 0.02, 0.02, 0.02]
  mean                   : -0.08499999999999999
  check_effectiveness    : 4 adopted proposals moved outcomes by -8.5% on average, below the +1.0
  after reverting p0     : [None, 0.02, 0.02, 0.02]
  mean                   : 0.02
  check_effectiveness    : None
  -> reverting the one change that hurt removes its measurement entirely.
```

### Why it matters

FR-14.7a.4 exists so a loop that does not earn its keep switches itself off. The natural
operational response to a bad adopted change — revert it — is also the action that removes
the evidence that the loop produced a bad change. A loop whose adoptions are half harmful and
half neutral reports as effective as long as the harmful ones are reverted, which is exactly
what will happen. The `outcome_effect` docstring is emphatic that `None` and zero must not be
conflated; `settle` conflates them.

### Suggested fix

Make `outcome_effect` sticky: `outcome_effect if outcome_effect is not None else
record.outcome_effect`. Better, count `REVERTED` as a measured negative outcome in
`telemetry` — a reverted change is the clearest possible evidence the loop is not working —
and add `revert_rate` to `check_effectiveness`'s decision, not just to `as_dict`.

---

## N13 — MAJOR — the drift detector cannot see the case it exists for

**File:** `src/software_factory/improvement/loop.py:221-232`

```python
if scorer_delta <= 0:
    return None
if scorer_delta - outcome_delta < tolerance:
    return None
```

The first guard drops every case where the scorer's pass rate is flat or falling. The
function is also stateless: it compares one window's deltas and remembers nothing.

### Reproduction

`/tmp/.../scratchpad/n8_drift.py`:

```
== A: a FLAT scorer while the real-world outcome collapses is not reported ==
  scorer_delta=+0.000 outcome_delta=-0.400  gap=+0.400 -> None
  scorer_delta=+0.000 outcome_delta=-0.900  gap=+0.900 -> None
  scorer_delta=-0.001 outcome_delta=-0.900  gap=+0.899 -> None

== B: the detector is stateless, so a gap held just under tolerance never fires ==
  12 consecutive windows, none flagged; cumulative scorer +118.8% vs outcome +0.0%

== C: boundary -- the constant says 'may rise this much MORE ... before'
  gap exactly 0.10 -> True
  gap 0.0999       -> False
```

### Why it matters

The docstring for the module states the target signal as "a scorer whose pass rate rises
while its outcome partner stays flat". The equally diagnostic and arguably more dangerous
signal — the scorer says nothing changed while defect escape doubles — is filtered out by
`scorer_delta <= 0` before the gap is ever computed, even though `gap` is already the right
quantity and is already +0.40 or +0.90 in those cases. The justification given ("a scorer
getting *worse* while its outcome holds is a different signal") is about a different
quadrant than the one the guard actually excludes.

Case B is worse for a self-improving system than a wrong threshold: the loop is optimising
against the scorer, so it will discover the sub-tolerance rate on its own without needing to
intend to.

### Suggested fix

Drop the `scorer_delta <= 0` guard and gate on `gap >= tolerance` alone; if the
scorer-got-stricter quadrant genuinely needs excluding, exclude it precisely
(`outcome_delta >= 0 and scorer_delta < 0`). Then make the check cumulative: hold a rolling
window of `(scorer_delta, outcome_delta)` per scorer and test the summed gap, so a persistent
sub-tolerance divergence is detected.

---

## N14 — MAJOR — adapter health is never polled, so the unavailable branch is dead

**File:** `src/software_factory/intake/adapters.py:185-215`; `src/software_factory/intake/pipeline.py:141-158`

`Registry.accepts` reads `self.last_health`, which is only ever populated by
`Registry.check()`. No module in `src/` calls `check()` (`grep -rn "\.check()" src/` returns
one unrelated hit in `providers/base.py`). `accepts()` therefore returns `True`
unconditionally in the shipped code, and the `intake.provider_unavailable` refusal at
`pipeline.py:141` is unreachable.

### Reproduction

`/tmp/.../scratchpad/n12_backpressure.py`:

```
== C: Registry.accepts() is never armed, because check() is never called in src ==
  adapter registered and reporting UNAVAILABLE; accepts() -> True
  after an explicit reg.check(): False
```

`test_an_unavailable_provider_parks_rather_than_dropping` passes because the test calls
`registry.check()` itself (`tests/test_intake.py:342`). Nothing in production does.

### Why it matters

FR-18.9's whole mechanism — an unhealthy adapter parks affected work as BLOCKED with the
reason rather than dropping events — depends on a health report existing. Without the poll,
work starts against a dead provider and every reply, question and checkpoint on it silently
fails to deliver, which is the "checkpoint that will time out for the wrong reason" the
adapter docstring warns about at `adapters.py:125-129`. The `parks_work` flag on `Refused`
also has no consumer anywhere.

### Suggested fix

Poll on a schedule from whatever owns the `Registry`, and have `Pipeline` fall back to
polling the adapter for a provider with no cached report rather than treating "no report" as
healthy. Distinguish "no adapter registered" (accept — the CLI/local case FR-18.10 needs)
from "adapter registered, never checked" (do not accept silently).

---

## N15 — MAJOR — `submit` discards the verdict's policy fields

**File:** `src/software_factory/improvement/loop.py:349-382,413-434`

`submit` reads exactly one field of the `ProposalVerdict` it is given:

```
submit() body references of the verdict: ['verdict: ProposalVerdict,', 'if not verdict.accepted:']
```

`ProposalVerdict.requires_second_reviewer` and `.reason` are dropped, and `ProposalRecord`
has nowhere to put them.

### Reproduction

`/tmp/.../scratchpad/n16_submit.py`:

```
ProposalRecord fields: ['id','target','scorer','signature','status','opened_at','settled_at','evidence','outcome_effect']
record for requires_second_reviewer=True : {... 'status': OPEN, 'evidence': ('run-1',), ...}
record for requires_second_reviewer=False: {... 'status': OPEN, 'evidence': ('run-1',), ...}
identical apart from the id: True
```

### Why it matters

FR-25.3's two-approver requirement for changes touching a scorer, a gate, or an eval is
computed upstream and then thrown away at the moment the proposal becomes the artefact a
human reviews. A reviewer looking at the record cannot tell a single-approver proposal from
a two-approver one. Likewise, `may_propose`'s `already_rejected` refusal can only quote the
date of the rejection (`loop.py:337-338`) because the reason it was rejected was never
stored — while telling the caller to "bring new evidence" for a case it cannot describe.

### Suggested fix

Add `requires_second_reviewer: bool` and `verdict_reason: str` to `ProposalRecord`, populate
both in `_record`, surface `requires_second_reviewer` in `Telemetry.as_dict`, and quote
`verdict_reason` in the `loop.already_rejected` refusal.

---

## N16 — MAJOR — `factory.hand_back` publishes a schema for a call that always refuses

**File:** `src/software_factory/factory_tools/server.py:386-408`

The published schema marks `["work_item_id", "actor", "changed"]` required and lists
`branch` / `change_ref` as ordinary optional strings. The handler refuses unless one of those
two is non-empty (`server.py:230-241`). A calling agent that satisfies the schema — which is
the contract FR-19.9 says the surface publishes so an agent works without an operator
explaining it — is refused every time.

### Reproduction

`/tmp/.../scratchpad/n13_tests.py`:

```
  schema required: ['work_item_id', 'actor', 'changed']
  handler(**schema-complete payload) -> {'accepted': False, 'code': 'handoff.nothing_pushed', ...}
```

### Why it matters

`test_every_published_handler_is_callable_through_its_schema` exists specifically to catch
"a published schema whose handler does not accept it is a surface that fails on first
contact with the agent it was published for" — and it uses this exact payload and passes,
because it asserts only `isinstance(result, dict)` (see *Tests that assert the wrong thing*,
T1). The guidance string ("Push first") is the only place the real requirement appears, and
guidance is prose, not schema.

### Suggested fix

Express the constraint in the schema — JSON Schema `anyOf` with `required: ["branch"]` /
`required: ["change_ref"]` — and describe each property. Then strengthen the test to assert
`result.get("error") is None` and, for `hand_back`, `accepted is True` on a payload the
schema declares valid.

---

## N17 — MINOR — `event_identity` strips each part

**File:** `src/software_factory/intake/events.py:113-121`

`digest_parts` is injective (verified below); `event_identity` then destroys that by
stripping each part before hashing. Two events whose provider identifiers differ only by
surrounding whitespace share an id, and the later one is refused as `intake.redelivered`.

```
   'Fix the importer'    -> de568d28d1c4aad753dd41ad
   '  Fix the importer\n' -> de568d28d1c4aad753dd41ad  equal: True
   '\tFix the importer '   -> de568d28d1c4aad753dd41ad  equal: True
```

Exploitability depends on the adapter: `sf intake` builds the id from
`(ref, event, title)` (`cli.py:938`) and a title is attacker-written, so two issues whose
titles differ only in trailing whitespace collapse to one event. Whether a production
adapter puts attacker-controlled text into the identity is unknown — no real adapters exist
in this tree. **Marked MINOR on that basis; it becomes MAJOR the moment one does.**

**Fix:** hash the parts as given. If normalisation is wanted for cosmetic reasons, do it in
the adapter, deliberately, and not inside the function whose docstring is about forgery.

---

## N18 — MINOR — `Directory.add` and `resolve_identity` disagree about case

**File:** `src/software_factory/identity/principals.py:197-204,216`

`add` stores `principal.identities` verbatim; `resolve_identity` looks up
`f"{provider}:{handle}".lower()`. A `Directory` built directly with a mixed-case identity can
never resolve it:

```
   declared 'git-host:Amaya-R'
   resolve('git-host','Amaya-R') -> None
   resolve('git-host','amaya-r') -> None
```

`directory_from` compensates by lowercasing at load (`identity/loading.py:41`), so the
production path is safe; every other caller is not, and the tests only use lowercase
identities. The lookup also does not strip, while `matches()` does — so an author with a
trailing space passes an `{"author": [...]}` filter and then fails `resolve_identity`
(fail-closed, but for the wrong reason and with the wrong message).

Separately, the case-insensitive lookup means distinct provider handles differing only in
case resolve to the same principal. On providers whose handles are case-sensitive that is an
author-trust merge. **UNVERIFIED as an exploit** — no adapter in this tree names such a
provider — but the code fact is verified above.

**Fix:** normalise in one place. Lowercase (and strip) inside `Directory.add`, and have
`resolve_identity` apply the identical transform; assert the two agree in a test.

---

## N19 — MINOR — one function matches the same two keys two different ways

**File:** `src/software_factory/intake/pipeline.py:61-66`

```python
if self.provider != event.provider.value or self.event != event.event:
    return False
return matches(self.filter, event)
```

`provider` and `event` are exact, case-sensitive comparisons; the same two keys inside
`filter` go through `matches` → `_as_set` → `.strip().lower()`.

```
  Automation.selects (uses `!=`)          : False
  the same key through matches() (lower)  : True
```

The events module docstring's stated reason for centralising the filter language is that
specifying semantics per-adapter "is how two integrations end up with two meanings for one
word". Here one function has two meanings for one word. A trigger declared as
`event: Issue.Labelled` silently never fires.

**Fix:** route the provider/event gate through the same comparison, e.g.
`matches({"provider": self.provider, "event": self.event}, event)`.

---

## N20 — MINOR — lease hygiene

**File:** `src/software_factory/factory_tools/leases.py:156-196`

`renew` is defined, never called, and never tested (see N4's scan). It has two defects and
`release` has a third:

```
== B: `renew` drops the ttl it was given at acquire time ==
  original ttl 60m, after renew ttl = 0:05:00 -> renewing SHORTENS the lease

== C: `renew` on an expired lease silently transfers it, with the old holder's words ==
  run-2 is opening the release change on wi-1 (open_change, 300s remaining)

== D: `release` ignores expiry
  lease active later    : None
  release by run-1 later: True (release takes no `now` at all, so it cannot check expiry)

== E: no ceiling on ttl
  a year later: Held - run-1 is x on wi-1 (open_change, 283824000s remaining)
```

`renew` reads `self.leases[key].intent` (`:169-172`) without checking who holds it or whether
it is live, so a new holder inherits the previous holder's description of what they are
doing — and that string is what `describe()` shows every other actor. `release` takes no
`now`, so it cannot distinguish "my live lease" from "my expired lease that someone else may
have since taken" (it is safe today only because `acquire` overwrites the dict entry).
Nothing bounds `ttl`, so the "short and renewable" property the module docstring leads with
is a convention, not a constraint.

The expiry comparison itself is correct: `now < acquired_at + ttl`, exclusive at the
boundary, computed on read.

**Fix:** give `renew` a `ttl` parameter defaulting to the existing lease's; refuse to renew a
lease held by someone else even when expired (return `Held` or a distinct
`Expired`); pass `now` into `release` and refuse an expired one; clamp `ttl` to a
module-level `MAX_TTL`.

---

## N21 — MINOR — "case-insensitive" is `str.lower()`, with no unicode normalisation

**File:** `src/software_factory/intake/events.py:184-189`

```
  matches({'branch': {'not_in': ['straße']}}, branch='STRASSE') -> True   (not excluded)
  matches({'branch': 'straße'}, branch='STRASSE')               -> False  (not matched)

  branch='main'    excluded by not_in ['main']: True
  branch='MAIN'    excluded by not_in ['main']: True
  branch='maİn'    excluded by not_in ['main']: False
  branch='ＭＡＩＮ'    excluded by not_in ['main']: False
  branch='main​' excluded by not_in ['main']: False
```

Look-alike escapes from a denylist are inherent to string matching and not by themselves a
defect. What is a defect is the mismatch between the stated property ("Matching is
case-insensitive on strings") and `str.lower()`, which is not case folding.

**Fix:** `unicodedata.normalize("NFKC", value).casefold()` in `_as_set`, applied identically
to filter values and event values, and say in the docstring that a `not_in` denylist over
attacker-chosen text is not a security boundary.

---

## Tests that assert the wrong thing

**T1 — `tests/test_factory_tools.py:338` `test_every_published_handler_is_callable_through_its_schema`.**
Its docstring: "A published schema whose handler does not accept it is a surface that fails
on first contact with the agent it was published for." The assertion is
`assert isinstance(spec.handler(**calls[spec.name]), dict)`. Every refusal and every
`_unknown()` return is also a dict:

```
  handler(**schema-complete payload) -> {'accepted': False, 'code': 'handoff.nothing_pushed', ...}
  proof (get_work_item on a missing id returns a dict): True
```

The one case in the table that genuinely fails its schema (N16) is the case this test
exercises, and it passes. The test would pass if every handler returned only errors.
*Fix:* assert `"error" not in result` and, per handler, one field that only the success path
produces.

**T2 — `tests/test_factory_tools.py:298` `test_two_actors_cannot_both_hand_the_same_item_back`.**
Asserts FR-19.5a. It holds only because the second actor supplies a different `actor`
string; passing `actor="amaya"` is accepted and records a second handoff (N11). The test
encodes an unauthenticated string as an access control. *Fix:* add the impersonation case
and make it pass by binding the actor at the transport.

**T3 — `tests/test_intake.py:352` `test_backpressure_applies_after_deduplication`.**
The name asserts an ordering; the test uses two different event ids, so the deduplicator
never refuses either and the outcome is produced entirely by backpressure. Instrumented:

```
    deduplicator consulted for evt-1: refuses=False
  receive(evt-1): Started
    deduplicator consulted for evt-2: refuses=False
  receive(evt-2): intake.rate_limited
```

The assertion holds under the opposite ordering too. Worse, the ordering that *is* in place
is the one that causes N2, and no test covers it. *Fix:* rate-limit an event, then redeliver
**the same id** and assert the refusal is `intake.redelivered`... which is precisely the bug,
so the test should be written to assert the corrected behaviour (`Started` after the window
rolls).

**T4 — `tests/test_intake.py:157` `test_overlapping_filters_are_reported_conservatively`.**
Docstring: "FR-18.4 requires lint to report overlap." Lint does not call this function at
all, and the function is not conservative — it reports no overlap for `not_in` filters,
including a filter against itself (N6). The three assertions all happen to fall in the
region where it is correct. *Fix:* add `assert overlapping_keys({"branch": {"not_in":
["main"]}}, {"branch": "feature/x"})` and an integration test that two filtered automations
with the same filter produce an `automation.overlap` warning.

**T5 — `tests/test_intake.py:436` `test_a_forged_fingerprint_cannot_suppress_a_real_alert`.**
Asserts only that concatenation across a boundary does not collide. `fingerprint_of` strips
and lowercases every part, so a whole collision class remains and is untested:

```
  fingerprint_of('deploy failed','payments')     = 90d3f19ec25e8032
  fingerprint_of('  Deploy Failed ','PAYMENTS')  = 90d3f19ec25e8032  equal: True
```

The blocked path (`intake.duplicate`) is a hard refusal of the later alert, so on any adapter
that derives a fingerprint from attacker-written alert text this is the suppression the test
is named for. *Fix:* assert the normalisation case too, and decide deliberately whether
`fingerprint_of` should normalise at all.

**T6 — `tests/test_improvement.py:355`
`test_a_loop_whose_adopted_changes_move_nothing_switches_itself_off`.**
Nothing switches off. `check_effectiveness` returns a string; `disable` is a separate
function that no production code calls (N4), and the test asserts only that the string
contains "below". The name states a behaviour the system does not have. *Fix:* rename to
what it checks, and add a test of the composed behaviour once something calls both.

**T7 — `tests/test_improvement.py:108` `test_failures_with_no_detail_are_not_scattered`.**
Passes at the `len(detailed) < 2` early return (`clustering.py:166-167`), never reaching the
branch it appears to be about. A line-level trace of the whole file confirms
`clustering.py:189-190` — `groups.sort(key=len, reverse=True)` and `groups[0].extend(bare)`,
the branch that mixes bare failures into split groups and relabels their signatures (N3) —
is **never executed by the suite**. `clustering.py:184` (`return [members]` when the details
all cohere) is never executed either, so "similar details stay together" is also untested.
*Fix:* a case with three cohering detailed failures plus two bare ones, asserting one
cluster; and a case with two divergent detailed groups plus a bare failure, asserting where
the bare one lands and that the signatures are stable.

**T8 — `tests/test_intake.py:416` `test_a_recurring_signal_deduplicates_by_fingerprint`.**
Docstring: "FR-18.14: a recurring alert extends one work item rather than opening a
thousand." Nothing extends anything. The observed behaviour is a `Refused` with
`intake.duplicate` whose remediation tells a *human* to "Comment on the existing work item".
The test asserts the refusal, which is fine; the docstring asserts a capability the codebase
does not implement. *Fix:* correct the docstring, or implement the extension and assert it.

Also worth noting, though not wrong: `test_a_mapped_author_passes_the_trust_check`
(`test_intake.py:381`) constructs its `Directory` with a lowercase identity, so it cannot
observe N18; and `test_an_identifier_containing_the_separator_cannot_forge_another_id`
(`test_intake.py:97`) tests an arbitrary separator character, correctly, but no test probes
the collision `event_identity` actually has (N17).

---

## What I checked and found sound

- **`digests.digest_parts` is injective.** Decimal-length + `:` prefixing is prefix-free and
  uniquely decodable. Brute-forced all 9,724 part-sequences of length 0-3 over the alphabet
  `{a, b, :, 1}` (chosen to include the separator and a digit, the two characters that could
  break the encoding): **zero collisions**. The docstring's account of the 24-hex/96-bit
  truncation is accurate.
- **FR-14.3b: nothing in `improvement/` writes a definition.** `grep -rn
  "open(\|write_text\|Path(\|shutil\|os\." src/software_factory/improvement/` returns
  nothing. `submit` records `OPEN`, never `ADOPTED` (`loop.py:374-382`); `settle` mutates
  only the in-memory `LoopState`. There is no import path from this module to the loader,
  the filesystem, or a git operation. The claim holds structurally, as documented.
- **Lease expiry arithmetic.** `now < acquired_at + ttl` — exclusive at the boundary,
  computed on read, no sweep. `held()` and `active_for()` both honour an injected `now`.
  Two actors cannot both hold a live lease on the same `(work_item, action)` when they use
  distinct holder strings; distinct action classes correctly do not conflict.
- **`_split` terminates and does not blow up.** Fixed iteration over `detailed`, no
  back-edges, worst case O(n²) similarity comparisons. It cannot loop.
- **`_event_value` promotion order.** `author`, `event` and `provider` are read from the
  event's own fields before `attributes` (`events.py:149-157`), so an attacker cannot shadow
  the author with `attributes["author"]`. Verified.
- **`Deduplicator` window boundary.** `at >= now - window` keeps an entry exactly on the
  boundary and drops older ones; the recorded timestamp is the first sighting and is not
  refreshed by later hits, so a persistent duplicate cannot extend its own suppression.
- **`HealthReport.__post_init__`** genuinely enforces "a non-healthy adapter must say why",
  including whitespace-only detail. `Registry.check` does catch a raising adapter and record
  it as unavailable rather than propagating. (What is missing is anyone calling it — N14.)
- **`Registry.register`** refuses a second adapter for one provider, as documented.
- **`hand_back`'s content refusals** (`nothing_pushed`, `no_summary`) run before the lease is
  taken, so a refused handoff does not consume the lease. That ordering is correct.
- **`Principal.__post_init__`'s `PERSON_ONLY` check** cannot be configured around: it is on
  the dataclass, so `directory_from` cannot construct a non-person holding
  `ADOPT_DEFINITION_CHANGE`.
