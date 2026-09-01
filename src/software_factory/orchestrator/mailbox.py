"""Agents addressing each other (PRD FR-3.7, FR-34).

Delegation existed and communication did not. A parent could spawn a child and read its
final output; it could not ask it a question, and the child could not report a blocker,
hand something back, or say it had found a reason to stop. So a hard task could not be
escalated -- it could only fail -- and a fleet was a set of monologues.

**The bus is the ledger.** That is the design, not a shortcut. The property a message bus
most needs is a total order shared with run lifecycle events: a parent must never observe a
child's `RUN_FINISHED` before the message that produced the result, or it will act on a
conclusion it has not been told. A separate bus has to invent a global sequence number to
get that. Here it is the counter the ledger already had, so the ordering is a consequence of
where messages are stored rather than a rule something has to enforce.

Four further consequences follow from the same choice, and each is a thing a side-channel
would have had to build:

* **Durable.** A message survives a restart because the ledger does.
* **Auditable.** "Who told this agent to stop" is answerable from the same log as everything
  else, by the same `sf ledger` commands, with the same hash chain behind it.
* **Resumable.** A recipient is an address, not a process. An agent whose run has finished
  is still addressable, and its next run reads what arrived while it was gone -- which is
  what makes a fleet recoverable rather than merely restartable.
* **Bounded.** Messages are subject to the same retention and segmentation as every other
  entry, so an inbox cannot grow forever in a place nobody is looking.

What this deliberately does *not* do is deliver. There is no push, no socket, no callback.
A recipient reads its inbox when it next runs, or an operator reads it with `sf agent
inbox`. A local-first factory has no server to hold a connection open, and a delivery
mechanism that only works while both parties are running is one that fails exactly when a
fleet is in trouble.
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from software_factory.errors import ErrorCode, FactoryError
from software_factory.ledger.entry import EntryType
from software_factory.memory.records import utc_now

#: The longest message body this will carry.
#:
#: Messages coordinate; they do not transport work. The reference model says the same thing
#: in prose -- "use messages for coordination signals rather than for piping full
#: transcripts around" -- and prose is not a limit. A parent that needs a child's whole
#: output reads the child's run; a parent that needs to know it is done reads a message.
MAX_BODY_CHARS = 4_000

#: How many messages one `inbox` call returns.
#:
#: An agent handed four hundred messages has been handed a research project. The count of
#: what was left behind is returned alongside, so "there is more" is visible rather than
#: silently true.
MAX_INBOX = 50

#: Where read cursors live, relative to the state directory.
CURSOR_FILE = "inbox-cursors.json"


class MessageError(FactoryError):
    """A message this factory will not send."""

    code = ErrorCode.INVALID_REQUEST


class Kind(enum.StrEnum):
    """What a message is for.

    A closed set, because the recipient is often a model and an open vocabulary is one
    every sender invents differently. Each value names an action the reader can take.
    """

    STATUS = "status"
    """Progress, needing no reply."""

    QUESTION = "question"
    """A reply is expected before the sender can continue."""

    ANSWER = "answer"
    """A reply to a question, carrying `in_reply_to`."""

    RESULT = "result"
    """What a piece of delegated work produced."""

    BLOCKED = "blocked"
    """The sender has stopped and says what would unblock it."""

    HANDOFF = "handoff"
    """Work passed to the recipient, with what the sender knows about it."""


#: Kinds that assert something has *ended*. Ordering matters most for these: a parent acting
#: on a result it was never sent is the failure the shared sequence exists to prevent.
TERMINAL_KINDS = frozenset({Kind.RESULT, Kind.BLOCKED})


@dataclass(frozen=True, slots=True)
class Message:
    """One message, as it sits in the ledger."""

    seq: int
    at: datetime
    sender: str
    recipient: str
    kind: Kind
    body: str
    run: str = ""
    """The run this message is about, when it is about one."""

    in_reply_to: int = 0
    """The sequence number of the message being answered, or 0."""

    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "at": self.at.isoformat(),
            "from": self.sender,
            "to": self.recipient,
            "kind": self.kind.value,
            "body": self.body,
            "run": self.run,
            "inReplyTo": self.in_reply_to,
            "truncated": self.truncated,
        }

    def render(self) -> str:
        reply = f" (replying to #{self.in_reply_to})" if self.in_reply_to else ""
        return f"#{self.seq} {self.kind.value} from {self.sender}{reply}: {self.body}"


@dataclass(slots=True)
class Mailbox:
    """Send and read messages through one factory's ledger."""

    ledger: Any
    state_dir: Path | None = None

    def send(
        self,
        *,
        sender: str,
        recipient: str,
        kind: Kind | str,
        body: str,
        run: str = "",
        in_reply_to: int = 0,
    ) -> Message:
        """Address one agent. Returns the message, including the sequence it landed at.

        Refuses an unaddressed or empty message rather than recording one. A message with
        no recipient is a note to nobody, and a message with no body is a notification that
        something happened without saying what -- both are worse than silence, because they
        occupy an inbox somebody has to read.
        """
        sender = sender.strip()
        recipient = recipient.strip()
        if not sender or not recipient:
            raise MessageError(
                "a message needs a sender and a recipient",
                remediation="Address it to an agent id. A note to nobody is not a message.",
            )
        try:
            kind = Kind(kind)
        except ValueError as exc:
            raise MessageError(
                f"{kind!r} is not a message kind",
                remediation=f"Use one of: {', '.join(k.value for k in Kind)}.",
            ) from exc
        text = body.strip()
        if not text:
            raise MessageError(
                "a message needs a body",
                remediation=(
                    "Say what happened. A notification that something occurred without "
                    "saying what still costs the reader their attention."
                ),
            )
        if kind is Kind.ANSWER and not in_reply_to:
            raise MessageError(
                "an answer must name the question it answers",
                remediation=(
                    "Pass the question's sequence number. An answer nobody can attach to a "
                    "question is a statement, and the asker is still waiting."
                ),
            )

        truncated = len(text) > MAX_BODY_CHARS
        entry = self.ledger.append(
            EntryType.AGENT_MESSAGE,
            actor=sender,
            subject=recipient,
            payload={
                "kind": kind.value,
                "body": text[:MAX_BODY_CHARS],
                "run": run,
                "inReplyTo": int(in_reply_to),
                "truncated": truncated,
            },
        )
        return Message(
            seq=int(getattr(entry, "seq", 0)),
            at=_at(getattr(entry, "ts", "")),
            sender=sender,
            recipient=recipient,
            kind=kind,
            body=text[:MAX_BODY_CHARS],
            run=run,
            in_reply_to=int(in_reply_to),
            truncated=truncated,
        )

    def inbox(
        self, recipient: str, *, after: int = 0, limit: int = MAX_INBOX
    ) -> tuple[list[Message], int]:
        """Messages addressed to `recipient` after sequence `after`.

        Returns the messages and how many were left behind. An agent handed four hundred
        messages has been handed a research project, and a truncation nobody is told about
        is a truncation that reads as "there was nothing else".
        """
        found = [
            message
            for message in self._all()
            if message.recipient == recipient and message.seq > after
        ]
        return found[:limit], max(0, len(found) - limit)

    def thread(self, seq: int) -> list[Message]:
        """A message and everything that replies to it, in order.

        One level, not a tree. A reply to a reply is rare between agents and a recursive
        walk over a log is a scan nobody bounded.
        """
        messages = self._all()
        root = next((m for m in messages if m.seq == seq), None)
        if root is None:
            return []
        return [root, *[m for m in messages if m.in_reply_to == seq]]

    def unanswered(self, recipient: str) -> list[Message]:
        """Questions addressed to `recipient` that nothing has answered.

        The list an operator actually wants: a fleet stalls when a question goes unanswered,
        and nothing else in the record distinguishes "waiting" from "finished".
        """
        messages = self._all()
        answered = {m.in_reply_to for m in messages if m.kind is Kind.ANSWER}
        return [
            m
            for m in messages
            if m.kind is Kind.QUESTION and m.recipient == recipient and m.seq not in answered
        ]

    # ------------------------------------------------------------------- read cursors

    def cursor(self, recipient: str) -> int:
        """How far this recipient has read. Zero when it has never read.

        Kept in the state directory rather than the ledger, and the distinction is
        deliberate: what was *sent* is a fact about the factory and belongs in the record,
        while what somebody has *read* is a local convenience. Writing read receipts into an
        append-only log would double its size to record something nobody audits.
        """
        return int(self._cursors().get(recipient, 0))

    def mark_read(self, recipient: str, seq: int) -> None:
        if self.state_dir is None:
            return
        cursors = self._cursors()
        cursors[recipient] = max(int(cursors.get(recipient, 0)), int(seq))
        path = Path(self.state_dir) / CURSOR_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cursors, indent=2), encoding="utf-8")

    def unread(self, recipient: str, *, limit: int = MAX_INBOX) -> tuple[list[Message], int]:
        return self.inbox(recipient, after=self.cursor(recipient), limit=limit)

    def _cursors(self) -> dict[str, int]:
        if self.state_dir is None:
            return {}
        path = Path(self.state_dir) / CURSOR_FILE
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A lost cursor re-reads messages. A cursor that raises loses the inbox.
            return {}
        return {str(k): int(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    # ------------------------------------------------------------------------ internals

    def _all(self) -> list[Message]:
        messages: list[Message] = []
        for entry in self.ledger.read():
            if entry.type is not EntryType.AGENT_MESSAGE:
                continue
            payload = entry.payload
            raw_kind = str(payload.get("kind", ""))
            if raw_kind not in set(Kind):
                continue
            messages.append(
                Message(
                    seq=int(entry.seq),
                    at=_at(entry.ts),
                    sender=str(entry.actor),
                    recipient=str(entry.subject),
                    kind=Kind(raw_kind),
                    body=str(payload.get("body", "")),
                    run=str(payload.get("run", "")),
                    in_reply_to=int(payload.get("inReplyTo", 0) or 0),
                    truncated=bool(payload.get("truncated", False)),
                )
            )
        return messages


@dataclass(frozen=True, slots=True)
class Lifecycle:
    """A run's observable state, folded from the ledger.

    Named states rather than a boolean, for the same reason `RunStatus` has no `unknown`: a
    parent deciding whether to wait, retry or give up needs to know *which* way a child
    ended, and "not running" answers none of those.
    """

    run: str
    work_item: str
    agent: str
    stage: str
    state: str
    seq: int
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "run": self.run,
            "workItem": self.work_item,
            "agent": self.agent,
            "stage": self.stage,
            "state": self.state,
            "seq": self.seq,
            "reason": self.reason,
        }


#: Run statuses mapped to the state a *watcher* cares about. `blocked` is separated from
#: `failed` because they need different responses: one waits for a person, the other does
#: not improve by waiting.
_WATCHER_STATE = {
    "completed": "succeeded",
    "gate_failed": "blocked",
    "budget_exceeded": "failed",
    "contract_violation": "blocked",
    "provider_failed": "failed",
    "setup_failed": "failed",
    "cancelled": "cancelled",
}


def lifecycle(entries: Any) -> list[Lifecycle]:
    """Every run's latest observable state, in ledger order.

    The ordering guarantee lives here rather than being asserted: because lifecycle entries
    and messages come from one log, a caller folding both sees them interleaved exactly as
    they happened. A watcher that reads to sequence N has, by construction, seen every
    message sent before every transition it knows about.
    """
    states: dict[str, Lifecycle] = {}
    for entry in entries:
        payload = entry.payload
        run = str(payload.get("run") or "")
        if entry.type is EntryType.RUN_STARTED:
            run = run or str(entry.subject)
            states[run] = Lifecycle(
                run=run,
                work_item=str(payload.get("workItem", entry.subject)),
                agent=str(payload.get("agent", entry.actor)),
                stage=str(payload.get("stage", "")),
                state="running",
                seq=int(entry.seq),
            )
        elif entry.type is EntryType.RUN_FINISHED and run in states:
            status = str(payload.get("status", ""))
            previous = states[run]
            states[run] = Lifecycle(
                run=run,
                work_item=previous.work_item,
                agent=previous.agent,
                stage=previous.stage,
                state=_WATCHER_STATE.get(status, "failed"),
                seq=int(entry.seq),
                reason=str(payload.get("reason") or ""),
            )
    return sorted(states.values(), key=lambda life: life.seq)


def _at(raw: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return utc_now()


@dataclass(slots=True)
class Conversation:
    """What one agent should be told at the start of a run.

    Assembled rather than dumped. An agent handed its whole message history spends its
    first turns reading; an agent handed the questions it owes answers to, and what arrived
    since it last ran, can act.
    """

    unread: list[Message] = field(default_factory=list)
    owed: list[Message] = field(default_factory=list)
    left_behind: int = 0

    @classmethod
    def for_agent(cls, mailbox: Mailbox, agent: str) -> Conversation:
        unread, left = mailbox.unread(agent)
        return cls(unread=unread, owed=mailbox.unanswered(agent), left_behind=left)

    @property
    def empty(self) -> bool:
        return not self.unread and not self.owed

    def render(self) -> str:
        """The text an agent sees. Empty when there is nothing, rather than a heading."""
        if self.empty:
            return ""
        lines: list[str] = []
        if self.owed:
            lines.append("Questions you have not answered:")
            lines.extend(f"  {m.render()}" for m in self.owed)
        fresh = [m for m in self.unread if m.kind is not Kind.QUESTION or m not in self.owed]
        if fresh:
            lines.append("Messages since your last run:")
            lines.extend(f"  {m.render()}" for m in fresh)
        if self.left_behind:
            lines.append(f"  ({self.left_behind} older messages not shown)")
        return "\n".join(lines)
