"""Computer use: driving a browser under a declared contract (PRD FR-22.3, FR-12.1).

FR-22.3 has always said an agent may drive a browser or desktop session and that the
session is recorded. Nothing behind it existed: no tool, no effect class, no grant, no
session contract. That is the worst state for a capability to be in — the document promises
it, `sf audit` reports an agent as able to do something with no grant model behind it, and
the capability arrives one day without any of the controls the rest of the system has.

Four decisions carry this, and each is about the ways a browser is unlike every other tool
the harness already has.

**`UI` is its own effect class.** Driving a browser is not a read and not an exec. It can
click "delete". An effect model that cannot express that has a hole in the middle of it, and
classifying UI as `EXEC` would mean every run that may run tests may also drive a browser.

**A session contract, on the same footing as `BlastRadius`.** A browser reaches the network
without touching any of the tools the network policy inspects, so the network policy does
not see it. The contract states which origins the session may reach and whether it may
authenticate at all, and both are enforced before navigation rather than described in a
prompt.

**Credentials are refused by name, not by hope.** A session that can type is a session that
can exfiltrate through a form. `ui.type` refuses any value that matches a secret the run
holds, whatever the field is called, because "do not type the token into a web page" is not
a rule a prompt can enforce.

**The recording is not optional.** Everywhere else in this design recording degrades to a
stated absence. Here the session *is* the evidence: there is no diff to review, no command
to re-run, and an unrecorded UI session is an action nobody can check. A session that cannot
record does not open.

The driver is optional at import. A factory with no browser installed must still load, still
validate, and still report the capability as unavailable with a reason — rather than failing
to start because of a feature nobody in that factory uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from software_factory.errors import ErrorCode, FactoryError
from software_factory.memory.records import utc_now

#: Schemes a session may navigate to. `file:` is excluded deliberately: a browser pointed at
#: the local disk is a read tool with none of the read tool's path checks.
ALLOWED_SCHEMES = frozenset({"http", "https"})

#: How many actions one session may take before it must be reopened.
#:
#: A bound, like every other one here, because a loop that clicks forever is a loop that
#: spends forever and produces a recording nobody will watch.
MAX_ACTIONS = 200

#: The shortest secret worth scanning typed text for.
#:
#: Below this the match rate against ordinary prose is high enough that the check would fire
#: on words, and a control that cries wolf is one an operator turns off.
MIN_SECRET_LENGTH = 8


class UiError(FactoryError):
    """A UI session was asked for something its contract does not allow."""

    code = ErrorCode.NOT_AUTHORIZED


class UiUnavailableError(FactoryError):
    """No driver is installed, so this factory cannot drive a browser."""

    code = ErrorCode.FEATURE_NOT_AVAILABLE


@dataclass(frozen=True, slots=True)
class UiContract:
    """What one session may reach, and what it may do there.

    Beside `BlastRadius` rather than inside it, because the questions are different: a blast
    radius is about paths and effects in a workspace, and this is about origins and identity
    on somebody else's machine.
    """

    origins: frozenset[str] = frozenset()
    """Origins this session may navigate to, as `scheme://host[:port]`.

    Empty means none. A session with no declared origins is not a session with unrestricted
    access -- that default would make forgetting to declare origins the most permissive
    configuration, which is the wrong direction for the one tool that can click "delete"."""

    may_authenticate: bool = False
    """Whether the session may sign in at all.

    Separate from the origin list because reaching a page and holding an identity on it are
    different powers, and most research tasks need only the first."""

    secrets: frozenset[str] = frozenset()
    """Secret *values* the run holds, so typed text can be checked against them.

    Held here only to be refused. Nothing reads them out, `__repr__` does not print them,
    and the check is a comparison rather than a log."""

    record_to: Path | None = None
    """Where the session recording is written. Required: see the module docstring."""

    max_actions: int = MAX_ACTIONS

    def __repr__(self) -> str:
        return (
            f"UiContract(origins={sorted(self.origins)!r}, "
            f"may_authenticate={self.may_authenticate}, secrets=<{len(self.secrets)} held>, "
            f"record_to={self.record_to!r}, max_actions={self.max_actions})"
        )

    def check_origin(self, url: str) -> None:
        """Refuse a navigation the contract does not cover."""
        parsed = urlparse(url)
        if parsed.scheme not in ALLOWED_SCHEMES:
            raise UiError(
                f"a session may not navigate to a {parsed.scheme or 'schemeless'} URL",
                remediation=(
                    "Use http or https. A browser pointed at the local disk is a read tool "
                    "with none of the read tool's path checks."
                ),
            )
        origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        if origin not in {o.lower() for o in self.origins}:
            raise UiError(
                f"{origin} is not an origin this session may reach",
                remediation=(
                    "Declare it in the session contract. Allowed: "
                    + (", ".join(sorted(self.origins)) or "none")
                ),
            )

    def check_text(self, text: str) -> None:
        """Refuse typing a value that matches a secret this run holds.

        Whatever the field is called. A session that can type is a session that can
        exfiltrate through a form, and "do not paste the token into a web page" is not a
        rule a prompt can enforce.
        """
        for secret in self.secrets:
            if len(secret) >= MIN_SECRET_LENGTH and secret in text:
                raise UiError(
                    "this text contains a secret this run holds, so it will not be typed",
                    remediation=(
                        "A UI session may not enter credentials it was not granted. If the "
                        "session genuinely needs to sign in, grant it explicitly."
                    ),
                )

    def check_authentication(self) -> None:
        if not self.may_authenticate:
            raise UiError(
                "this session may not authenticate",
                remediation=(
                    "Set `may_authenticate` on the session contract if signing in is part "
                    "of the task. Most research tasks need only to reach a page."
                ),
            )


@dataclass(slots=True)
class UiAction:
    """One recorded action. The recording is a list of these plus whatever the driver kept."""

    kind: str
    detail: str
    at: datetime = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "detail": self.detail, "at": self.at.isoformat()}


@dataclass(slots=True)
class UiSession:
    """A driven browser session, under a contract.

    The driver is injected. A test drives a fake and exercises every refusal without a
    browser; a run drives Playwright. The contract checks are in *this* class rather than in
    the driver, so a second driver cannot arrive without them.
    """

    contract: UiContract
    driver: Any = None
    actions: list[UiAction] = field(default_factory=list)
    closed: bool = False

    def __post_init__(self) -> None:
        if self.contract.record_to is None:
            raise UiError(
                "a UI session must be recorded",
                remediation=(
                    "Set `record_to` on the contract. Elsewhere a missing recording degrades "
                    "to a stated absence; here the session is the only evidence there is, "
                    "and an unrecorded one is an action nobody can review."
                ),
            )

    # ------------------------------------------------------------------ the tool surface

    def navigate(self, url: str) -> dict[str, Any]:
        self._ensure_open()
        self.contract.check_origin(url)
        self._record("navigate", url)
        return self._drive("navigate", url=url)

    def click(self, selector: str) -> dict[str, Any]:
        self._ensure_open()
        self._record("click", selector)
        return self._drive("click", selector=selector)

    def type(self, selector: str, text: str, *, authenticating: bool = False) -> dict[str, Any]:
        self._ensure_open()
        if authenticating:
            self.contract.check_authentication()
        self.contract.check_text(text)
        # The recording keeps *what was typed where* only for non-secret text; a recording
        # that replays a password is a recording that must be handled like one.
        self._record("type", f"{selector} <- {len(text)} chars")
        return self._drive("type", selector=selector, text=text)

    def observe(self) -> dict[str, Any]:
        """Read the page. The one action with no side effect, and still counted.

        Counted because an agent that loops on observe is an agent that is stuck, and a
        bound that only counts the actions we consider dangerous cannot see that.
        """
        self._ensure_open()
        self._record("observe", "")
        return self._drive("observe")

    def close(self) -> dict[str, Any]:
        if self.closed:
            return {"closed": True, "actions": len(self.actions)}
        self._record("close", "")
        self.closed = True
        result = self._drive("close")
        self._write_recording()
        return {**result, "closed": True, "actions": len(self.actions)}

    # ------------------------------------------------------------------------ internals

    def _ensure_open(self) -> None:
        if self.closed:
            raise UiError(
                "this session is closed",
                remediation="Open a new session; a closed one cannot be reused.",
            )
        if len(self.actions) >= self.contract.max_actions:
            raise UiError(
                f"this session has taken its {self.contract.max_actions} actions",
                remediation=(
                    "A loop that clicks forever spends forever and produces a recording "
                    "nobody will watch. Close the session and state what you learned."
                ),
            )

    def _record(self, kind: str, detail: str) -> None:
        self.actions.append(UiAction(kind=kind, detail=detail))

    def _drive(self, action: str, **kwargs: Any) -> dict[str, Any]:
        if self.driver is None:
            raise UiUnavailableError(
                "no browser driver is available in this factory",
                remediation=(
                    "Install a driver, or run this work on a runner that has one. The "
                    "capability is declared and unavailable rather than silently absent."
                ),
            )
        return dict(getattr(self.driver, action)(**kwargs) or {})

    def _write_recording(self) -> None:
        import json

        path = self.contract.record_to
        assert path is not None  # checked in __post_init__
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([a.as_dict() for a in self.actions], indent=2), encoding="utf-8")


#: A conservative reading of what looks like a credential field, for the *advisory* warning
#: in `describe`. Never used to allow anything -- the enforcement is `check_text`, which
#: compares against secrets the run actually holds rather than guessing from a name.
_CREDENTIAL_FIELD = re.compile(r"pass(word|wd)|secret|token|api[-_]?key|otp|2fa", re.I)


def describe(contract: UiContract) -> str:
    """What the agent is told it may do, generated from the contract.

    Generated rather than written, for the same reason as the courage clause: text can only
    ever describe grants that are really in force, so it cannot overstate them.
    """
    origins = ", ".join(sorted(contract.origins)) or "no origins"
    lines = [
        f"You may drive a browser session reaching {origins}.",
        (
            "You may sign in where the task requires it."
            if contract.may_authenticate
            else "You may not sign in. Reaching a page is allowed; holding an identity on it is not."
        ),
        (
            "Text you type is checked against the secrets this run holds and will be "
            "refused if it matches one, whatever the field is called."
        ),
        f"The session is recorded to {contract.record_to}, and it cannot run unrecorded.",
        f"At most {contract.max_actions} actions, including observations.",
    ]
    return " ".join(lines)


def available() -> tuple[bool, str]:
    """Whether this machine can drive a browser, and why not when it cannot.

    A tuple rather than a bool for the same reason `HealthReport` is not a bool: "no" needs
    a reason an operator can act on.
    """
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "playwright is not installed in this environment"
    return True, ""
