"""Running one work item through the factory (PRD FR-4, FR-9, FR-13, FR-15).

This is where the pieces meet: a workspace is created, a pack is assembled from the
definition and the repository, the turn loop runs an agent, gates evaluate what it
produced, and everything is written to the ledger. It is deliberately thin -- each
subsystem already enforces its own rules, and the coordinator's job is to sequence them
and record what happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from software_factory.definition.loader import Definition
from software_factory.definition.models import AgentRole, Effect, Stage
from software_factory.definition.resolve import resolve_for_agent
from software_factory.evals.evidence import EvidenceBundle, EvidenceClass, EvidenceItem
from software_factory.evals.gates import GateContext, GateReport, ViolationClass, run_gates
from software_factory.harness.awareness import (
    AwarenessPack,
    Citation,
    CitationKind,
    Item,
    Origin,
    PackAssembler,
    SectionId,
    Snapshot,
)
from software_factory.harness.loop import Budget, RunResult, RunStatus, TurnLoop
from software_factory.harness.routing import RoutingState, starting_tier
from software_factory.harness.tools import BlastRadius, Grants
from software_factory.ledger import EntryType, Ledger
from software_factory.memory.records import utc_now
from software_factory.orchestrator.workitem import (
    Blocker,
    StageMachine,
    Transition,
    WorkClass,
    WorkItem,
)
from software_factory.providers.base import Provider
from software_factory.runtime.executor import LocalExecutor, SandboxPolicy
from software_factory.runtime.tools import build_registry
from software_factory.runtime.workspace import Workspace, WorkspaceFactory
from software_factory.spec.units import SpecStore

#: What each role must produce. Downstream stages consume validated structures, never
#: free prose (HARNESS.md O-1).
STAGE_SCHEMAS: dict[Stage, dict[str, Any]] = {
    Stage.TRIAGE: {
        "type": "object",
        "required": ["findings", "scope", "calibration"],
        "properties": {
            "findings": {"type": "string"},
            "scope": {"type": "string"},
            "open_questions": {"type": "array"},
            "calibration": {"type": "object"},
        },
    },
    Stage.DESIGN: {
        "type": "object",
        "required": ["plan", "acceptance", "calibration"],
        "properties": {
            "plan": {"type": "string"},
            "acceptance": {"type": "array"},
            "calibration": {"type": "object"},
        },
    },
    Stage.BUILD: {
        "type": "object",
        "required": ["summary", "claims", "calibration"],
        "properties": {
            "summary": {"type": "string"},
            "claims": {"type": "array"},
            "calibration": {"type": "object"},
        },
    },
    Stage.REVIEW: {
        "type": "object",
        "required": ["verdict", "findings", "calibration"],
        "properties": {
            "verdict": {"type": "string"},
            "findings": {"type": "array"},
            "calibration": {"type": "object"},
        },
    },
}

ROLE_FOR_STAGE: dict[Stage, AgentRole] = {
    Stage.TRIAGE: AgentRole.SCOUT,
    Stage.DESIGN: AgentRole.ARCHITECT,
    Stage.BUILD: AgentRole.BUILDER,
    Stage.REVIEW: AgentRole.CRITIC,
    Stage.VERIFY: AgentRole.PROVER,
}


@dataclass(slots=True)
class StageOutcome:
    """What one stage produced."""

    stage: Stage
    agent: str
    run: RunResult
    gates: GateReport
    bundle: EvidenceBundle
    pack_digest: str

    @property
    def advanced(self) -> bool:
        return self.run.ok and not self.gates.blocked

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "agent": self.agent,
            "advanced": self.advanced,
            "packDigest": self.pack_digest,
            "run": self.run.as_dict(),
            "gates": self.gates.as_dict(),
            "evidence": self.bundle.as_dict(),
        }


@dataclass(slots=True)
class WorkOutcome:
    """The result of running a work item as far as it got."""

    item: WorkItem
    stages: list[StageOutcome] = field(default_factory=list)
    diff: str = ""
    changed_paths: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "workItem": self.item.as_dict(),
            "stages": [s.as_dict() for s in self.stages],
            "changedPaths": list(self.changed_paths),
            "diffBytes": len(self.diff),
        }


class Coordinator:
    """Runs work items. One per factory, reused across items."""

    def __init__(
        self,
        definition: Definition,
        *,
        provider: Provider,
        workspaces: WorkspaceFactory,
        ledger: Ledger,
        spec: SpecStore | None = None,
        allow_unsandboxed: bool = False,
    ) -> None:
        self.definition = definition
        self.provider = provider
        self.workspaces = workspaces
        self.ledger = ledger
        self.spec = spec or SpecStore()
        self.machine = StageMachine()
        self.allow_unsandboxed = allow_unsandboxed

    # ------------------------------------------------------------------------ public

    def run(self, item: WorkItem, *, stages: list[Stage] | None = None) -> WorkOutcome:
        """Carry a work item through the requested stages, stopping at the first block."""
        outcome = WorkOutcome(item=item)
        item.definition_revision = item.definition_revision or "local"
        self.ledger.append(
            EntryType.WORK_ITEM_CREATED,
            actor="coordinator",
            subject=item.id,
            payload={"title": item.title, "workClass": item.work_class.value},
        )

        workspace = self.workspaces.create(run_id=item.id)
        try:
            for stage in stages or self._default_path(item):
                moved = self.machine.advance(
                    item, stage, actor="coordinator", reason=f"entering {stage.value}"
                )
                if not isinstance(moved, Transition):
                    self._block(item, Blocker.GATE_FAILED_TERMINAL, moved.message)
                    break

                stage_outcome = self._run_stage(item, stage, workspace)
                outcome.stages.append(stage_outcome)

                if not stage_outcome.advanced:
                    self._block(
                        item,
                        self._blocker_for(stage_outcome),
                        self._action_for(stage_outcome),
                    )
                    break

            outcome.diff = workspace.diff()
            outcome.changed_paths = tuple(sorted(workspace.changed_paths()))
        finally:
            self.ledger.append(
                EntryType.WORK_ITEM_TRANSITION,
                actor="coordinator",
                subject=item.id,
                payload={"stage": item.stage.value, "blocker": item.blocker},
            )
        return outcome

    # ----------------------------------------------------------------------- stages

    def _default_path(self, item: WorkItem) -> list[Stage]:
        """The shortest path that still meets the quality policy.

        Triage is skipped when the request already explains what is wrong and what to
        change; review never is (FR-3.3a).
        """
        if item.work_class is WorkClass.DEFECT and len(item.request) > 200:
            return [Stage.TRIAGE, Stage.BUILD, Stage.REVIEW]
        if item.work_class in (WorkClass.FEATURE, WorkClass.REFACTOR):
            return [Stage.TRIAGE, Stage.DESIGN, Stage.BUILD, Stage.REVIEW]
        return [Stage.TRIAGE, Stage.BUILD, Stage.REVIEW]

    def _run_stage(self, item: WorkItem, stage: Stage, workspace: Workspace) -> StageOutcome:
        role = ROLE_FOR_STAGE.get(stage, AgentRole.CUSTOM)
        agent = self._agent_for(role)
        agent_name = agent[0] if agent else role.value.lower()
        prompt = agent[1] if agent else f"You are the {role.value.lower()} agent."

        execution = (
            resolve_for_agent(self.definition.factory, agent[2])
            if agent
            else resolve_for_agent(self.definition.factory, None)  # type: ignore[arg-type]
        )

        policy = SandboxPolicy(workspace=workspace.root, wall_clock_s=900)
        executor = LocalExecutor(policy, allow_unsandboxed=self.allow_unsandboxed)
        registry = build_registry(workspace, executor)

        effects = frozenset(execution.effects or (Effect.READ, Effect.WRITE, Effect.EXEC))
        grants = Grants(
            tools=frozenset(execution.tools or ())
            or frozenset(
                t.name for t in registry.granted(Grants(allow_all_tools=True, effects=effects))
            ),
            effects=effects,
        )

        contract = BlastRadius(
            writable_paths=(str(workspace.root),),
            effects_allowed=effects,
            external_actions=frozenset(),
            checkpoints=True,
        )

        pack = self._assemble(item, stage, role, workspace, registry, grants)
        self.ledger.append(
            EntryType.PACK_ASSEMBLED,
            actor=agent_name,
            subject=item.id,
            payload={"stage": stage.value, "digest": pack.digest(), "tokens": pack.tokens()},
        )

        ladder = self.definition.factory.ladder
        tier = execution.tier or (starting_tier(ladder) if ladder else "local-small")
        routing = (
            RoutingState(ladder=ladder, current=tier)
            if ladder
            else RoutingState(ladder=self._synthetic_ladder(), current="local-small")
        )

        self.ledger.append(
            EntryType.RUN_STARTED,
            actor=agent_name,
            subject=item.id,
            payload={"stage": stage.value, "tier": routing.current},
        )

        loop = TurnLoop(
            provider=self.provider,
            registry=registry,
            grants=grants,
            pack=pack,
            contract=contract,
            budget=Budget(),
            routing=routing,
            role_prompt=prompt,
            task=item.request,
            output_schema=STAGE_SCHEMAS.get(stage),
        )
        run = loop.run()

        self.ledger.append(
            EntryType.RUN_FINISHED,
            actor=agent_name,
            subject=item.id,
            payload={"stage": stage.value, "status": run.status.value, "reason": run.reason},
        )

        bundle = self._bundle(item, stage, run, workspace)
        gates = run_gates(
            self._gate_context(item, stage, run, bundle, registry, workspace), stage=stage.value
        )
        for result in gates.results:
            self.ledger.append(
                EntryType.GATE_EVALUATED,
                actor=agent_name,
                subject=item.id,
                payload={"stage": stage.value, **result.as_dict()},
            )
        bundle.seal()

        return StageOutcome(
            stage=stage,
            agent=agent_name,
            run=run,
            gates=gates,
            bundle=bundle,
            pack_digest=pack.digest(),
        )

    # ------------------------------------------------------------------------- pack

    def _assemble(
        self,
        item: WorkItem,
        stage: Stage,
        role: AgentRole,
        workspace: Workspace,
        registry: Any,
        grants: Grants,
    ) -> AwarenessPack:
        budget = 6000
        assembler = PackAssembler(role=role, budget_tokens=budget)

        assembler.register(
            SectionId.MISSION,
            lambda: (
                [
                    Item(
                        content=f"{item.title} — {stage.value} stage of work item {item.id}.",
                        citation=Citation(kind=CitationKind.WORK_ITEM, ref=item.id),
                        origin=Origin.HUMAN_AUTHORED,
                    )
                ],
                None,
            ),
        )

        surface = workspace.changed_paths() or set(self._top_level(workspace))
        assembler.register(
            SectionId.SPEC_SLICE,
            lambda: (
                [
                    Item(
                        content=f"{unit.id}: {unit.intent}",
                        citation=Citation(kind=CitationKind.SPEC, ref=unit.id),
                    )
                    for unit in self.spec.slice_for(surface)
                ],
                None if self.spec.units else "no spec units defined yet",
            ),
        )

        assembler.register(
            SectionId.TERRAIN,
            lambda: (
                [
                    Item(
                        content=path,
                        citation=Citation(kind=CitationKind.FILE, ref=path),
                    )
                    for path in sorted(self._top_level(workspace))[:40]
                ],
                None,
            ),
        )

        assembler.register(
            SectionId.TOOLBELT,
            lambda: (
                [
                    Item(
                        content=line,
                        citation=Citation(kind=CitationKind.POLICY, ref="toolbelt"),
                    )
                    for line in registry.render_toolbelt(grants)
                ],
                None,
            ),
        )

        assembler.register(
            SectionId.CONTRACT,
            lambda: (
                [
                    Item(
                        content=f"Required output schema: {STAGE_SCHEMAS.get(stage, {})}",
                        citation=Citation(kind=CitationKind.POLICY, ref=f"schema:{stage.value}"),
                    )
                ],
                None,
            ),
        )

        return assembler.assemble(
            Snapshot(
                commit=workspace.head,
                definition_revision=item.definition_revision,
                memory_revision="none",
                ledger_seq=self.ledger.tail()[0],
                skill_revision="none",
                assembled_at=utc_now(),
            )
        )

    @staticmethod
    def _top_level(workspace: Workspace) -> set[str]:
        return {
            str(path.relative_to(workspace.root))
            for path in workspace.root.rglob("*")
            if path.is_file() and ".git" not in path.parts and ".factory" not in path.parts
        }

    # ------------------------------------------------------------------- evaluation

    def _bundle(
        self, item: WorkItem, stage: Stage, run: RunResult, workspace: Workspace
    ) -> EvidenceBundle:
        bundle = EvidenceBundle(
            id=f"ev_{item.id}_{stage.value.lower()}",
            run_id=item.id,
            work_item_id=item.id,
            stage=stage.value,
        )
        diff = workspace.diff()
        if diff:
            bundle.add(
                EvidenceItem(
                    id="diff",
                    evidence_class=EvidenceClass.DIFF,
                    digest=str(hash(diff)),
                    location=f"{item.id}/diff.patch",
                )
            )
        for claim in (run.output or {}).get("claims", []) or []:
            if isinstance(claim, str):
                bundle.claim(claim, *(["diff"] if diff else []))
        return bundle

    def _gate_context(
        self,
        item: WorkItem,
        stage: Stage,
        run: RunResult,
        bundle: EvidenceBundle,
        registry: Any,
        workspace: Workspace,
    ) -> GateContext:
        violations: dict[ViolationClass, int] = {}
        if registry.escalating_violations():
            violations[ViolationClass.ESCALATING] = len(registry.escalating_violations())
        return GateContext(
            stage=stage.value,
            work_class=item.work_class.value,
            calibration=run.calibration,
            violations=violations,
            diff_text=workspace.diff() or "",
            bundle=bundle,
            has_test_command=False,  # no test command configured in the local slice
            build_ok=True,
            external_actions=(),
            permitted_external=frozenset(),
            builder_engine=("stub", "small"),
            critic_engine=("stub", "mid"),
        )

    # --------------------------------------------------------------------- plumbing

    def _agent_for(self, role: AgentRole) -> tuple[str, str, Any] | None:
        for agent in self.definition.agents.values():
            if agent.definition.role is role:
                return agent.name, agent.prompt, agent.definition.execution
        return None

    def _synthetic_ladder(self) -> Any:
        from software_factory.definition.models import Ladder

        return Ladder.model_validate(
            {
                "tiers": [
                    {
                        "name": "local-small",
                        "provider": "local",
                        "model": "local-model",
                        "contextWindow": 32000,
                        "workingSetCeiling": 20000,
                        "local": True,
                    }
                ],
                "defaultTier": "local-small",
            }
        )

    def _block(self, item: WorkItem, blocker: Blocker, action: str) -> None:
        self.machine.block(item, blocker, actor="coordinator", action=action or "investigate")
        self.ledger.append(
            EntryType.WORK_ITEM_BLOCKED,
            actor="coordinator",
            subject=item.id,
            payload={"blocker": blocker.value, "action": action},
        )

    @staticmethod
    def _blocker_for(outcome: StageOutcome) -> Blocker:
        match outcome.run.status:
            case RunStatus.BUDGET_EXCEEDED:
                return Blocker.BUDGET_EXCEEDED
            case RunStatus.CONTRACT_VIOLATION:
                return Blocker.GATE_FAILED_TERMINAL
            case RunStatus.PROVIDER_FAILED:
                return Blocker.EXTERNAL_DEPENDENCY
            case _:
                return Blocker.GATE_FAILED_TERMINAL

    @staticmethod
    def _action_for(outcome: StageOutcome) -> str:
        """A blocker must name what would clear it, not merely that something failed."""
        if outcome.gates.blocked:
            findings = outcome.gates.findings
            if findings:
                return findings[0].remediation
            return "review the failing gates"
        return outcome.run.reason or "investigate the run"


def local_coordinator(
    definition: Definition,
    *,
    repo: Path,
    state_dir: Path,
    provider: Provider,
    allow_unsandboxed: bool = False,
) -> Coordinator:
    """Assemble a coordinator for a single-machine run. The reference topology."""
    return Coordinator(
        definition,
        provider=provider,
        workspaces=WorkspaceFactory(repo, state_dir),
        ledger=Ledger(state_dir / "ledger.jsonl"),
        allow_unsandboxed=allow_unsandboxed,
    )
