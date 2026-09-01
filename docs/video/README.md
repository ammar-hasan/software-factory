# The product film

**`software-factory.mp4`** — 1280×720, 80.0 s, H.264 + AAC, with `software-factory.srt`
and `software-factory.vtt` sidecars.

Everything needed to rebuild it lives in `film/`. Nothing in the film is a mock-up: every
plate on screen is either a command actually run against this repository or a line from a
generated report in it, and the one screenshot is an unretouched crop of
`docs/images/dashboard-overview.png`.

## What is here

| Path | What it is |
| --- | --- |
| `software-factory.mp4` | the deliverable — a stream copy of the rendered artefact with all container and stream metadata cleared |
| `software-factory.srt`, `.vtt` | published captions for the whole film |
| `film/reel.config.mjs` | the scene script: the render source of truth |
| `film/theme.css` | the look; every colour traced to `src/software_factory/observability/dash.py` |
| `film/creative-brief.md` | the direction, the two proof branches, why one was chosen, and the rejection criteria |
| `film/creative.md` | the creative-identity contract, checked against the measured result |
| `film/claims.md` | the source of every factual line in the narration |
| `film/assets/` | `dashboard-overview.png` (the real capture) and `dashboard-autonomy.png` (an unretouched crop of it) |
| `film/assets.lock.json` | the tracked-media register |
| `film/out/` | build output — not committed. `video.mp4` there is the canonical rendered artefact, bound to its own evidence receipt |

## Rebuild it

Requires the video toolchain on the machine (Node 18+, Python 3.10+, ffmpeg).

```bash
cd docs/video/film
narova check                 # validate the scene script and the claims ledger
narova build --reuse --release
narova judge                 # inspect the encoded result against the assertions
```

`--release` preflights the strict source checks, re-renders only what changed, and runs a
motion audit over the encoded file. The last run reported `release check passed — 0 warnings`
and `motion audit: pass — no 2s frozen or 0.5s black segments`, re-rendering 4 of 8 scene
spans with all 8 scenes' audio byte-identical to the previous build.

### What the checks said

| Check | Result |
| --- | --- |
| release preflight | `release check passed — 0 warnings` |
| encoded motion audit | pass — no 2 s frozen or 0.5 s black segments |
| composition lint | 0 errors, 0 layout issues across 9 samples, 0 contrast warnings |
| `narova judge` | 3 assertions · 2 `ALIGNED`, 24 `OBSERVED`, 5 `UNCERTAIN`, **0 `DIVERGED`** |
| `narova provenance` | 2 tracked media records, integrity verified; script authorship declared |
| `narova critique explainer` | no advice — passes all craft heuristics |

The five `UNCERTAIN` results are the honest ones: this machine has no semantic perceiver
configured, so the judge reports that it cannot establish whether the *meaning* of a frame
matched the intent, rather than guessing. The probe comparisons it can make all held.

### Digests

| Artefact | SHA-256 |
| --- | --- |
| `film/out/video.mp4` (canonical render, bound to its evidence receipt) | `873611e1b16491dfdb758d4a470d7e4ce0cbf60d14f879135962972c9c9a334e` |
| `software-factory.mp4` (metadata-cleared stream copy of the above) | `50ec7e5bb68eb3c2a1a434d5529c820b477fc9316aa3b6ac6064472544519ca9` |
| approved proof branch `proof-a` | `afb0197c7624b767815097c9e1c50a0b5dc952e093325d1792b68c8ba02f2c20` |

The deliverable is a stream copy — no re-encode — with every container and stream metadata
tag cleared, so the published file names no tool of any kind. The frames and audio are bit
for bit the frames and audio of the canonical render.

## Two rules the film obeys

1. **The progress marker is the subject's own stage machine.** The rail on the left prints
   INTAKE, TRIAGE, DESIGN, BUILD, REVIEW, VERIFY, HANDOFF, COMPLETE — the output of
   `sf stages`, verbatim — and advances one stage per scene. The film travels through the
   factory while it explains the factory.
2. **If a machine said it, it is monospace inside a bordered plate; if a person wrote it, it
   is not.** No authored sentence is ever dressed as terminal output, and no terminal output
   is ever paraphrased into prose.

## What the film deliberately does not say

- No pass rate, cost, latency or quality result. None exists: `sf experiment status` reports
  `insufficient_data — no trials recorded`, and the film says exactly that on screen and out
  loud rather than routing around it.
- No comparison with any named product, vendor, model or service. The one comparative
  sentence is a verbatim quotation of this project's own hypothesis about harness quality; it
  is spoken by a second voice, set inside a quotation rule, and captioned beneath an
  attribution reading *"the project's own hypothesis — not a result."*
- Nothing invented. Where a number would have been convenient, the film shows the real
  dashboard card saying `not observable`, with the product's own reason attached.
