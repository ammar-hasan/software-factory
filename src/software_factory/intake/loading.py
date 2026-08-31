"""Building an intake pipeline from a loaded definition.

The automations live in files (FR-18.3) and the pipeline is runtime state. Keeping the
translation here rather than inside `Pipeline` means the pipeline can be built in three lines
in a test, which matters more than it sounds: intake is the surface an attacker reaches
first, and a component that is awkward to put into an unusual state is one whose unusual
states nobody checks.
"""

from __future__ import annotations

from software_factory.definition.loader import Definition
from software_factory.definition.models import AuthorTrust
from software_factory.economics.scheduling import Priority
from software_factory.identity.loading import directory_from
from software_factory.intake.pipeline import Automation, Pipeline


def automations_from(definition: Definition) -> list[Automation]:
    """Every declared trigger, as one automation each.

    One per *trigger* rather than per automation file: FR-18.4 says one event may match
    several automations and each match starts its own run, and an automation declaring three
    triggers is three ways in. Flattening them here keeps the matching loop simple and keeps
    the "each match is its own run" property visible in the data.
    """
    built: list[Automation] = []
    for loaded in definition.automations.values():
        declared = loaded.definition
        agent = declared.agent or _conductor_name(definition)
        for trigger in declared.triggers:
            built.append(
                Automation(
                    name=(
                        loaded.name
                        if len(declared.triggers) == 1
                        else f"{loaded.name}:{trigger.provider}.{trigger.event}"
                    ),
                    agent=agent,
                    prompt=loaded.prompt,
                    provider=trigger.provider,
                    event=trigger.event,
                    filter=dict(trigger.filter),
                    enabled=declared.enabled,
                    # FR-18.6: restrictive by default. Accepting anyone is a declared
                    # field, so it cannot end up in the match predicate -- which is what
                    # made the opt-out an attacker-chosen trigger.
                    require_known_author=trigger.author_trust is AuthorTrust.KNOWN,
                    priority=Priority.NORMAL,
                )
            )
    return built


def pipeline_from(definition: Definition) -> Pipeline:
    """A pipeline wired to this factory's automations and principals."""
    return Pipeline(
        automations=automations_from(definition),
        directory=directory_from(definition),
    )


def _conductor_name(definition: Definition) -> str:
    conductor = definition.conductor()
    return conductor.name if conductor else "conductor"
