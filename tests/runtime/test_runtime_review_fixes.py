"""Regression tests for the whole-repo review runtime/infra fixes.

Covers: FixedAgent checkpoint key coercion, thread-local runtime context clearing,
sqlite writer-loop per-item atomicity, build_engine loop-strategy passthrough, and
mastodon token caching.
"""

from __future__ import annotations

import json
import threading

import pytest
from omegaconf import OmegaConf

from silisocs.agents.fixed import FixedAgent
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform
from silisocs.runtime.construction.engines import build_engine
from silisocs.runtime.language_models import OpenAICompatibleLanguageModel
from silisocs.simulation_engines.policies.loops import FixedStepsLoopStrategy

# --------------------------------------------------------------------------- #
# #6 FixedAgent checkpoint key coercion
# --------------------------------------------------------------------------- #


def _fixed_agent() -> FixedAgent:
    return FixedAgent(
        name="Fixed",
        action_output_mode="parsed_action",
        advance_without_episode_observation=True,
        fixed_action_plan={
            2: [
                {"action_type": "post", "content": "a"},
                {"action_type": "post", "content": "b"},
            ]
        },
    )


def test_fixed_agent_restores_int_episode_cursors_after_json_roundtrip() -> None:
    agent = _fixed_agent()
    agent._current_episode = 2
    agent._episode_cursors = {2: 1}  # episode 2's first action already consumed

    # A real checkpoint serializes via JSON, which stringifies mapping keys.
    serialized = json.loads(json.dumps(agent.get_state()))
    assert "2" in serialized["episode_cursors"]  # keys are strings on disk

    restored = _fixed_agent()
    restored.set_state(serialized)
    assert restored._episode_cursors == {2: 1}
    assert all(isinstance(k, int) for k in restored._episode_cursors)
    # The int episode index now hits the restored cursor (no replay from 0).
    assert restored._episode_cursors.get(2, 0) == 1


# --------------------------------------------------------------------------- #
# #7 clear_runtime_context is thread-local
# --------------------------------------------------------------------------- #


def test_clear_runtime_context_is_thread_local() -> None:
    model = OpenAICompatibleLanguageModel(
        model_name="x", api_base="http://localhost:8000/v1", api_key="k", debug=False
    )
    model.set_runtime_context(agent_name="Main", episode_idx=5, phase="action")

    def worker() -> None:
        model.set_runtime_context(agent_name="Worker", episode_idx=9, phase="probe")
        model.clear_runtime_context()  # must only clear THIS thread's context

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    # The main thread's context must survive the worker's clear.
    assert getattr(model._local, "episode_idx", None) == 5
    assert getattr(model._local, "agent_name", None) == "Main"


# --------------------------------------------------------------------------- #
# #8 writer-loop per-item atomicity
# --------------------------------------------------------------------------- #


def test_writer_loop_rolls_back_partial_item(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=True)
    try:
        # Create the scratch table on the writer connection itself.
        platform._execute_write([("CREATE TABLE scratch (id INTEGER)", ())], sync=True)

        # An item whose main statement succeeds but whose subsidiary statement
        # fails must roll back the whole item, not commit the partial insert.
        with pytest.raises(Exception):
            platform._execute_write(
                [
                    ("INSERT INTO scratch (id) VALUES (1)", ()),
                    ("INSERT INTO no_such_table (id) VALUES (1)", ()),
                ],
                sync=True,
            )

        with platform.get_connection() as conn:
            count = int(conn.execute("SELECT COUNT(*) AS c FROM scratch").fetchone()["c"])
        assert count == 0
    finally:
        platform.shutdown()


# --------------------------------------------------------------------------- #
# #10 build_engine loop-strategy passthrough
# --------------------------------------------------------------------------- #


class _MarkerLoop(FixedStepsLoopStrategy):
    name = "marker"


def test_build_engine_honors_custom_loop_strategy_for_base_step() -> None:
    cfg = OmegaConf.create(
        {
            "sim": {
                "engine": {
                    "step": {"built_in": "base"},
                    "loop": {"class_path": "tests.runtime.test_runtime_review_fixes._MarkerLoop"},
                    "turn_policy": {"built_in": "single_action"},
                }
            }
        }
    )
    engine = build_engine(cfg)
    assert isinstance(engine.loop_strategy, _MarkerLoop)


# --------------------------------------------------------------------------- #
# #14 mastodon login token cache
# --------------------------------------------------------------------------- #


def test_mastodon_login_caches_access_token(monkeypatch) -> None:
    # The mastodon backend depends on optional extras (loguru, Mastodon.py).
    login_mod = pytest.importorskip("silisocs.environments.backends.mastodon.mastodon_ops.login")

    login_mod.clear_token_cache()
    calls = {"log_in": 0}

    class _FakeClient:
        def log_in(self, *args, **kwargs):
            calls["log_in"] += 1
            return "tok-123"

    monkeypatch.setattr(login_mod, "get_client", lambda: _FakeClient())
    monkeypatch.setattr(login_mod, "get_env_variable", lambda name: "x")

    try:
        assert login_mod.login("alice") == "tok-123"
        assert login_mod.login("alice") == "tok-123"
        # Second call served from cache: no second OAuth round-trip.
        assert calls["log_in"] == 1
    finally:
        login_mod.clear_token_cache()
