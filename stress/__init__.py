"""Stress scenarios: the factory under load, contention, and hostile input.

Distinct from `trials/`, which asks whether the factory does the right thing on one work
item. These ask whether it keeps doing the right thing when several things happen at once,
when the ledger is large, when a provider misbehaves, and when the text coming back is
hostile rather than merely wrong.

The distinction matters because the failures differ in kind. A trial finds a wrong answer;
a stress run finds a corrupt ledger, a lost entry, a lock nobody releases, a number that
was right at ten runs and wrong at ten thousand, or a crash that takes the process with it.
None of those show up on a happy path, and all of them are what an operator meets first.
"""
