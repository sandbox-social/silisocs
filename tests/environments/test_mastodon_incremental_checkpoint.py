"""Incremental action-log read for Mastodon checkpoint ``get_state``.

``SocialNetworkApp.get_state`` embeds the backend's replayable action history so
``set_state`` can rebuild the live server. Under the study default
(``every_n_steps=1``) ``get_state`` runs every step; re-reading the whole
``action_events.jsonl`` each time is O(n) per save / O(n^2) over a run.

These tests prove the read is now incremental — a second ``get_state`` only
parses events appended since the previous call — while still returning the full
history, and that a ``get_state`` -> ``set_state`` round-trip replays everything.

The live Mastodon client is never touched here: with ``perform_operations``
defaulting to ``False`` the app performs no network I/O, and ``invoke_action_with_kwargs``
is stubbed for the round-trip test (mirroring ``tests/runtime/test_checkpoint_completeness.py``).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from silisocs.environments.backends.mastodon.apps import SocialNetworkApp


def _append_action_rows(path, rows) -> None:
    """Append JSONL action rows the way the runtime logger does (one line each)."""
    with open(path, "a", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row) + "\n" for row in rows)


def _post_row(idx: int, *, user: str = "Alice") -> dict:
    return {
        "event_type": "action",
        "episode": idx,
        "label": "post",
        "source_user": user,
        "data": {"toot_id": f"OLD{idx}", "post_text": f"hi-{idx}"},
    }


def test_get_state_reads_incrementally_but_returns_full_history(tmp_path, monkeypatch):
    """Second ``get_state`` parses only the newly appended events, returns all of them."""
    log = tmp_path / "action_events.jsonl"
    app = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=str(log)))

    # Spy on the per-line parser to count how many JSONL lines each read processes.
    parsed_lines: list[int] = []
    real_append = app._append_parsed_action_event
    count = {"n": 0}

    def _spy(stripped_line: str) -> None:
        count["n"] += 1
        return real_append(stripped_line)

    monkeypatch.setattr(app, "_append_parsed_action_event", _spy)

    # Append N=3 events, then read.
    _append_action_rows(log, [_post_row(i) for i in range(3)])
    count["n"] = 0
    first = app.get_state()["replay_events"]
    parsed_lines.append(count["n"])
    assert [e["data"]["post_text"] for e in first] == ["hi-0", "hi-1", "hi-2"]
    assert parsed_lines[0] == 3  # first read parses all N lines

    # Append M=2 more events, then read again.
    _append_action_rows(log, [_post_row(i) for i in range(3, 5)])
    count["n"] = 0
    second = app.get_state()["replay_events"]
    parsed_lines.append(count["n"])

    # Incrementality: the second read only processed the M new lines ...
    assert parsed_lines[1] == 2
    # ... yet still returns the complete N+M history in order.
    assert [e["data"]["post_text"] for e in second] == [
        "hi-0",
        "hi-1",
        "hi-2",
        "hi-3",
        "hi-4",
    ]

    # A read with no new bytes parses nothing and still returns everything.
    count["n"] = 0
    third = app.get_state()["replay_events"]
    assert count["n"] == 0
    assert [e["data"]["post_text"] for e in third] == [e["data"]["post_text"] for e in second]


def test_returned_events_are_independent_snapshots(tmp_path):
    """Each ``get_state`` returns a fresh list, not an alias of the internal cache."""
    log = tmp_path / "action_events.jsonl"
    app = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=str(log)))
    _append_action_rows(log, [_post_row(0)])

    first = app.get_state()["replay_events"]
    _append_action_rows(log, [_post_row(1)])
    second = app.get_state()["replay_events"]

    # Mutating an earlier snapshot must not corrupt later reads.
    first.clear()
    third = app.get_state()["replay_events"]
    assert len(second) == 2
    assert len(third) == 2


def test_truncation_falls_back_to_full_reread(tmp_path):
    """If the log is truncated/rotated in place, the next read re-parses from scratch."""
    log = tmp_path / "action_events.jsonl"
    app = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=str(log)))

    _append_action_rows(log, [_post_row(i) for i in range(4)])
    assert len(app.get_state()["replay_events"]) == 4

    # Simulate rotation-in-place: a smaller file with different content.
    log.write_text(json.dumps(_post_row(99)) + "\n", encoding="utf-8")
    events = app.get_state()["replay_events"]
    assert [e["data"]["post_text"] for e in events] == ["hi-99"]


def test_missing_file_then_recreated_is_read_from_start(tmp_path):
    """A vanished log clears the cache; a re-created one is read from the beginning."""
    log = tmp_path / "action_events.jsonl"
    app = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=str(log)))

    _append_action_rows(log, [_post_row(0), _post_row(1)])
    assert len(app.get_state()["replay_events"]) == 2

    log.unlink()
    assert app.get_state().get("replay_events") is None  # empty -> key omitted

    _append_action_rows(log, [_post_row(5)])
    events = app.get_state()["replay_events"]
    assert [e["data"]["post_text"] for e in events] == ["hi-5"]


def test_partial_trailing_line_is_not_consumed_until_complete(tmp_path):
    """A half-written record (no trailing newline) is held back until completed."""
    log = tmp_path / "action_events.jsonl"
    app = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=str(log)))

    _append_action_rows(log, [_post_row(0)])
    # Write a second record WITHOUT a trailing newline (simulating a mid-flush read).
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_post_row(1)))

    first = app.get_state()["replay_events"]
    # Only the complete first line is consumed.
    assert [e["data"]["post_text"] for e in first] == ["hi-0"]

    # Now the writer finishes the line; the held-back record is read next time.
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("\n")
    second = app.get_state()["replay_events"]
    assert [e["data"]["post_text"] for e in second] == ["hi-0", "hi-1"]


def test_get_state_then_set_state_replays_full_history(tmp_path):
    """A get_state -> set_state round-trip replays every logged action as its user."""
    log = tmp_path / "action_events.jsonl"
    src = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=str(log)))

    # Build a history across two write batches, reading between them (incremental).
    _append_action_rows(
        log,
        [
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "Alice",
                "data": {"toot_id": "OLD1", "post_text": "hello"},
            },
            # A non-action bookkeeping row must be ignored by replay.
            {
                "event_type": "timeline_retrieval",
                "episode": 0,
                "label": "get_home_feed",
                "source_user": "Alice",
                "data": {},
            },
        ],
    )
    src.get_state()  # first incremental read
    _append_action_rows(
        log,
        [
            {
                "event_type": "action",
                "episode": 1,
                "label": "follow",
                "source_user": "Bob",
                "data": {"target_user": "Alice"},
            },
        ],
    )
    state = src.get_state()  # second incremental read; full history embedded

    # The embedded history is complete despite the incremental reads.
    assert [e["label"] for e in state["replay_events"]] == ["post", "follow"]

    # Replay into a fresh app: each action re-invoked as its original user.
    dst = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=""))
    calls: list[tuple[str, dict]] = []
    dst.invoke_action_with_kwargs = lambda name, args: calls.append((name, dict(args))) or ""
    dst.set_state(state)

    assert calls == [
        ("post_toot", {"status": "hello", "agent_name": "Alice"}),
        ("follow_user", {"target_user": "Alice", "agent_name": "Bob"}),
    ]
