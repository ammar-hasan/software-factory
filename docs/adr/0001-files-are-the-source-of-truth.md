# 1. Files are the source of truth

**Status:** Accepted · **Date:** 2026-08-31

## Context

A factory's behaviour is the sum of its agents, prompts, skills, automations, runners,
scorers, and policy. That state has to live somewhere. The obvious options are a
database with a UI over it, or files in the user's own repository.

A database is easier to build and easier to edit safely. It is also opaque: you cannot
diff it, cannot review a change to it, cannot revert it, cannot test a proposed version,
and cannot run the factory if the thing holding the database is unavailable.

## Decision

Everything that changes factory behaviour is a file in a repository the operator owns.
UIs and APIs are *editors over files*, never a second store. `sf plan` resolves what the
files mean; nothing resolves to state a file does not describe.

This is why the self-improvement loop is possible at all: an agent proposing a change to
a prompt or a skill is opening a pull request, and it goes through the same review as any
other change.

## Alternatives considered

- **Database with export.** Export is not the same as source of truth — the exported form
  drifts, and nothing forces the two to agree.
- **Files plus a "quick settings" store for a few fields.** This is the same decision made
  worse: the moment two fields live outside the files, no one can answer "what is this
  factory configured to do?" by reading anything.

## Consequences

Editing is slower and less forgiving; a typo is a validation error rather than a form
that will not submit. We pay that with atomic whole-tree validation (FR-2.3), errors that
cite file and line, and `sf plan` to show the resolved result before anything runs.

## What would change our mind

Evidence that operators routinely cannot make a valid change without help — that is, if
validation errors become the dominant support burden rather than an occasional one.
