---
name: tests-actually-run
description: Did the run execute the repository's validation before claiming completion?
agents: [builder]
labels:
  - value: ran_and_attached
    score: 1
    description: The transcript shows a validation command, its exit status, and attached results.
  - value: ran_not_attached
    score: 0.5
    description: A validation command ran but its structured results were not attached.
  - value: not_run
    score: 0
    description: No validation command appears in the transcript.
passingScore: 1
samplingRate: 25
judge:
  type: oz
  model: judge-model
selfImprovement: false
---

Read the run transcript and decide which single label applies.

Look for an actual command invocation and its result, not a claim that tests were run.
A summary sentence stating that tests pass, with no command and no output, is
`not_run` -- the point of this scorer is to catch exactly that.
