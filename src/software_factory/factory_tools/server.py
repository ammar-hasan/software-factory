"""The factory's own tool surface (PRD FR-19.1-19.9).

Any tool-capable coding agent can work with the factory in both directions: send work in, and
take work out. The surface is defined here as data -- name, description, schema, handler --
so it can be served over MCP, over a local socket (FR-19.8), or called directly, without the
tool definitions moving.

Four constraints shape it, and each is a thing this module refuses to do:

* **One record** (FR-19.3). Work continued locally lands on the *same* work item with the
  same history. `pick_up` does not create anything; it returns what already exists.
* **The server never modifies the caller's files** (FR-19.4). `pick_up` returns setup
  *guidance* -- commands the caller's own agent runs -- and this process runs none of them.
  A tool server that writes into a caller's checkout is a tool server nobody can trust with
  a repository they care about.
* **Picking up does not claim** (FR-19.5). Every response that names a work item also names
  its active runs and leases, so a second actor sees the situation rather than discovering it
  by collision.
* **Unpushed work is invisible** (FR-19.6). `hand_back` refuses a handoff with no branch or
  change reference, and says why: the factory cannot see a commit that exists only on one
  laptop, and accepting the handoff would record work that nobody else can find.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from software_factory.definition.models import Stage
from software_factory.factory_tools.leases import ActionClass, Held, LeaseBook
from software_factory.orchestrator.workitem import WorkItem


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool the factory offers, and the guidance a caller needs to use it well.

    ``guidance`` exists because of FR-19.9: the server publishes its own usage guidance so a
    calling agent picks up the correct workflow without an operator explaining it. A schema
    says what is accepted; guidance says what to do with it and when not to.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., dict[str, Any]]
    guidance: str = ""
    external: bool = False
    """True when calling this produces something outside the factory. These are the tools
    that take a lease (FR-19.5a)."""


@dataclass(frozen=True, slots=True)
class SetupGuidance:
    """What a caller's own agent should run to continue a work item locally (FR-19.4).

    Commands, not actions. This process executes none of them, and the distinction is the
    whole reason a caller can point this at a repository they care about.
    """

    work_item_id: str
    branch: str
    base_commit: str
    commands: tuple[str, ...]
    context: str
    warning: str = (
        "Picking work up does not claim, lock, or pause it. Another actor may be working on "
        "the same item; call `announce_pickup` to say you are, and check `active_runs` "
        "before doing anything externally visible."
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "workItem": self.work_item_id,
            "branch": self.branch,
            "baseCommit": self.base_commit,
            "commands": list(self.commands),
            "context": self.context,
            "warning": self.warning,
        }


@dataclass(slots=True)
class FactoryToolServer:
    """The tool surface over one factory's work items.

    Holds no transport. MCP, a local socket, and a direct call are three ways to reach the
    same handlers, and putting a transport in here would make the local-mode requirement
    (FR-19.8) a second implementation rather than a second binding.
    """

    factory_name: str
    work_items: dict[str, WorkItem] = field(default_factory=dict)
    leases: LeaseBook = field(default_factory=LeaseBook)
    active_runs: dict[str, list[str]] = field(default_factory=dict)
    notification_routes: tuple[str, ...] = ()
    conversation: dict[str, list[dict[str, str]]] = field(default_factory=dict)

    # ------------------------------------------------------------------ read surface

    def list_work_items(self, *, stage: str | None = None) -> dict[str, Any]:
        """Work items, optionally filtered by stage."""
        items = sorted(self.work_items.values(), key=lambda item: item.id)
        if stage is not None:
            items = [item for item in items if item.stage.value == stage]
        return {
            "factory": self.factory_name,
            "workItems": [self._summarise(item) for item in items],
        }

    def search_work_items(self, query: str) -> dict[str, Any]:
        """Substring search over title and request. Deliberately simple: a caller that needs
        more can list and filter, and a clever search here would be a second ranking model
        to keep honest."""
        needle = query.strip().lower()
        matched = [
            item
            for item in sorted(self.work_items.values(), key=lambda i: i.id)
            if needle in item.title.lower() or needle in item.request.lower()
        ]
        return {"query": query, "workItems": [self._summarise(item) for item in matched]}

    def get_work_item(self, work_item_id: str) -> dict[str, Any]:
        item = self.work_items.get(work_item_id)
        if item is None:
            return _unknown(work_item_id)
        return {
            "workItem": self._summarise(item),
            "history": [t.render() for t in item.history],
            "activeRuns": self.active_runs.get(work_item_id, []),
            "leases": [lease.describe() for lease in self.leases.active_for(work_item_id)],
        }

    def read_conversation(self, work_item_id: str) -> dict[str, Any]:
        if work_item_id not in self.work_items:
            return _unknown(work_item_id)
        return {
            "workItem": work_item_id,
            "messages": list(self.conversation.get(work_item_id, [])),
        }

    def list_notification_routes(self) -> dict[str, Any]:
        """FR-19.7: notifications are best-effort by design and must be described as such."""
        return {
            "routes": list(self.notification_routes),
            "note": (
                "Notifications are best-effort. A route that is down drops the notice; the "
                "work item's own state is the record, not the notification."
            ),
        }

    # ----------------------------------------------------------------- write surface

    def pick_up(
        self, work_item_id: str, *, worktree_root: str = "../factory-worktrees"
    ) -> dict[str, Any]:
        """Setup guidance for continuing a work item locally (FR-19.3, FR-19.4).

        Creates nothing and claims nothing. The same work item, the same history, and a set
        of commands the caller's own agent runs.
        """
        item = self.work_items.get(work_item_id)
        if item is None:
            return _unknown(work_item_id)

        branch = f"factory/{work_item_id}"
        guidance = SetupGuidance(
            work_item_id=work_item_id,
            branch=branch,
            base_commit=item.base_commit or "HEAD",
            commands=(
                f"git worktree add {worktree_root}/{work_item_id} -b {branch} "
                f"{item.base_commit or 'HEAD'}",
                f"cd {worktree_root}/{work_item_id}",
            ),
            context=f"{item.title}\n\n{item.request}",
        )
        return {
            "setup": guidance.as_dict(),
            "activeRuns": self.active_runs.get(work_item_id, []),
            "leases": [lease.describe() for lease in self.leases.active_for(work_item_id)],
        }

    def announce_pickup(self, work_item_id: str, *, actor: str, intent: str) -> dict[str, Any]:
        """Say you are working on this. One call, as FR-19.5 requires.

        Not a lock. It writes a message into the conversation so a second actor reading the
        work item sees a person rather than an absence.
        """
        if work_item_id not in self.work_items:
            return _unknown(work_item_id)
        message = {"actor": actor, "kind": "pickup", "text": intent}
        self.conversation.setdefault(work_item_id, []).append(message)
        return {
            "announced": True,
            "workItem": work_item_id,
            "othersActive": self.active_runs.get(work_item_id, []),
        }

    def message_conductor(self, work_item_id: str, *, actor: str, text: str) -> dict[str, Any]:
        if work_item_id not in self.work_items:
            return _unknown(work_item_id)
        self.conversation.setdefault(work_item_id, []).append(
            {"actor": actor, "kind": "message", "text": text}
        )
        return {"delivered": True, "workItem": work_item_id}

    def hand_back(
        self,
        work_item_id: str,
        *,
        actor: str,
        branch: str = "",
        change_ref: str = "",
        changed: str = "",
        validated: str = "",
        remaining: str = "",
    ) -> dict[str, Any]:
        """Return work to the factory (FR-19.6).

        Refuses without a pushed branch or change reference, and says why rather than
        recording a handoff of something nobody else can see. Takes a HANDOFF lease, because
        two actors handing the same item back produces two handoffs.
        """
        item = self.work_items.get(work_item_id)
        if item is None:
            return _unknown(work_item_id)

        if not (branch.strip() or change_ref.strip()):
            return {
                "accepted": False,
                "code": "handoff.nothing_pushed",
                "message": "no branch or change reference was supplied",
                "remediation": (
                    "Push your branch and hand back its name, or open a change and hand back "
                    "its reference. The factory cannot see a commit that exists only on your "
                    "machine, and recording the handoff anyway would record work nobody else "
                    "can find."
                ),
            }
        if not changed.strip():
            return {
                "accepted": False,
                "code": "handoff.no_summary",
                "message": "a handoff must say what changed",
                "remediation": (
                    "State what you changed, what you validated, and what remains. The next "
                    "actor reads this instead of the diff."
                ),
            }

        lease = self.leases.acquire(
            work_item_id, ActionClass.HANDOFF, holder=actor, intent="handing work back"
        )
        if isinstance(lease, Held):
            return {
                "accepted": False,
                "code": "handoff.leased",
                "message": lease.message,
                "remediation": lease.remediation,
            }

        self.conversation.setdefault(work_item_id, []).append(
            {
                "actor": actor,
                "kind": "handoff",
                "text": (
                    f"changed: {changed}\nvalidated: {validated or 'nothing stated'}\n"
                    f"remaining: {remaining or 'nothing stated'}"
                ),
            }
        )
        return {
            "accepted": True,
            "workItem": work_item_id,
            "branch": branch,
            "changeRef": change_ref,
            "note": (
                "Recorded on the same work item with the same history: there is no second "
                "identity for locally continued work."
            ),
        }

    # ------------------------------------------------------------------- tool specs

    def specs(self) -> list[ToolSpec]:
        """The published surface, with its own usage guidance (FR-19.2, FR-19.9)."""
        return [
            ToolSpec(
                name="factory.list_work_items",
                description="List this factory's work items, optionally by stage.",
                input_schema={
                    "type": "object",
                    "properties": {"stage": {"type": "string", "enum": [s.value for s in Stage]}},
                },
                handler=self.list_work_items,
                guidance="Start here. Stages are the factory's, not yours; do not invent one.",
            ),
            ToolSpec(
                name="factory.search_work_items",
                description="Search work items by title and request text.",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                handler=self.search_work_items,
            ),
            ToolSpec(
                name="factory.get_work_item",
                description="One work item, its history, its active runs and its leases.",
                input_schema={
                    "type": "object",
                    "properties": {"work_item_id": {"type": "string"}},
                    "required": ["work_item_id"],
                },
                handler=self.get_work_item,
                guidance=(
                    "Read `activeRuns` and `leases` before doing anything externally "
                    "visible. Nothing here locks; this is how you find out."
                ),
            ),
            ToolSpec(
                name="factory.pick_up",
                description="Setup guidance for continuing a work item locally.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "work_item_id": {"type": "string"},
                        "worktree_root": {"type": "string"},
                    },
                    "required": ["work_item_id"],
                },
                handler=self.pick_up,
                guidance=(
                    "Returns commands for *you* to run. This server never touches your "
                    "files. Picking up does not claim the item -- call "
                    "`factory.announce_pickup` so others can see you."
                ),
            ),
            ToolSpec(
                name="factory.announce_pickup",
                description="Say you are working on a work item. Not a lock.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "work_item_id": {"type": "string"},
                        "actor": {"type": "string"},
                        "intent": {"type": "string"},
                    },
                    "required": ["work_item_id", "actor", "intent"],
                },
                handler=self.announce_pickup,
            ),
            ToolSpec(
                name="factory.read_conversation",
                description="The conversation on a work item.",
                input_schema={
                    "type": "object",
                    "properties": {"work_item_id": {"type": "string"}},
                    "required": ["work_item_id"],
                },
                handler=self.read_conversation,
            ),
            ToolSpec(
                name="factory.message_conductor",
                description="Send a message to the conductor on a work item.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "work_item_id": {"type": "string"},
                        "actor": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["work_item_id", "actor", "text"],
                },
                handler=self.message_conductor,
            ),
            ToolSpec(
                name="factory.list_notification_routes",
                description="Where notifications can be sent. Best-effort by design.",
                input_schema={"type": "object", "properties": {}},
                handler=self.list_notification_routes,
            ),
            ToolSpec(
                name="factory.hand_back",
                description="Return work to the factory with a pushed branch or change.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "work_item_id": {"type": "string"},
                        "actor": {"type": "string"},
                        "branch": {"type": "string"},
                        "change_ref": {"type": "string"},
                        "changed": {"type": "string"},
                        "validated": {"type": "string"},
                        "remaining": {"type": "string"},
                    },
                    "required": ["work_item_id", "actor", "changed"],
                },
                handler=self.hand_back,
                external=True,
                guidance=(
                    "Push first. Unpushed work is invisible to the factory, and this refuses "
                    "rather than recording a handoff nobody else can find."
                ),
            ),
        ]

    def _summarise(self, item: WorkItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "title": item.title,
            "stage": item.stage.value,
            "workClass": item.work_class.value,
            "origin": item.source.ref,
            "activeRuns": self.active_runs.get(item.id, []),
        }


def _unknown(work_item_id: str) -> dict[str, Any]:
    return {
        "error": "work_item.unknown",
        "message": f"no work item {work_item_id!r} in this factory",
        "remediation": "Call `factory.list_work_items` to see what exists.",
    }
