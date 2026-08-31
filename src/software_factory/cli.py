"""`sf` -- the command line surface (PRD FR-21.1).

The CLI is the complete surface: anything the dashboard or API can do, `sf` can do, and
every command supports `--json` so it composes in a shell (FR-21.3). Exit codes are part
of the contract:

* ``0`` success
* ``1`` the thing being checked failed (validation errors, a broken chain)
* ``2`` the command could not run (missing definition, bad arguments)
"""

from __future__ import annotations

import json
import sys
from contextlib import suppress
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from software_factory import SCHEMA_VERSIONS, __version__
from software_factory.definition import load, load_strict, resolve_for_agent
from software_factory.definition.loader import Definition
from software_factory.definition.resolve import explain_execution
from software_factory.definition.schema import export_schema, schema_kinds
from software_factory.definition.validate import lint as run_lint
from software_factory.definition.validate import validate as run_validate
from software_factory.errors import FactoryError, Severity, ValidationReport
from software_factory.ledger import Ledger

app = typer.Typer(
    name="sf",
    help="Run a software factory: agents that carry work from intake to a reviewable change.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNUSABLE = 2

RootArg = Annotated[
    Path,
    typer.Argument(
        help="Path to the factory definition directory (the one containing factory.yaml).",
    ),
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Emit machine-readable JSON.")]


def _emit(payload: dict[str, Any]) -> None:
    console.print_json(json.dumps(payload, default=str))


def _fail(exc: FactoryError, as_json: bool) -> None:
    """Report a deliberate failure and exit. Never a traceback: the user did nothing wrong."""
    if as_json:
        _emit({"ok": False, "error": exc.as_dict()})
    else:
        err_console.print(f"[bold red]error[/] {exc.message}")
        err_console.print(f"[dim]{exc.remediation}[/]")
    raise typer.Exit(EXIT_UNUSABLE)


def _render_report(report: ValidationReport, root: Path) -> None:
    if not report.issues:
        console.print(f"[green]clean[/] — {root}")
        return

    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("", width=7)
    table.add_column("where", overflow="fold")
    table.add_column("what", overflow="fold")

    for issue in report.issues:
        tag = "[red]error[/]" if issue.severity is Severity.ERROR else "[yellow]warn[/]"
        where = issue.location()
        with suppress(ValueError, OSError):
            # Paths outside the definition root stay absolute; relative is just nicer.
            where = str(Path(where).relative_to(root))
        detail = issue.message
        if issue.accepted:
            detail += f"\n[dim]accepted: {', '.join(issue.accepted)}[/]"
        if issue.remediation:
            detail += f"\n[dim]→ {issue.remediation}[/]"
        table.add_row(tag, where, detail)

    console.print(table)
    console.print(
        f"\n[bold]{len(report.errors)}[/] error(s), [bold]{len(report.warnings)}[/] warning(s)"
    )


def _load_or_exit(root: Path, as_json: bool) -> tuple[Definition, ValidationReport]:
    try:
        return load(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        raise  # unreachable; keeps the type checker honest


@app.callback(invoke_without_command=True)
def main_callback(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show the version and exit.")] = False,
) -> None:
    if version:
        console.print(__version__)
        raise typer.Exit(EXIT_OK)
    if ctx.invoked_subcommand is None:
        console.print(ctx.get_help())
        raise typer.Exit(EXIT_OK)


@app.command()
def init(
    root: RootArg = Path(),
    name: Annotated[str, typer.Option(help="Factory name.")] = "factory",
    owner: Annotated[str, typer.Option(help="Repository owner.")] = "your-org",
    repo: Annotated[str, typer.Option(help="Repository name.")] = "your-repo",
    force: Annotated[bool, typer.Option(help="Overwrite existing files.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Write a complete, valid factory definition. Works offline, needs no account."""
    from software_factory.scaffold import init_factory

    result = init_factory(root, name=name, owner=owner, repo=repo, force=force)

    # Re-running init over a hand-edited tree is expected: existing files are kept, so the
    # result may legitimately not load. Report that rather than raising through the CLI.
    load_error: dict[str, Any] | None = None
    try:
        definition, report = load(root)
        run_validate(definition, report)
    except FactoryError as exc:
        report = ValidationReport()
        load_error = exc.as_dict()

    ok = report.ok and load_error is None

    if as_json:
        _emit(
            {
                "ok": ok,
                "created": [str(p) for p in result.created],
                "skipped": [str(p) for p in result.skipped],
                "validation": report.as_dict(),
                "error": load_error,
            }
        )
        raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)

    if load_error is not None:
        console.print(f"\n[red]the tree does not load[/] {load_error['message']}")
        console.print(f"[dim]{load_error['remediation']}[/]")
        raise typer.Exit(EXIT_FAILED)

    for path in result.created:
        console.print(f"  [green]+[/] {path.relative_to(root)}")
    for path in result.skipped:
        console.print(f"  [dim]· {path.relative_to(root)} (exists)[/]")

    if not result.wrote_anything:
        console.print("\n[yellow]nothing written[/] — pass --force to overwrite.")
    if not ok:
        console.print("\n[red]the scaffold did not validate — this is a bug[/]")
        _render_report(report, root)
        raise typer.Exit(EXIT_FAILED)

    console.print(
        f"\n[green]factory ready[/] at {root}\n"
        "  [dim]sf validate[/]  structure and cross-references\n"
        "  [dim]sf plan[/]      the resolved configuration for every agent\n"
        "  [dim]sf audit[/]     what each agent can reach"
    )


@app.command()
def validate(root: RootArg = Path(), as_json: JsonOpt = False) -> None:
    """Check the definition: structure, then everything a single file cannot check itself."""
    definition, report = _load_or_exit(root, as_json)
    run_validate(definition, report)

    if as_json:
        _emit({"ok": report.ok, "validation": report.as_dict()})
    else:
        _render_report(report, root)
    raise typer.Exit(EXIT_OK if report.ok else EXIT_FAILED)


@app.command()
def lint(root: RootArg = Path(), as_json: JsonOpt = False) -> None:
    """Advisory checks: sizing, collisions, unpinned images, policy overreach."""
    definition, _ = _load_or_exit(root, as_json)
    report = run_lint(definition)

    if as_json:
        _emit({"ok": report.ok, "validation": report.as_dict()})
    else:
        _render_report(report, root)
    raise typer.Exit(EXIT_OK if report.ok else EXIT_FAILED)


@app.command()
def plan(
    root: RootArg = Path(),
    explain: Annotated[
        bool, typer.Option(help="Show which file supplied each resolved value.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Print the fully resolved configuration for every agent, without running anything."""
    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    agents: dict[str, Any] = {}
    for agent in definition.agents.values():
        resolved = resolve_for_agent(definition.factory, agent.definition.execution)
        entry: dict[str, Any] = {
            "role": agent.definition.role.value,
            "execution": resolved.model_dump(exclude_none=True, mode="json"),
            "skills": [s.name for s in definition.skills_for(agent.name)],
        }
        if explain:
            entry["origins"] = [
                {"field": o.field, "value": o.value, "source": o.source}
                for o in explain_execution(
                    definition.factory.agent_defaults, agent.definition.execution
                )
            ]
        agents[agent.name] = entry

    if as_json:
        _emit({"ok": True, "factory": definition.factory.name, "agents": agents})
        raise typer.Exit(EXIT_OK)

    for name, entry in agents.items():
        console.print(f"\n[bold]{name}[/] [dim]({entry['role']})[/]")
        execution = entry["execution"]
        for key in ("tier", "model", "harness", "runner", "executor", "workerHost"):
            if key in execution:
                console.print(f"    {key:<12} {execution[key]}")
        for key in ("secrets", "tools", "effects"):
            if execution.get(key):
                console.print(f"    {key:<12} {', '.join(map(str, execution[key]))}")
        if execution.get("mcpServers"):
            console.print(f"    {'mcpServers':<12} {', '.join(execution['mcpServers'])}")
        if entry["skills"]:
            console.print(f"    {'skills':<12} {', '.join(entry['skills'])}")
        if explain:
            for origin in entry["origins"]:
                console.print(f"      [dim]{origin['field']} ← {origin['source']}[/]")
    raise typer.Exit(EXIT_OK)


@app.command()
def audit(root: RootArg = Path(), as_json: JsonOpt = False) -> None:
    """Report what every agent can reach, from the definition, without running anything.

    This is the security answer to "what is this factory able to do?" -- computed from
    grants, never from prompts, because instructions cannot widen access.
    """
    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    rows: list[dict[str, Any]] = []
    egress: set[str] = set()
    unverified: list[str] = []

    for agent in definition.agents.values():
        resolved = resolve_for_agent(definition.factory, agent.definition.execution)
        runner = definition.runners.get(resolved.runner or "")
        network = runner.definition.network.value if runner else "unknown"
        allowlist = list(runner.definition.network_allowlist) if runner else []
        egress.update(allowlist)
        if runner and runner.definition.setup_commands:
            unverified.append(
                f"{agent.name}: runner {runner.name!r} runs {len(runner.definition.setup_commands)} "
                "setup command(s); their egress cannot be determined statically"
            )
        rows.append(
            {
                "agent": agent.name,
                "role": agent.definition.role.value,
                "secrets": list(resolved.secrets or ()),
                "mcpServers": sorted(resolved.mcp_servers or {}),
                "tools": list(resolved.tools or ()),
                "effects": [e.value for e in (resolved.effects or ())],
                "runner": resolved.runner,
                "network": network,
                "networkAllowlist": allowlist,
            }
        )

    if as_json:
        _emit(
            {
                "ok": True,
                "factory": definition.factory.name,
                "agents": rows,
                "egress": sorted(egress),
                "unverifiedEgress": unverified,
            }
        )
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("agent", "role", "network", "secrets", "tool servers"):
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(
            str(row["agent"]),
            str(row["role"]),
            str(row["network"]),
            ", ".join(row["secrets"]) or "[dim]none[/]",
            ", ".join(row["mcpServers"]) or "[dim]none[/]",
        )
    console.print(table)

    console.print("\n[bold]declared egress[/]")
    console.print("  " + (", ".join(sorted(egress)) if egress else "[dim]none[/]"))
    if unverified:
        console.print("\n[yellow]not verifiable statically[/]")
        for note in unverified:
            console.print(f"  · {note}")
    raise typer.Exit(EXIT_OK)


@app.command()
def schema(
    kind: Annotated[str | None, typer.Argument(help=f"One of: {', '.join(schema_kinds())}")] = None,
) -> None:
    """Print the JSON Schema for a definition file kind. Offline, unauthenticated."""
    if kind is None:
        for available in schema_kinds():
            console.print(available)
        raise typer.Exit(EXIT_OK)
    if kind not in schema_kinds():
        err_console.print(f"[bold red]error[/] unknown kind {kind!r}")
        err_console.print(f"[dim]accepted: {', '.join(schema_kinds())}[/]")
        raise typer.Exit(EXIT_UNUSABLE)
    console.print_json(json.dumps(export_schema(kind)))


ledger_app = typer.Typer(help="Inspect and verify the append-only ledger.", no_args_is_help=True)
app.add_typer(ledger_app, name="ledger")


@ledger_app.command("verify")
def ledger_verify(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    as_json: JsonOpt = False,
) -> None:
    """Verify sequence, chaining, and per-entry hashes. Names the first divergence."""
    ledger = Ledger(path)
    try:
        ledger.verify()
    except FactoryError as exc:
        if as_json:
            _emit({"ok": False, "error": exc.as_dict()})
        else:
            err_console.print(f"[bold red]chain broken[/] {exc.message}")
            err_console.print(f"[dim]{exc.remediation}[/]")
        raise typer.Exit(EXIT_FAILED) from exc

    last_seq, last_hash = ledger.tail()
    if as_json:
        _emit({"ok": True, "entries": last_seq, "head": last_hash})
    else:
        console.print(f"[green]verified[/] {last_seq} entries, head {last_hash[:12]}…")
    raise typer.Exit(EXIT_OK)


@ledger_app.command("tail")
def ledger_tail(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    count: Annotated[int, typer.Option("-n", help="How many entries to show.")] = 20,
    as_json: JsonOpt = False,
) -> None:
    """Show the most recent entries."""
    ledger = Ledger(path)
    try:
        entries = list(ledger.read())[-count:]
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if as_json:
        _emit({"ok": True, "entries": [json.loads(e.to_json()) for e in entries]})
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("seq", "when", "type", "actor", "subject"):
        table.add_column(column, overflow="fold")
    for entry in entries:
        table.add_row(str(entry.seq), entry.ts, entry.type.value, entry.actor, entry.subject)
    console.print(table)
    raise typer.Exit(EXIT_OK)


@app.command()
def doctor(as_json: JsonOpt = False) -> None:
    """Report what works in this environment and what does not (FR-28.1)."""
    import platform
    import shutil

    checks: list[dict[str, Any]] = [
        {
            "check": "python",
            "ok": sys.version_info >= (3, 11),
            "detail": platform.python_version(),
            "remediation": "Install Python 3.11 or newer.",
        },
        {
            "check": "git",
            "ok": shutil.which("git") is not None,
            "detail": shutil.which("git") or "not found",
            "remediation": "Install git; the factory works in git worktrees.",
        },
        {
            "check": "container-runtime",
            "ok": any(shutil.which(c) for c in ("docker", "podman")),
            "detail": next((c for c in ("docker", "podman") if shutil.which(c)), "not found"),
            "remediation": "Optional. Needed only for the container executor.",
        },
        {
            "check": "schema-versions",
            "ok": True,
            "detail": ", ".join(SCHEMA_VERSIONS),
            "remediation": "",
        },
    ]
    required = {"python", "git"}
    ok = all(c["ok"] for c in checks if c["check"] in required)

    if as_json:
        _emit({"ok": ok, "checks": checks})
        raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)

    for check in checks:
        mark = "[green]ok  [/]" if check["ok"] else "[red]fail[/]"
        console.print(f"  {mark} {check['check']:<20} {check['detail']}")
        if not check["ok"] and check["remediation"]:
            console.print(f"       [dim]{check['remediation']}[/]")
    raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
