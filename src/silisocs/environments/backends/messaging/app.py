"""Direct agent-to-agent messaging environment backend.

The default communication channel for multi-agent experiments: agents exchange
private messages (and optional broadcasts) with each other, mediated by the
backend like every other interaction in silisocs — there is no side channel.
Negotiation, coordination, and cheap-talk studies compose it with a game
backend through multi-GM flow chains ("talk, then move": chain the flow through
a messaging GM before the game GM), or run it standalone.

Design notes:

* **Delivery is observational.** ``SEND_MESSAGE`` appends to the shared message
  log; a recipient sees the message in their next observation (the generic
  ``app_observation`` component). Nothing is pushed mid-step, so ordering stays
  deterministic under any executor.
* **Privacy is a rendering rule.** Every message is stored once; ``observe``
  shows an agent only what they sent, what was sent to them, and broadcasts.
  The committed log records everything (the experimenter sees all traffic).
* **Analysis comes free.** ``SEND_MESSAGE`` is tagged ``interaction.directed``
  with a ``network.target_actor`` field, so the existing interaction-network
  panel draws the who-messages-whom graph for any run of this backend without
  panel changes.
* **No database.** State is an in-memory message list snapshotted via
  ``get_state``/``set_state`` (``provides_checkpoint_state=True``), like the
  other reference backends (``resource_market``, ``virtual_space``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from silisocs.environments.backends.base import ActionResult, BackendApp, app_action

_BROADCAST = "*"


@dataclass
class MessagingApp(BackendApp):
    """Private direct messages plus optional broadcasts between named agents."""

    action_logger: Any = None
    provides_checkpoint_state = True
    app_description: str = "A direct-messaging channel between the participants."
    # How many delivered messages an observation shows (most recent first kept).
    history_window: int = 20
    # Maximum accepted message length; longer submissions are rejected, not cut,
    # so the committed log never contains text the recipient didn't see.
    max_message_length: int = 2000

    def __post_init__(self) -> None:
        super().__init__()
        self._participants: list[str] = []
        # Append-only shared log; privacy is applied when rendering observe().
        # Each row: {"round", "sender", "recipient" (_BROADCAST for all), "text"}.
        self._messages: list[dict[str, Any]] = []

    def name(self) -> str:
        return "messaging"

    def description(self) -> str:
        return self.app_description

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del kwargs
        self._participants = [str(name) for name in agent_names]
        self._messages = []

    # ---- actions --------------------------------------------------------

    def _ensure_participant(self, agent_name: str) -> str | None:
        if agent_name not in self._participants:
            return f"Unknown participant: {agent_name}"
        return None

    def _validate_text(self, text: str) -> str | None:
        if not str(text).strip():
            return "Message text must not be empty."
        if len(str(text)) > int(self.max_message_length):
            return (
                f"Message is too long ({len(str(text))} characters; "
                f"limit {int(self.max_message_length)})."
            )
        return None

    @app_action(
        selectable_name="SEND_MESSAGE",
        description="Send a private message to one named participant.",
        tags=("interaction.directed", "communication.message"),
        fields={
            "network.target_actor": "recipient",
            "communication.text": "text",
            "game.round": "round",
        },
    )
    def send_message(self, agent_name: str, recipient: str = "", text: str = "") -> ActionResult:
        """Deliver a private message; only the recipient (and sender) will see it."""
        error = (
            self._ensure_participant(agent_name)
            or self._ensure_recipient(agent_name, recipient)
            or self._validate_text(text)
        )
        if error:
            return ActionResult(error, committed=False)
        row = {
            "round": self.current_episode(),
            "sender": agent_name,
            "recipient": str(recipient),
            "text": str(text),
        }
        self._messages.append(row)
        return ActionResult(
            f"Message sent to {recipient}.",
            data=dict(row),
        )

    def _ensure_recipient(self, agent_name: str, recipient: str) -> str | None:
        if not str(recipient).strip():
            return "Name a recipient for the message."
        if recipient == agent_name:
            return "You cannot message yourself."
        if recipient not in self._participants:
            known = ", ".join(name for name in self._participants if name != agent_name)
            return f"Unknown recipient: {recipient}. You can message: {known}."
        return None

    @app_action(
        selectable_name="BROADCAST",
        description="Send a message that every participant will see.",
        tags=("communication.message", "communication.broadcast"),
        fields={"communication.text": "text", "game.round": "round"},
    )
    def broadcast_message(self, agent_name: str, text: str = "") -> ActionResult:
        """Deliver a message to everyone (shown to all participants)."""
        error = self._ensure_participant(agent_name) or self._validate_text(text)
        if error:
            return ActionResult(error, committed=False)
        row = {
            "round": self.current_episode(),
            "sender": agent_name,
            "recipient": _BROADCAST,
            "text": str(text),
        }
        self._messages.append(row)
        return ActionResult(
            "Message broadcast to all participants.",
            data=dict(row),
        )

    # ---- observation ----------------------------------------------------

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        """Render the actor's mailbox: their private threads and all broadcasts."""
        del kwargs
        others = [name for name in self._participants if name != actor_name]
        header = (
            "MESSAGES\n"
            f"You are {actor_name}. You can send private messages to: "
            f"{', '.join(others) if others else 'nobody (no other participants)'}."
        )
        visible = [
            row
            for row in self._messages
            if row["recipient"] == _BROADCAST or actor_name in (row["sender"], row["recipient"])
        ]
        if self.history_window and self.history_window > 0:
            visible = visible[-int(self.history_window) :]
        if not visible:
            return f"{header}\n\nInbox: no messages yet."
        lines = ["Inbox (oldest first):"]
        for row in visible:
            if row["recipient"] == _BROADCAST:
                origin = (
                    "You broadcast"
                    if row["sender"] == actor_name
                    else (f"{row['sender']} broadcast")
                )
                lines.append(f"  [round {row['round'] + 1}] {origin}: {row['text']}")
            elif row["sender"] == actor_name:
                lines.append(
                    f"  [round {row['round'] + 1}] You -> {row['recipient']}: {row['text']}"
                )
            else:
                lines.append(f"  [round {row['round'] + 1}] {row['sender']} -> You: {row['text']}")
        return f"{header}\n\n" + "\n".join(lines)

    # ---- checkpoint contract -------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return serializable messaging state for checkpoints."""
        return {
            "participants": list(self._participants),
            "messages": [dict(row) for row in self._messages],
            "committed_events": self._committed_events_state(),
        }

    def set_state(self, state: dict[str, Any]) -> None:
        """Restore messaging state from a checkpoint payload."""
        if not isinstance(state, Mapping):
            raise TypeError("MessagingApp checkpoint state must be a mapping.")
        self._participants = [str(name) for name in state.get("participants", [])]
        raw_messages = state.get("messages", [])
        if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, str):
            raise TypeError("MessagingApp checkpoint messages must be a sequence.")
        self._messages = []
        for row in raw_messages:
            if not isinstance(row, Mapping):
                raise TypeError("MessagingApp checkpoint message rows must be mappings.")
            self._messages.append(
                {
                    "round": int(row.get("round", 0)),
                    "sender": str(row.get("sender", "")),
                    "recipient": str(row.get("recipient", "")),
                    "text": str(row.get("text", "")),
                }
            )
        self._restore_committed_events(state.get("committed_events"))
