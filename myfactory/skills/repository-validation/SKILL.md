---
name: repository-validation
description: >-
  Run this repository's own lint, typecheck and test commands and attach the structured
  results. Use before calling any code change complete. Not for spec-only or
  documentation-only changes.
version: 1
status: active
owners: [acme]
reviewBy: "2027-09-01"
evals: [validation-runs]
appliesTo:
  roles: [BUILDER, CRITIC]
  stages: [BUILD, REVIEW]
---

# Repository validation

Run the repository's own commands, in this order, stopping at the first failure:

1. Formatter and linter.
2. Type checker, if the project has one.
3. The test suite, or the subset covering the change surface.

Attach the structured results to the work item. Report the exact command, the exit
status, and per-test outcomes -- not a summary. If a command does not exist, say so;
do not substitute a different one and do not report success for a check you did not run.
