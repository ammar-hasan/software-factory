"""The README's own commands, run.

The commands in the README are the first thing anybody does with this project, and until
this existed nothing checked that they work. Walking them by hand once found two that did
not: `sf work --dry-run` printed the stages while the text promised "and why", and
`sf worker route` — the one worker command written without `--root` — answered a mistyped
path with a `DefinitionError` traceback.

Both were one-line fixes and neither was findable from inside the code. A README is a
promise made in a different file from the one that keeps it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from software_factory.cli import app

ROOT = Path(__file__).resolve().parent.parent
runner = CliRunner()

#: Typer's own code for a command line it could not parse — a flag the README shows and the
#: CLI does not have. Distinct from this project's codes, which is the point: a non-zero
#: exit here is often correct (`sf experiment status` reports `insufficient_data`), and only
#: a *usage* error means the README printed something the CLI cannot accept.
USAGE_ERROR = 2

#: Commands the README shows that this suite deliberately does not run, and why.
#:
#: Named rather than filtered silently. A skip list nobody can read is a way to make a
#: failing check pass, and the reason is the part that has to survive review.
SKIPPED = {
    "work": "spends real money against a live model (the --dry-run form is covered)",
    "dash": "serves until interrupted",
    "api": "serves until interrupted",
    "media": "needs a recording this repository does not ship",
    "send": "writes a message; the read side is covered by `agent lifecycle`",
}


def readme_commands() -> list[str]:
    """Every `sf …` line in a fenced block, as written."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    found = []
    for block in re.findall(r"```(?:bash|console|sh)\n(.*?)```", text, re.S):
        for raw in block.splitlines():
            line = raw.strip().removeprefix("$ ").strip()
            if line.startswith("sf ") and not line.endswith("\\"):
                found.append(line.split("#")[0].strip())
    return found


@pytest.fixture(scope="module")
def factory(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real factory and a real repository, made by the README's own first command."""
    root = tmp_path_factory.mktemp("readme")
    repo = root / "payments-service"
    repo.mkdir()
    (repo / "importer.py").write_text("def load(path):\n    return open(path).read()\n")
    env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        "PATH": __import__("os").environ.get("PATH", ""),
        "HOME": str(root),
    }
    for args in (("init", "--quiet", "-b", "main"), ("add", "-A"), ("commit", "-qm", "x")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, env=env)

    # `sf init myfactory` writes to the *current* directory. The version of this fixture
    # that did not move first created `myfactory/` in the repository root, and a `git add
    # -A` committed it -- a test writing into the tree it is testing, which is the shape
    # this project keeps finding elsewhere.
    here = Path.cwd()
    os.chdir(root)
    try:
        result = runner.invoke(
            app,
            [
                "init",
                "myfactory",
                "--name",
                "payments",
                "--owner",
                "acme",
                "--repo",
                "payments-service",
            ],
            catch_exceptions=False,
        )
    finally:
        os.chdir(here)
    # `sf init` is the README's first command and everything below depends on it.
    assert result.exit_code == 0, result.output
    assert not (ROOT / "myfactory").exists(), "the fixture wrote into the repository"
    return root


def localise(command: str, root: Path) -> list[str]:
    """The command as written, with its example paths pointed at the fixture."""
    argv = __import__("shlex").split(command)[1:]
    return [
        str(root / "payments-service")
        if arg.startswith("~/code/")
        else str(root / "factories")
        if arg.startswith("~/factories")
        else arg
        for arg in argv
    ]


def test_the_readme_shows_commands_this_suite_recognises() -> None:
    """A README rewrite that renames every command must not leave this quietly passing."""
    commands = readme_commands()

    assert len(commands) >= 10, f"only {len(commands)} commands found in the README"


def test_every_runnable_readme_command_runs(factory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No traceback, and no usage error from a flag the README shows and the CLI lacks.

    A non-zero exit is fine and often correct — `sf experiment status` reports
    `insufficient_data`, `sf worker route` reports `unavailable`. What is not fine is
    exit code 2, which is Typer saying the command line itself was wrong.
    """
    monkeypatch.chdir(factory)
    ran, failures = 0, []

    for command in readme_commands():
        argv = localise(command, factory)
        if argv and argv[0] in SKIPPED:
            continue
        if "--dry-run" not in argv and argv[:1] == ["work"]:
            continue
        ran += 1
        result = runner.invoke(app, argv)
        if result.exit_code == USAGE_ERROR:
            failures.append(f"`{command}` -> usage error: {result.output.strip()[:160]}")
        elif result.exception is not None and not isinstance(result.exception, SystemExit):
            failures.append(f"`{command}` -> {type(result.exception).__name__}")
        elif "Traceback" in result.output:
            failures.append(f"`{command}` -> printed a traceback")

    assert ran >= 8, f"only {ran} README commands were actually run"
    assert failures == [], failures


def test_the_dry_run_says_what_the_readme_says_it_says(factory: Path) -> None:
    """The README promises the dry run prints the stages "and why". It printed the stages."""
    result = runner.invoke(
        app,
        [
            "work",
            "The CSV importer mangles BOM headers",
            "--factory",
            str(factory / "myfactory"),
            "--repo",
            str(factory / "payments-service"),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "TRIAGE" in result.output
    assert "DESIGN" in result.output, "a feature's path includes DESIGN"
    assert "so DESIGN is planned" in result.output, "the dry run did not say why"
