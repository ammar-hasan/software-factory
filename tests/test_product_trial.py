"""The product-trial instrument itself.

`scripts/product_trial.py` needs a live model and is not part of the suite. Its *carrying*
logic is, because that is the part that was silently wrong: the factory works in a clone and
never touches the source repository — correct isolation — so a trial that wants each step to
build on the last has to carry the change across itself, and the first version did not.

The consequence was not a crash. Every step re-solved the same seed, `git log` showed only
pytest's bytecode caches where the factory's work should have been, and "the product's own
tests pass" was measured against a repository that had never received the change. A sequence
built to test step-by-step evolution tested six independent first attempts, and reported
success.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import product_trial as trial

from software_factory.providers import StubProvider, calls, says
from software_factory.scaffold import init_factory

pytestmark = pytest.mark.integration

ENTRY_POINT = '"""Entry point."""\n\n\ndef main():\n    return 0\n'


def stage_output(**fields: object) -> str:
    base: dict[str, object] = {
        "calibration": {
            "criteria": [{"id": "C1", "confidence": 0.8, "evidence": ["repo.read x"]}],
            "unknowns": [],
        }
    }
    base.update(fields)
    return json.dumps(base)


def scripted(*, writes: bool = True) -> StubProvider:
    """A model that writes one file and then satisfies every stage.

    Five stage outputs, not four: `STEPS[0]` is feature-class, so the path is TRIAGE,
    DESIGN, BUILD, REVIEW, HANDOFF. With four the run stalled in DESIGN and never handed
    off — and because the first version of the trial landed a change regardless of the
    outcome, the tests still passed. They were asserting that a *blocked* step's work
    reached the product, which is the bug, not the behaviour.
    """
    script = []
    if writes:
        script.append(calls("file.write", {"path": "jsonlint/__main__.py", "content": ENTRY_POINT}))
    script += [
        says(stage_output(findings="ok", scope="one function")),
        says(stage_output(plan="add a flag", acceptance=["--version prints and exits 0"])),
        says(stage_output(summary="added an entry point", claims=["it exists"])),
        says(stage_output(verdict="accept", findings=[])),
        says(stage_output(summary="handed off")),
    ]
    return StubProvider(script)


def prepared(tmp_path: Path):
    repo = trial.seed_repo(tmp_path)
    factory = tmp_path / "factory"
    init_factory(factory, name="jsonlint", owner="acme", repo="jsonlint", local_model="stub")
    return repo, factory


def test_the_change_reaches_the_product_not_only_the_workspace(tmp_path: Path) -> None:
    """The whole point of the instrument.

    Without this the trial measures six first attempts at the same seed and calls it an
    evolution.
    """
    repo, factory = prepared(tmp_path)

    result = trial.run_step(
        trial.STEPS[0],
        repo=repo,
        factory=factory,
        state=tmp_path / "state",
        provider=scripted(),
    )

    assert result.landed is True, result.error
    assert (repo / "jsonlint" / "__main__.py").is_file(), "the work stayed in the workspace"
    assert "Entry point" in (repo / "jsonlint" / "__main__.py").read_text(encoding="utf-8")


def test_the_products_tests_run_against_the_landed_change(tmp_path: Path) -> None:
    """Run before the change lands, they test the seed and pass trivially — which is what
    the first version reported, for a repository that had never received the work."""
    repo, factory = prepared(tmp_path)

    result = trial.run_step(
        trial.STEPS[0],
        repo=repo,
        factory=factory,
        state=tmp_path / "state",
        provider=scripted(),
    )

    assert result.tests_pass is True
    assert result.landed is True


def test_a_step_that_changes_nothing_reports_nothing_landed(tmp_path: Path) -> None:
    """`None`, not `False`. "The step produced no change" and "the change would not apply"
    are different findings, and only the second is a fault."""
    repo, factory = prepared(tmp_path)

    result = trial.run_step(
        trial.STEPS[0],
        repo=repo,
        factory=factory,
        state=tmp_path / "state",
        provider=scripted(writes=False),
    )

    assert result.landed is None
    assert result.tests_pass is None


def test_bytecode_never_looks_like_the_factorys_work(tmp_path: Path) -> None:
    """The seed carries a `.gitignore`.

    Without one, the first between-step commit captured pytest's caches and nothing else,
    and in `git log` it was indistinguishable from the factory's change landing.
    """
    repo, _ = prepared(tmp_path)
    (repo / "jsonlint" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (repo / "jsonlint" / "__pycache__" / "core.cpython-311.pyc").write_bytes(b"\x00")

    staged = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    )

    assert staged.stdout.strip() == "", f"bytecode is not ignored: {staged.stdout!r}"


def test_every_step_declares_what_would_make_it_wrong() -> None:
    """Deciding afterwards what a run was testing is how every trial comes out a success."""
    assert trial.STEPS
    for step in trial.STEPS:
        assert step.expectation.strip(), step.name
        assert step.failure.strip(), step.name
        assert step.tests.strip(), step.name


def test_a_blocked_steps_work_does_not_land(tmp_path: Path) -> None:
    """A blocked step is the factory refusing to hand its work over.

    Landing it anyway overrides that refusal with the trial's own optimism — which is
    exactly what happened on a real run: step 3 blocked, its rejected work landed, and step
    4 started from a tree with ten failing tests. Every measurement after that point was
    against a product the factory had declined to produce.
    """
    repo, factory = prepared(tmp_path)

    # A model that writes a file and then never emits a valid stage output: the run cannot
    # advance, so the item blocks with the partial work sitting in the workspace.
    provider = StubProvider(
        [calls("file.write", {"path": "jsonlint/__main__.py", "content": ENTRY_POINT})]
        + [says("not valid stage output") for _ in range(8)]
    )
    result = trial.run_step(
        trial.STEPS[0], repo=repo, factory=factory, state=tmp_path / "state", provider=provider
    )

    assert not result.reached_handoff, "the fixture was meant to block"
    assert result.landed is not True
    assert not (repo / "jsonlint" / "__main__.py").exists(), "rejected work reached the product"


def test_a_provider_outage_is_not_reported_as_a_factory_decision(tmp_path: Path) -> None:
    """A dropped connection tells you nothing about the factory.

    A real run blocked with `RemoteDisconnected: Remote end closed connection without
    response` and the trial recorded it as `BLOCKED` — which reads as the factory refusing
    to hand the work over, when it was a network. That is the same class of dishonesty as
    reporting a metric with no data as zero.
    """
    from software_factory.providers.base import ProviderError

    class Dropping:
        name = "dropping"

        def complete(self, *args, **kwargs):
            raise ProviderError("RemoteDisconnected: Remote end closed connection")

        def available(self) -> tuple[bool, str]:
            return True, ""

    repo, factory = prepared(tmp_path)

    result = trial.run_step(
        trial.STEPS[0], repo=repo, factory=factory, state=tmp_path / "state", provider=Dropping()
    )

    assert result.infrastructure is True, (result.blocker, result.action)
    assert result.landed is not True, "work from an outage must not land"


def test_a_factory_block_is_not_an_infrastructure_failure(tmp_path: Path) -> None:
    """The other direction, which matters more: a genuine refusal must never be excused as
    infrastructure, or the trial retries the factory until it agrees."""
    repo, factory = prepared(tmp_path)

    provider = StubProvider([says("not valid stage output") for _ in range(8)])
    result = trial.run_step(
        trial.STEPS[0], repo=repo, factory=factory, state=tmp_path / "state", provider=provider
    )

    assert result.infrastructure is False
