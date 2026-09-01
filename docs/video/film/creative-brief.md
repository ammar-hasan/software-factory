# Creative brief

Status: approved
Ambition: ambitious

## Creative intent

- **Audience, purpose, and viewing context:** engineers and engineering leaders
  who will meet this project as a repository. Watched once, probably muted the
  first time, on a README or a project page. Purpose: make the architecture and
  the honesty legible in ninety seconds, and make a sceptical reader want to
  open the PRD.
- **What should become newly felt, understood, or possible:** that the
  interesting thing here is not "agents", it is the harness — and that the
  project's central claim is stated as a claim, with an experiment that has not
  yet run. The film must leave a viewer able to say what would falsify it.
- **Unusual hypothesis worth proving:** that a product film can carry real,
  unretouched machine output as its principal visual material and be *more*
  persuasive for it — that evidence is a better image than illustration. The
  risky part is that terminal text is small, dense and static, and the
  conventional wisdom is that it will not hold a frame.
- **Source/reference evidence that must survive:** the exact wording of the
  central hypothesis; the exact string `insufficient_data — no trials
  recorded`; the failure class `import failure` on `regression-proven`; the
  dashboard's own sentence "A metric with no data reports its absence."; and
  the subject's colour tokens.
- **Hard constraints and deliberate exclusions:** no competing or reference
  product, no vendor, model, service or brand name anywhere — script, screen,
  file names, or metadata. No number that is not read from a real file or a
  real run. No implied result: the experiment has no trials, and the film says
  so out loud rather than routing around it.

## Directions considered

**A — "Instrument."** Near-black evidence field. A left rail printing the
subject's own stage machine advances through the film, so the film's progress
bar *is* the factory's state model. Statements in display sans; anything a
machine emitted in monospace inside a bordered plate, never paraphrased.
Temporal logic: one continuous journey, fades only. Representation:
evidentiary.

**B — "Ledger."** Light graphite field, no rail, no plates. The claim is
kinetic type filling the frame; each beat replaces the frame wholesale and the
previous line stays as a dimmed ledger row that accumulates down the screen.
Temporal logic: accumulative and fragmented. Representation: typographic and
symbolic — the backlog becomes the record.

These differ on representation (evidentiary vs symbolic), temporal logic
(continuous vs accumulative), spatial logic (fixed instrument vs replaced
frame), information density (dense vs sparse), and the role of type (vehicle vs
material). Not merely palette.

## Proof branches

| Branch | Rationale | Smallest decisive proof | Status |
|---|---|---|---|
| proof-a | An evidence instrument can hold a spoken claim without becoming a centred title card, and the stage rail turns exposition into travel | the single "bet" beat, three turns, with the rail present and the claim typeset large | approved |
| proof-b | Accumulation is the truer metaphor: a backlog becoming a record, with type as material rather than as a label | the same "bet" beat, kinetic type on a light field, previous line dimming into a ledger row | rejected |

Selected proof branch: proof-a

Why it won against the rejection criteria: proof-b read as a design sample
rather than as a film about a system — the light field and the kinetic setting
made the quoted hypothesis look like an endorsement, which is the one reading
this brief forbids. It also had nowhere to put unretouched machine output,
which is the film's principal visual material, and its accumulating rows would
have collided with the caption band by the fourth beat. proof-a keeps the claim
visibly quoted, holds a dense evidence plate without crowding, and its rail
gives the film a spine that is a fact about the subject rather than a graphic
device.

`narova branch compare proof-a proof-b` on the shared assertion
`restrained-claim` measured both at `video.static_ratio = 0.98` and
`video.cut_count = 0`, and returned `UNCERTAIN` for both with the note that
available evidence cannot establish whether the intended creative effect
survived rendering. That is the correct answer: the mechanical probes only
confirm both proofs are as still as intended, and they do not — and should not —
decide whether a claim reads as a quotation. The selection above is a creative
judgement made against the written rejection criteria, and the tool ranked
nothing.

Expanded from proof branch: proof-a
Expanded proof identity: afb0197c7624b767815097c9e1c50a0b5dc952e093325d1792b68c8ba02f2c20

## Medium and behavior

- **Chosen medium/material and why it carries the idea:** authored HTML/CSS
  over the browser renderer, with two real captured artefacts (a dashboard
  screenshot and transcribed command output). The subject is a command-line
  system with a local dashboard; its own surfaces are the honest material.
- **Representation logic:** evidentiary. Every plate on screen is something the
  system actually printed.
- **Temporal logic:** continuous linear journey. The rail advances one stage per
  scene and never goes back.
- **Spatial or compositional logic:** fixed instrument. Rail left, content
  column right, hairline rules. Nothing moves except entrances.
- **Role of speech, sound, silence, and text:** speech-led and unscored. The
  narrator reports; a second voice speaks the quoted hypothesis. Screen text is
  always fewer words than spoken, except inside evidence plates, where the text
  *is* the subject.
- **Signature behavior unique to this work:** the progress bar is the subject's
  stage machine, and the monospace/sans split is a load-bearing distinction
  between what a machine said and what a person wrote.

## Beat map

| Scene | Stage | What materially changes, and why it advances the intent |
|---|---|---|
| `intake` | INTAKE | The queue fills: request chips arrive on the beat the narrator names them, then one turns red. The backlog is shown as arrival rate, not as a pile. |
| `triage` | TRIAGE | The stage sequence prints as a real dry-run line. The film's rail is explained without a word being spent on it. |
| `bet` | DESIGN | The frame gives itself entirely to the quoted claim. A hairline grows under it on the last turn, and the attribution admits it is untested. |
| `mechanisms` | BUILD | Four mechanisms enter one per clause, left-ruled, as a list rather than a diagram — a diagram would imply a flow the project does not claim. |
| `gate` | REVIEW | Real gate output lands, then the refusal line draws its underline as the narrator says "refused". The only red on screen belongs to a real FAIL. |
| `honesty` | VERIFY | The real dashboard capture appears, then the real `sf experiment status` verdict lands on top of it. The film's least flattering fact gets its largest plate. |
| `local` | HANDOFF | The rail is nearly complete; the claim narrows to what is portable — files, harness, guarantees. |
| `close` | COMPLETE | Content column drops away; the rail resolves at COMPLETE beside the wordmark and the licence. |

## Pilot gate

- [x] 2–3 small proof branches were saved with distinct rationale.
- [x] Each proof isolates a decisive creative risk instead of pretending to be a full film.
- [x] The selected proof demonstrates the intended representation and temporal behavior.
- [x] A relevant edge state, transition, detail, or interaction was inspected.
- [x] Rendered proof frames were compared with references and this written intent.
- [x] One branch was selected; the weaker direction was rejected before expansion.

## Rejection criteria

Rebuild the proof if any of these is observably true in the rendered frames:

- The frame resolves to a centred title card with an accent line — the default
  house style this brief exists to avoid.
- The quoted hypothesis reads as the film's own assertion rather than as a
  quotation under test (no visible attribution, no second voice, no hedge).
- Any evidence plate is illegible at 1280×720, or is a paraphrase dressed as
  terminal output.
- The stage rail is decorative: if it can be deleted without the film losing a
  fact, it is a graphic device and must go.
- Motion draws attention to itself — anything ambient, looping, or drifting.
- The caption band collides with content in any beat.
