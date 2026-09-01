# Creative identity

No human reviewed the look while this was authored, so the identity is declared
here and checked against the measured result rather than left to drift.

palette: dark near-black cool, one indigo accent
provenance: source
structure: linear journey through the subject's own stage machine
motion: reveal + cue entrances, one grow, one draw

## Palette / theme — provenance: source

Every colour token is lifted from the subject's own dashboard stylesheet,
`src/software_factory/observability/dash.py`, `:root` block: `--bg #0b0c10`,
`--raise #101218`, `--card #14161e`, `--line #1e212b`, `--ink #eceef5`,
`--dim #9096a8`, `--faint #61667a`, `--accent #7c8cff`, `--accent-2 #58e6d0`,
`--good #4ec9a0`, `--warn #e8b458`, `--bad #f2748c`. Nothing was invented and
nothing was prettied up; the film looks like the product because it is the
product's palette. The comment in that file — "One accent, used sparingly. A
palette with three accents has none." — is honoured: indigo carries state, and
the good/warn/bad hues appear only where the subject itself uses them, on gate
verdicts.

Typography is generic-only (`system-ui`, `ui-monospace`) so the film composes
and renders with no network font fetch, which is the same property the subject
claims for its own dashboard.

**One recorded departure.** The product's `--faint` (`#61667a`) measures 3.3:1
against this film's background — fine on a screen you can lean into, below AA
for text in a video that is often watched small and compressed. The faint role
is lifted to `#868ca0`, and the rail's upcoming stages to `#777c8d`, the
darkest neutrals that clear 4.5:1 on `#0b0c10`. Everything else is the source
token unchanged. `hyperframes check` reported 45 contrast warnings before this
change and none after; 0 errors and 0 layout issues in both runs.

## Graphics / layout — provenance: source

Left rail plus a single content column, hairline-ruled. Two rules do all the
work:

1. **The rail is the film's progress bar, and it is the subject's stage
   machine** — INTAKE, TRIAGE, DESIGN, BUILD, REVIEW, VERIFY, HANDOFF,
   COMPLETE, printed verbatim from `sf stages`. The film advances through the
   factory as it explains the factory. This is the signature move: it is not
   decoration, it is the subject's own state model used as time.
2. **If the machine said it, it is monospace inside a bordered plate; if a
   person wrote it, it is not.** No authored sentence is ever set as terminal
   output, and no terminal output is ever paraphrased into prose. A viewer can
   tell evidence from voice at a glance, which is the whole argument of the
   film in typographic form.

Nothing is centred except by accident of a short line. Built-in layout classes
are off (`patterns` unset); every class in `theme.css` was written for this
film.

## Transitions — provenance: invented

Default `fade` throughout, deliberately. The rail must read as one continuous
instrument across the whole run; a wipe or slide would make each scene a new
place and break the single-journey claim. The only structural cut is the last
scene, which drops the content column and leaves the rail resolved at COMPLETE.

## Animation — provenance: brief

Entrances only: `reveal` at scene start, `data-cue="k"` on the beat that
earns it, one `data-grow` hairline, one `data-draw` underline in the gate
scene. No drift, no float, no ambient motion. The brief asks for a claim under
test rather than a boast, and restless motion is how a technical film reads as
an advertisement.

## Sound — provenance: brief

Speech-led, no bed, no SFX. Two voices: the narrator reports; the project's
central claim is spoken by a second voice, because it is a quotation on the
record rather than the film's own assertion. Captions on, neutral subtitle
preset, SRT/VTT exported.

## What this identity refuses

- No stock footage, no abstract particle field, no camera move over a code
  editor, no glowing network graph. The subject is a system whose entire pitch
  is that it says what it can and cannot show; a film that decorates it with
  invented imagery contradicts the product in its first five seconds.
- No number appears on screen that was not read out of a real file or a real
  command's output.
