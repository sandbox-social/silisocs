"""Unit tests for the MessagingApp backend (direct instantiation)."""

from __future__ import annotations

import json

from silisocs.environments.backends.messaging.app import MessagingApp


class _Logger:
    def __init__(self) -> None:
        self.episode_idx = 0
        self.rows: list[dict] = []

    def log(self, row: dict) -> None:
        self.rows.append(row)


def _app(**kwargs) -> MessagingApp:
    app = MessagingApp(**kwargs)
    app.initialize(agent_names=["Alex", "Blair", "Casey"])
    app.action_logger = _Logger()
    return app


def _send(app: MessagingApp, sender: str, recipient: str, text: str) -> str:
    return app.invoke_action_with_kwargs(
        "SEND_MESSAGE", {"agent_name": sender, "recipient": recipient, "text": text}
    )


def test_private_message_reaches_only_sender_and_recipient() -> None:
    app = _app()
    _send(app, "Alex", "Blair", "meet at noon?")
    assert "Alex -> You: meet at noon?" in app.observe("Blair")
    assert "You -> Blair: meet at noon?" in app.observe("Alex")
    # A third party sees nothing of the private thread.
    assert "meet at noon" not in app.observe("Casey")
    assert "Inbox: no messages yet." in app.observe("Casey")


def test_broadcast_reaches_everyone() -> None:
    app = _app()
    app.invoke_action_with_kwargs("BROADCAST", {"agent_name": "Casey", "text": "hello all"})
    assert "Casey broadcast: hello all" in app.observe("Alex")
    assert "Casey broadcast: hello all" in app.observe("Blair")
    assert "You broadcast: hello all" in app.observe("Casey")


def test_invalid_sends_are_rejected_uncommitted() -> None:
    app = _app()
    assert "Unknown recipient" in _send(app, "Alex", "Zed", "hi")
    assert "You can message: Blair, Casey" in _send(app, "Alex", "Zed", "hi")
    assert "cannot message yourself" in _send(app, "Alex", "Alex", "hi")
    assert "must not be empty" in _send(app, "Alex", "Blair", "   ")
    assert "Unknown participant" in _send(app, "Zed", "Alex", "hi")
    long_text = "x" * 5000
    assert "too long" in _send(app, "Alex", "Blair", long_text)
    assert app._messages == []
    assert app.count_committed_events(labels=["send_message"]) == 0


def test_committed_rows_carry_round_and_target_fields() -> None:
    app = _app()
    app.action_logger.episode_idx = 3
    _send(app, "Alex", "Blair", "round three ping")
    assert app.count_committed_events(labels=["send_message"]) == 1
    (row,) = [r for r in app.action_logger.rows if r["label"] == "send_message"]
    assert row["source_user"] == "Alex"
    assert row["data"]["recipient"] == "Blair"
    assert row["data"]["round"] == 3
    assert row["data"]["text"] == "round three ping"


def test_observation_windows_to_the_most_recent_messages() -> None:
    app = _app(history_window=2)
    for idx in range(5):
        _send(app, "Alex", "Blair", f"note {idx}")
    view = app.observe("Blair")
    assert "note 3" in view and "note 4" in view
    assert "note 0" not in view and "note 2" not in view
    # The observe component's `limit` param overrides the backend default,
    # matching how the other generic backends configure observation size.
    wide = app.observe("Blair", limit=5)
    assert "note 0" in wide and "note 4" in wide
    narrow = app.observe("Blair", limit=1)
    assert "note 4" in narrow and "note 3" not in narrow


def test_checkpoint_round_trips_messages_and_committed_mirror() -> None:
    app = _app()
    _send(app, "Alex", "Blair", "before checkpoint")
    app.invoke_action_with_kwargs("BROADCAST", {"agent_name": "Blair", "text": "for everyone"})

    payload = json.loads(json.dumps(app.get_state()))  # a real checkpoint is JSON
    restored = MessagingApp()
    restored.action_logger = _Logger()
    restored.set_state(payload)

    assert restored._participants == ["Alex", "Blair", "Casey"]
    assert "Alex -> You: before checkpoint" in restored.observe("Blair")
    assert "Blair broadcast: for everyone" in restored.observe("Casey")
    assert restored.count_committed_events(labels=["send_message"]) == 1
    assert restored.count_committed_events(labels=["broadcast_message"]) == 1
