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
from collections import deque
from contextlib import suppress
from datetime import datetime
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
                    }
                    for c in clusters
                ],
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
    for column in ("signature", "failures", "work items", "pattern"):
        table.add_column(column, overflow="fold")
    for cluster in clusters:
        table.add_row(
            cluster.signature,
            str(cluster.size),
            str(len(cluster.work_items)),
            cluster.failures[0].describe(),
        )
    console.print(table)
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


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
