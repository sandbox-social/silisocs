"""Phase-1 scalability (SCALABILITY_PLAN.md): O(active) per-step behavior tests.

Covers: participation-first ``run_step`` (GM updates and scheduling receive the
active roster; ``requires_full_roster`` components still get the population),
GM context caching, lock-free read-only observation, scoped (active-users-only)
recommendation updates, seed-stable probe sampling, the checkpoint save-strategy
slot (sharded round-trip incl. DB sidecar extraction), and count-only episode
telemetry.
"""

from __future__ import annotations

import base64
import json
from typing import Any, cast

from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform
from silisocs.environments.gm.components.social_media.update import (
    SocialRecommendationUpdateComponent,
)
from silisocs.evaluations.probes.deployment import (
    ProbeDeploymentOrchestrator,
    ProbeDeploymentPolicy,
)
from silisocs.runtime.checkpointing.save import (
    MonolithicJsonSaveStrategy,
    ShardedCheckpointSaveStrategy,
    build_checkpoint_save_strategy,
)
from silisocs.runtime.checkpointing.state import load_checkpoint_file
from silisocs.runtime.telemetry import SimMetricsCollector
from silisocs.simulation_engines.base_engines import RuntimeEngine
from silisocs.simulation_engines.recorders import DefaultEngineRecorder
from silisocs.simulation_engines.runtime_base import StepResult


class _Agent:
    def __init__(self, name: str) -> None:
        self.name = name

    def observe(self, observation: str) -> None:
        del observation

    def act(self, action_spec: Any) -> str:
        del action_spec
        return "do_nothing"


class _RecordingGM:
    """Minimal GM double recording what ``update`` receives."""

    name = "recording_gm"
    agent_flow_tags: dict[str, str] = {}

    def __init__(self) -> None:
        self.update_rosters: list[list[str]] = []

    def update(self, *, step: int, agents: list[Any], context: Any | None = None) -> None:
        del step, context
        self.update_rosters.append([agent.name for agent in agents])

    def acting_agents(self, candidate_agents: list[Any]) -> list[str]:
        return [agent.name for agent in candidate_agents]

    def action_prompt(self, agent_name: str) -> Any:
        raise AssertionError(f"unexpected action_prompt for {agent_name}")

    def make_observation(self, agent_name: str) -> str:
        del agent_name
        return ""

    def resolve_action(self, agent_name: str, action: Any) -> str:
        del agent_name, action
        return ""


class _FixedParticipation:
    """Participation double: only the configured names are active."""

    def __init__(self, active: list[str]) -> None:
        self._active = list(active)

    def participating_agents(self, *, agent_names: list[str], step_index: int, seed: int):
        del agent_names, step_index, seed
        return list(self._active)


# ------------------------------------------------- participation-first run_step


def test_run_step_passes_active_roster_to_gm_update() -> None:
    agents = [_Agent("Alice"), _Agent("Bob"), _Agent("Cara")]
    gm = _RecordingGM()

    class _CapturingStep:
        name = "capture"

        def __init__(self) -> None:
            self.roster: list[str] | None = None

        def run(self, *, engine, step_index, game_masters, agents, verbose) -> StepResult:
            del engine, step_index, game_masters, verbose
            self.roster = [agent.name for agent in agents]
            return StepResult(skipped=True)

    step_strategy = _CapturingStep()
    engine = RuntimeEngine(
        step_strategy=step_strategy,
        participation=_FixedParticipation(["Bob"]),
    )
    engine.run_step(step_index=0, game_masters=[gm], agents=agents, verbose=False)
    assert gm.update_rosters == [["Bob"]], "GM update must receive the ACTIVE roster"
    assert step_strategy.roster == ["Bob"]


def test_component_gm_update_honors_requires_full_roster() -> None:
    from silisocs.environments.gm.components.base import UpdateComponent

    received: dict[str, list[str]] = {}

    class _ActiveOnly(UpdateComponent):
        def update(self, *, step, agents, context=None):
            del step, context
            received["active"] = [agent.name for agent in agents]

    class _FullRoster(UpdateComponent):
        requires_full_roster = True

        def update(self, *, step, agents, context=None):
            del step, context
            received["full"] = [agent.name for agent in agents]

    # Exercise the dispatch seam without building a full ComponentGameMaster:
    # the update method reads self.agents and self._components_for_role only.
    from silisocs.environments.gm.game_master import ComponentGameMaster

    gm = object.__new__(ComponentGameMaster)
    gm.agents = (_Agent("Alice"), _Agent("Bob"), _Agent("Cara"))
    components = [_ActiveOnly(), _FullRoster()]
    gm._components_for_role = lambda *, role: components  # type: ignore[method-assign]
    ComponentGameMaster.update(gm, step=1, agents=[_Agent("Bob")])

    assert received["active"] == ["Bob"]
    assert received["full"] == ["Alice", "Bob", "Cara"]


# ----------------------------------------------------------- context caching


def test_refresh_context_rebuilds_only_on_roster_change() -> None:
    from silisocs.environments.gm.game_master import ComponentGameMaster

    gm = object.__new__(ComponentGameMaster)
    gm._name = "gm"
    gm.backend = None
    gm.model = None
    gm.agent_flow_tags = {}
    roster = (_Agent("Alice"), _Agent("Bob"))
    gm.agents = roster
    gm._context_agent_names = tuple(agent.name for agent in roster)
    gm.context = cast(Any, object())

    before = gm.context
    ComponentGameMaster._refresh_context(gm, list(roster))
    assert gm.context is before, "unchanged roster must not rebuild the context"

    ComponentGameMaster._refresh_context(gm, [roster[0]])
    assert gm.context is not before
    assert gm._context_agent_names == ("Alice",)


# ------------------------------------------------- lock-free read-only observe


def test_read_only_observation_runs_without_gm_lock() -> None:
    lock_states: dict[str, bool] = {}
    engine = RuntimeEngine()

    class _ObserveGM(_RecordingGM):
        flavor = ""

        def __init__(self, lock_free: bool) -> None:
            super().__init__()
            self._lock_free = lock_free

        def observation_is_lock_free(self, agent_name: str) -> bool:
            del agent_name
            return self._lock_free

        def make_observation(self, agent_name: str) -> str:
            del agent_name
            # If the engine holds this GM's lock, a non-blocking acquire fails.
            lock = engine._gm_lock(self)
            acquired = lock.acquire(blocking=False)
            if acquired:
                lock.release()
            lock_states[type(self).flavor] = acquired
            return "obs"

    class _LockFreeGM(_ObserveGM):
        flavor = "lock_free"

    class _LockedGM(_ObserveGM):
        flavor = "locked"

    from silisocs.runtime.types import ActionSpec

    spec = ActionSpec(prompt="act")
    engine.run_agent_step(
        game_master=_LockFreeGM(True), agent=_Agent("Alice"), action_spec=spec, verbose=False
    )
    engine.run_agent_step(
        game_master=_LockedGM(False), agent=_Agent("Alice"), action_spec=spec, verbose=False
    )
    assert lock_states == {"lock_free": True, "locked": False}


def test_builtin_observe_components_declare_read_only() -> None:
    from silisocs.environments.gm.components.observe import (
        AppObservationComponent,
        EpisodeObservation,
    )
    from silisocs.environments.gm.components.social_media.observe import (
        TimelineMakeObservation,
    )

    assert AppObservationComponent.read_only is True
    assert EpisodeObservation.read_only is True
    assert TimelineMakeObservation.read_only is True

    from silisocs.environments.gm.components.base import ObservationComponent

    assert ObservationComponent.read_only is False, "custom components must opt in"


# --------------------------------------------------- scoped recsys updates


def _stub_scoring(platform: TwitterLikePlatform) -> None:
    """Replace the ML scorer with a deterministic stub (no sklearn/torch in CI).

    The scoped-delete semantics under test live in ``update_recommendations``
    itself; scoring just needs to yield rows per user.
    """

    def _stub(users, posts, max_posts, state=None, scoped_user_ids=None):
        del state, scoped_user_ids
        return {
            int(user["id"]): [
                int(post["id"]) for post in posts if int(post["user_id"]) != int(user["id"])
            ][:max_posts]
            for user in users
        }

    platform._rec_embedding = _stub  # type: ignore[method-assign]


def _twitter_app(tmp_path) -> TwitterLikeApp:
    app = TwitterLikeApp(db_path=str(tmp_path / "tw.db"))
    app._platform = TwitterLikePlatform(str(tmp_path / "tw.db"), use_queue=False)
    return app


def test_scoped_update_recommendations_preserves_other_users_rows(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    platform.create_users([("alice", "likes ml"), ("bob", "likes art"), ("cara", "likes sql")])
    for username in ("alice", "bob", "cara"):
        platform.create_post(username, f"post by {username}")
    platform.init_recsys(recsys_type="twitter_tfidf")
    _stub_scoring(platform)

    assert platform.update_recommendations(max_posts=5) >= 0
    with platform.get_connection() as conn:
        before = {
            row["user_id"]: row["n"]
            for row in conn.execute(
                "SELECT user_id, COUNT(*) AS n FROM recommendations GROUP BY user_id"
            ).fetchall()
        }
    alice_id = platform.get_user_id("alice")
    bob_id = platform.get_user_id("bob")
    assert alice_id is not None and bob_id is not None
    assert before.get(bob_id, 0) > 0

    # Scoped update for alice only: bob's rows must survive untouched.
    platform.create_post("cara", "fresh content about ml and sql")
    assert platform.update_recommendations(active_user_ids=[alice_id], max_posts=5) >= 0
    with platform.get_connection() as conn:
        after = {
            row["user_id"]: row["n"]
            for row in conn.execute(
                "SELECT user_id, COUNT(*) AS n FROM recommendations GROUP BY user_id"
            ).fetchall()
        }
    assert after.get(bob_id, 0) == before.get(bob_id, 0)
    assert after.get(alice_id, 0) > 0
    platform.shutdown()


def test_app_scoped_update_with_unknown_agents_refreshes_nothing(tmp_path) -> None:
    app = _twitter_app(tmp_path)
    app._platform.create_users([("alice", "bio")])
    app._user_mapping = {"Alice Smith": "alice"}
    app._platform.create_users([("bob", "bio")])
    app._platform.create_post("alice", "hello world")
    app._platform.create_post("bob", "hi there")
    app._platform.init_recsys(recsys_type="twitter_tfidf")
    _stub_scoring(app._platform)
    assert app._platform.update_recommendations(max_posts=5) >= 0
    with app._platform.get_connection() as conn:
        before = conn.execute("SELECT COUNT(*) AS n FROM recommendations").fetchone()["n"]

    # Unknown display names resolve to zero users: nothing may be cleared.
    app.update_recommendations(active_agent_names=["Ghost Agent"])
    with app._platform.get_connection() as conn:
        after = conn.execute("SELECT COUNT(*) AS n FROM recommendations").fetchone()["n"]
    assert after == before
    app._platform.shutdown()


def test_update_component_passes_active_agents_and_skips_empty_steps() -> None:
    calls: list[dict[str, Any]] = []

    class _Backend:
        action_logger = None

        def recsys_active_types(self) -> set[str]:
            return {"twitter"}

        def update_recommendations(
            self, active_user_ids=None, max_posts=10, active_agent_names=None
        ) -> None:
            calls.append({"active_agent_names": active_agent_names, "max_posts": max_posts})

    component = SocialRecommendationUpdateComponent(
        backend=_Backend(), backend_type="twitter_like", default_recsys_type="twitter"
    )
    component.update(step=1, agents=[_Agent("Alice"), _Agent("Bob")])
    assert calls == [{"active_agent_names": ["Alice", "Bob"], "max_posts": 10}]

    # An all-inactive step must not trigger a (full!) recompute.
    component2 = SocialRecommendationUpdateComponent(
        backend=_Backend(), backend_type="twitter_like", default_recsys_type="twitter"
    )
    calls.clear()
    component2.update(step=1, agents=[])
    assert calls == []

    # lazy=False keeps the original full-population call.
    component3 = SocialRecommendationUpdateComponent(
        backend=_Backend(),
        backend_type="twitter_like",
        default_recsys_type="twitter",
        lazy=False,
    )
    calls.clear()
    component3.update(step=1, agents=[_Agent("Alice")])
    assert calls == [{"active_agent_names": None, "max_posts": 10}]


def test_update_component_falls_back_for_backends_without_scoping() -> None:
    calls: list[dict[str, Any]] = []

    class _LegacyBackend:
        action_logger = None

        def recsys_active_types(self) -> set[str]:
            return {"twitter"}

        def update_recommendations(self, active_user_ids=None, max_posts=10) -> None:
            calls.append({"active_user_ids": active_user_ids, "max_posts": max_posts})

    component = SocialRecommendationUpdateComponent(
        backend=_LegacyBackend(), backend_type="twitter_like", default_recsys_type="twitter"
    )
    component.update(step=1, agents=[_Agent("Alice")])
    assert calls == [{"active_user_ids": None, "max_posts": 10}]


# ------------------------------------------------------------ probe sampling


def test_probe_sampling_is_seed_stable_and_bounded() -> None:
    policy = ProbeDeploymentPolicy(sample_k=3)
    orchestrator = ProbeDeploymentOrchestrator({}, probe_event_logger=None, policy=policy, seed=7)
    agents = [_Agent(f"agent_{i}") for i in range(10)]

    first = orchestrator._select_agents(agents, step=4)
    second = orchestrator._select_agents(list(reversed(agents)), step=4)
    assert len(first) == 3
    assert {a.name for a in first} == {a.name for a in second}, (
        "selection must not depend on roster order"
    )

    other_step = orchestrator._select_agents(agents, step=5)
    assert len(other_step) == 3

    other_seed = ProbeDeploymentOrchestrator(
        {}, probe_event_logger=None, policy=policy, seed=8
    )._select_agents(agents, step=4)
    assert len(other_seed) == 3


def test_probe_sample_fraction_and_validation() -> None:
    policy = ProbeDeploymentPolicy.from_probes_config({"deployment": {"sample_fraction": 0.25}})
    orchestrator = ProbeDeploymentOrchestrator({}, probe_event_logger=None, policy=policy, seed=1)
    agents = [_Agent(f"a{i}") for i in range(10)]
    assert len(orchestrator._select_agents(agents, step=0)) == 3  # ceil(0.25 * 10)

    import pytest

    with pytest.raises(ValueError, match="sample_k"):
        ProbeDeploymentPolicy.from_probes_config({"deployment": {"sample_k": 0}})
    with pytest.raises(ValueError, match="sample_fraction"):
        ProbeDeploymentPolicy.from_probes_config({"deployment": {"sample_fraction": 1.5}})
    with pytest.raises(ValueError, match="not both"):
        ProbeDeploymentPolicy.from_probes_config(
            {"deployment": {"sample_k": 2, "sample_fraction": 0.5}}
        )


# -------------------------------------------------------- checkpoint save seam


def _checkpoint_payload() -> dict[str, Any]:
    db_bytes = b"sqlite-bytes-" * 64
    return {
        "schema_version": 6,
        "step": 3,
        "checkpoint_counter": 3,
        "runtime_metadata": {"game_masters": []},
        "shared_memories_table": {"abc123": ["shared line"]},
        "objects": {
            "Alice": {
                "class_path": "tests.fake.Agent",
                "role": "agent",
                "compat": None,
                "params": {"name": "Alice"},
                "state": {"episode": 3},
            },
            "gm": {
                "class_path": "tests.fake.GM",
                "role": "game_master",
                "compat": None,
                "params": {"sequence": 0},
                "state": {
                    "backend": {
                        "backend_type": "twitter_like",
                        "state": {
                            "db_snapshot_b64": base64.b64encode(db_bytes).decode("ascii"),
                            "user_mapping": {"Alice": "alice"},
                        },
                    }
                },
            },
        },
    }


def test_sharded_checkpoint_round_trips_with_db_sidecar(tmp_path) -> None:
    payload = _checkpoint_payload()
    strategy = ShardedCheckpointSaveStrategy(objects_per_shard=1)
    manifest_file = strategy.save(payload, step=3, checkpoint_path=str(tmp_path))

    assert manifest_file.endswith("step_3_checkpoint.json")
    manifest = json.loads((tmp_path / "step_3_checkpoint.json").read_text())
    assert manifest["format"] == "sharded"
    assert len(manifest["objects_shards"]) == 2, "objects_per_shard=1 with 2 objects"
    assert "objects" not in manifest
    sidecars = list(tmp_path.glob("step_3_gm_*.db"))
    assert len(sidecars) == 1, "the DB snapshot must land as a raw sidecar file"
    original_b64 = payload["objects"]["gm"]["state"]["backend"]["state"]["db_snapshot_b64"]
    assert sidecars[0].read_bytes() == base64.b64decode(original_b64)

    reloaded = load_checkpoint_file(manifest_file)
    assert reloaded == payload, "load must reassemble the exact monolithic payload"

    # The strategy must not mutate live object state handed in by get_state().
    assert payload["objects"]["gm"]["state"]["backend"]["state"]["db_snapshot_b64"] == original_b64


def test_monolithic_save_matches_previous_format(tmp_path) -> None:
    payload = _checkpoint_payload()
    path = MonolithicJsonSaveStrategy().save(payload, step=3, checkpoint_path=str(tmp_path))
    assert json.loads((tmp_path / "step_3_checkpoint.json").read_text()) == payload
    assert load_checkpoint_file(path) == payload


def test_build_checkpoint_save_strategy_slot() -> None:
    import pytest

    assert isinstance(build_checkpoint_save_strategy(None), MonolithicJsonSaveStrategy)

    class _Slot:
        def __init__(self, built_in: str, params: dict | None = None) -> None:
            self.built_in = built_in
            self.class_path = None
            self.params = params or {}

    strategy = build_checkpoint_save_strategy(_Slot("sharded", {"objects_per_shard": 7}))
    assert isinstance(strategy, ShardedCheckpointSaveStrategy)
    assert strategy.objects_per_shard == 7
    with pytest.raises(ValueError, match="Unknown sim.checkpoint.save.built_in"):
        build_checkpoint_save_strategy(_Slot("bogus"))


# ------------------------------------------------------- telemetry O(active)


def test_recorder_keeps_counts_and_gates_name_lists(tmp_path) -> None:
    result = StepResult(active_agent_names=("Bob", "Alice"))

    metrics = SimMetricsCollector.reset()
    recorder = DefaultEngineRecorder(output_rootname=str(tmp_path))
    recorder.record_episode(episode=0, duration_s=0.1, total_agents=5, step_result=result)
    episode = metrics.to_dict()["episode_metrics"][0]
    assert episode["active_agents"] == 2
    assert "active_agent_names" not in episode

    metrics = SimMetricsCollector.reset()
    recorder = DefaultEngineRecorder(output_rootname=str(tmp_path), record_active_agent_names=True)
    recorder.record_episode(episode=0, duration_s=0.1, total_agents=5, step_result=result)
    episode = metrics.to_dict()["episode_metrics"][0]
    assert episode["active_agent_names"] == ["Alice", "Bob"]
    SimMetricsCollector.reset()
