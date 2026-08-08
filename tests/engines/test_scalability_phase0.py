"""Phase-0 scalability fixes (SCALABILITY_PLAN.md): behavior regression tests.

Covers the observable behavior of the defect fixes: the bounded init pool, bulk
backend setup (``create_users`` / ``add_follows``), Markov participation's
incremental memo (must match from-scratch derivation exactly), checkpoint
``shared_memories`` dedupe + resolve round-trip, the recsys row-count return,
and debug-log truncation.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, cast

from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform
from silisocs.runtime.checkpointing.state import (
    _SHARED_MEMORIES_REF,
    _stage_checkpoint_objects,
    make_checkpoint_data,
)
from silisocs.runtime.concurrency import run_tasks
from silisocs.runtime.construction.assembly import RuntimeObjects
from silisocs.runtime.construction.specs import RuntimeRole, RuntimeSpec
from silisocs.runtime.language_models.openai import OpenAILanguageModel
from silisocs.simulation_engines.policies.participation import ActivityMarkovParticipation


def _twitter(tmp_path) -> TwitterLikePlatform:
    return TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)


# --------------------------------------------------------------- bounded init pool


def test_run_tasks_bounds_concurrent_workers() -> None:
    active = 0
    peak = 0
    lock = threading.Lock()

    def task() -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.005)
        with lock:
            active -= 1
        return 1

    results = run_tasks({f"t{i}": task for i in range(64)}, max_workers=8)
    assert len(results) == 64
    assert peak <= 8


def test_run_tasks_large_map_runs_on_small_pool() -> None:
    # One thread per task previously failed outright ("can't start new thread")
    # around a thousand tasks; a large task map must run on a small fixed pool.
    # (Kept to 8 workers so the test never races the host's own pid/thread cap.)
    results = run_tasks({f"t{i}": (lambda: 1) for i in range(500)}, max_workers=8)
    assert len(results) == 500


# ---------------------------------------------------------- bulk backend setup


def test_bulk_create_users_and_add_follows(tmp_path) -> None:
    platform = _twitter(tmp_path)
    platform.create_users([("u1", ""), ("u2", "bio2"), ("u3", "")])
    assert platform.get_user_id("u2") is not None
    # Idempotent for existing usernames (same semantics as create_user).
    platform.create_users([("u1", "changed")])

    applied = platform.add_follows(
        [
            ("u1", "u2"),
            ("u1", "u2"),  # duplicate edge: deduped
            ("u1", "u1"),  # self-follow: dropped
            ("u2", "u3"),
            ("u1", "ghost"),  # unknown followee: dropped
        ]
    )
    assert applied == [("u1", "u2"), ("u2", "u3")]

    with platform.get_connection() as conn:
        edges = conn.execute("SELECT follower_id, followee_id FROM follows").fetchall()
        assert len(edges) == 2
        u2 = conn.execute(
            "SELECT following_count, followers_count FROM users WHERE username = 'u2'"
        ).fetchone()
        # u2 follows u3 and is followed by u1 — counters recomputed authoritatively.
        assert u2["following_count"] == 1
        assert u2["followers_count"] == 1
    platform.shutdown()


def test_add_follows_skips_edges_already_in_db(tmp_path) -> None:
    """Regression: re-running init over a pre-populated DB must be a no-op.

    ``add_follows`` used to pair INSERT OR IGNORE with an UNCONDITIONAL
    activity insert and report the edge as applied — duplicating 'follow'
    notifications and over-reporting init_follow events for existing edges.
    """
    platform = _twitter(tmp_path)
    platform.create_users([("u1", ""), ("u2", ""), ("u3", "")])
    assert platform.add_follows([("u1", "u2")]) == [("u1", "u2")]

    # Second pass: the existing edge is skipped, only the new one applies.
    applied = platform.add_follows([("u1", "u2"), ("u2", "u3")])
    assert applied == [("u2", "u3")]

    with platform.get_connection() as conn:
        activities = conn.execute(
            "SELECT COUNT(*) FROM activities WHERE action_type = 'follow'"
        ).fetchone()[0]
        assert activities == 2, "one activity per real edge, none for the re-run"
    platform.shutdown()


def test_get_connection_reaps_dead_thread_connections(tmp_path) -> None:
    """Regression: per-step thread pools must not leak one connection per worker.

    ``get_connection`` registers a thread-local connection per thread; before
    the reap, connections opened by pool threads stayed registered (and open)
    forever after their threads died — thousands of fds over a long run.
    """
    import threading

    platform = _twitter(tmp_path)
    platform.create_users([("u1", "")])

    def _touch() -> None:
        with platform.get_connection() as conn:
            conn.execute("SELECT 1").fetchone()

    for _round in range(10):
        threads = [threading.Thread(target=_touch) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    _touch()  # main-thread connection triggers a final reap of the dead ones
    with platform._connections_lock:
        live = len(platform._connections)
    assert live <= 9, f"dead threads' connections must be reaped, found {live} registered"
    platform.shutdown()


def test_iter_in_chunks_stays_under_sqlite_variable_cap() -> None:
    """Population-scaled IN(...) lists must be chunked below 999 bound vars."""
    from silisocs.environments.backends.social.sqlite_engine import SqliteSocialEngineBase

    ids = list(range(1234))
    chunks = list(SqliteSocialEngineBase._iter_in_chunks(ids))
    assert [len(c) for c in chunks] == [500, 500, 234]
    assert [i for chunk in chunks for i in chunk] == ids
    assert list(SqliteSocialEngineBase._iter_in_chunks([])) == []


def test_update_recommendations_returns_row_count(tmp_path) -> None:
    platform = _twitter(tmp_path)
    # No recsys types initialized: the no-op path reports 0 rows written, so the
    # app layer can log counts without a COUNT(*) scan.
    assert platform.update_recommendations() == 0
    platform.shutdown()


# ------------------------------------------------- Markov participation memo


def test_markov_incremental_memo_matches_from_scratch() -> None:
    rates = {"user": {"inactive_to_active": 0.4, "active_to_inactive": 0.5}}
    roles = {f"a{i}": "user" for i in range(6)}
    names = list(roles)
    forward = ActivityMarkovParticipation(activity_transition_rates=rates, sim_roles=roles)
    stepped = [
        forward.participating_agents(agent_names=names, step_index=s, seed=7) for s in range(8)
    ]
    for step in range(8):
        fresh = ActivityMarkovParticipation(activity_transition_rates=rates, sim_roles=roles)
        assert (
            fresh.participating_agents(agent_names=names, step_index=step, seed=7) == stepped[step]
        ), f"memoized chain diverged from from-scratch derivation at step {step}"


# ------------------------------------------- checkpoint shared_memories dedupe


class _StubAgent:
    def __init__(self, name: str) -> None:
        self.name = name

    def get_state(self) -> dict:
        return {}

    def set_state(self, state: dict) -> None:
        del state


def test_checkpoint_dedupes_shared_memories_and_resolves_on_load() -> None:
    shared = ["They are an active user.", "Context line."]
    runtime = RuntimeObjects(agents=cast(list[Any], [_StubAgent("a"), _StubAgent("b")]))
    for name in ("a", "b"):
        runtime.object_specs[name] = RuntimeSpec(
            class_path="tests.fake.Agent",
            role=RuntimeRole.AGENT,
            params={"name": name, "shared_memories": list(shared)},
        )

    payload = make_checkpoint_data(runtime, step=1)

    table = payload["shared_memories_table"]
    assert len(table) == 1, "identical shared_memories must be stored once"
    (key,) = table
    assert table[key] == shared
    for name in ("a", "b"):
        assert payload["objects"][name]["params"]["shared_memories"] == {_SHARED_MEMORIES_REF: key}

    # Round-trip through JSON and the staging path: refs resolve back to the text.
    reloaded = json.loads(json.dumps(payload))
    staged = _stage_checkpoint_objects(runtime, reloaded, models={}, object_to_model={})
    for _name, spec, _state, _existing in staged:
        assert spec.params["shared_memories"] == shared


# --------------------------------------------------------- debug log truncation


def test_debug_log_truncation_bounds_record_size() -> None:
    model = OpenAILanguageModel("test-model", api_key="test", debug=False)
    short = "x" * 100
    assert model._truncate_for_log(short) == short
    long = "y" * 50_000
    truncated = model._truncate_for_log(long)
    assert "[log truncated" in truncated
    assert len(truncated) < 21_000
