"""Integrations: concrete adapters for the contract in `intake.adapters` (PRD FR-18).

Kept in their own package, importing nothing from the orchestrator, because FR-18.2 says
adding an integration must not touch orchestration code -- and the only way that stays true
is if the dependency points one way. An adapter satisfies a structural `Protocol`; it never
imports the thing that will call it.
"""

from software_factory.integrations.slack import (
    SlackAdapter,
    SlackCredentials,
    SlackError,
    SlackSignatureError,
    challenge_for,
    verify_signature,
)

__all__ = [
    "SlackAdapter",
    "SlackCredentials",
    "SlackError",
    "SlackSignatureError",
    "challenge_for",
    "verify_signature",
]
