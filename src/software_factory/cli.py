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
import os
import sys
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
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
from software_factory.definition.validate import unused_effects
from software_factory.definition.validate import validate as run_validate
from software_factory.errors import FactoryError, Severity, ValidationReport
from software_factory.ledger import EntryType, Ledger
from software_factory.providers.base import Provider
from software_factory.runtime.tools import BUILTIN_TOOL_EFFECTS

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


def _report_egress(definition: Any, as_json: bool) -> None:
    """FR-20.6: enumerate every outbound destination, including the ones that cannot be."""
    from software_factory.definition.egress import Certainty, enumerate_egress

    report = enumerate_egress(definition)

    if as_json:
        _emit({"ok": True, "egress": report.as_dict()})
        raise typer.Exit(EXIT_OK)

    if report.offline_capable:
        console.print(
            "[green]offline-capable[/] — nothing in this definition can reach the network."
        )
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("destination", "certainty", "from", "why"):
        table.add_column(column, overflow="fold")
    for destination in report.destinations:
        colour = {
            Certainty.DECLARED: "",
            Certainty.IMPLIED: "cyan",
            Certainty.INDETERMINATE: "yellow",
        }[destination.certainty]
        marked = (
            f"[{colour}]{destination.certainty.value}[/]" if colour else destination.certainty.value
        )
        table.add_row(destination.target, marked, destination.source, destination.detail)
    console.print(table)

    indeterminate = report.by_certainty(Certainty.INDETERMINATE)
    if indeterminate:
        console.print(
            f"\n[yellow]{len(indeterminate)} destination(s) cannot be determined from the "
            "definition.[/] An egress report that omitted them would read as a complete "
            "list; inspect them directly to close the gap."
        )
    raise typer.Exit(EXIT_OK)


@app.command()
def audit(
    root: RootArg = Path(),
    egress_only: Annotated[
        bool,
        typer.Option(
            "--egress",
            help="Enumerate every outbound destination reachable from this definition.",
        ),
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Report what every agent can reach, from the definition, without running anything.

    This is the security answer to "what is this factory able to do?" -- computed from
    grants, never from prompts, because instructions cannot widen access.
    """
    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if egress_only:
        _report_egress(definition, as_json)
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
        # Least privilege is only auditable if over-grant is visible. An effect granted
        # that no granted tool can use is a widened blast radius nobody asked for.
        surplus = unused_effects(resolved, BUILTIN_TOOL_EFFECTS)
        rows.append(
            {
                "agent": agent.name,
                "role": agent.definition.role.value,
                "secrets": list(resolved.secrets or ()),
                "mcpServers": sorted(resolved.mcp_servers or {}),
                "tools": list(resolved.tools or ()),
                "effects": [e.value for e in (resolved.effects or ())],
                "unusedEffects": [e.value for e in surplus],
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
        # A bounded window, not the whole ledger: `list(...)[-count:]` held every entry in
        # memory to show twenty of them, on a log designed to grow forever.
        entries = list(deque(ledger.read(), maxlen=count))
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
def work(
    request: Annotated[str, typer.Argument(help="What you want done, in your own words.")],
    root: Annotated[
        Path, typer.Option("--factory", help="The factory definition directory.")
    ] = Path(),
    repo: Annotated[Path, typer.Option("--repo", help="The repository to work in.")] = Path(),
    title: Annotated[str, typer.Option(help="Short title for the work item.")] = "",
    work_class: Annotated[
        str, typer.Option("--class", help="defect | feature | refactor | chore | investigation")
    ] = "",
    state: Annotated[Path, typer.Option(help="Where run state and the ledger live.")] = Path(
        ".factory"
    ),
    allow_unsandboxed: Annotated[
        bool,
        typer.Option(help="Run without OS sandboxing. Only when no sandbox is available."),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Plan the stages without running anything.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Run one work item end to end, locally.

    Needs a model provider to do real work. Without one configured it plans the stages
    and stops, rather than pretending -- a factory that cannot reach inference should say
    so, not produce unverified output (PR-9).
    """
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import Coordinator
    from software_factory.orchestrator.workitem import classify_request

    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    resolved_class = WorkClass(work_class) if work_class else classify_request(request)
    item = WorkItem(
        id=new_id(),
        factory=definition.factory.name,
        title=title or request[:72],
        request=request,
        source=SourceContext(provider="cli", kind="direct", ref="local"),
        work_class=resolved_class,
    )

    if dry_run:
        coordinator = Coordinator.__new__(Coordinator)  # planning needs no runtime
        planned = [
            stage.value
            # Planning is a pure function of the work item, so it needs no runtime.
            for stage in Coordinator._default_path(coordinator, item)
        ]
        if as_json:
            _emit(
                {
                    "ok": True,
                    "workItem": item.as_dict(),
                    "plannedStages": planned,
                    "note": "dry run: nothing was executed",
                }
            )
            raise typer.Exit(EXIT_OK)
        console.print(f"[bold]{item.title}[/] [dim]({resolved_class.value})[/]")
        console.print(f"  planned stages: {' → '.join(planned)}")
        console.print("\n[dim]dry run: nothing was executed[/]")
        raise typer.Exit(EXIT_OK)

    provider = _resolve_provider()
    if provider is None:
        message = "no model provider is configured, so this run would produce nothing verifiable"
        remediation = (
            "Set SF_PROVIDER_ENDPOINT to a local model endpoint, or use --dry-run to see "
            "the planned stages."
        )
        if as_json:
            _emit({"ok": False, "error": {"message": message, "remediation": remediation}})
        else:
            err_console.print(f"[bold red]cannot run[/] {message}")
            err_console.print(f"[dim]{remediation}[/]")
        raise typer.Exit(EXIT_UNUSABLE)

    from software_factory.orchestrator.coordinator import local_coordinator

    coordinator = local_coordinator(
        definition,
        repo=repo,
        state_dir=state,
        provider=provider,
        allow_unsandboxed=allow_unsandboxed,
    )
    try:
        outcome = coordinator.run(item)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if as_json:
        _emit({"ok": item.stage.value != "BLOCKED", "outcome": outcome.as_dict()})
        raise typer.Exit(EXIT_OK if item.stage.value != "BLOCKED" else EXIT_FAILED)

    for stage in outcome.stages:
        mark = "[green]ok  [/]" if stage.advanced else "[red]stop[/]"
        console.print(f"  {mark} {stage.stage.value:<8} [dim]{stage.agent}[/]")
        for finding in stage.gates.findings:
            console.print(f"       [yellow]·[/] {finding.render()}")
    if item.blocker:
        console.print(f"\n[yellow]blocked[/] ({item.blocker.value}): {item.blocker_action}")
        raise typer.Exit(EXIT_FAILED)
    console.print(f"\n[green]{item.stage.value}[/] — {len(outcome.changed_paths)} file(s) changed")
    raise typer.Exit(EXIT_OK)


def _resolve_provider() -> Provider | None:
    """Find a configured provider, or ``None``.

    Deliberately explicit: a factory with no reachable inference must say so rather than
    silently doing less. There is no default that quietly points somewhere.
    """
    import os

    endpoint = os.environ.get("SF_PROVIDER_ENDPOINT")
    if not endpoint:
        return None
    raise FactoryError(
        f"provider endpoint {endpoint!r} is configured but no HTTP provider is built yet",
        remediation=(
            "The HTTP provider lands with the integrations milestone. Until then, use "
            "--dry-run, or drive the coordinator directly from Python with your own "
            "Provider implementation."
        ),
    )


spec_app = typer.Typer(
    help="Work with the Living Spec: induct it, slice it, check agreement.",
    no_args_is_help=True,
)
app.add_typer(spec_app, name="spec")


@spec_app.command("induct")
def spec_induct(
    repo: Annotated[Path, typer.Argument(help="The repository to read.")] = Path(),
    prefix: Annotated[
        str, typer.Option(help="Only scan paths under this prefix, for incremental onboarding.")
    ] = "",
    id_prefix: Annotated[str, typer.Option("--id-prefix", help="Prefix for unit ids.")] = "SPEC",
    limit: Annotated[int, typer.Option(help="Maximum units to propose.")] = 200,
    as_json: JsonOpt = False,
) -> None:
    """Propose draft spec units from an existing codebase.

    Proposes; never writes. Every unit arrives as `draft` and gates nothing until a
    person promotes it, so running this on a large repository cannot block anyone.
    """
    from software_factory.spec.induction import induct

    report = induct(repo, prefix=prefix, id_prefix=id_prefix, limit=limit)

    if as_json:
        _emit({"ok": True, "induction": report.as_dict()})
        raise typer.Exit(EXIT_OK)

    if not report.units:
        console.print(
            f"[yellow]nothing to propose[/] — scanned {report.scanned} file(s) with no public "
            "definitions or tests"
        )
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("id", "title", "from", "conf", "criteria"):
        table.add_column(column, overflow="fold")
    for unit in report.units[:40]:
        origin = unit.provenance[0].split(":")[0] if unit.provenance else "?"
        table.add_row(
            unit.id,
            unit.title,
            origin,
            f"{unit.confidence:.1f}",
            str(len(unit.acceptance)) if unit.acceptance else "-",
        )
    console.print(table)

    counts = ", ".join(
        f"{count} from {source}" for source, count in sorted(report.by_source().items())
    )
    console.print(
        f"\n[bold]{len(report.units)}[/] draft unit(s) proposed from {report.scanned} file(s)"
        + (f" ({counts})" if counts else "")
    )
    if report.skipped:
        console.print(f"[dim]{len(report.skipped)} file(s) skipped[/]")
    console.print(
        "\n[dim]Nothing was written. Draft units gate nothing until a person promotes them.[/]"
    )
    raise typer.Exit(EXIT_OK)


memory_app = typer.Typer(
    help="Inspect the memory fabric: lanes, provenance, and what a claim rests on.",
    no_args_is_help=True,
)
app.add_typer(memory_app, name="memory")


@memory_app.command("stats")
def memory_stats(
    path: Annotated[Path, typer.Argument(help="Path to the memory JSONL file.")],
    as_json: JsonOpt = False,
) -> None:
    """Lane counts, quarantine backlog, and size. The health check for FR-15.3."""
    from software_factory.memory import MemoryStore

    store = MemoryStore(path)
    try:
        store.load()
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    stats = store.stats()
    if as_json:
        _emit({"ok": True, "stats": stats})
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("lane")
    table.add_column("count", justify="right")
    for lane in ("working", "candidate", "canon", "archive"):
        table.add_row(lane, str(stats.get(lane, 0)))
    console.print(table)
    console.print(
        f"\n[bold]{stats['total']}[/] memories, [bold]{stats['quarantined']}[/] quarantined, "
        f"{stats['bytes']:,} bytes"
    )
    raise typer.Exit(EXIT_OK)


@memory_app.command("why")
def memory_why(
    path: Annotated[Path, typer.Argument(help="Path to the memory JSONL file.")],
    memory_id: Annotated[str, typer.Argument(help="The memory to explain.")],
    as_json: JsonOpt = False,
) -> None:
    """Print a memory's complete provenance tree.

    The subsystem's primary trust instrument: a memory a human cannot trace is a memory
    a human should not accept.
    """
    from software_factory.memory import MemoryStore

    store = MemoryStore(path)
    try:
        store.load()
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    tree = store.provenance_tree(memory_id)
    if as_json:
        _emit({"ok": bool(tree.get("found")), "provenance": tree})
        raise typer.Exit(EXIT_OK if tree.get("found") else EXIT_FAILED)

    if not tree.get("found"):
        err_console.print(f"[bold red]not found[/] {memory_id}")
        raise typer.Exit(EXIT_FAILED)
    _render_provenance(tree, depth=0)
    raise typer.Exit(EXIT_OK)


def _render_provenance(node: dict[str, Any], *, depth: int) -> None:
    indent = "  " * depth
    # The tree can carry a `cycle` or `truncated` marker instead of a full node. Both are
    # printed, never skipped: a reader tracing why a memory exists needs to know the walk
    # stopped and where, and a silently missing branch reads as a shorter provenance.
    if node.get("cycle"):
        console.print(f"{indent}[bold]{node['id']}[/] [yellow](already shown above)[/]")
        return
    if node.get("truncated"):
        console.print(f"{indent}[bold]{node['id']}[/] [yellow](depth limit reached)[/]")
        return
    if not node.get("found", False):
        console.print(f"{indent}[bold]{node['id']}[/] [red](not found)[/]")
        return
    console.print(
        f"{indent}[bold]{node['id']}[/] [dim]({node['lane']}, {node['kind']}, "
        f"trust {node['trust']}, confidence {node['confidence']:.2f})[/]"
    )
    console.print(f"{indent}  {node['content']}")
    for source in node.get("sources", []):
        console.print(f"{indent}  [dim]<- {source['kind']}:{source['ref']}[/]")
    if node.get("promotion"):
        console.print(f"{indent}  [dim]promoted by {node['promotion']['criterion']}[/]")
    for parent in node.get("parents", []):
        _render_provenance(parent, depth=depth + 1)


@memory_app.command("blast")
def memory_blast(
    path: Annotated[Path, typer.Argument(help="Path to the memory JSONL file.")],
    memory_id: Annotated[str, typer.Argument(help="The memory to assess.")],
    as_json: JsonOpt = False,
) -> None:
    """What invalidating this memory would affect.

    Run before accepting a high-fan-out claim: a memory hundreds of others rest on
    deserves more scrutiny than one nothing depends on.
    """
    from software_factory.memory import MemoryStore, blast_radius

    store = MemoryStore(path)
    try:
        store.load()
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    impact = blast_radius(store, memory_id)
    if as_json:
        _emit({"ok": True, "impact": impact})
        raise typer.Exit(EXIT_OK)

    canon_affected = impact["canon_affected"]
    canon_count = len(canon_affected) if isinstance(canon_affected, list) else 0
    console.print(
        f"invalidating [bold]{memory_id}[/] would affect [bold]{impact['total']}[/] memory(ies), "
        f"[bold]{canon_count}[/] of them in canon"
    )
    raise typer.Exit(EXIT_OK)


@memory_app.command("policy")
def memory_policy(
    path: Annotated[Path, typer.Argument(help="Path to the memory JSONL file.")],
    apply: Annotated[bool, typer.Option(help="Apply the pass. Default is a dry run.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Run the policy pass: contradiction, expiry, consolidation."""
    from software_factory.memory import MemoryStore, run_pass

    if not apply:
        console.print("[dim]dry run: pass --apply to write changes[/]") if not as_json else None

    store = MemoryStore(path)
    try:
        store.load()
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if not apply:
        stats = store.stats()
        if as_json:
            _emit({"ok": True, "dryRun": True, "stats": stats})
        else:
            console.print(f"{stats['total']} memories would be examined")
        raise typer.Exit(EXIT_OK)

    report = run_pass(store)
    if as_json:
        _emit({"ok": True, "report": report.as_dict()})
        raise typer.Exit(EXIT_OK)

    console.print(
        f"quarantined {len(report.quarantined)}, expired {len(report.expired)}, "
        f"merged {len(report.merged)}, weakened {len(report.weakened)}"
    )
    raise typer.Exit(EXIT_OK)


@app.command()
def gates(as_json: JsonOpt = False) -> None:
    """List the baseline gates and which stages they run at."""
    from software_factory.evals import STAGE_GATES
    from software_factory.evals.gates import BASELINE_GATES

    by_gate: dict[str, list[str]] = {name: [] for name in BASELINE_GATES}
    for stage, names in STAGE_GATES.items():
        for name in names:
            by_gate.setdefault(name, []).append(stage)

    if as_json:
        _emit({"ok": True, "gates": {k: sorted(v) for k, v in by_gate.items()}})
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("gate")
    table.add_column("stages", overflow="fold")
    for name, stages in sorted(by_gate.items()):
        table.add_row(name, ", ".join(sorted(stages)) or "[dim]not scheduled[/]")
    console.print(table)
    raise typer.Exit(EXIT_OK)


@app.command()
def intake(
    root: RootArg = Path(),
    provider: Annotated[
        str, typer.Option("--provider", help="Event provider, e.g. git-host.")
    ] = "cli",
    event: Annotated[str, typer.Option("--event", help="Event name, e.g. issue.labelled.")] = "",
    author: Annotated[str, typer.Option("--author", help="Provider handle of the author.")] = "",
    ref: Annotated[str, typer.Option("--ref", help="Where a reply goes.")] = "local",
    title: Annotated[str, typer.Option("--title")] = "",
    attribute: Annotated[
        list[str] | None,
        typer.Option("--attribute", "-a", help="key=value, repeatable. Filters match these."),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Put one event through intake and report what it would start.

    FR-18.10 requires local parity: every capability reachable through an integration must
    also be reachable through `sf`, so a fully local factory loses convenience and nothing
    else. This is that path, and it is also how an operator checks a filter without waiting
    for the event it is meant to catch.
    """
    from software_factory.intake import FactoryEvent, Ignored, Origin, Provider, Started
    from software_factory.intake import Refused as IntakeRefused
    from software_factory.intake.events import event_identity
    from software_factory.intake.loading import pipeline_from

    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if provider not in set(Provider):
        console.print(
            f"[red]unknown provider {provider!r}[/]  "
            f"known: {', '.join(sorted(p.value for p in Provider))}"
        )
        raise typer.Exit(EXIT_UNUSABLE)

    attributes: dict[str, Any] = {}
    for pair in attribute or []:
        key, _, value = pair.partition("=")
        if not key or not value:
            console.print(f"[red]--attribute expects key=value, got {pair!r}[/]")
            raise typer.Exit(EXIT_UNUSABLE)
        # A repeated key becomes a list, because that is what an event with several labels
        # looks like and a filter matches such an attribute by intersection.
        existing = attributes.get(key)
        if existing is None:
            attributes[key] = value
        elif isinstance(existing, list):
            existing.append(value)
        else:
            attributes[key] = [existing, value]

    pipeline = pipeline_from(definition)
    outcomes = pipeline.receive(
        FactoryEvent(
            id=event_identity(Provider(provider), ref, event, title),
            provider=Provider(provider),
            event=event,
            origin=Origin(provider=Provider(provider), ref=ref),
            title=title,
            author=author,
            attributes=attributes,
        )
    )

    if as_json:
        _emit(
            {
                "ok": True,
                "outcomes": [
                    {
                        "kind": type(o).__name__.lower(),
                        "automation": getattr(o, "automation", None),
                        "agent": getattr(o, "agent", None),
                        "code": getattr(o, "code", None),
                        "message": getattr(o, "message", None),
                        "remediation": getattr(o, "remediation", None),
                        "reason": getattr(o, "reason", None),
                    }
                    for o in outcomes
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    for outcome in outcomes:
        if isinstance(outcome, Started):
            console.print(f"[green]starts[/] {outcome.automation} → agent {outcome.agent}")
        elif isinstance(outcome, Ignored):
            console.print(
                f"[dim]ignored[/] — {outcome.reason}. Most events are not for this factory; "
                "this is not an error."
            )
        elif isinstance(outcome, IntakeRefused):
            console.print(f"[yellow]refused[/] {outcome.code}: {outcome.message}")
            console.print(f"  [dim]{outcome.remediation}[/]")
    raise typer.Exit(EXIT_OK)


@app.command()
def serve(root: RootArg = Path(), as_json: JsonOpt = False) -> None:
    """Print the factory's tool surface, so a coding agent can work with it (FR-19).

    The surface itself is transport-free: MCP, a local socket (FR-19.8) and a direct call
    are three bindings of the same handlers. This prints the published schemas and guidance
    so a calling agent picks up the correct workflow without an operator explaining it
    (FR-19.9).

    It lists the surface rather than binding a socket, because the work items a running
    factory holds live in the orchestrator's state and this command has none. A `--ledger`
    option here would be an option that does nothing, which is the shape of promise this
    project keeps finding unkept elsewhere.
    """
    from software_factory.factory_tools import FactoryToolServer

    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    server = FactoryToolServer(factory_name=definition.factory.name)
    specs = server.specs()

    if as_json:
        _emit(
            {
                "ok": True,
                "factory": definition.factory.name,
                "tools": [
                    {
                        "name": spec.name,
                        "description": spec.description,
                        "inputSchema": spec.input_schema,
                        "guidance": spec.guidance,
                        "external": spec.external,
                    }
                    for spec in specs
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("tool", "external", "what it does"):
        table.add_column(column, overflow="fold")
    for spec in specs:
        table.add_row(
            spec.name,
            "[yellow]yes[/]" if spec.external else "",
            spec.description + (f"\n[dim]{spec.guidance}[/]" if spec.guidance else ""),
        )
    console.print(table)
    console.print(
        "\n[dim]Tools marked external produce something outside the factory and take a "
        "lease: picking a work item up does not claim it, but doing something visible to it "
        "twice produces two of the artifact.[/]"
    )
    raise typer.Exit(EXIT_OK)


@app.command()
def improve(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    min_size: Annotated[
        int, typer.Option("--min-size", help="Failures needed before a cluster is worth a run.")
    ] = 3,
    as_json: JsonOpt = False,
) -> None:
    """Cluster recent failures into the patterns worth diagnosing (FR-14.2, step one).

    Clustering first is economic: diagnosing one failure costs a run, and diagnosing forty
    instances of one failure costs forty runs and produces one answer. This reports the
    clusters; proposing against one is a run, and adopting a proposal is a human decision
    that needs two approvers when it touches a scorer, a gate, or an eval (FR-25.3).
    """
    from software_factory.improvement import Failure, cluster_failures
    from software_factory.improvement.loop import LoopState, check_effectiveness, may_propose

    ledger = Ledger(path)
    try:
        entries = list(ledger.read())
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    failures = [
        Failure(
            run_id=str(entry.payload.get("run", entry.subject)),
            work_item_id=str(entry.payload.get("workItem", entry.subject)),
            stage=str(entry.payload.get("stage", "unknown")),
            agent=str(entry.actor),
            gate=str(entry.payload.get("gate", "")),
            failure_class=str(entry.payload.get("outcome", "")),
            detail=str(entry.payload.get("detail", "")),
            at=datetime.fromisoformat(entry.ts.replace("Z", "+00:00")),
        )
        for entry in entries
        if entry.type is EntryType.GATE_EVALUATED and entry.payload.get("outcome") == "fail"
    ]

    clusters = cluster_failures(failures, min_size=min_size)

    # The loop's own guards, consulted rather than merely present. `may_propose`,
    # `check_effectiveness` and `detect_drift` implement every safety property the loop
    # claims -- cooling periods, the open-proposal bound, the anti-thrash rule, the
    # self-switch-off -- and none of them had a caller outside tests, so a command
    # reporting "the patterns worth diagnosing" was answering a narrower question than it
    # sounded like: the patterns that *exist*, with nothing said about whether the loop
    # should act on any of them.
    state = LoopState.from_ledger(entries)
    blocked: dict[str, Any] = {}
    for cluster in clusters:
        refusal = may_propose(state, cluster, target=f"cluster/{cluster.signature}")
        if refusal is not None:
            blocked[cluster.signature] = {
                "code": refusal.code,
                "message": refusal.message,
                "remediation": refusal.remediation,
            }

    ineffective = check_effectiveness(state)

    if as_json:
        _emit(
            {
                "ok": True,
                "failures": len(failures),
                "clusters": [
                    {
                        "signature": c.signature,
                        "size": c.size,
                        "workItems": list(c.work_items),
                        "stage": c.stage,
                        "agent": c.agent,
                        "describe": c.describe(),
                        "mayPropose": c.signature not in blocked,
                        "refusal": blocked.get(c.signature),
                    }
                    for c in clusters
                ],
                "proposalsOnRecord": len(state.records),
                "openProposals": len(state.open_proposals()),
                "loopEffectiveness": ineffective,
            }
        )
        raise typer.Exit(EXIT_OK)

    if not failures:
        console.print("[dim]No gate failures in this ledger. Nothing to improve from.[/]")
        raise typer.Exit(EXIT_OK)
    if not clusters:
        console.print(
            f"[dim]{len(failures)} failure(s), none repeating {min_size} times or more.[/]\n"
            "A one-off has no pattern to generalise from, and a proposal drawn from one "
            "instance is a proposal fitted to one instance."
        )
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("signature", "failures", "work items", "pattern", "propose?"):
        table.add_column(column, overflow="fold")
    for cluster in clusters:
        refusal = blocked.get(cluster.signature)
        table.add_row(
            cluster.signature,
            str(cluster.size),
            str(len(cluster.work_items)),
            cluster.failures[0].describe(),
            "[green]yes[/]" if refusal is None else f"[yellow]{refusal['code']}[/]",
        )
    console.print(table)
    if ineffective:
        console.print(f"\n[red]{ineffective}[/]")
    console.print(
        "\n[dim]Work items matter more than failure count: forty failures across two items "
        "is a flaky pair, six across six is a pattern.[/]"
    )
    raise typer.Exit(EXIT_OK)


@app.command()
def metrics(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    days: Annotated[int, typer.Option("--days", help="Window to report over.")] = 7,
    integration: Annotated[
        list[str] | None,
        typer.Option("--integration", help="An integration this factory has, e.g. git-host."),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Metrics for a window, folded from the ledger.

    A metric that needs an integration this factory does not have is reported as
    *unavailable with a reason*, never as zero (FR-15.5): "changes merged: 0" reads as a
    factory that merges nothing, and "unavailable -- no git-host adapter" reads as a factory
    nobody has told about its git host. Those are different situations.
    """
    from datetime import timedelta

    from software_factory.observability import Availability, Window, compute

    ledger = Ledger(path)
    try:
        entries = list(ledger.read())
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    report = compute(
        entries,
        window=Window.last(timedelta(days=days)),
        integrations=frozenset(integration or []),
    )

    if as_json:
        _emit({"ok": True, "metrics": report.as_dict()})
        raise typer.Exit(EXIT_OK)

    runs = report.runs
    console.print(
        f"[bold]{runs.total}[/] run(s) over {days}d — "
        f"{runs.work} work, {runs.evaluation} evaluation, {runs.benchmark} benchmark, "
        f"{runs.improvement} improvement"
    )
    if runs.total:
        console.print(
            f"[dim]{runs.measurement_share:.0%} of runs are the factory measuring itself; "
            "a rising total with flat output can be measurement rather than work.[/]\n"
        )

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("metric", "value", "note"):
        table.add_column(column, overflow="fold")
    for measure in report.measures:
        if measure.availability is not Availability.AVAILABLE:
            table.add_row(
                measure.name,
                f"[yellow]{measure.availability.value}[/]",
                measure.reason,
            )
            continue
        note = ""
        if measure.estimate:
            note = f"estimate; excludes {', '.join(measure.excludes)}"
        elif measure.sample:
            note = f"n={measure.sample}"
        table.add_row(measure.name, f"{measure.value:g} {measure.unit}".strip(), note)
    console.print(table)
    raise typer.Exit(EXIT_OK)


@app.command()
def dash(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8787,
    integration: Annotated[
        list[str] | None, typer.Option("--integration", help="An integration this factory has.")
    ] = None,
    root: Annotated[
        Path | None,
        typer.Option("--root", help="The factory tree. Defaults to the state directory's parent."),
    ] = None,
) -> None:
    """Serve the dashboard from the local ledger.

    Local-first and read-mostly (FR-15.8): no framework, no CDN, no build step, and bound to
    loopback. A dashboard needing `npm install` to look at a factory running offline on a
    laptop fails PR-2 on the first day somebody tries it, and one reachable from the network
    has published a factory's whole history to whoever can reach the port.

    Steering a live run is a *decision* channel and therefore authenticated and
    capability-checked (FR-25.5), so this server does not offer one.
    """
    from software_factory.observability.dash import serve

    if not path.exists():
        console.print(f"[red]no ledger at {path}[/]")
        raise typer.Exit(EXIT_UNUSABLE)

    server = serve(
        path,
        host=host,
        port=port,
        integrations=frozenset(integration or []),
        root=root,
        ready=lambda url: console.print(f"dashboard on {url}  [dim](ctrl-c to stop)[/]"),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\nstopped")
    finally:
        server.server_close()
    raise typer.Exit(EXIT_OK)


govern_app = typer.Typer(
    help="Data classification, retention, and ledger segmentation.", no_args_is_help=True
)
app.add_typer(govern_app, name="govern")


@govern_app.command("classes")
def govern_classes(as_json: JsonOpt = False) -> None:
    """What each persisted class can contain, how long it is kept, and why.

    A retention policy that does not say what is *in* the thing being retained is a number
    with no argument behind it (FR-27.1).
    """
    from software_factory.governance import DEFAULT_CLASSIFICATION

    rows = [rule.as_dict() for rule in DEFAULT_CLASSIFICATION.values()]

    if as_json:
        _emit({"ok": True, "classes": rows})
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("class", "may contain", "retention", "erasable", "why"):
        table.add_column(column, overflow="fold")
    for row in rows:
        raw_retention = row["retention"]
        if raw_retention is None:
            kept = "[dim]until erased[/]"
        else:
            seconds = int(str(raw_retention))
            kept = f"{seconds // 86400}d" if seconds >= 86400 else f"{seconds // 3600}h"
        table.add_row(
            str(row["class"]),
            ", ".join(row["contains"]),  # type: ignore[arg-type]
            kept,
            "yes" if row["erasableBySubject"] else "[dim]no[/]",
            str(row["rationale"]),
        )
    console.print(table)
    raise typer.Exit(EXIT_OK)


@govern_app.command("seal")
def govern_seal(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    size: Annotated[int, typer.Option("--size", help="Entries per segment.")] = 10_000,
    as_json: JsonOpt = False,
) -> None:
    """Seal complete ledger segments so an archived prefix stays verifiable.

    Bounded growth (NFR-3.2) is otherwise a claim with no mechanism: an append-only log grows
    forever and `verify()` gets slower every day until nobody runs it. Segment digests chain
    across the boundary, so verifying a later segment needs the earlier segment's digest and
    not its entries (FR-27.2). Sealing does not delete anything -- archiving is a separate
    act, and the manifest is what makes it safe to take.
    """
    from software_factory.governance import Manifest, seal

    manifest_path = path.with_suffix(path.suffix + ".segments")
    ledger = Ledger(path)
    manifest = Manifest.load(manifest_path)
    try:
        sealed = seal(ledger, manifest, size=size)
        manifest.verify()
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if as_json:
        _emit(
            {
                "ok": True,
                "manifest": str(manifest_path),
                "sealed": [s.as_dict() for s in sealed],
                "sealedThrough": manifest.sealed_through,
            }
        )
        raise typer.Exit(EXIT_OK)

    if not sealed:
        console.print(
            f"[dim]Nothing to seal: fewer than {size} unsealed entries. "
            f"Sealed through {manifest.sealed_through}.[/]"
        )
        raise typer.Exit(EXIT_OK)
    for segment in sealed:
        console.print(
            f"sealed segment {segment.index}: entries {segment.first_seq}-{segment.last_seq} "
            f"[dim]({segment.digest[:12]})[/]"
        )
    console.print(f"\nManifest: {manifest_path}")
    raise typer.Exit(EXIT_OK)


@govern_app.command("sweep")
def govern_sweep(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Actually tombstone. Without it, nothing is destroyed."),
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Expire what is past its retention, keeping what a legal hold covers (FR-27.1).

    Dry by default, and the report says which it was. A retention report that asserts
    deletions it did not make is worse than no report -- it is shaped to be shown to an
    auditor, and it would be a positive claim nothing established.
    """
    from software_factory.governance import Artifact, DataClass, Retention

    try:
        entries = list(Ledger(path).read())
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    # The ledger's own entries are the artifacts a local factory has to answer for. Bodies
    # live elsewhere in a larger deployment; the classification and the arithmetic are the
    # same either way, which is the point of doing it here.
    artifacts = [
        Artifact(
            id=str(entry.seq),
            data_class=DataClass.LEDGER,
            created_at=datetime.fromisoformat(entry.ts.replace("Z", "+00:00")),
        )
        for entry in entries
    ]

    tombstoned: list[str] = []
    report = Retention().sweep(
        artifacts,
        tombstone=(lambda a: tombstoned.append(a.id)) if apply else None,
        dry_run=not apply,
    )

    if as_json:
        _emit({"ok": True, "sweep": report.as_dict()})
        raise typer.Exit(EXIT_OK)

    console.print(
        f"examined {report.examined}, expiring {len(report.expired)}, "
        f"held {len(report.held)}, already tombstoned {len(report.already_tombstoned)}"
    )
    if not apply:
        console.print("[yellow]dry run — nothing was destroyed; pass --apply to act[/]")
    raise typer.Exit(EXIT_OK)


@govern_app.command("erase")
def govern_erase(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    subject: Annotated[str, typer.Argument(help="Whose data to erase.")],
    requested_by: Annotated[str, typer.Option("--by", help="Who asked, for the receipt.")],
    apply: Annotated[bool, typer.Option("--apply", help="Actually destroy.")] = False,
    as_json: JsonOpt = False,
) -> None:
    """Answer a subject-erasure request, and say honestly what cannot be erased (FR-27.3).

    The report names what remains and why. The ledger holds references and decisions, never
    bodies, and the record that a thing existed and was erased survives by design -- a
    subject is entitled to know that, not to be told "everything is gone".
    """
    from software_factory.governance import Artifact, DataClass, Retention

    try:
        entries = list(Ledger(path).read())
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    artifacts = [
        Artifact(
            id=str(entry.seq),
            data_class=DataClass.LEDGER,
            created_at=datetime.fromisoformat(entry.ts.replace("Z", "+00:00")),
            subjects=frozenset({str(entry.actor)}),
        )
        for entry in entries
    ]

    destroyed: list[str] = []
    report = Retention().erase(
        subject,
        artifacts,
        requested_by=requested_by,
        destroy=(lambda a: destroyed.append(a.id)) if apply else None,
        dry_run=not apply,
    )

    if as_json:
        _emit({"ok": True, "erasure": report.as_dict()})
        raise typer.Exit(EXIT_OK)

    console.print(
        f"examined {report.examined} for {subject!r}: erased {len(report.erased)}, "
        f"unerasable {len(report.unerasable)}, blocked by hold {len(report.blocked_by_hold)}"
    )
    for artifact_id, why in report.unerasable[:3]:
        console.print(f"  [yellow]{artifact_id}[/] {why}")
    if not apply:
        console.print("[yellow]dry run — nothing was destroyed; pass --apply to act[/]")
    raise typer.Exit(EXIT_OK)


@govern_app.command("verify")
def govern_verify(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    as_json: JsonOpt = False,
) -> None:
    """Verify the segment chain, and each sealed range against the entries still present.

    The chain check alone establishes that the manifest is internally consistent, which
    reads far stronger than it is: the entries it describes could have been rewritten
    underneath it. Where the entries are present they are re-hashed. Where they are not --
    an archived prefix, the case this whole mechanism exists for -- the output says so by
    name, rather than reporting a check it did not perform.
    """
    from software_factory.governance import Manifest
    from software_factory.ledger import Ledger

    manifest_path = path.with_suffix(path.suffix + ".segments")
    manifest = Manifest.load(manifest_path)
    try:
        if path.exists():
            report = manifest.verify_against(Ledger(path))
        else:
            manifest.verify()
            report = None
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if as_json:
        _emit(
            {
                "ok": True,
                "segments": len(manifest.segments),
                "sealedThrough": manifest.sealed_through,
                "verification": report.as_dict() if report else {"chainOnly": "all"},
            }
        )
        raise typer.Exit(EXIT_OK)
    console.print(
        f"[green]verified[/] — {len(manifest.segments)} segment(s), "
        f"sealed through entry {manifest.sealed_through}"
    )
    if report is None:
        console.print("[yellow]the ledger was not present; the segment chain only[/]")
    elif report.chain_only:
        console.print(
            f"[yellow]segments {sorted(report.chain_only)} were checked as chain links "
            "only — their entries are not present, so their contents were not verified[/]"
        )
    raise typer.Exit(EXIT_OK)


@app.command()
def spend(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    limit: Annotated[float, typer.Option("--limit", help="Cap in cost units.")] = 100.0,
    period_hours: Annotated[int, typer.Option("--hours", help="Window to report over.")] = 24,
    as_json: JsonOpt = False,
) -> None:
    """Spend against a cap, attributed by cause, agent, stage, and work item.

    Per-run budgets bound one agent. A hundred runs each inside their budget is a hundred
    budgets' worth of spend, and "every run was within its limit" is the sentence that
    precedes every surprise invoice (FR-26.1).
    """
    from datetime import timedelta

    from software_factory.economics import Ledgerless, SpendCap, charges_from

    ledger = Ledger(path)
    try:
        entries = list(ledger.read())
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    # The fold lives in `economics` so this command and the coordinator's own cap check
    # compute the same number. Two folds with one name is how a factory reports itself
    # within budget while spending past it.
    charges = charges_from(entries)

    cap = SpendCap(scope=str(path), limit_units=limit, period=timedelta(hours=period_hours))
    report = Ledgerless(cap).report(charges)

    if as_json:
        _emit({"ok": True, "spend": report.as_dict()})
        raise typer.Exit(EXIT_OK)

    colour = {
        "ok": "green",
        "warning": "yellow",
        "intake_stopped": "yellow",
        "halted": "red",
    }[report.state.value]
    console.print(
        f"[{colour}]{report.state.value}[/] — {report.spent:.2f} of {report.limit:.2f} units "
        f"({report.fraction:.0%}) over the last {period_hours}h"
    )
    if not charges:
        console.print(
            "[dim]No attributed spend in the window. `MODEL_CALLED` entries carry "
            "`costUnits`; a factory that has not run yet has none.[/]"
        )
        raise typer.Exit(EXIT_OK)

    console.print(f"[dim]overhead (not primary work): {report.overhead_fraction:.0%}[/]\n")
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("breakdown")
    table.add_column("units", justify="right")
    for label, values in (
        ("by cause", report.by_cause),
        ("by agent", report.by_agent),
        ("by stage", report.by_stage),
    ):
        table.add_row(f"[bold]{label}[/]", "")
        for key, value in sorted(values.items(), key=lambda pair: -pair[1]):
            table.add_row(f"  {key}", f"{value:.2f}")
    console.print(table)
    raise typer.Exit(EXIT_OK)


@app.command()
def principals(root: RootArg = Path(), as_json: JsonOpt = False) -> None:
    """Who this factory recognises, and what each of them may decide.

    The security answer to "who can approve, override, widen, or stop?" -- computed from the
    definition, because that is the only place a capability grant can be reviewed by
    somebody other than the person who wrote it (FR-25.2).
    """
    from software_factory.identity.loading import directory_from
    from software_factory.identity.principals import Capability

    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    book = directory_from(definition)
    rows = [
        {
            "id": p.id,
            "kind": p.kind.value,
            "displayName": p.display_name,
            "groups": sorted(p.groups),
            "capabilities": sorted(c.value for c in p.capabilities),
            "identities": sorted(p.identities),
            "active": p.active,
        }
        for p in book.all()
    ]
    unheld = sorted(c.value for c in Capability if not book.holders(c))

    if as_json:
        _emit({"ok": True, "principals": rows, "unheldCapabilities": unheld})
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("principal", "kind", "groups", "may decide"):
        table.add_column(column, overflow="fold")
    for row in rows:
        name = str(row["id"]) + ("" if row["active"] else " [dim](inactive)[/]")
        table.add_row(
            name,
            str(row["kind"]),
            ", ".join(row["groups"]) or "[dim]none[/]",  # type: ignore[arg-type]
            ", ".join(row["capabilities"]) or "[dim]nothing[/]",  # type: ignore[arg-type]
        )
    console.print(table)
    if unheld:
        console.print(
            f"\n[yellow]No principal holds:[/] {', '.join(unheld)}.\n"
            "A checkpoint answered by a capability nobody holds parks its work item "
            "and never clears."
        )
    raise typer.Exit(EXIT_OK)


@app.command()
def stages(as_json: JsonOpt = False) -> None:
    """Print the default stage graph and which stages cannot be skipped."""
    from software_factory.orchestrator import DEFAULT_NON_SKIPPABLE, DEFAULT_TRANSITIONS

    graph = {
        stage.value: sorted(target.value for target in targets)
        for stage, targets in DEFAULT_TRANSITIONS.items()
    }
    non_skippable = sorted(s.value for s in DEFAULT_NON_SKIPPABLE)

    if as_json:
        _emit({"ok": True, "transitions": graph, "nonSkippable": non_skippable})
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("stage")
    table.add_column("may move to", overflow="fold")
    for stage, targets in graph.items():
        marker = " [yellow]*[/]" if stage in non_skippable else ""
        table.add_row(f"{stage}{marker}", ", ".join(targets) or "[dim]terminal[/]")
    console.print(table)
    console.print(
        "\n[yellow]*[/] cannot be skipped on an agent's authority; skipping needs a human decision"
    )
    raise typer.Exit(EXIT_OK)


checkpoints_app = typer.Typer(
    help="Human checkpoints: what is waiting on a person, and answering it.",
    no_args_is_help=True,
)
app.add_typer(checkpoints_app, name="checkpoints")


@checkpoints_app.command("list")
def checkpoints_list(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    root: Annotated[Path, typer.Option("--factory", help="Factory root, for principals.")] = Path(),
    as_json: JsonOpt = False,
) -> None:
    """What is waiting on a person, who can clear it, and how overdue it is (FR-16.1).

    Rebuilt from the ledger rather than from memory: a checkpoint has to outlive the
    process that opened it, or "a person decides" means "a person decides before the run
    ends". Its due state is computed from the clock here, so a deadline that passed while
    nothing was running is not missed.
    """
    from software_factory.identity.checkpoints import CheckpointBook, CheckpointStatus
    from software_factory.identity.loading import directory_from

    try:
        definition = load_strict(root)
        book = CheckpointBook.from_ledger(Ledger(path).read(), directory_from(definition))
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    rows: list[dict[str, Any]] = []
    routable: dict[str, list[str]] = {}
    for checkpoint in sorted(book.checkpoints.values(), key=lambda c: c.opened_at):
        routable[checkpoint.id] = book.routable_to(checkpoint.id)
        rows.append(
            {
                **checkpoint.as_dict(),
                "dueState": checkpoint.due_state().value,
                "routableTo": routable[checkpoint.id],
            }
        )

    open_rows = [r for r in rows if r["status"] != CheckpointStatus.RESOLVED.value]
    if as_json:
        _emit({"ok": True, "checkpoints": rows, "open": len(open_rows)})
        raise typer.Exit(EXIT_OK)

    if not open_rows:
        console.print("[green]nothing is waiting on a person[/]")
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("id")
    table.add_column("kind")
    table.add_column("work item")
    table.add_column("state")
    table.add_column("who can clear it", overflow="fold")
    for row in open_rows:
        colour = {"open": "green", "reminded": "yellow", "parked": "red"}.get(
            str(row["dueState"]), "white"
        )
        table.add_row(
            str(row["id"]),
            str(row["kind"]),
            str(row["workItem"]),
            f"[{colour}]{row['dueState']}[/]",
            ", ".join(routable[str(row["id"])]) or "[red]nobody[/]",
        )
    console.print(table)
    raise typer.Exit(EXIT_OK)


@checkpoints_app.command("resolve")
def checkpoints_resolve(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    checkpoint_id: Annotated[str, typer.Argument(help="Which checkpoint to answer.")],
    principal: Annotated[str, typer.Option("--as", help="Who is deciding.")],
    answer: Annotated[str, typer.Option("--answer", help="The decision, and why.")],
    root: Annotated[Path, typer.Option("--factory", help="Factory root, for principals.")] = Path(),
    as_json: JsonOpt = False,
) -> None:
    """Answer a checkpoint as a named principal, recording the decision.

    The authorisation and the decision are written as one ledger entry, so an approval and
    the thing it authorised cannot end up as two records that disagree.
    """
    from software_factory.identity import Refused as IdentityRefused
    from software_factory.identity.checkpoints import CheckpointBook
    from software_factory.identity.loading import directory_from

    try:
        definition = load_strict(root)
        ledger = Ledger(path)
        book = CheckpointBook.from_ledger(ledger.read(), directory_from(definition))
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    decision = book.resolve(checkpoint_id, principal_id=principal, answer=answer)
    if isinstance(decision, IdentityRefused):
        if as_json:
            _emit(
                {
                    "ok": False,
                    "code": decision.code,
                    "message": decision.message,
                    "remediation": decision.remediation,
                }
            )
        else:
            console.print(f"[red]{decision.message}[/]\n  [dim]{decision.remediation}[/]")
        raise typer.Exit(EXIT_FAILED)

    ledger.append(
        EntryType.CHECKPOINT_RESOLVED,
        actor=principal,
        subject=checkpoint_id,
        payload={"answer": answer, "decision": decision.as_dict()},
    )

    if as_json:
        _emit({"ok": True, "decision": decision.as_dict()})
        raise typer.Exit(EXIT_OK)
    console.print(f"[green]resolved[/] {checkpoint_id} — decided by {decision.principal_id}")
    raise typer.Exit(EXIT_OK)


@app.command()
def delegation(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    as_json: JsonOpt = False,
) -> None:
    """Which agents served each request, in what relation, and what each cost (FR-34.4).

    Spend is already attributed by agent. What this adds is the shape: a run whose own spend
    is small and whose descendants' is not is exactly the case a flat per-agent report
    renders as innocent.

    A factory that never delegates gets a flat list, which is the same view rather than a
    different one -- a reader should not have to know which case they are in.
    """
    from software_factory.orchestrator.delegation import tree_from

    try:
        roots = tree_from(Ledger(path).read())
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    delegated = [root for root in roots if root.children]

    if as_json:
        _emit(
            {
                "ok": True,
                "runs": len(roots),
                "delegating": len(delegated),
                "tree": [root.as_dict() for root in roots],
            }
        )
        raise typer.Exit(EXIT_OK)

    if not roots:
        console.print("[dim]No runs in this ledger.[/]")
        raise typer.Exit(EXIT_OK)

    for root in roots:
        console.print(root.render())
    if not delegated:
        console.print(
            "\n[dim]No run delegated. This is the flat case, shown by the same view: a "
            "reader should not have to know which case they are in.[/]"
        )
    raise typer.Exit(EXIT_OK)


@app.command()
def explain(
    path: Annotated[Path, typer.Argument(help="Path to the ledger JSONL file.")],
    work_item_id: Annotated[str, typer.Argument(help="Which work item to ask about.")],
    question: Annotated[str, typer.Argument(help="What you want to know.")],
    as_json: JsonOpt = False,
) -> None:
    """Ask a handed-off work item why it did something (FR-32).

    Answered from what the run wrote down at the time -- its decisions, what it tried, the
    constraints it found -- and from nothing else. It does not re-run anything: an answer
    produced by re-running is an answer about a *different* execution, so a reviewer asking
    "why did you do that" would be told what a second run would do.

    When the record does not contain the answer it says so. That is the point where a person
    is most likely to accept a plausible reconstruction: reading a change they did not
    write, from a system that has been right so far, in a hurry.
    """
    from software_factory.orchestrator.explain import Explainer

    try:
        entries = list(Ledger(path).read())
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    answer = Explainer.from_ledger(entries).answer(work_item_id, question)

    if as_json:
        _emit({"ok": answer.answered, "answer": answer.as_dict()})
        raise typer.Exit(EXIT_OK if answer.answered else EXIT_FAILED)

    if not answer.answered:
        console.print(f"[yellow]{answer.note}[/]")
        raise typer.Exit(EXIT_FAILED)

    console.print(f"[bold]{answer.work_item_id}[/] — {answer.question}\n")
    for citation in answer.citations:
        console.print(f"  [dim]{citation.kind.value} · {citation.stage} · {citation.run_id}[/]")
        console.print(f"  {citation.text}\n")
    console.print(f"[dim]{answer.note}[/]")
    raise typer.Exit(EXIT_OK)


@app.command()
def providers(
    root: RootArg = Path(),
    probe: Annotated[
        bool,
        typer.Option(
            "--probe",
            help="Contact each endpoint. Off by default: this command must work offline.",
        ),
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """What each tier will actually call, and whether it can right now.

    A definition can name a provider that resolves to nothing, and until this command
    existed the only way to find out was to start a run and watch it fail. The check is
    offline unless `--probe` is passed: key presence and endpoint resolution are knowable
    without contacting anything, and a diagnostic that needs the network to tell you the
    network is misconfigured is not much of a diagnostic.
    """
    from software_factory.providers.registry import UnknownProviderError, resolve, spec_for

    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    ladder = definition.factory.ladder
    rows: list[dict[str, Any]] = []
    for tier in ladder.tiers if ladder else ():
        try:
            spec = spec_for(tier.provider)
        except UnknownProviderError as exc:
            rows.append(
                {
                    "tier": tier.name,
                    "provider": tier.provider,
                    "model": tier.model,
                    "endpoint": "",
                    "local": False,
                    "usable": False,
                    "reason": str(exc),
                }
            )
            continue

        resolution = resolve(tier.provider)
        reason = resolution.reason
        if not reason and probe:
            ok, detail = resolution.provider.available()
            if not ok:
                reason = detail

        rows.append(
            {
                "tier": tier.name,
                "provider": spec.name,
                "model": tier.model,
                "endpoint": resolution.base_url,
                "local": spec.local,
                "usable": not reason,
                "reason": reason,
                "apiKeyEnv": spec.api_key_env,
                # The definition's own `local:` flag and the provider's are separate
                # claims, and a mismatch is worth seeing: a tier marked local that
                # resolves to a hosted endpoint sends data off the machine while the
                # egress report says it does not.
                "declaredLocal": tier.local,
            }
        )

    mismatched = [r for r in rows if r.get("declaredLocal") and not r.get("local")]
    ok = all(r["usable"] for r in rows) and not mismatched

    if as_json:
        _emit({"ok": ok, "tiers": rows, "mismatchedLocality": [r["tier"] for r in mismatched]})
        raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)

    if not rows:
        console.print("[yellow]this factory declares no ladder, so no tier resolves[/]")
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("tier")
    table.add_column("provider")
    table.add_column("model")
    table.add_column("endpoint", overflow="fold")
    table.add_column("state")
    for row in rows:
        state = "[green]ready[/]" if row["usable"] else f"[red]{row['reason']}[/]"
        where = row["endpoint"] or "[dim]unresolved[/]"
        if row.get("local"):
            where = f"{where} [dim](this machine)[/]"
        table.add_row(row["tier"], row["provider"], row["model"], where, state)
    console.print(table)

    for row in mismatched:
        console.print(
            f"[yellow]tier {row['tier']!r} declares `local: true` but {row['provider']!r} "
            f"resolves to {row['endpoint']} — the egress report would understate where "
            f"this factory sends data[/]"
        )
    if not probe:
        console.print("\n[dim]key presence and resolution only; --probe contacts each endpoint[/]")
    raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)


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


chat_app = typer.Typer(
    help="The Slack integration: verify a delivery, replay one locally, check health.",
    no_args_is_help=True,
)
app.add_typer(chat_app, name="chat")

#: Credentials come from the environment, never from a flag.
#:
#: A token passed as `--token xoxb-...` is visible to every process on the host through
#: `ps`, and lands in the shell history of whoever ran it. This codebase already found that
#: exact leak in the container executor, where secret *values* went on a docker command
#: line with a test asserting the leak as the requirement. There is no reason to make it
#: twice.
SLACK_TOKEN_ENV = "SF_SLACK_BOT_TOKEN"
SLACK_SECRET_ENV = "SF_SLACK_SIGNING_SECRET"


def _slack_credentials(*, require_token: bool) -> Any:
    from software_factory.integrations import SlackCredentials

    return SlackCredentials(
        bot_token=os.environ.get(SLACK_TOKEN_ENV, "" if require_token else "unused"),
        signing_secret=os.environ.get(SLACK_SECRET_ENV, ""),
        bot_user_id=os.environ.get("SF_SLACK_BOT_USER_ID", ""),
        team_domain=os.environ.get("SF_SLACK_TEAM_DOMAIN", ""),
    )


@chat_app.command("verify")
def chat_verify(
    body: Annotated[Path, typer.Argument(help="File holding the raw request body.")],
    timestamp: Annotated[str, typer.Option("--timestamp", help="X-Slack-Request-Timestamp.")],
    signature: Annotated[str, typer.Option("--signature", help="X-Slack-Signature.")],
    as_json: JsonOpt = False,
) -> None:
    """Check one delivery's signature against this workspace's signing secret.

    Verification is over the **raw bytes** of the request, which is why this takes a file
    rather than parsed JSON: re-serialising a parsed body changes whitespace and key order,
    so a receiver that verifies `json.dumps(json.loads(body))` rejects every genuine Slack
    request -- and the failure looks exactly like a wrong secret, so the usual fix is to
    stop verifying.
    """
    from software_factory.integrations import verify_signature

    raw = body.read_bytes()
    try:
        verify_signature(
            signing_secret=os.environ.get(SLACK_SECRET_ENV, ""),
            timestamp=timestamp,
            body=raw,
            signature=signature,
        )
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if as_json:
        _emit({"ok": True, "verified": True, "bytes": len(raw)})
    else:
        console.print(f"[green]verified[/] {len(raw)} bytes signed by this workspace's secret")
    raise typer.Exit(EXIT_OK)


@chat_app.command("sign")
def chat_sign(
    body: Annotated[Path, typer.Argument(help="File holding the raw request body.")],
    as_json: JsonOpt = False,
) -> None:
    """Print the headers Slack would send for this body.

    So a receiver can be exercised end to end without a workspace. A signing helper that
    only exists inside a test suite is one the shipped path is never checked against.
    """
    from software_factory.integrations.slack import signature_headers

    headers = signature_headers(
        signing_secret=os.environ.get(SLACK_SECRET_ENV, ""), body=body.read_bytes()
    )
    if as_json:
        _emit({"ok": True, "headers": headers})
    else:
        for name, value in headers.items():
            console.print(f"{name}: {value}")
    raise typer.Exit(EXIT_OK)


@chat_app.command("receive")
def chat_receive(
    envelope: Annotated[Path, typer.Argument(help="File holding a Slack event envelope.")],
    root: Annotated[
        Path, typer.Option("--root", help="The factory definition directory.")
    ] = Path(),
    channel: Annotated[
        list[str] | None,
        typer.Option("--channel", help="A channel whose plain messages are requests. Repeatable."),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Put a saved Slack delivery through intake, with no network at all.

    FR-18.10's local parity, and the way to check a filter without waiting for the message
    it is meant to catch. Mentions are requests wherever they happen; a plain message is
    only a request in a channel named with `--channel`, because reading everything in a
    workspace is a decision an operator makes on purpose.
    """
    from software_factory.intake import Ignored, Started
    from software_factory.intake import Refused as IntakeRefused
    from software_factory.intake.loading import pipeline_from
    from software_factory.integrations import SlackAdapter, challenge_for

    try:
        raw = json.loads(envelope.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]cannot read {envelope}[/]: {exc}")
        raise typer.Exit(EXIT_UNUSABLE) from exc

    challenge = challenge_for(raw)
    if challenge is not None:
        # Not an event. Echoing it is the whole of Slack's URL verification, and treating
        # it as work would file an item every time somebody re-saved the app config.
        if as_json:
            _emit({"ok": True, "challenge": challenge, "startedWork": False})
        else:
            console.print(f"[dim]url_verification[/] — echo back: {challenge}")
        raise typer.Exit(EXIT_OK)

    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    try:
        adapter = SlackAdapter(
            credentials=_slack_credentials(require_token=False),
            channels=frozenset(channel or []),
        )
        event = adapter.normalise(raw)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if event is None:
        if as_json:
            _emit({"ok": True, "ignored": True, "reason": "not an event this factory acts on"})
        else:
            console.print(
                "[dim]ignored[/] — a bot message, an edit, or a channel this factory does "
                "not read. Most events are not for this factory; this is not an error."
            )
        raise typer.Exit(EXIT_OK)

    outcomes = pipeline_from(definition).receive(event)

    if as_json:
        _emit(
            {
                "ok": True,
                "event": {
                    "id": event.id,
                    "event": event.event,
                    "title": event.title,
                    "author": event.author,
                    "replyTo": event.origin.ref,
                    "backpressureSource": event.origin.source_key,
                    "attributes": event.attributes,
                },
                "outcomes": [
                    {
                        "kind": type(o).__name__.lower(),
                        "automation": getattr(o, "automation", None),
                        "agent": getattr(o, "agent", None),
                        "code": getattr(o, "code", None),
                        "message": getattr(o, "message", None),
                        "reason": getattr(o, "reason", None),
                    }
                    for o in outcomes
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    console.print(f"[bold]{event.event}[/] {event.id}  [dim]from[/] {event.author}")
    console.print(f"  [dim]title[/]  {event.title}")
    console.print(f"  [dim]reply[/]  {event.origin.render()}")
    for outcome in outcomes:
        if isinstance(outcome, Started):
            console.print(f"[green]starts[/] {outcome.automation} → agent {outcome.agent}")
        elif isinstance(outcome, Ignored):
            console.print(f"[dim]ignored[/] — {outcome.reason}")
        elif isinstance(outcome, IntakeRefused):
            console.print(f"[yellow]refused[/] {outcome.code}: {outcome.message}")
            console.print(f"  [dim]{outcome.remediation}[/]")
    raise typer.Exit(EXIT_OK)


@chat_app.command("health")
def chat_health(as_json: JsonOpt = False) -> None:
    """Ask Slack whether this app's token still works.

    Reads `SF_SLACK_BOT_TOKEN` and `SF_SLACK_SIGNING_SECRET` from the environment. This is
    the one command here that opens a socket; everything else is local.
    """
    from software_factory.integrations import SlackAdapter

    try:
        adapter = SlackAdapter(credentials=_slack_credentials(require_token=True))
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    adapter.authenticate()
    report = adapter.health()

    if as_json:
        _emit({"ok": report.accepts_events, "health": report.as_dict()})
    else:
        colour = {"healthy": "green", "degraded": "yellow", "unavailable": "red"}
        console.print(
            f"[{colour[report.status.value]}]{report.status.value}[/]"
            f"{'  ' + report.detail if report.detail else ''}"
        )
    raise typer.Exit(EXIT_OK if report.accepts_events else EXIT_FAILED)


change_app = typer.Typer(
    help="Observe what the repository did with a change after handoff.",
    no_args_is_help=True,
)
app.add_typer(change_app, name="change")

GIT_HOST_TOKEN_ENV = "SF_GIT_HOST_TOKEN"
GIT_HOST_SECRET_ENV = "SF_GIT_HOST_WEBHOOK_SECRET"


def _git_host_credentials() -> Any:
    from software_factory.integrations import GitHostCredentials

    return GitHostCredentials(
        token=os.environ.get(GIT_HOST_TOKEN_ENV, ""),
        webhook_secret=os.environ.get(GIT_HOST_SECRET_ENV, ""),
        factory_login=os.environ.get("SF_GIT_HOST_FACTORY_LOGIN", ""),
    )


@change_app.command("observe")
def change_observe(
    payload: Annotated[Path, typer.Argument(help="File holding a `pull_request` webhook body.")],
    ledger_path: Annotated[
        Path, typer.Option("--ledger", help="Where to record the observation.")
    ] = Path(".factory/ledger.jsonl"),
    event: Annotated[
        str, typer.Option("--event", help="The host's event name (the X-GitHub-Event header).")
    ] = "pull_request",
    commits: Annotated[
        Path | None,
        typer.Option("--commits", help="File holding the branch's commit list, for autonomy."),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Record what the repository says happened to a change (FR-15.14).

    This is the observation three outcome metrics have always been waiting on. `sf metrics`
    reported `changes_merged`, `autonomy` and `cycle_time_to_merge` as unavailable because
    nothing observed the repository after merge, and a factory that counted its own handoffs
    as merges would be grading its own homework.

    Reads a saved webhook body, so it works with the network denied. Pass `--commits` with
    the branch's commit list to make autonomy (O-5) computable: without it the human-commit
    count is *unknown*, and unknown is recorded as unknown rather than as zero -- treating
    it as zero reports perfect autonomy for every change the factory ever produced.
    """
    from software_factory.integrations import GitHostAdapter
    from software_factory.ledger import EntryType, Ledger

    try:
        raw = json.loads(payload.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]cannot read {payload}[/]: {exc}")
        raise typer.Exit(EXIT_UNUSABLE) from exc

    raw["_event"] = event
    if commits is not None:
        try:
            raw["_commits"] = json.loads(commits.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            console.print(f"[red]cannot read {commits}[/]: {exc}")
            raise typer.Exit(EXIT_UNUSABLE) from exc

    observation = GitHostAdapter(credentials=_git_host_credentials()).observe(raw)
    if observation is None:
        if as_json:
            _emit({"ok": True, "recorded": False, "reason": "not a change lifecycle event"})
        else:
            console.print(
                "[dim]nothing to record[/] — this delivery is not a change opening, "
                "merging or closing. A push to the branch is not a state change."
            )
        raise typer.Exit(EXIT_OK)

    Ledger(ledger_path).append(
        EntryType.CHANGE_OBSERVED,
        actor="git-host",
        subject=observation.change,
        payload=observation.as_payload(),
    )

    if as_json:
        _emit({"ok": True, "recorded": True, "observation": observation.as_payload()})
        raise typer.Exit(EXIT_OK)

    console.print(f"[green]recorded[/] {observation.change} — {observation.state.value}")
    if observation.human_commits is None:
        console.print(
            "  [yellow]human commits unknown[/] — autonomy stays unavailable rather than "
            "reporting this change as fully autonomous"
        )
    else:
        console.print(f"  human commits: {observation.human_commits}")
    raise typer.Exit(EXIT_OK)


@change_app.command("receive")
def change_receive(
    payload: Annotated[Path, typer.Argument(help="File holding a webhook body.")],
    root: Annotated[
        Path, typer.Option("--root", help="The factory definition directory.")
    ] = Path(),
    event: Annotated[
        str, typer.Option("--event", help="The host's event name (the X-GitHub-Event header).")
    ] = "issues",
    as_json: JsonOpt = False,
) -> None:
    """Put a saved git-host delivery through intake, with no network at all.

    FR-18.10's local parity for the second adapter, on the same terms as `sf chat receive`.
    """
    from software_factory.intake import Ignored, Started
    from software_factory.intake import Refused as IntakeRefused
    from software_factory.intake.loading import pipeline_from
    from software_factory.integrations import GitHostAdapter

    try:
        raw = json.loads(payload.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[red]cannot read {payload}[/]: {exc}")
        raise typer.Exit(EXIT_UNUSABLE) from exc

    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    raw["_event"] = event
    adapter = GitHostAdapter(
        credentials=_git_host_credentials(),
        repositories=frozenset(r.slug() for r in definition.factory.repositories),
    )
    factory_event = adapter.normalise(raw)

    if factory_event is None:
        if as_json:
            _emit({"ok": True, "ignored": True, "reason": "not an event this factory acts on"})
        else:
            console.print(
                "[dim]ignored[/] — another repository, the factory's own comment, or an "
                "action this factory does not act on. Most events are not for this factory."
            )
        raise typer.Exit(EXIT_OK)

    outcomes = pipeline_from(definition).receive(factory_event)

    if as_json:
        _emit(
            {
                "ok": True,
                "event": {
                    "id": factory_event.id,
                    "event": factory_event.event,
                    "title": factory_event.title,
                    "author": factory_event.author,
                    "replyTo": factory_event.origin.ref,
                    "backpressureSource": factory_event.origin.source_key,
                    "attributes": factory_event.attributes,
                },
                "outcomes": [
                    {
                        "kind": type(o).__name__.lower(),
                        "automation": getattr(o, "automation", None),
                        "agent": getattr(o, "agent", None),
                        "code": getattr(o, "code", None),
                        "message": getattr(o, "message", None),
                    }
                    for o in outcomes
                ],
            }
        )
        raise typer.Exit(EXIT_OK)

    console.print(f"[bold]{factory_event.event}[/]  [dim]from[/] {factory_event.author}")
    console.print(f"  [dim]title[/]  {factory_event.title}")
    console.print(f"  [dim]reply[/]  {factory_event.origin.render()}")
    for outcome in outcomes:
        if isinstance(outcome, Started):
            console.print(f"[green]starts[/] {outcome.automation} → agent {outcome.agent}")
        elif isinstance(outcome, Ignored):
            console.print(f"[dim]ignored[/] — {outcome.reason}")
        elif isinstance(outcome, IntakeRefused):
            console.print(f"[yellow]refused[/] {outcome.code}: {outcome.message}")
    raise typer.Exit(EXIT_OK)


@change_app.command("verify")
def change_verify(
    body: Annotated[Path, typer.Argument(help="File holding the raw delivery body.")],
    signature: Annotated[str, typer.Option("--signature", help="X-Hub-Signature-256.")],
    as_json: JsonOpt = False,
) -> None:
    """Check one delivery's signature against this repository's webhook secret.

    Note what is *not* here: a replay window. The host signs the body alone, with no
    timestamp in the signed material, so nothing enforceable can be computed from a clock --
    a window derived from an unsigned header is a window the sender chooses. Replay
    protection is event identity and the deduplicator.
    """
    from software_factory.integrations import verify_git_host_signature

    try:
        verify_git_host_signature(
            webhook_secret=os.environ.get(GIT_HOST_SECRET_ENV, ""),
            body=body.read_bytes(),
            signature=signature,
        )
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if as_json:
        _emit({"ok": True, "verified": True})
    else:
        console.print("[green]verified[/] signed by this repository's webhook secret")
    raise typer.Exit(EXIT_OK)


schedule_app = typer.Typer(
    help="Scheduled triggers: what is declared, what is due, and firing them.",
    no_args_is_help=True,
)
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("list")
def schedule_list(root: RootArg = Path(), as_json: JsonOpt = False) -> None:
    """Every schedule this factory declares, and when each next fires.

    Declared schedules were validated and read by nothing: a factory could describe a
    nightly sweep, pass `sf validate` and `sf lint` clean, and never run it once. This is
    the first half of the answer -- what the definition actually asks for.
    """
    from software_factory.orchestrator.schedule import Schedule, describe

    try:
        definition = load_strict(root)
        schedule = Schedule.from_definition(definition)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    now = datetime.now(UTC)
    rows = [
        {
            "trigger": trigger.id,
            "automation": trigger.automation,
            "event": trigger.event,
            "cron": trigger.cron.source,
            "reading": describe(trigger.cron),
            "nextFire": None if at is None else at.isoformat(),
        }
        for trigger, at in schedule.upcoming(now=now)
    ]

    if as_json:
        _emit({"ok": True, "schedules": rows})
        raise typer.Exit(EXIT_OK)

    if not rows:
        console.print("[dim]no schedules declared[/] — no automation has a `schedule` trigger")
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("trigger", "cron", "reading", "next fire (UTC)"):
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(
            str(row["trigger"]),
            str(row["cron"]),
            str(row["reading"]),
            str(row["nextFire"] or "[dim]none within the horizon[/]"),
        )
    console.print(table)
    raise typer.Exit(EXIT_OK)


@schedule_app.command("due")
def schedule_due(
    root: RootArg = Path(),
    ledger_path: Annotated[
        Path, typer.Option("--ledger", help="Where firings are recorded.")
    ] = Path(".factory/ledger.jsonl"),
    as_json: JsonOpt = False,
) -> None:
    """What would fire right now, without firing it.

    A missed window fires **once**, not once per occurrence: a factory that was off for
    three days with an hourly sweep does not have seventy-two pieces of work waiting, it
    has one and seventy-one duplicates. The skipped count is reported rather than
    discarded, because "we were down and it did not run" is a fact an operator needs.
    """
    due = _due_schedules(root, ledger_path, as_json)

    if as_json:
        _emit({"ok": True, "due": [d.as_payload() for d in due]})
        raise typer.Exit(EXIT_OK)

    if not due:
        console.print("[dim]nothing due[/]")
        raise typer.Exit(EXIT_OK)
    for item in due:
        console.print(f"[green]due[/] {item.trigger.id}  [dim]for[/] {item.occurrence.isoformat()}")
        if item.skipped:
            console.print(
                f"  [yellow]{item.skipped} occurrence(s) skipped[/] since "
                f"{item.last_fired.isoformat() if item.last_fired else 'never'} — "
                "firing once, not once per occurrence"
            )
    raise typer.Exit(EXIT_OK)


@schedule_app.command("run")
def schedule_run(
    root: RootArg = Path(),
    ledger_path: Annotated[
        Path, typer.Option("--ledger", help="Where firings are recorded.")
    ] = Path(".factory/ledger.jsonl"),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report what would fire without recording it.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Fire everything due, through the same intake path as any other event.

    Meant to be called by whatever already runs on a timer on the host -- cron, a systemd
    timer, a CI schedule. This does not hold a process open: a scheduler that must stay
    running to be correct is a scheduler whose correctness depends on nobody restarting it,
    and every firing here is derived from the ledger instead.
    """
    from software_factory.intake import FactoryEvent, Ignored, Origin, Provider, Started
    from software_factory.intake import Refused as IntakeRefused
    from software_factory.intake.events import event_identity
    from software_factory.intake.loading import pipeline_from
    from software_factory.ledger import EntryType, Ledger

    due = _due_schedules(root, ledger_path, as_json)
    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    pipeline = pipeline_from(definition)
    ledger = Ledger(ledger_path)
    fired: list[dict[str, Any]] = []

    for item in due:
        event = FactoryEvent(
            # The *occurrence*, not the wall clock: two evaluations seconds apart must not
            # both produce a distinct event for the same scheduled instant.
            id=event_identity(
                Provider.SCHEDULE, item.trigger.id, item.trigger.event, item.occurrence.isoformat()
            ),
            provider=Provider.SCHEDULE,
            event=item.trigger.event,
            origin=Origin(
                provider=Provider.SCHEDULE, ref=item.trigger.id, source=item.trigger.automation
            ),
            title=f"scheduled: {item.trigger.id}",
            author="",
            attributes={
                "automation": item.trigger.automation,
                "cron": item.trigger.cron.source,
                "occurrence": item.occurrence.isoformat(),
                "skipped": item.skipped,
            },
        )
        outcomes = [] if dry_run else pipeline.receive(event)
        if not dry_run:
            # Recorded after the pipeline has seen it, so a crash mid-intake leaves the
            # trigger still due rather than silently consumed.
            ledger.append(
                EntryType.SCHEDULE_FIRED,
                actor="scheduler",
                subject=item.trigger.id,
                payload=item.as_payload(),
            )
        fired.append(
            {
                **item.as_payload(),
                "outcomes": [
                    {
                        "kind": type(o).__name__.lower(),
                        "automation": getattr(o, "automation", None),
                        "agent": getattr(o, "agent", None),
                        "reason": getattr(o, "reason", None),
                        "message": getattr(o, "message", None),
                    }
                    for o in outcomes
                ],
            }
        )

    if as_json:
        _emit({"ok": True, "dryRun": dry_run, "fired": fired})
        raise typer.Exit(EXIT_OK)

    if not fired:
        console.print("[dim]nothing due[/]")
        raise typer.Exit(EXIT_OK)
    for record in fired:
        mark = "[dim]would fire[/]" if dry_run else "[green]fired[/]"
        console.print(f"{mark} {record['trigger']}  [dim]for[/] {record['occurrence']}")
        if record["skipped"]:
            console.print(f"  [yellow]{record['skipped']} occurrence(s) skipped[/]")
        for outcome in record["outcomes"]:
            if outcome["kind"] == "started":
                console.print(f"  [green]starts[/] {outcome['automation']} → {outcome['agent']}")
            elif outcome["kind"] == "ignored":
                console.print(f"  [dim]ignored[/] — {outcome['reason']}")
            else:
                console.print(f"  [yellow]refused[/] {outcome['message']}")
    del Started, Ignored, IntakeRefused
    raise typer.Exit(EXIT_OK)


def _due_schedules(root: Path, ledger_path: Path, as_json: bool) -> list[Any]:
    from software_factory.ledger import Ledger
    from software_factory.orchestrator.schedule import Schedule

    try:
        definition = load_strict(root)
        schedule = Schedule.from_definition(definition)
    except FactoryError as exc:
        _fail(exc, as_json)
        raise
    entries = list(Ledger(ledger_path).read()) if ledger_path.exists() else []
    return schedule.with_history(entries).due(now=datetime.now(UTC))


workspace_app = typer.Typer(
    help="More than one factory in one tree: list, validate, and compare them.",
    no_args_is_help=True,
)
app.add_typer(workspace_app, name="workspace")

WorkspaceArg = Annotated[
    Path,
    typer.Argument(help="Path to the directory containing workspace.yaml."),
]


@workspace_app.command("init")
def workspace_init(
    root: WorkspaceArg = Path(),
    name: Annotated[str, typer.Option("--name", help="Workspace name.")] = "workspace",
    factory: Annotated[
        list[str] | None,
        typer.Option("--factory", help="A factory root, relative to the workspace. Repeatable."),
    ] = None,
    as_json: JsonOpt = False,
) -> None:
    """Write a workspace file listing factory roots (FR-1.5)."""
    from software_factory.definition.workspace import WORKSPACE_FILE, scaffold_workspace

    members = factory or []
    if not members:
        console.print(
            "[red]a workspace needs at least one factory[/]  pass --factory <path>, repeatable"
        )
        raise typer.Exit(EXIT_UNUSABLE)
    if (root / WORKSPACE_FILE).exists():
        console.print(f"[red]{root / WORKSPACE_FILE} already exists[/]")
        raise typer.Exit(EXIT_UNUSABLE)

    path = scaffold_workspace(root, name=name, members=members)
    if as_json:
        _emit({"ok": True, "wrote": str(path), "factories": members})
    else:
        console.print(f"[green]wrote[/] {path}")
    raise typer.Exit(EXIT_OK)


@workspace_app.command("list")
def workspace_list(root: WorkspaceArg = Path(), as_json: JsonOpt = False) -> None:
    """Every factory in the workspace, side by side.

    A member that failed to load is listed with its reason rather than dropped: a workspace
    listing four factories and reporting three hides the broken one, and the broken one is
    the reason to look.
    """
    from software_factory.definition.workspace import load_workspace, summarise

    try:
        workspace = load_workspace(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    rows = [s.as_dict() for s in summarise(workspace)]
    if as_json:
        _emit({"ok": True, "workspace": workspace.document.name, "factories": rows})
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("factory", "repositories", "agents", "automations", "runs", "handoffs"):
        table.add_column(column, overflow="fold")
    for row in rows:
        if not row["loaded"]:
            table.add_row(f"[red]{row['name']}[/]", f"[dim]{row['error']}[/]", "—", "—", "—", "—")
            continue
        table.add_row(
            str(row["name"]),
            ", ".join(row["repositories"]),
            str(row["agents"]),
            str(row["automations"]),
            # A factory with no ledger has not been run *or* has not been found. Those are
            # different facts, and on a comparison table the second must not read as a
            # team doing nothing.
            "—" if row["runs"] is None else str(row["runs"]),
            "—" if row["handoffs"] is None else str(row["handoffs"]),
        )
    console.print(table)
    if any(not row["ledgerPresent"] for row in rows if row["loaded"]):
        console.print(
            "[dim]— means no ledger was found at the declared state directory, which is "
            "not the same as a factory that has done nothing[/]"
        )
    raise typer.Exit(EXIT_OK)


@workspace_app.command("validate")
def workspace_validate(root: WorkspaceArg = Path(), as_json: JsonOpt = False) -> None:
    """Validate every factory, plus the rules that only exist across factories.

    FR-1.4 requires a warning when two factories in the same tree overlap on a repository.
    That is a P0 requirement no single-factory lint can perform, so it was unimplementable
    rather than unimplemented -- and nobody was going to notice by reading a command that
    only ever sees one factory.
    """
    from software_factory.definition.workspace import load_workspace

    try:
        workspace = load_workspace(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    issues = [issue.as_dict() for issue in workspace.report.issues]
    overlaps = {r: list(n) for r, n in workspace.overlaps().items()}
    ok = workspace.report.ok

    if as_json:
        _emit(
            {
                "ok": ok,
                "workspace": workspace.document.name,
                "factories": len(workspace.factories),
                "loaded": len(workspace.loaded),
                "overlaps": overlaps,
                "issues": issues,
            }
        )
        raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)

    console.print(
        f"[bold]{workspace.document.name}[/] — {len(workspace.loaded)} of "
        f"{len(workspace.factories)} factories loaded"
    )
    for issue in issues:
        colour = "red" if issue["severity"] == "error" else "yellow"
        console.print(f"[{colour}]{issue['severity']}[/] {issue['code']}: {issue['message']}")
    if not issues:
        console.print("[green]clean[/] — no cross-factory problems")
    raise typer.Exit(EXIT_OK if ok else EXIT_FAILED)


@workspace_app.command("metrics")
def workspace_metrics(
    root: WorkspaceArg = Path(),
    days: Annotated[int, typer.Option("--days", help="Window, in days.")] = 7,
    as_json: JsonOpt = False,
) -> None:
    """Fold every factory's ledger over one window, side by side.

    The honest form of a cross-team analytics tier: the same folds this project already
    applies to one ledger, applied to several, with the same refusal to render an absence
    as a zero.
    """
    from datetime import timedelta

    from software_factory.definition.workspace import load_workspace
    from software_factory.ledger import Ledger
    from software_factory.observability.metrics import Window, compute

    try:
        workspace = load_workspace(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    window = Window.last(timedelta(days=days))
    rows: list[dict[str, Any]] = []
    for factory in workspace.loaded:
        if not factory.ledger_path.is_file():
            rows.append({"factory": factory.name, "available": False, "reason": "no ledger found"})
            continue
        report = compute(list(Ledger(factory.ledger_path).read()), window=window)
        rows.append(
            {
                "factory": factory.name,
                "available": True,
                "runs": report.runs.total,
                "measures": [m.as_dict() for m in report.measures],
            }
        )

    if as_json:
        _emit({"ok": True, "workspace": workspace.document.name, "days": days, "factories": rows})
        raise typer.Exit(EXIT_OK)

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("factory", overflow="fold")
    table.add_column("runs")
    names = sorted({m["name"] for row in rows if row.get("available") for m in row["measures"]})
    for name in names:
        table.add_column(name, overflow="fold")
    for row in rows:
        if not row.get("available"):
            table.add_row(
                f"[dim]{row['factory']}[/]", f"[dim]{row['reason']}[/]", *["—"] * len(names)
            )
            continue
        by_name = {m["name"]: m for m in row["measures"]}
        cells = []
        for name in names:
            measure = by_name.get(name)
            if measure is None or measure["value"] is None:
                cells.append("[dim]—[/]")
            else:
                cells.append(str(measure["value"]))
        table.add_row(str(row["factory"]), str(row["runs"]), *cells)
    console.print(table)
    console.print("[dim]— means unavailable or insufficient data, never zero[/]")
    raise typer.Exit(EXIT_OK)


stop_app = typer.Typer(
    help="Stop work that is already running, and withdraw a stop.", no_args_is_help=True
)
app.add_typer(stop_app, name="stop")


@stop_app.command("now")
def stop_now(
    work_item: Annotated[str, typer.Argument(help="The work item to stop, or `*` for everything.")],
    by: Annotated[str, typer.Option("--by", help="Who is stopping it. Required.")],
    reason: Annotated[str, typer.Option("--reason", help="Why. Required.")],
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Stop a running work item, or the whole fleet.

    Takes effect **between turns**, not at the next stage boundary. A stage is the unit a
    schedule thinks in; a turn is the unit spend happens in, and a stop that lands ten
    minutes and a hundred thousand tokens later is indistinguishable from one that does not
    work.

    `--by` and `--reason` are required rather than optional. `EMERGENCY_STOP` is a
    person-only capability, and a stop that leaves no record is an unexplained gap in a
    run's history -- exactly the gap somebody will later be trying to explain.
    """
    from software_factory.ledger import EntryType, Ledger
    from software_factory.orchestrator.stopping import ALL, StopBook

    if not by.strip() or not reason.strip():
        console.print("[red]--by and --reason are both required[/]")
        raise typer.Exit(EXIT_UNUSABLE)

    stop = StopBook.in_state(state).request(work_item, by=by.strip(), reason=reason.strip())
    ledger_path = state / "ledger.jsonl"
    if ledger_path.exists():
        Ledger(ledger_path).append(
            EntryType.HUMAN_DECISION,
            actor=by.strip(),
            subject=work_item,
            payload={"decision": "emergency_stop", **stop.as_dict()},
        )

    if as_json:
        _emit({"ok": True, "stop": stop.as_dict()})
        raise typer.Exit(EXIT_OK)

    scope = "the whole fleet" if work_item == ALL else work_item
    console.print(f"[yellow]stopping[/] {scope} — {stop.reason}")
    console.print(
        "[dim]Takes effect between turns. A run already inside a model call finishes that "
        "call and then stops.[/]"
    )
    raise typer.Exit(EXIT_OK)


@stop_app.command("list")
def stop_list(
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Every outstanding stop."""
    from software_factory.orchestrator.stopping import StopBook

    stops = StopBook.in_state(state).all()
    if as_json:
        _emit({"ok": True, "stops": [s.as_dict() for s in stops]})
        raise typer.Exit(EXIT_OK)
    if not stops:
        console.print("[dim]nothing is stopped[/]")
        raise typer.Exit(EXIT_OK)
    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("subject", "by", "at", "reason"):
        table.add_column(column, overflow="fold")
    for stop in stops:
        table.add_row(stop.subject, stop.by, stop.at.isoformat(), stop.reason)
    console.print(table)
    raise typer.Exit(EXIT_OK)


@stop_app.command("clear")
def stop_clear(
    work_item: Annotated[
        str | None, typer.Argument(help="Which stop to withdraw. Omit for all of them.")
    ] = None,
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Withdraw a stop so the work can be resumed.

    Separate from issuing one, and never implicit. A stop that expired on its own would be
    a stop an operator has to keep re-issuing to be sure of, which is the opposite of what
    a stop is for.
    """
    from software_factory.orchestrator.stopping import StopBook

    withdrawn = StopBook.in_state(state).clear(work_item)
    if as_json:
        _emit({"ok": True, "withdrawn": withdrawn})
    else:
        console.print(f"[green]withdrew[/] {withdrawn} stop(s)")
    raise typer.Exit(EXIT_OK)


skill_app = typer.Typer(
    help="Skills: what this factory declares, and running one directly.", no_args_is_help=True
)
app.add_typer(skill_app, name="skill")


@skill_app.command("list")
def skill_list(root: RootArg = Path(), as_json: JsonOpt = False) -> None:
    """Every skill, its status, and the arguments it accepts when invoked."""
    try:
        definition = load_strict(root)
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    rows = []
    for owner, skill in _all_skills(definition):
        spec = skill.definition
        rows.append(
            {
                "name": skill.name,
                "owner": owner,
                "status": spec.status.value,
                "description": spec.description,
                "invocable": bool(spec.arguments),
                "arguments": {
                    name: {
                        "description": arg.description,
                        "required": arg.required,
                        "default": arg.default,
                    }
                    for name, arg in sorted(spec.arguments.items())
                },
            }
        )

    if as_json:
        _emit({"ok": True, "skills": rows})
        raise typer.Exit(EXIT_OK)

    if not rows:
        console.print("[dim]this factory declares no skills[/]")
        raise typer.Exit(EXIT_OK)
    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("skill", "owner", "status", "arguments"):
        table.add_column(column, overflow="fold")
    for row in rows:
        args = ", ".join(f"{n}{'' if a['required'] else '?'}" for n, a in row["arguments"].items())
        table.add_row(
            str(row["name"]),
            str(row["owner"]),
            str(row["status"]),
            args or "[dim]not invocable[/]",
        )
    console.print(table)
    console.print(
        "[dim]? marks an optional argument. A skill with none is selected by the "
        "registry for a run rather than invoked.[/]"
    )
    raise typer.Exit(EXIT_OK)


@skill_app.command("render")
def skill_render(
    name: Annotated[str, typer.Argument(help="Which skill.")],
    root: RootArg = Path(),
    arg: Annotated[list[str] | None, typer.Option("--arg", help="name=value, repeatable.")] = None,
    as_json: JsonOpt = False,
) -> None:
    """Show what a skill's body becomes with these arguments, without running anything.

    The cheap half of invocation, and the one worth having on its own: a skill whose
    rendered prompt nobody can see before paying for a run is a prompt debugged by
    inference.
    """
    from software_factory.skills.registry import render

    try:
        definition = load_strict(root)
        skill = _find_skill(definition, name)
        body = render(skill.body, dict(skill.definition.arguments), _arg_map(arg))
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    if as_json:
        _emit({"ok": True, "skill": name, "body": body})
    else:
        console.print(body)
    raise typer.Exit(EXIT_OK)


@skill_app.command("run")
def skill_run(
    name: Annotated[str, typer.Argument(help="Which skill to run.")],
    root: RootArg = Path(),
    repo: Annotated[Path, typer.Option("--repo", help="The repository to work in.")] = Path(),
    arg: Annotated[list[str] | None, typer.Option("--arg", help="name=value, repeatable.")] = None,
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    allow_unsandboxed: Annotated[
        bool, typer.Option(help="Run without OS sandboxing. Only when none is available.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Run a skill directly, as its own work item.

    Skills were only ever *selected* by the registry for a run in progress. Being able to
    invoke one -- "run the triage skill over this backlog" -- is a different capability, and
    the lifecycle machinery already carries everything it needs: a skill declares its scope,
    its owners and its evals.

    It goes through the ordinary stage machine rather than a shortcut. A skill run that
    skipped the gates would be a way to get unreviewed work through the factory by naming
    it differently, which is the shape of hole this project spends most of its effort not
    having.
    """
    from software_factory.orchestrator import SourceContext, WorkClass, WorkItem, new_id
    from software_factory.orchestrator.coordinator import local_coordinator
    from software_factory.skills.registry import render

    try:
        definition = load_strict(root)
        skill = _find_skill(definition, name)
        body = render(skill.body, dict(skill.definition.arguments), _arg_map(arg))
    except FactoryError as exc:
        _fail(exc, as_json)
        return

    item = WorkItem(
        id=new_id(),
        factory=definition.factory.name,
        title=f"skill: {skill.name}",
        request=body,
        source=SourceContext(provider="cli", kind="skill", ref=skill.name),
        work_class=WorkClass.CHORE,
    )

    provider = _resolve_provider()
    if provider is None:
        _fail(
            FactoryError(
                "no model provider is configured, so this run would produce nothing verifiable",
                remediation=(
                    "Set SF_PROVIDER_ENDPOINT to a model endpoint, or use `sf skill render` "
                    "to see what this skill would ask for without running it."
                ),
            ),
            as_json,
        )
        return

    coordinator = local_coordinator(
        definition,
        repo=repo,
        state_dir=state,
        provider=provider,
        allow_unsandboxed=allow_unsandboxed,
    )
    coordinator.run(item)

    result = {
        "ok": item.blocker is None,
        "workItem": item.id,
        "skill": skill.name,
        "stage": item.stage.value,
        "blocker": item.blocker.value if item.blocker else None,
        "action": item.blocker_action,
    }
    if as_json:
        _emit(result)
    else:
        console.print(f"[bold]{skill.name}[/] → {item.stage.value}  [dim]{item.id}[/]")
        if item.blocker:
            console.print(f"[yellow]blocked[/] {item.blocker.value} — {item.blocker_action}")
    raise typer.Exit(EXIT_OK if item.blocker is None else EXIT_FAILED)


def _all_skills(definition: Any) -> list[tuple[str, Any]]:
    """Every skill in the tree, with who owns it. Agent skills included.

    A listing that showed only factory-level skills would hide the ones an agent carries,
    which are the ones most likely to be invocable.
    """
    found: list[tuple[str, Any]] = []
    for agent_name, agent in sorted(definition.agents.items()):
        for skill in agent.skills:
            found.append((agent_name, skill))
    for skill in definition.skills.values():
        found.append(("factory", skill))
    return sorted(found, key=lambda pair: (pair[1].name, pair[0]))


def _find_skill(definition: Any, name: str) -> Any:
    matches = [skill for _owner, skill in _all_skills(definition) if skill.name == name]
    if not matches:
        known = ", ".join(sorted({s.name for _o, s in _all_skills(definition)})) or "none"
        raise FactoryError(
            f"no skill named {name!r} in this factory",
            remediation=f"Declared skills: {known}.",
        )
    return matches[0]


def _arg_map(pairs: list[str] | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not key or not sep:
            raise FactoryError(
                f"--arg expects name=value, got {pair!r}",
                remediation="Write --arg path=src/importers/csv.py",
            )
        values[key.strip()] = value
    return values


api_app = typer.Typer(help="The local HTTP API, and the keys that reach it.", no_args_is_help=True)
app.add_typer(api_app, name="api")

key_app = typer.Typer(help="API keys.", no_args_is_help=True)
api_app.add_typer(key_app, name="key")


@key_app.command("create")
def api_key_create(
    principal: Annotated[str, typer.Option("--principal", help="Who this key acts as.")],
    label: Annotated[str, typer.Option("--label", help="What it is for.")] = "",
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Issue an API key. It is printed once and never recoverable.

    Stored as a hash. A key file an operator can read their key back out of is a second
    copy of the credential, and the usual outcome is that it gets copied somewhere with
    weaker permissions.
    """
    from software_factory.observability.api import KeyStore

    issued, secret = KeyStore.in_state(state).create(principal=principal, label=label)

    if as_json:
        _emit({"ok": True, "keyId": issued.key_id, "principal": principal, "key": secret})
        raise typer.Exit(EXIT_OK)
    console.print(f"[green]issued[/] {issued.key_id} for [bold]{principal}[/]")
    console.print(f"\n  {secret}\n")
    console.print("[yellow]This is the only time this key is shown.[/] It is stored hashed.")
    raise typer.Exit(EXIT_OK)


@key_app.command("list")
def api_key_list(
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Issued keys. Never the keys themselves."""
    from software_factory.observability.api import KeyStore

    keys = KeyStore.in_state(state).all()
    if as_json:
        _emit(
            {
                "ok": True,
                "keys": [
                    {
                        "keyId": k.key_id,
                        "principal": k.principal,
                        "label": k.label,
                        "createdAt": k.created_at.isoformat(),
                    }
                    for k in keys
                ],
            }
        )
        raise typer.Exit(EXIT_OK)
    if not keys:
        console.print("[dim]no keys issued[/]")
        raise typer.Exit(EXIT_OK)
    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("key id", "principal", "label", "created"):
        table.add_column(column, overflow="fold")
    for key in keys:
        table.add_row(
            key.key_id, key.principal, key.label or "[dim]—[/]", key.created_at.isoformat()
        )
    console.print(table)
    raise typer.Exit(EXIT_OK)


@key_app.command("revoke")
def api_key_revoke(
    key_id: Annotated[str, typer.Argument(help="Which key.")],
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Revoke a key immediately."""
    from software_factory.observability.api import KeyStore

    revoked = KeyStore.in_state(state).revoke(key_id)
    if as_json:
        _emit({"ok": revoked, "keyId": key_id})
    elif revoked:
        console.print(f"[green]revoked[/] {key_id}")
    else:
        console.print(f"[yellow]no key {key_id}[/]")
    raise typer.Exit(EXIT_OK if revoked else EXIT_FAILED)


@api_app.command("serve")
def api_serve(
    root: RootArg = Path(),
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8788,
    integration: Annotated[
        list[str] | None, typer.Option("--integration", help="An integration this factory has.")
    ] = None,
) -> None:
    """Serve the API, so something other than a person at a terminal can drive this.

    Authenticated on **every** request, including the reads. A ledger is a factory's whole
    history, and "read-only" is not the same as "public". Binding anywhere but loopback
    with no keys issued is refused rather than being a documented footgun.
    """
    from software_factory.identity.loading import directory_from
    from software_factory.observability.api import serve as serve_api

    ledger_path = state / "ledger.jsonl"
    if not ledger_path.exists():
        console.print(f"[red]no ledger at {ledger_path}[/]")
        raise typer.Exit(EXIT_UNUSABLE)

    directory = None
    try:
        definition = load_strict(root)
        directory = directory_from(definition)
    except FactoryError as exc:
        # A capability check that passes because no directory loaded is worse than none: it
        # looks like enforcement and is not. So this is stated, and privileged routes will
        # refuse.
        console.print(f"[yellow]no principal directory[/] ({exc.message})")
        console.print("[dim]privileged routes will refuse every caller until this loads[/]")

    try:
        server = serve_api(
            ledger_path,
            host=host,
            port=port,
            root=root,
            directory=directory,
            integrations=frozenset(integration or []),
            ready=lambda url: console.print(f"api on {url}  [dim](ctrl-c to stop)[/]"),
        )
    except FactoryError as exc:
        _fail(exc, False)
        return

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\nstopped")
    finally:
        server.server_close()
    raise typer.Exit(EXIT_OK)


def main() -> None:
    """Console-script entry point."""
    app()


agent_app = typer.Typer(
    help="Messages between agents, and what each run is doing.", no_args_is_help=True
)
app.add_typer(agent_app, name="agent")


def _mailbox(state: Path):  # type: ignore[no-untyped-def]
    """The factory's mailbox, or a usable error saying which factory has none.

    Refuses to create the ledger. A mailbox conjured on an empty directory answers every
    question with "no messages", which reads identically to a healthy fleet and is the one
    answer an operator must never be given by mistake.
    """
    from software_factory.ledger import Ledger
    from software_factory.orchestrator.mailbox import Mailbox

    path = state / "ledger.jsonl"
    if not path.exists():
        console.print(f"[red]no ledger at {path}[/]")
        console.print("[dim]Run something first, or point --state at the factory that did.[/]")
        raise typer.Exit(EXIT_UNUSABLE)
    return Mailbox(ledger=Ledger(path), state_dir=state)


@agent_app.command("send")
def agent_send(
    recipient: Annotated[str, typer.Argument(help="The agent to address.")],
    body: Annotated[str, typer.Argument(help="What to tell them.")],
    kind: Annotated[
        str, typer.Option("--kind", help="status, question, answer, result, blocked or handoff.")
    ] = "status",
    sender: Annotated[
        str, typer.Option("--from", help="Who is sending. Defaults to `operator`.")
    ] = "operator",
    in_reply_to: Annotated[
        int, typer.Option("--reply-to", help="The sequence number this answers.")
    ] = 0,
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Address one agent. The message reaches it at the start of its next run.

    Not a broadcast. An operator who can address the fleet at once can interrupt every
    agent with one keystroke, and the message that is worth everyone's attention is rare
    enough to be worth sending twice.
    """
    from software_factory.errors import FactoryError

    mailbox = _mailbox(state)
    try:
        message = mailbox.send(
            sender=sender, recipient=recipient, kind=kind, body=body, in_reply_to=in_reply_to
        )
    except FactoryError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(EXIT_UNUSABLE) from exc

    if as_json:
        _emit({"ok": True, "message": message.as_dict()})
        raise typer.Exit(EXIT_OK)
    console.print(f"[green]sent[/] #{message.seq} to {recipient} ({message.kind.value})")
    if message.truncated:
        console.print("[yellow]the body was truncated[/]")
    raise typer.Exit(EXIT_OK)


@agent_app.command("inbox")
def agent_inbox(
    agent: Annotated[str, typer.Argument(help="Whose inbox to read.")],
    unread_only: Annotated[
        bool, typer.Option("--unread", help="Only what the agent has not been shown.")
    ] = False,
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """What one agent has been sent.

    Reading does not mark anything read. The cursor belongs to the agent's runs, and an
    operator looking at an inbox to find out why a fleet is stuck must not be the reason
    the agent never sees the question.
    """
    mailbox = _mailbox(state)
    messages, left = mailbox.unread(agent) if unread_only else mailbox.inbox(agent)
    owed = {m.seq for m in mailbox.unanswered(agent)}

    if as_json:
        _emit(
            {
                "ok": True,
                "agent": agent,
                "messages": [m.as_dict() for m in messages],
                "unanswered": sorted(owed),
                "leftBehind": left,
            }
        )
        raise typer.Exit(EXIT_OK)

    if not messages:
        console.print(f"[dim]nothing for {agent}[/]")
        raise typer.Exit(EXIT_OK)
    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("seq", "from", "kind", "body", ""):
        table.add_column(column, overflow="fold")
    for message in messages:
        table.add_row(
            str(message.seq),
            message.sender,
            message.kind.value,
            message.body,
            "[yellow]unanswered[/]" if message.seq in owed else "",
        )
    console.print(table)
    if left:
        console.print(f"[dim]{left} older messages not shown[/]")
    raise typer.Exit(EXIT_OK)


@agent_app.command("thread")
def agent_thread(
    seq: Annotated[int, typer.Argument(help="The message to open.")],
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """A message and its replies."""
    messages = _mailbox(state).thread(seq)
    if as_json:
        _emit({"ok": True, "thread": [m.as_dict() for m in messages]})
        raise typer.Exit(EXIT_OK)
    if not messages:
        console.print(f"[dim]no message at #{seq}[/]")
        raise typer.Exit(EXIT_OK)
    for message in messages:
        console.print(message.render())
    raise typer.Exit(EXIT_OK)


@agent_app.command("lifecycle")
def agent_lifecycle(
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    running_only: Annotated[
        bool, typer.Option("--running", help="Only runs that have not ended.")
    ] = False,
    as_json: JsonOpt = False,
) -> None:
    """Every run's observable state, and which questions are still unanswered.

    The two together are the fleet view: a run can be `running` and healthy, or `running`
    and waiting on a question nobody has read, and only the second is a stall. A fleet
    view that shows the first and not the second reports a busy factory that is doing
    nothing.
    """
    from software_factory.ledger import Ledger
    from software_factory.orchestrator.mailbox import lifecycle

    path = state / "ledger.jsonl"
    if not path.exists():
        console.print(f"[red]no ledger at {path}[/]")
        raise typer.Exit(EXIT_UNUSABLE)
    ledger = Ledger(path)
    lives = lifecycle(ledger.read())
    if running_only:
        lives = [life for life in lives if life.state == "running"]

    mailbox = _mailbox(state)
    stalled = {
        life.agent: len(mailbox.unanswered(life.agent))
        for life in lives
        if mailbox.unanswered(life.agent)
    }

    if as_json:
        _emit(
            {
                "ok": True,
                "runs": [life.as_dict() for life in lives],
                "unanswered": stalled,
            }
        )
        raise typer.Exit(EXIT_OK)

    if not lives:
        console.print("[dim]no runs yet[/]")
        raise typer.Exit(EXIT_OK)
    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("run", "agent", "stage", "state", "waiting on", "reason"):
        table.add_column(column, overflow="fold")
    colours = {
        "running": "cyan",
        "succeeded": "green",
        "blocked": "yellow",
        "failed": "red",
        "cancelled": "dim",
    }
    for life in lives:
        colour = colours.get(life.state, "white")
        owed = stalled.get(life.agent, 0)
        table.add_row(
            life.run[:12],
            life.agent,
            life.stage,
            f"[{colour}]{life.state}[/]",
            f"[yellow]{owed} question(s)[/]" if owed else "",
            life.reason,
        )
    console.print(table)
    raise typer.Exit(EXIT_OK)


worker_app = typer.Typer(
    help="The machines this factory dispatches to, and who holds each one.",
    no_args_is_help=True,
)
app.add_typer(worker_app, name="worker")


def _pool(root: Path, state: Path):  # type: ignore[no-untyped-def]
    from software_factory.definition import load_strict
    from software_factory.orchestrator.workers import WorkerPool

    definition = load_strict(root)
    return WorkerPool.from_dicts(
        [worker.model_dump(by_alias=False) for worker in definition.factory.workers],
        state_dir=state,
    )


@worker_app.command("list")
def worker_list(
    root: Annotated[Path, typer.Option("--root", help="The factory directory.")] = Path(),
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Every worker, what it can do, and how much of it is in use.

    `free` can be negative, and is reported rather than clamped. It means capacity was
    lowered while leases were held, and clamping would turn an over-committed machine into
    a merely full one and hide the edit that caused it.
    """
    summary = _pool(root, state).summarise()
    if as_json:
        _emit({"ok": True, **summary})
        raise typer.Exit(EXIT_OK)

    workers = summary["workers"]
    if not workers:
        console.print("[dim]no workers are configured[/]")
        console.print(
            "[dim]Work with no `requires` still runs locally; work that asks for a label "
            "will be refused by name.[/]"
        )
        raise typer.Exit(EXIT_OK)
    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("worker", "host", "labels", "in use", "free", ""):
        table.add_column(column, overflow="fold")
    for worker in workers:
        free = worker["free"]
        table.add_row(
            worker["name"],
            worker["host"],
            ", ".join(worker["labels"]) or "[dim]none[/]",
            str(worker["inUse"]),
            f"[red]{free}[/]" if free < 0 else str(free),
            "[yellow]draining[/]" if worker["draining"] else "",
        )
    console.print(table)
    raise typer.Exit(EXIT_OK)


@worker_app.command("route")
def worker_route(
    requires: Annotated[
        list[str] | None, typer.Option("--requires", help="A label the work needs. Repeatable.")
    ] = None,
    root: Annotated[Path, typer.Option("--root", help="The factory directory.")] = Path(),
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Ask where work with these labels *would* run, without claiming anything.

    A dry run, because the alternative is finding out by submitting work: the answer to
    "will this route" should not itself consume a slot, and an operator checking a label
    before a big batch must not have to remember to release it.
    """
    from software_factory.orchestrator.workers import Availability

    pool = _pool(root, state)
    needed = frozenset(requires or ())
    matching = pool.candidates(needed)
    placement = pool.place("__dry-run__", requires=needed)
    if placement.placed:
        pool.release("__dry-run__")

    if as_json:
        _emit(
            {
                "ok": placement.availability is Availability.AVAILABLE,
                "requires": sorted(needed),
                "candidates": [worker.name for worker in matching],
                **placement.as_dict(),
            }
        )
        raise typer.Exit(EXIT_OK if placement.placed else EXIT_UNUSABLE)

    if placement.placed and placement.lease:
        console.print(f"[green]would run on[/] {placement.lease.worker} ({placement.lease.host})")
        others = [w.name for w in matching if w.name != placement.lease.worker]
        if others:
            console.print(f"[dim]also eligible: {', '.join(sorted(others))}[/]")
        raise typer.Exit(EXIT_OK)

    colour = "yellow" if placement.availability is Availability.SATURATED else "red"
    console.print(f"[{colour}]{placement.availability.value}[/] — {placement.reason}")
    raise typer.Exit(EXIT_UNUSABLE)


@worker_app.command("leases")
def worker_leases(
    root: Annotated[Path, typer.Option("--root", help="The factory directory.")] = Path(),
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Who holds each worker, and which leases the pool took back.

    Reclaimed leases are shown because an expiry is a guess: the run may still be alive and
    merely slow. Without the list, a run that was reclaimed and one that finished cleanly
    leave identical traces, and "did this execute twice" has no answer.
    """
    pool = _pool(root, state)
    held = pool.leases()
    reclaimed = pool.reclaimed()

    if as_json:
        _emit(
            {
                "ok": True,
                "leases": [lease.as_dict() for lease in held],
                "reclaimed": [lease.as_dict() for lease in reclaimed],
            }
        )
        raise typer.Exit(EXIT_OK)

    if not held:
        console.print("[dim]nothing is leased[/]")
    else:
        table = Table(show_header=True, header_style="bold", box=None)
        for column in ("run", "worker", "held since", "expires"):
            table.add_column(column, overflow="fold")
        for lease in held:
            table.add_row(
                lease.run, lease.worker, lease.at.isoformat(), lease.expires_at.isoformat()
            )
        console.print(table)

    if reclaimed:
        console.print(
            f"\n[yellow]{len(reclaimed)} lease(s) were reclaimed after expiring[/] "
            "[dim]— those runs may still have been alive[/]"
        )
        for lease in reclaimed[-5:]:
            console.print(f"  [dim]{lease.run} on {lease.worker}, held from {lease.at:%H:%M}[/]")
    raise typer.Exit(EXIT_OK)


@worker_app.command("release")
def worker_release(
    run: Annotated[str, typer.Argument(help="The run whose lease to give back.")],
    root: Annotated[Path, typer.Option("--root", help="The factory directory.")] = Path(),
    state: Annotated[
        Path, typer.Option("--state", help="Where run state and the ledger live.")
    ] = Path(".factory"),
    as_json: JsonOpt = False,
) -> None:
    """Hand a worker back by hand, for a run that died without releasing it.

    Reports whether a lease was actually held. Releasing a run that held nothing means the
    operator has the wrong id, and answering "done" to that sends them away satisfied while
    the machine stays occupied.
    """
    released = _pool(root, state).release(run)
    if as_json:
        _emit({"ok": released, "run": run, "released": released})
        raise typer.Exit(EXIT_OK if released else EXIT_UNUSABLE)
    if released:
        console.print(f"[green]released[/] {run}")
        raise typer.Exit(EXIT_OK)
    console.print(f"[yellow]{run} held no lease[/]")
    console.print("[dim]Check `sf worker leases` for the run id.[/]")
    raise typer.Exit(EXIT_UNUSABLE)


# `orchestrate`, not `plan`: `sf plan` already means "show the resolved configuration", and
# registering a group under that name shadowed it silently -- typer resolved the group and
# the existing command simply stopped answering. Two tests caught it; nothing in the CLI
# itself objected, which is why `test_no_command_name_is_claimed_twice` now exists.
orchestrate_app = typer.Typer(
    help="Named multi-agent patterns: fan-out, swarm, critic, supervisor, dag.",
    no_args_is_help=True,
)
app.add_typer(orchestrate_app, name="orchestrate")


_JOINS = "all, any, quorum or best"


def _join(name: str, quorum: int):  # type: ignore[no-untyped-def]
    from software_factory.orchestrator.patterns import Join

    try:
        join = Join(name)
    except ValueError as exc:
        console.print(f"[red]{name!r} is not a join rule[/] — use {_JOINS}")
        raise typer.Exit(EXIT_UNUSABLE) from exc
    if join is Join.QUORUM and quorum < 1:
        console.print("[red]--join quorum needs --quorum N[/]")
        console.print(
            "[dim]There is no default on purpose: `all`, `any` and a quorum are different "
            "questions, and a fan-in that guesses answers one nobody asked.[/]"
        )
        raise typer.Exit(EXIT_UNUSABLE)
    return join


def _show(plan) -> None:  # type: ignore[no-untyped-def]
    console.print(f"[bold]{plan.name}[/] [dim]({plan.pattern}, join {plan.join.value})[/]")
    for index, wave in enumerate(plan.order(), start=1):
        names = ", ".join(step.name for step in wave)
        parallel = " [dim](in parallel)[/]" if len(wave) > 1 else ""
        console.print(f"  wave {index}: {names}{parallel}")


def _run_or_show(  # type: ignore[no-untyped-def]
    plan, *, root: Path, repo: Path, state: Path, dry_run: bool, as_json: bool
) -> None:
    """Build the plan, then either describe it or carry it out.

    `--dry-run` is the default for a reason that is not caution: a plan is the one thing in
    this factory that multiplies cost by a number the operator typed, and seeing the shape
    before paying for it is cheaper than every other way of finding out it was wrong.
    """
    if dry_run:
        if as_json:
            _emit({"ok": True, "dryRun": True, "plan": plan.as_dict()})
        else:
            _show(plan)
            console.print(
                f"\n[dim]{len(plan.steps)} step(s), up to {plan.width} at once. "
                "Add --execute to run it.[/]"
            )
        raise typer.Exit(EXIT_OK)

    from software_factory.definition import load_strict
    from software_factory.orchestrator.coordinator import local_coordinator

    provider = _resolve_provider()
    if provider is None:
        # Refused rather than run against a stub. A plan is the one command whose cost is
        # multiplied by a number the operator typed, and a fan-out of twenty against no
        # model produces twenty runs that prove nothing and still take the time.
        message = "no model provider is configured, so this plan would produce nothing"
        remediation = (
            "Set SF_PROVIDER_ENDPOINT to a model endpoint, or drop --execute to see the "
            "shape without running it."
        )
        if as_json:
            _emit({"ok": False, "error": {"message": message, "remediation": remediation}})
        else:
            err_console.print(f"[bold red]cannot run[/] {message}")
            err_console.print(f"[dim]{remediation}[/]")
        raise typer.Exit(EXIT_UNUSABLE)

    definition = load_strict(root)
    coordinator = local_coordinator(definition, repo=repo, state_dir=state, provider=provider)
    result = coordinator.run_plan(plan)

    if as_json:
        _emit({"ok": result.satisfied, **result.as_dict()})
        raise typer.Exit(EXIT_OK if result.satisfied else EXIT_UNUSABLE)

    _show(plan)
    console.print()
    table = Table(show_header=True, header_style="bold", box=None)
    for column in ("step", "state", "score", "detail"):
        table.add_column(column, overflow="fold")
    colours = {"succeeded": "green", "failed": "red", "skipped": "dim"}
    for outcome in result.outcomes:
        table.add_row(
            outcome.step,
            f"[{colours.get(outcome.state.value, 'white')}]{outcome.state.value}[/]",
            "—" if outcome.score is None else f"{outcome.score:.2f}",
            outcome.detail,
        )
    console.print(table)
    verdict = "[green]satisfied[/]" if result.satisfied else "[red]not satisfied[/]"
    console.print(f"\n{verdict} — {result.reason}")
    raise typer.Exit(EXIT_OK if result.satisfied else EXIT_UNUSABLE)


RootOpt = Annotated[Path, typer.Option("--root", help="The factory directory.")]
StateOpt = Annotated[Path, typer.Option("--state", help="Where run state and the ledger live.")]
ExecuteOpt = Annotated[
    bool, typer.Option("--execute", help="Actually run it. Without this, only the shape is shown.")
]


@orchestrate_app.command("fan-out")
def plan_fan_out(
    request: Annotated[list[str], typer.Argument(help="One request per branch.")],
    join: Annotated[str, typer.Option("--join", help=f"How to read the results: {_JOINS}.")],
    name: Annotated[str, typer.Option("--name", help="What to call this plan.")] = "fan-out",
    quorum: Annotated[int, typer.Option("--quorum", help="How many must succeed.")] = 0,
    agent: Annotated[str, typer.Option("--agent", help="Which agent runs each branch.")] = "",
    requires: Annotated[
        list[str] | None, typer.Option("--requires", help="A worker label each branch needs.")
    ] = None,
    root: RootOpt = Path(),
    repo: Annotated[Path, typer.Option("--repo", help="The repository to work in.")] = Path(),
    state: StateOpt = Path(".factory"),
    execute: ExecuteOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """Several independent branches, read together."""
    from software_factory.errors import FactoryError
    from software_factory.orchestrator.patterns import fan_out

    try:
        plan = fan_out(
            name,
            request,
            join=_join(join, quorum),
            quorum=quorum,
            agent=agent,
            requires=tuple(requires or ()),
        )
    except FactoryError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(EXIT_UNUSABLE) from exc
    _run_or_show(plan, root=root, repo=repo, state=state, dry_run=not execute, as_json=as_json)


@orchestrate_app.command("swarm")
def plan_swarm(
    request: Annotated[str, typer.Argument(help="The task every attempt gets.")],
    attempts: Annotated[int, typer.Option("--attempts", help="How many tries.")] = 3,
    name: Annotated[str, typer.Option("--name", help="What to call this plan.")] = "swarm",
    agent: Annotated[
        list[str] | None, typer.Option("--agent", help="One agent per attempt. Repeatable.")
    ] = None,
    root: RootOpt = Path(),
    repo: Annotated[Path, typer.Option("--repo", help="The repository to work in.")] = Path(),
    state: StateOpt = Path(".factory"),
    execute: ExecuteOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """The same task attempted several times, and the best result kept.

    Scored, never raced. First-past-the-post selects for speed, and the fastest answer is
    the one that did the least work — so a raced swarm reliably picks the shallowest attempt
    and pays for the rest.
    """
    from software_factory.errors import FactoryError
    from software_factory.orchestrator.patterns import swarm

    try:
        plan = swarm(name, request, attempts=attempts, agents=agent or ())
    except FactoryError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(EXIT_UNUSABLE) from exc
    _run_or_show(plan, root=root, repo=repo, state=state, dry_run=not execute, as_json=as_json)


@orchestrate_app.command("critic")
def plan_critic(
    produce: Annotated[str, typer.Argument(help="What to build.")],
    review: Annotated[str, typer.Argument(help="What to check about it.")],
    name: Annotated[str, typer.Option("--name", help="What to call this plan.")] = "critic",
    producer: Annotated[str, typer.Option("--producer", help="Who builds it.")] = "",
    reviewer: Annotated[str, typer.Option("--reviewer", help="Who checks it.")] = "",
    root: RootOpt = Path(),
    repo: Annotated[Path, typer.Option("--repo", help="The repository to work in.")] = Path(),
    state: StateOpt = Path(".factory"),
    execute: ExecuteOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """One agent produces, a different agent judges.

    The two may not be the same agent: self-review is a rubber stamp with a latency cost.
    """
    from software_factory.errors import FactoryError
    from software_factory.orchestrator.patterns import critic

    try:
        plan = critic(name, produce=produce, review=review, producer=producer, reviewer=reviewer)
    except FactoryError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(EXIT_UNUSABLE) from exc
    _run_or_show(plan, root=root, repo=repo, state=state, dry_run=not execute, as_json=as_json)


@orchestrate_app.command("supervisor")
def plan_supervisor(
    plan_request: Annotated[str, typer.Argument(help="What the supervisor should work out.")],
    worker: Annotated[list[str], typer.Argument(help="One request per worker.")],
    join: Annotated[str, typer.Option("--join", help=f"How to read the results: {_JOINS}.")],
    name: Annotated[str, typer.Option("--name", help="What to call this plan.")] = "supervisor",
    quorum: Annotated[int, typer.Option("--quorum", help="How many must succeed.")] = 0,
    root: RootOpt = Path(),
    repo: Annotated[Path, typer.Option("--repo", help="The repository to work in.")] = Path(),
    state: StateOpt = Path(".factory"),
    execute: ExecuteOpt = False,
    as_json: JsonOpt = False,
) -> None:
    """One step plans, several execute, and the results are read together."""
    from software_factory.errors import FactoryError
    from software_factory.orchestrator.patterns import supervisor

    try:
        plan = supervisor(
            name,
            plan_request=plan_request,
            worker_requests=worker,
            join=_join(join, quorum),
            quorum=quorum,
        )
    except FactoryError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(EXIT_UNUSABLE) from exc
    _run_or_show(plan, root=root, repo=repo, state=state, dry_run=not execute, as_json=as_json)


# The module-as-script entry point stays at the very bottom, and it matters that it does.
# Run as `python -m software_factory.cli`, this file executes top to bottom: a guard placed
# mid-file calls `app()` and exits *before* any command group defined below it is
# registered. That is how `sf worker` came to exist, pass its tests, appear in the
# generated reference, and answer "no such command" to the one invocation the CI script
# uses -- because the `sf` console script imports the module first and never hits the
# guard, so the two entry points disagreed about which commands existed.
if __name__ == "__main__":  # pragma: no cover
    main()
