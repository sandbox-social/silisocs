"""In-memory committed-events mirror on ``SocialBackendApp``.

The mirror is a lock-free runtime read path (``iter_committed_events`` /
``count_committed_events``) over the same events written to
``action_events.jsonl``: one record per committed action, and nothing for a
failed or idempotent-no-op action. Authoritative-snapshot backends round-trip it
through get_state/set_state; Mastodon's flexible logger override still funnels
through the one shared implementation.
"""

from __future__ import annotations

from typing import Any

import pytest

from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp


class _RecordingLogger:
    """Minimal action_logger stand-in with a settable episode index."""

    def __init__(self) -> None:
        self.episode_idx: int | None = 0
        self.rows: list[dict[str, Any]] = []

    def log(self, log_data: Any) -> None:
        self.rows.append(dict(log_data))


@pytest.fixture
def twitter(tmp_path: Any) -> Any:
    logger = _RecordingLogger()
    app = TwitterLikeApp(db_path=str(tmp_path / "tw.db"), action_logger=logger)
    app.setup_social_state(agent_names=["Alice", "Bob"], following_graph={})
    yield app, logger
    app.shutdown()


def test_mirror_appends_only_on_commit(twitter: Any) -> None:
    app, _logger = twitter
    before = app.count_committed_events(labels=["post", "like"])
    app.create_tweet("Alice", "hello world")  # commits -> mirrored
    app.like_tweet("Bob", 1)  # commits -> mirrored
    assert "Error" in app.like_tweet("Bob", 999)  # failure -> not mirrored
    assert "already liked" in app.like_tweet("Bob", 1)  # no-op -> not mirrored
    assert app.count_committed_events(labels=["post", "like"]) - before == 2


def test_mirror_filters(twitter: Any) -> None:
    app, logger = twitter
    logger.episode_idx = 1
    app.create_tweet("Alice", "alpha beta")
    logger.episode_idx = 2
    app.create_tweet("Bob", "gamma delta")
    app.like_tweet("Bob", 1)

    # label filter
    assert app.count_committed_events(labels=["post"]) == 2
    # agent filter
    assert app.count_committed_events(agent="Alice", labels=["post"]) == 1
    # episode bounds: inclusive lower, exclusive upper
    assert app.count_committed_events(labels=["post"], before_episode=2) == 1
    assert app.count_committed_events(labels=["post"], since_episode=2) == 1
    # text_contains_any matches authored text, case-insensitive
    assert app.count_committed_events(labels=["post"], text_contains_any=["ALPHA"]) == 1
    assert app.count_committed_events(text_contains_any=["nonexistent"]) == 0
    # iter yields copies -> mutating a result (top-level OR nested data) never
    # corrupts the mirror.
    first = next(app.iter_committed_events(labels=["post"], text_contains_any=["alpha"]))
    first["label"] = "mutated"
    first["data"]["post_text"] = "HACKED"
    assert app.count_committed_events(labels=["post"]) == 2
    assert app.count_committed_events(text_contains_any=["hacked"]) == 0


def test_mirror_excludes_unstamped_events_from_episode_bounds(twitter: Any) -> None:
    app, logger = twitter
    logger.episode_idx = None  # e.g. pre-run seed content
    app.create_tweet("Alice", "seed")
    assert app.count_committed_events(labels=["post"]) == 1
    assert app.count_committed_events(labels=["post"], since_episode=0) == 0
    assert app.count_committed_events(labels=["post"], before_episode=99) == 0


@pytest.mark.parametrize("factory", [TwitterLikeApp, RedditLikeApp])
def test_mirror_snapshot_round_trip(factory: Any, tmp_path: Any) -> None:
    src = factory(db_path=str(tmp_path / "a.db"), action_logger=_RecordingLogger())
    src.setup_social_state(agent_names=["Alice", "Bob"])
    if factory is TwitterLikeApp:
        src.create_tweet("Alice", "hello")
        src.like_tweet("Bob", 1)
    else:
        src.create_reddit_post("Alice", "general", "Hi", "hello reddit")
        src.upvote("Bob", 1, "post")
    expected = list(src.iter_committed_events())
    state = src.get_state()
    src.shutdown()

    dst = factory(db_path=str(tmp_path / "b.db"), action_logger=_RecordingLogger())
    dst.set_state(state)
    assert list(dst.iter_committed_events()) == expected
    dst.shutdown()


def test_reddit_text_contains_any_matches_content_and_title(tmp_path: Any) -> None:
    # Reddit logs authored text under content/title (not post_text); the base filter
    # must still find it, or it would be a silent no-op on a Reddit backend.
    app = RedditLikeApp(db_path=str(tmp_path / "rd.db"), action_logger=_RecordingLogger())
    app.setup_social_state(agent_names=["Alice"])
    app.create_reddit_post("Alice", "general", "Vaccine debate", "measles outbreak thread")
    assert app.count_committed_events(labels=["post"], text_contains_any=["VACCINE"]) == 1
    assert app.count_committed_events(labels=["post"], text_contains_any=["measles"]) == 1
    assert app.count_committed_events(labels=["post"], text_contains_any=["nope"]) == 0
    app.shutdown()


def test_mastodon_override_mirrors_and_tolerates_missing_logger() -> None:
    from silisocs.environments.backends.mastodon.apps import SocialNetworkApp

    app = SocialNetworkApp(perform_operations=False, action_logger=None)
    # Both call forms funnel through the shared base implementation and mirror.
    app._log_action_event({"source_user": "Alice", "label": "post", "data": {"post_text": "hi"}})
    app._log_action_event("Bob", "follow", {"target_user": "Alice"})
    assert app.count_committed_events(labels=["post"]) == 1
    assert app.count_committed_events(agent="Bob") == 1


def test_mastodon_resume_round_trips_mirror_including_non_replayable_labels() -> None:
    from silisocs.environments.backends.mastodon.apps import SocialNetworkApp

    logger = _RecordingLogger()
    logger.episode_idx = 2
    src = SocialNetworkApp(perform_operations=False, action_logger=logger)
    src.set_user_mapping({"Alice": "user0001", "Bob": "user0002"})
    src._log_action_event("Alice", "post", {"toot_id": "1", "post_text": "hello"})
    # block_user is committed but NOT replayable — replay-only restore would drop it,
    # so the exact mirror round-trip is what keeps resume answers correct.
    src._log_action_event("Alice", "block_user", {"target_user": "Bob"})
    expected = list(src.iter_committed_events())
    state = src.get_state()

    dst = SocialNetworkApp(perform_operations=False, action_logger=_RecordingLogger())
    dst.set_state(state)
    assert list(dst.iter_committed_events()) == expected
    # episodes survive (a replay-only rebuild would collapse them), and the
    # non-replayable label is present.
    assert dst.count_committed_events(labels=["block_user"], since_episode=2) == 1
