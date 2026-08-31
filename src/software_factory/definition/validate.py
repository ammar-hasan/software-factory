"""Cross-reference validation and lint (PRD FR-2.4, FR-1.4, FR-3.5, FR-7.11, FR-16.2).

Structural validation lives in the models; this module checks the things a single
file cannot know about itself: that references resolve, that exactly one conductor
exists, that a critic does not share the builder's blind spot, and that nothing
claims an authority it does not have.

Errors block a load. Warnings never do -- a factory that refuses to start because a
skill is undated is a factory nobody adopts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from software_factory.definition.loader import Definition, LoadedScorer, LoadedSkill
from software_factory.definition.models import (
    AgentRole,
    Effect,
    ExecutionDefaults,
    SkillStatus,
    description_is_specific,
)
from software_factory.definition.resolve import resolve_execution
from software_factory.errors import Severity, ValidationIssue, ValidationReport

#: Phrases in a skill body that claim an authority skills do not have (FR-7.11).
#: Skills change what an agent knows how to do, never what it can reach, so a body
#: promising access is either wrong or a social-engineering attempt.
_AUTHORITY_CLAIMS = re.compile(
    r"\b(?:"
    r"grants?\s+(?:you\s+)?(?:\w+\s+){0,2}?(?:access|permission|rights?|privileges?)"
    r"|(?:you|it)\s+(?:now\s+)?(?:ha(?:ve|s)|get|gets)\s+(?:\w+\s+){0,2}?"
    r"(?:access|permission|rights?|privileges?)\b"
    r"|(?:lets?|allows?|enables?)\s+you\s+(?:to\s+)?(?:read|write|access|use|deploy)"
    r"\s+(?:\w+\s+){0,3}?(?:secret|secrets|credential|credentials|token|tokens|key|keys|"
    r"password|passwords|database|production)\b"
    r"|this\s+skill\s+(?:grants?|unlocks?|authoris\w*|authoriz\w*)"
    r"|ignore\s+(?:\w+\s+){0,2}?(?:previous|prior|above|earlier|preceding)\s+"
    r"(?:instruction|instructions|rule|rules)"
    r"|disregard\s+(?:\w+\s+){0,2}?(?:instruction|instructions|rule|rules|polic\w+)"
    r"|bypass(?:ing)?\s+(?:\w+\s+){0,2}?(?:gate|gates|polic\w+|permission|permissions|"
    r"approval|approvals|check|checks|review)"
    r"|(?:disable|skip|turn\s+off|suppress|do\s+not\s+run|don't\s+run)\s+"
    r"(?:\w+\s+){0,2}?(?:gate|gates|check|checks|test|tests|validation|review|reviews|"
    r"approval|approvals)\b"
    r")",
    re.IGNORECASE,
)
"""Phrasings that claim an authority skills do not have (FR-7.11).

Skills change what an agent knows, never what it can reach, so a body or description
promising access is either wrong or a social-engineering attempt.

The optional word runs exist because the first version was defeated by a single inserted
word: "ignore *all* previous instructions" and "you have *full* access" both slipped past
a pattern that expected the phrases adjacent.
"""

#: Phrases in a policy file that claim to enforce what only an external system can
#: enforce (FR-16.2). Policy expresses intent; repositories and executors enforce.
_FALSE_ENFORCEMENT = re.compile(
    r"\b(?:prevents?|blocks?|forbids?|makes\s+it\s+impossible)\b[^.]{0,60}\b(?:merg\w+|push\w+)\b",
    re.IGNORECASE,
)


def validate(definition: Definition, report: ValidationReport | None = None) -> ValidationReport:
    """Run every cross-file check over a loaded definition."""
    report = report or ValidationReport()
    _check_conductor(definition, report)
    _check_agent_references(definition, report)
    _check_automation_references(definition, report)
    _check_scorer_references(definition, report)
    _check_review_independence(definition, report)
    _check_skills(definition, report)
    _check_secrets_declared(definition, report)
    _check_runner_pinning(definition, report)
    _check_ladder(definition, report)
    _check_automation_overlap(definition, report)
    return report


def _check_conductor(definition: Definition, report: ValidationReport) -> None:
    conductors = [a for a in definition.agents.values() if a.definition.role is AgentRole.CONDUCTOR]
    if len(conductors) == 1:
        return
    if not conductors:
        report.add(
            ValidationIssue(
                severity=Severity.ERROR,
                code="factory.no_conductor",
                message="no agent declares `role: CONDUCTOR`",
                path=definition.root,
                remediation=(
                    "Exactly one agent must be the conductor -- the entry point and the "
                    "only agent that talks to the requester. Set `role: CONDUCTOR` on one."
                ),
            )
        )
        return
    for agent in conductors:
        report.add(
            ValidationIssue(
                severity=Severity.ERROR,
                code="factory.multiple_conductors",
                message=(
                    f"{len(conductors)} agents declare `role: CONDUCTOR` "
                    f"({', '.join(a.name for a in conductors)}); exactly one is allowed"
                ),
                path=agent.path,
                key="role",
                remediation="Demote all but one to `CUSTOM`, or split into separate factories.",
            )
        )


def _check_runner_ref(
    definition: Definition,
    report: ValidationReport,
    execution: ExecutionDefaults,
    path: Path,
    who: str,
) -> None:
    """A runner named but not defined is a validation error, not a runtime surprise (FR-2.4)."""
    runner = execution.runner
    if runner is None or runner in definition.runners:
        return
    report.add(
        ValidationIssue(
            severity=Severity.ERROR,
            code="runner.unknown",
            message=f"{who} selects unknown runner {runner!r}",
            path=path,
            key="runner",
            accepted=tuple(sorted(definition.runners)),
            remediation="Define `runners/<name>.yaml`, or select a runner that exists.",
        )
    )


def _check_agent_references(definition: Definition, report: ValidationReport) -> None:
    for agent in definition.agents.values():
        execution = agent.definition.execution
        _check_runner_ref(definition, report, execution, agent.path, f"agent {agent.name!r}")
        fallback = agent.definition.fallback
        if fallback and fallback not in definition.agents:
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="agent.unknown_fallback",
                    message=f"agent {agent.name!r} names unknown fallback agent {fallback!r}",
                    path=agent.path,
                    key="fallback",
                    accepted=tuple(sorted(definition.agents)),
                    remediation="Name an agent that exists, or remove `fallback`.",
                )
            )
        if fallback == agent.name:
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="agent.self_fallback",
                    message=f"agent {agent.name!r} lists itself as its own fallback",
                    path=agent.path,
                    key="fallback",
                    remediation="A fallback must be a different agent.",
                )
            )
        if not agent.prompt.strip():
            report.add(
                ValidationIssue(
                    severity=Severity.WARNING,
                    code="agent.empty_prompt",
                    message=f"agent {agent.name!r} has an empty prompt body",
                    path=agent.path,
                    remediation="Write the agent's durable role instructions after the frontmatter.",
                )
            )


def _check_automation_references(definition: Definition, report: ValidationReport) -> None:
    for automation in definition.automations.values():
        target = automation.definition.agent
        if target and target not in definition.agents:
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="automation.unknown_agent",
                    message=(f"automation {automation.name!r} routes to unknown agent {target!r}"),
                    path=automation.path,
                    key="agent",
                    accepted=tuple(sorted(definition.agents)),
                    remediation="Route to an agent that exists, or omit `agent` to use the conductor.",
                )
            )
        _check_runner_ref(
            definition,
            report,
            automation.definition.execution,
            automation.path,
            f"automation {automation.name!r}",
        )
        for trigger in automation.definition.triggers:
            if not trigger.filter and trigger.provider != "schedule":
                report.add(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        code="automation.unfiltered_trigger",
                        message=(
                            f"automation {automation.name!r} has an unfiltered "
                            f"{trigger.provider}/{trigger.event} trigger, so every such event "
                            "starts a run"
                        ),
                        path=automation.path,
                        key="triggers",
                        remediation=(
                            "Add a `filter` narrowing to the repositories, labels, or authors "
                            "this automation should act on."
                        ),
                    )
                )


def _check_scorer_references(definition: Definition, report: ValidationReport) -> None:
    for scorer in definition.scorers.values():
        for agent_name in scorer.definition.agents:
            if agent_name not in definition.agents:
                report.add(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        code="scorer.unknown_agent",
                        message=f"scorer {scorer.name!r} targets unknown agent {agent_name!r}",
                        path=scorer.path,
                        key="agents",
                        accepted=tuple(sorted(definition.agents)),
                        remediation="Target an agent that exists.",
                    )
                )
                continue
            _check_judge_independence(definition, report, scorer, agent_name)


def _check_judge_independence(
    definition: Definition,
    report: ValidationReport,
    scorer: LoadedScorer,
    agent_name: str,
) -> None:
    """A judge sharing its subject's model and harness cannot see its blind spots (evals.md E-19)."""
    agent = definition.agents[agent_name]
    subject = resolve_execution(definition.factory.agent_defaults, agent.definition.execution)
    judge = scorer.definition.judge
    same_harness = (subject.harness.type if subject.harness else "oz") == judge.type
    subject_model = subject.model or (subject.harness.model if subject.harness else None)
    same_model = subject_model is not None and subject_model == judge.model
    if same_harness and same_model:
        report.add(
            ValidationIssue(
                severity=Severity.WARNING,
                code="scorer.shared_blind_spot",
                message=(
                    f"scorer {scorer.name!r} judges agent {agent_name!r} with the same model "
                    f"and harness it runs on; the judge inherits the subject's blind spots"
                ),
                path=scorer.path,
                key="judge",
                remediation="Give the judge a different model or harness from the agent it scores.",
            )
        )


def _check_review_independence(definition: Definition, report: ValidationReport) -> None:
    """A critic on the builder's model and harness reviews with the builder's blind spots (FR-3.5)."""
    builders = [a for a in definition.agents.values() if a.definition.role is AgentRole.BUILDER]
    critics = [a for a in definition.agents.values() if a.definition.role is AgentRole.CRITIC]
    for critic in critics:
        if critic.definition.allow_shared_blind_spot:
            continue
        critic_exec = resolve_execution(
            definition.factory.agent_defaults, critic.definition.execution
        )
        for builder in builders:
            builder_exec = resolve_execution(
                definition.factory.agent_defaults, builder.definition.execution
            )
            if _same_engine(critic_exec, builder_exec):
                report.add(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        code="agent.shared_blind_spot",
                        message=(
                            f"critic {critic.name!r} and builder {builder.name!r} resolve to the "
                            "same model and harness; independent review needs independent "
                            "failure modes"
                        ),
                        path=critic.path,
                        remediation=(
                            "Give the critic a different model or harness, or set "
                            "`allowSharedBlindSpot: true` to accept the risk explicitly."
                        ),
                    )
                )


def _same_engine(left: ExecutionDefaults, right: ExecutionDefaults) -> bool:
    left_harness = left.harness.type if left.harness else "oz"
    right_harness = right.harness.type if right.harness else "oz"
    left_model = left.model or (left.harness.model if left.harness else None) or left.tier
    right_model = right.model or (right.harness.model if right.harness else None) or right.tier
    return left_harness == right_harness and left_model == right_model


def _check_skills(definition: Definition, report: ValidationReport) -> None:
    for skill in _all_skills(definition):
        if not description_is_specific(skill.definition.description):
            report.add(
                ValidationIssue(
                    severity=Severity.WARNING,
                    code="skill.generic_description",
                    message=(
                        f"skill {skill.name!r} has a description with no domain-bearing terms; "
                        "it cannot be selected reliably"
                    ),
                    path=skill.path,
                    key="description",
                    remediation="State when to use it, what it produces, and what it is not for.",
                )
            )
        # The description is what reaches the selection prompt, so scanning only the body
        # left the more exposed field unchecked.
        claim = _AUTHORITY_CLAIMS.search(skill.body) or _AUTHORITY_CLAIMS.search(
            skill.definition.description
        )
        if claim:
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="skill.claims_authority",
                    message=(
                        f"skill {skill.name!r} body claims to grant access or bypass a control "
                        f"({claim.group(0)!r}); skills change knowledge, never access"
                    ),
                    path=skill.path,
                    remediation=(
                        "Remove the claim. Scope access through the agent's tool, secret, and "
                        "MCP grants instead."
                    ),
                )
            )
        successor = skill.definition.superseded_by
        known = {s.name for s in _all_skills(definition)}
        if successor and successor not in known:
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="skill.unknown_successor",
                    message=f"skill {skill.name!r} names unknown successor {successor!r}",
                    path=skill.path,
                    key="supersededBy",
                    accepted=tuple(sorted(known)),
                    remediation="Point at a skill that exists, or drop the pointer.",
                )
            )

    _check_skill_collisions(definition, report)


def _check_skill_collisions(definition: Definition, report: ValidationReport) -> None:
    """Two skills with near-identical descriptions cannot be told apart (skills.md §4.1)."""
    skills = [s for s in _all_skills(definition) if s.definition.status is not SkillStatus.RETIRED]
    for index, left in enumerate(skills):
        for right in skills[index + 1 :]:
            if left.owner_agent and right.owner_agent and left.owner_agent != right.owner_agent:
                continue  # different agents never see each other's skills
            score = _description_similarity(
                left.definition.description, right.definition.description
            )
            if score >= 0.75:
                report.add(
                    ValidationIssue(
                        severity=Severity.WARNING,
                        code="skill.description_collision",
                        message=(
                            f"skills {left.name!r} and {right.name!r} have descriptions that are "
                            f"{score:.0%} similar; selection between them will be unreliable"
                        ),
                        path=right.path,
                        key="description",
                        remediation=(
                            "Sharpen both descriptions, or merge the skills "
                            "(see docs/harness/skills.md)."
                        ),
                    )
                )


def _description_similarity(left: str, right: str) -> float:
    """Jaccard similarity over content words. Deterministic and dependency-free."""
    stop = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "to",
        "for",
        "of",
        "in",
        "on",
        "when",
        "use",
        "used",
        "this",
        "that",
        "with",
        "is",
        "it",
        "as",
        "by",
        "from",
        "not",
    }

    def tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop and len(w) > 2}

    left_set, right_set = tokens(left), tokens(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _check_secrets_declared(definition: Definition, report: ValidationReport) -> None:
    """Secret *names* belong in definitions; secret *values* never do (FR-2.8)."""
    value_shaped = re.compile(r"^(?:sk-|ghp_|gho_|github_pat_|xox[baprs]-|AKIA)[A-Za-z0-9_\-]{8,}$")
    sources: list[tuple[Path, str, tuple[str, ...]]] = [
        (definition.root / "factory.yaml", "the factory", definition.factory.secrets),
        *(
            (agent.path, f"agent {agent.name!r}", agent.definition.execution.secrets or ())
            for agent in definition.agents.values()
        ),
        *(
            (
                automation.path,
                f"automation {automation.name!r}",
                automation.definition.execution.secrets or (),
            )
            for automation in definition.automations.values()
        ),
    ]
    for path, who, names in sources:
        for name in names:
            if value_shaped.match(name):
                report.add(
                    ValidationIssue(
                        severity=Severity.ERROR,
                        code="secret.value_in_definition",
                        message=(
                            f"{who} declares what looks like a secret *value* under `secrets`"
                        ),
                        path=path,
                        key="secrets",
                        remediation=(
                            "Declare the secret's NAME here and store the value in the secret "
                            "store. Then rotate the exposed credential immediately."
                        ),
                    )
                )


def _check_runner_pinning(definition: Definition, report: ValidationReport) -> None:
    """Unpinned images make a runner non-reproducible (FR-17.9)."""
    for runner in definition.runners.values():
        image = runner.definition.platform.image
        if runner.definition.platform.os == "linux" and not image:
            report.add(
                ValidationIssue(
                    severity=Severity.ERROR,
                    code="runner.missing_image",
                    message=f"linux runner {runner.name!r} declares no `platform.image`",
                    path=runner.path,
                    key="platform.image",
                    remediation="Set the container image the sandbox boots.",
                )
            )
        elif image and "@sha256:" not in image:
            report.add(
                ValidationIssue(
                    severity=Severity.WARNING,
                    code="runner.unpinned_image",
                    message=(
                        f"runner {runner.name!r} uses an unpinned image {image!r}; runs are not "
                        "reproducible and the image can change under you"
                    ),
                    path=runner.path,
                    key="platform.image",
                    remediation="Pin by digest, e.g. `ubuntu:24.04@sha256:...`.",
                )
            )


def _check_ladder(definition: Definition, report: ValidationReport) -> None:
    ladder = definition.factory.ladder
    if ladder is None:
        report.add(
            ValidationIssue(
                severity=Severity.WARNING,
                code="factory.no_ladder",
                message="no routing ladder declared; every agent pins its own model",
                path=definition.root,
                remediation=(
                    "Declare `ladder.tiers` so agents can start low and escalate on evidence."
                ),
            )
        )
        return
    if not any(tier.local for tier in ladder.tiers):
        report.add(
            ValidationIssue(
                severity=Severity.WARNING,
                code="factory.no_local_tier",
                message="the ladder has no local tier, so this factory cannot run offline",
                path=definition.root,
                key="ladder",
                remediation=(
                    "Add a tier with `local: true` pointing at a local endpoint if you want "
                    "offline operation."
                ),
            )
        )


def _check_automation_overlap(definition: Definition, report: ValidationReport) -> None:
    """Two automations matching the same event both fire, doubling the work (FR-18.4)."""
    seen: dict[tuple[str, str], list[str]] = {}
    for automation in definition.automations.values():
        if not automation.definition.enabled:
            continue
        for trigger in automation.definition.triggers:
            if trigger.filter:
                continue  # filtered triggers may legitimately coexist
            seen.setdefault((trigger.provider, trigger.event), []).append(automation.name)
    for (provider, event), names in seen.items():
        if len(names) > 1:
            report.add(
                ValidationIssue(
                    severity=Severity.WARNING,
                    code="automation.overlap",
                    message=(
                        f"{len(names)} unfiltered automations match {provider}/{event} "
                        f"({', '.join(sorted(names))}); each match starts its own run"
                    ),
                    path=definition.root,
                    remediation="Add filters so at most one automation matches a given event.",
                )
            )


def _all_skills(definition: Definition) -> Iterable[LoadedSkill]:
    yield from definition.skills.values()
    for agent in definition.agents.values():
        yield from agent.skills


def lint(definition: Definition) -> ValidationReport:
    """Advisory checks only -- everything :func:`validate` reports as a warning, plus more."""
    report = validate(definition)
    _lint_sizing(definition, report)
    _lint_policy_claims(definition, report)
    return report


def _lint_sizing(definition: Definition, report: ValidationReport) -> None:
    """Factories are sized by product surface, not by team (FR-1.4)."""
    if len(definition.factory.repositories) > 8:
        report.add(
            ValidationIssue(
                severity=Severity.WARNING,
                code="factory.oversized",
                message=(
                    f"factory {definition.factory.name!r} spans "
                    f"{len(definition.factory.repositories)} repositories; one policy across "
                    "that many surfaces is usually too coarse"
                ),
                path=definition.root / "factory.yaml",
                key="repositories",
                remediation=(
                    "Split into factories by product surface -- repositories that ship together."
                ),
            )
        )


def _lint_policy_claims(definition: Definition, report: ValidationReport) -> None:
    """Policy expresses intent; repositories and executors enforce (FR-16.2)."""
    policy_dir = definition.root / "policy"
    if not policy_dir.is_dir():
        return
    for path in sorted(policy_dir.rglob("*.yaml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = _FALSE_ENFORCEMENT.search(text)
        if match:
            report.add(
                ValidationIssue(
                    severity=Severity.WARNING,
                    code="policy.false_enforcement",
                    message=(
                        f"{path.name} claims to prevent merging or pushing; policy files cannot "
                        "enforce that"
                    ),
                    path=path,
                    remediation=(
                        "Enforce merge authority with branch protection and repository "
                        "permissions. State the policy as intent, not as a control."
                    ),
                )
            )


def unused_effects(
    execution: ExecutionDefaults, tool_effects: Mapping[str, Effect] | None = None
) -> tuple[Effect, ...]:
    """Effect classes granted but not needed by any granted tool (`sf audit`).

    It used to return `execution.effects` unchanged, having never looked at
    `execution.tools`. Reporting every effect as unused is the same as reporting none: the
    least-privilege audit this exists for produced noise in both directions, and the
    docstring made a claim the body could not support.

    `tool_effects` maps tool name to the effect it needs; without one there is nothing to
    compare against and the answer is honestly empty rather than confidently wrong. A tool
    the map does not know is not evidence that its effect is unused, so it is skipped and
    the effects it might need stay unreported.
    """
    granted = tuple(dict.fromkeys(execution.effects or ()))
    if not granted:
        return ()
    if tool_effects is None:
        return ()

    names = tuple(execution.tools or ())
    unknown = [name for name in names if name not in tool_effects]
    if unknown:
        # Some granted tool's effect is unknowable here, so any effect could be the one it
        # needs. Claiming an effect is unused on that basis would be a guess.
        return ()

    needed = {tool_effects[name] for name in names}
    return tuple(effect for effect in granted if effect not in needed)
