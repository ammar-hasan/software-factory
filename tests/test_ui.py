"""Computer use, under a declared contract (PRD FR-22.3, FR-12.1).

FR-22.3 has always said an agent may drive a browser and that the session is recorded.
Nothing behind it existed: no tool, no effect class, no grant, no session contract. These
tests are almost entirely about refusals, because a browser is unlike every other tool the
harness has — it reaches the network without touching anything the network policy inspects,
and it types wherever it is pointed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from software_factory.definition.models import Effect
from software_factory.runtime.ui import (
    UiContract,
    UiError,
    UiSession,
    UiUnavailableError,
    describe,
)


class FakeDriver:
    """Records what it was asked to do. Opens no browser."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def navigate(self, **kw):
        self.calls.append(("navigate", kw))
        return {"url": kw["url"]}

    def click(self, **kw):
        self.calls.append(("click", kw))
        return {"clicked": kw["selector"]}

    def type(self, **kw):
        self.calls.append(("type", kw))
        return {"typed": True}

    def observe(self, **kw):
        self.calls.append(("observe", kw))
        return {"title": "a page"}

    def close(self, **kw):
        self.calls.append(("close", kw))
        return {}


def contract(tmp_path: Path, **over) -> UiContract:
    base = {
        "origins": frozenset({"https://docs.example.test"}),
        "record_to": tmp_path / "session.json",
    }
    base.update(over)
    return UiContract(**base)


def session(tmp_path: Path, **over) -> UiSession:
    return UiSession(contract=contract(tmp_path, **over), driver=FakeDriver())


# ------------------------------------------------------------------ the effect class


def test_ui_is_its_own_effect_class() -> None:
    """Driving a browser is not a read and not an exec — it can click "delete".

    Classifying it as EXEC would mean every run that may run tests may also drive a browser.
    """
    assert Effect.UI.value == "ui"
    assert Effect.UI not in (Effect.READ, Effect.WRITE, Effect.EXEC, Effect.NETWORK)


# ------------------------------------------------------------------------ origins


def test_a_session_reaches_only_declared_origins(tmp_path: Path) -> None:
    s = session(tmp_path)

    assert s.navigate("https://docs.example.test/page")["url"].endswith("/page")

    with pytest.raises(UiError, match="not an origin"):
        s.navigate("https://elsewhere.test/")


def test_no_declared_origins_means_none_rather_than_all(tmp_path: Path) -> None:
    """The wrong default here would make forgetting to declare origins the most permissive
    configuration — for the one tool that can click "delete"."""
    s = session(tmp_path, origins=frozenset())

    with pytest.raises(UiError):
        s.navigate("https://docs.example.test/")


def test_a_file_url_is_refused(tmp_path: Path) -> None:
    """A browser pointed at the local disk is a read tool with none of the read tool's path
    checks."""
    with pytest.raises(UiError, match="may not navigate"):
        session(tmp_path).navigate("file:///etc/passwd")


def test_an_origin_check_is_not_a_prefix_check(tmp_path: Path) -> None:
    """`https://docs.example.test.evil.test` starts with the allowed origin."""
    with pytest.raises(UiError, match="not an origin"):
        session(tmp_path).navigate("https://docs.example.test.evil.test/")


# -------------------------------------------------------------------- credentials


def test_typing_a_secret_the_run_holds_is_refused(tmp_path: Path) -> None:
    """A session that can type is a session that can exfiltrate through a form, and "do not
    paste the token into a web page" is not a rule a prompt can enforce."""
    s = session(tmp_path, secrets=frozenset({"xoxb-a-real-looking-token"}))

    with pytest.raises(UiError, match="contains a secret"):
        s.type("#search", "xoxb-a-real-looking-token")


def test_a_secret_is_refused_whatever_the_field_is_called(tmp_path: Path) -> None:
    """The check is against the value, not the field name — a field called `q` is the
    interesting case, not one called `password`."""
    s = session(tmp_path, secrets=frozenset({"ghp-another-real-looking-token"}))

    with pytest.raises(UiError):
        s.type("#q", "look up ghp-another-real-looking-token for me")


def test_ordinary_text_types_fine(tmp_path: Path) -> None:
    s = session(tmp_path, secrets=frozenset({"xoxb-a-real-looking-token"}))

    assert s.type("#search", "byte order mark")["typed"] is True


def test_a_short_string_is_not_treated_as_a_secret(tmp_path: Path) -> None:
    """A control that fires on ordinary words is one an operator turns off."""
    s = session(tmp_path, secrets=frozenset({"abc"}))

    assert s.type("#search", "abc def")["typed"] is True


def test_a_session_may_not_sign_in_unless_granted(tmp_path: Path) -> None:
    """Reaching a page and holding an identity on it are different powers."""
    with pytest.raises(UiError, match="may not authenticate"):
        session(tmp_path).type("#user", "amaya", authenticating=True)

    allowed = session(tmp_path, may_authenticate=True)
    assert allowed.type("#user", "amaya", authenticating=True)["typed"] is True


def test_the_contract_never_prints_its_secrets(tmp_path: Path) -> None:
    text = repr(contract(tmp_path, secrets=frozenset({"xoxb-a-real-looking-token"})))

    assert "xoxb" not in text
    assert "1 held" in text


# --------------------------------------------------------------------- recording


def test_a_session_cannot_open_unrecorded(tmp_path: Path) -> None:
    """Elsewhere a missing recording degrades to a stated absence. Here the session is the
    only evidence there is: no diff to review, no command to re-run."""
    with pytest.raises(UiError, match="must be recorded"):
        UiSession(contract=UiContract(origins=frozenset({"https://x.test"})), driver=FakeDriver())


def test_closing_writes_the_recording(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.navigate("https://docs.example.test/a")
    s.click("#next")
    s.close()

    recorded = json.loads((tmp_path / "session.json").read_text())
    assert [a["kind"] for a in recorded] == ["navigate", "click", "close"]


def test_the_recording_does_not_replay_what_was_typed(tmp_path: Path) -> None:
    """A recording that replays a password is a recording that must be handled like one."""
    s = session(tmp_path, may_authenticate=True)
    s.type("#user", "amaya-is-a-long-value", authenticating=True)
    s.close()

    recorded = (tmp_path / "session.json").read_text()
    assert "amaya-is-a-long-value" not in recorded
    assert "21 chars" in recorded


# ------------------------------------------------------------------------ bounds


def test_a_session_is_bounded_by_actions(tmp_path: Path) -> None:
    """A loop that clicks forever spends forever and produces a recording nobody watches."""
    s = session(tmp_path, max_actions=3)
    for _ in range(3):
        s.observe()

    with pytest.raises(UiError, match="taken its 3 actions"):
        s.observe()


def test_observations_count_against_the_bound(tmp_path: Path) -> None:
    """An agent looping on observe is an agent that is stuck, and a bound counting only the
    actions we consider dangerous cannot see that."""
    s = session(tmp_path, max_actions=2)
    s.observe()
    s.observe()

    with pytest.raises(UiError):
        s.navigate("https://docs.example.test/")


def test_a_closed_session_cannot_be_reused(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.close()

    with pytest.raises(UiError, match="closed"):
        s.navigate("https://docs.example.test/")


# ------------------------------------------------------------------- availability


def test_a_factory_with_no_driver_says_so_rather_than_failing_to_load(tmp_path: Path) -> None:
    """Declared and unavailable, not silently absent."""
    s = UiSession(contract=contract(tmp_path), driver=None)

    with pytest.raises(UiUnavailableError, match="no browser driver"):
        s.navigate("https://docs.example.test/")


def test_what_the_agent_is_told_is_generated_from_the_contract(tmp_path: Path) -> None:
    """Generated rather than written, so the text cannot overstate grants in force."""
    told = describe(contract(tmp_path))

    assert "docs.example.test" in told
    assert "may not sign in" in told
    assert "cannot run unrecorded" in told

    permissive = describe(contract(tmp_path, may_authenticate=True))
    assert "may sign in" in permissive


# ------------------------------------------------------------- the tool surface


def _registry(tmp_path: Path, *, with_ui: bool):
    """A registry built the way a run builds one, with or without a UI session."""
    from software_factory.runtime.executor import LocalExecutor, SandboxLevel, SandboxPolicy
    from software_factory.runtime.tools import build_registry
    from software_factory.runtime.workspace import Workspace

    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    workspace = Workspace(root=root, run_id="run-1", base_commit="deadbeef")
    executor = LocalExecutor(
        SandboxPolicy(workspace=root, wall_clock_s=20), level=SandboxLevel.PROCESS
    )
    return build_registry(workspace, executor, ui_session=session(tmp_path) if with_ui else None)


def registry_with_ui(tmp_path: Path):
    return _registry(tmp_path, with_ui=True)


def test_a_run_without_a_session_is_not_offered_the_tools(tmp_path: Path) -> None:
    """Computer use is a granted capability, not a default one.

    Registering the tools and relying on the grant check to refuse them would still put a
    browser in the pack for a model to reason about, on every run that never needed one.
    """
    registry = _registry(tmp_path, with_ui=False)

    assert registry.get("ui.navigate") is None


def test_the_ui_tools_are_declared_with_the_ui_effect(tmp_path: Path) -> None:
    """A static audit and the running registry must not disagree."""
    from software_factory.runtime.tools import BUILTIN_TOOL_EFFECTS

    registry = registry_with_ui(tmp_path)
    ui_tools = ["ui.click", "ui.close", "ui.navigate", "ui.observe", "ui.type"]

    for name in ui_tools:
        assert registry.get(name) is not None, name
        assert BUILTIN_TOOL_EFFECTS[name] is Effect.UI
        assert registry.get(name).effect is Effect.UI


def test_a_refused_navigation_is_denied_rather_than_an_error(tmp_path: Path) -> None:
    """The agent should read a contract refusal as a boundary, not as something to retry
    differently."""
    from software_factory.harness.tools import FailureKind

    registry = registry_with_ui(tmp_path)
    result = registry.get("ui.navigate").handler({"url": "https://elsewhere.test/"})

    assert getattr(result, "kind", None) is FailureKind.DENIED
    assert "not an origin" in result.message


def test_signing_in_is_declared_rather_than_inferred_from_a_field_name(tmp_path: Path) -> None:
    """A model that does not claim it is authenticating must not be able to sign in by
    choosing a field called `#password`."""
    registry = registry_with_ui(tmp_path)
    schema = registry.get("ui.type").input_schema

    assert "authenticating" in schema["properties"]
    assert "authenticating" not in schema["required"]
