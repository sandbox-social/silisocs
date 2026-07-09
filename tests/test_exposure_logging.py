"""Tests for exposure logging (what each agent SAW) — Tier 1b."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, cast

from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform
from silisocs.environments.gm.components.social_media.observe import TimelineMakeObservation
from silisocs.evaluations.action_events import (
    resolve_action_event_files,
    resolve_exposure_event_files,
)
from silisocs.evaluations.exposure import exposure_action_join
from silisocs.runtime.io import EventLogger, flush_jsonl_writers


def _seed_platform(tmp_path: Path) -> TwitterLikePlatform:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    platform.create_users([("alice", "a"), ("bob", "b")])
    platform.add_follows([("alice", "bob")])
    platform.create_post("bob", "hello from bob")
    return platform


def _app_with_exposure(tmp_path: Path, platform: TwitterLikePlatform) -> TwitterLikeApp:
    app = TwitterLikeApp(db_path=str(tmp_path / "tw.db"))
    app._platform = platform
    app._user_mapping = {"alice": "alice", "bob": "bob"}
    logger = EventLogger("exposure", str(tmp_path / "exposure_events.jsonl"))
    logger.episode_idx = 3
    app.exposure_logger = logger
    return app


# --------------------------------------------------------------- source tagging


def test_get_timeline_tags_source_per_mode(tmp_path) -> None:
    platform = _seed_platform(tmp_path)

    follower = platform.get_timeline("follower_chronological", "alice", 5)
    assert follower and all(p["source"] == "follower" for p in follower)

    # Stub the underlying queries so the tagging branch is exercised precisely.
    cast(Any, platform).get_recommendations = lambda username, limit, recsys_type=None: [
        {"id": 101, "username": "bob"}
    ]
    pure = platform.get_timeline("pure_recsys", "alice", 5, recsys_type="twitter_tfidf")
    assert pure[0]["source"] == "recsys:twitter_tfidf"

    cast(Any, platform).get_feed = lambda strategy, username, **kw: {
        "posts": [{"id": 202, "username": "bob"}]
    }
    hybrid = platform.get_timeline("hybrid_recsys_follower", "alice", 5, recsys_type="twhin")
    by_id = {p["id"]: p["source"] for p in hybrid}
    assert by_id[101] == "recsys:twhin"
    assert by_id[202] == "follower"
    platform.shutdown()


# --------------------------------------------------------------- observation parity


def test_observation_text_identical_with_logging_on_off(tmp_path) -> None:
    platform = _seed_platform(tmp_path)
    app = _app_with_exposure(tmp_path, platform)

    on = TimelineMakeObservation(model=None, agent_names=["alice"], backend=app, log_exposures=True)
    off = TimelineMakeObservation(
        model=None, agent_names=["alice"], backend=app, log_exposures=False
    )
    assert on.make_observation("alice") == off.make_observation("alice")
    platform.shutdown()


# --------------------------------------------------------------- events written


def _read_exposures(path: Path) -> list[dict[str, Any]]:
    flush_jsonl_writers(timeout_s=5.0)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_exposure_events_written_with_ids_source_rank(tmp_path) -> None:
    platform = _seed_platform(tmp_path)
    app = _app_with_exposure(tmp_path, platform)
    component = TimelineMakeObservation(model=None, agent_names=["alice"], backend=app)

    component.make_observation("alice")
    rows = _read_exposures(tmp_path / "exposure_events.jsonl")
    assert len(rows) == 1
    row = rows[0]
    assert row["agent"] == "alice"
    assert row["event_type"] == "exposure"
    assert row["episode"] == 3
    assert row["posts"], "bob's post must be recorded"
    first = row["posts"][0]
    assert set(first) == {"id", "author", "source", "rank"}
    assert first["source"] == "follower" and first["rank"] == 0
    platform.shutdown()


def test_exposure_toggle_off_writes_nothing(tmp_path) -> None:
    platform = _seed_platform(tmp_path)
    app = _app_with_exposure(tmp_path, platform)
    component = TimelineMakeObservation(
        model=None, agent_names=["alice"], backend=app, log_exposures=False
    )
    component.make_observation("alice")
    assert _read_exposures(tmp_path / "exposure_events.jsonl") == []
    platform.shutdown()


def test_exposure_event_index_monotonic_under_concurrent_observes(tmp_path) -> None:
    platform = _seed_platform(tmp_path)
    app = _app_with_exposure(tmp_path, platform)
    component = TimelineMakeObservation(model=None, agent_names=["alice"], backend=app)

    def observe() -> None:
        component.make_observation("alice")

    threads = [threading.Thread(target=observe) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = _read_exposures(tmp_path / "exposure_events.jsonl")
    indices = sorted(r["event_index"] for r in rows)
    assert indices == list(range(20)), "event_index must be unique + gapless under concurrency"
    platform.shutdown()


# --------------------------------------------------------------- discovery + join


def test_resolve_event_files_flat_and_nested(tmp_path) -> None:
    (tmp_path / "exposure_events.jsonl").write_text("{}\n")
    assert resolve_exposure_event_files(tmp_path) == [tmp_path / "exposure_events.jsonl"]

    nested = tmp_path / "nested"
    (nested / "gmA").mkdir(parents=True)
    (nested / "gmB").mkdir(parents=True)
    (nested / "gmA" / "exposure_events.jsonl").write_text("{}\n")
    (nested / "gmB" / "exposure_events.jsonl").write_text("{}\n")
    found = resolve_exposure_event_files(nested)
    assert [p.parent.name for p in found] == ["gmA", "gmB"]

    # The action-event resolver still behaves the same after the refactor.
    (tmp_path / "action_events.jsonl").write_text("{}\n")
    assert resolve_action_event_files(tmp_path) == [tmp_path / "action_events.jsonl"]


def test_exposure_action_join(tmp_path) -> None:
    (tmp_path / "exposure_events.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"agent": "alice", "posts": [{"id": 1}, {"id": 2}, {"id": 3}]},
                {"agent": "alice", "posts": [{"id": 4}]},
            ]
        )
        + "\n"
    )
    (tmp_path / "action_events.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                # like: post_id is the liked post, logged as a STRING (backends
                # str() their ids) — must still match the int exposure id.
                {"source_user": "alice", "label": "like_tweet", "data": {"post_id": "2"}},
                # reply: the NEW reply gets a fresh post_id; the engaged post is
                # reply_to_id (3), which must count as engagement.
                {
                    "source_user": "alice",
                    "label": "reply",
                    "data": {"post_id": "77", "reply_to_id": "3"},
                },
            ]
        )
        + "\n"
    )
    summary = exposure_action_join(tmp_path)
    assert summary["alice"]["exposures"] == 2
    assert summary["alice"]["exposed_post_ids"] == ["1", "2", "3", "4"]
    # 2 (liked, int/str match) and 3 (replied-to via reply_to_id); the reply's own
    # new id 77 is not an exposed post so it does not count.
    assert summary["alice"]["engaged_post_ids"] == ["2", "3"]
    assert summary["alice"]["engagement_rate"] == 0.5
