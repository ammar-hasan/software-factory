# My Project

A narova project. For an ambitious brief, keep the config to a small proof,
save 2–3 directions with rationale, approve one, and only then expand it:

```bash
narova check      # validate the config (fast)
narova critique creative  # challenge intent, proof evidence, and rejection criteria
narova synth      # create narration + timings
narova compose && narova shots --motion --proof  # inspect + reject an invisible pilot
narova branch save proof-a --rationale "why this direction may serve the brief"
# repeat for proof-b/proof-c; then: narova branch set proof-b --status approved
narova preview --detach  # persistent Studio; prints the review URL
narova build --reuse --release  # after approval -> out/video.mp4
```

The first build sets up its own Python venv (~/.narova/venv) and downloads a
voice model. One-time wait, not a hang. `narova doctor` checks the machine.
