"""End-to-end product trials: the factory working real repositories.

These are not unit tests and not a demo. A unit test proves one function does what it says;
a demo proves a path exists. A trial asks the question neither answers -- *does the whole
thing hold together against work that was not designed for it* -- and the only way to find
out is real repositories, real test commands, real diffs, and gates evaluating the actual
result rather than a fixture.

**What these trials do prove.** The pipeline from intake to handoff on a repository the
factory has never seen. Gates evaluating real artifacts: `tests-pass` running the
repository's own pytest, `build-green` compiling the real tree, `regression-proven`
comparing a real test run at the tip against a real one at the parent commit,
`secret-clean` screening an actual diff. Workspaces, evidence bundles, the ledger, the
stage machine, and the economics folding over what really happened.

**What they do not prove, and this matters more than the list above.** No live model is
reachable from this environment, so the model's output is scripted. That means these trials
say nothing about whether a modest model inside this harness produces good changes -- which
is the project's central bet and the thing §11.2 exists to test. What they establish is the
weaker and still necessary claim: *if* the model produces a given output, the factory does
the right thing with it. A trial that blurred those two would be the most flattering
possible misreading of its own result.

Two shapes, because they fail differently:

* **Greenfield** starts from nothing. Its risks are about absence -- no tests to run, no
  build to be green, no precedent to retrieve -- and the interesting question is whether the
  factory degrades honestly or quietly reports success it did not earn.
* **Brownfield** starts from a repository with history, a test suite, and a real defect. Its
  risks are about interference, and the interesting question is whether `regression-proven`
  actually stops a fix nobody demonstrated was a fix.
"""
