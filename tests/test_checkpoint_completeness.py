"""Tests for checkpoint completeness improvements.

Covers the cross-cutting checkpoint hardening work:

* core staging guards (no-op ``set_state`` detection, identity reconciliation,
  roster-drift warning);
* the backend checkpoint-capability contract and the custom restore-strategy
  ``class_path`` hook plus the non-replayable-backend guard;
* recsys lazy self-heal after a restore (backend rebuilt empty);
* multi-GM backend-path isolation and the db-path collision guard;
* multi-flow ``next_acting`` state fixes;
* the Concordia adapter loud-fail on un-restorable state.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from silisocs.agents.base_agent import Agent
from silisocs.environments.backends.base import SocialBackendApp
from silisocs.environments.backends.mastodon.apps import SocialNetworkApp
from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.resource_market.app import ResourceMarketApp
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.environments.backends.virtual_space.app import VirtualSpaceApp
from silisocs.environments.gm.components.social_media.update import (
    SocialRecommendationUpdateComponent,
)
from silisocs.evaluations.action_events import resolve_action_event_files
from silisocs.runtime.checkpointing.policy import CHECKPOINT_SCHEMA_VERSION
from silisocs.runtime.checkpointing.restore import (
    CheckpointRestoreStrategy,
    SocialActionEventReplayRestore,
    build_checkpoint_restore,
    build_per_gm_checkpoint_restores,
    run_checkpoint_restores,
)
from silisocs.runtime.checkpointing.state import (
    _set_state_is_noop,
    checkpoint_authoritative_gm_names,
    load_checkpoint_into_runtime,
)
from silisocs.runtime.construction.assembly import RuntimeObjects
from silisocs.runtime.construction.game_masters import _isolate_backend_paths
from silisocs.runtime.construction.specs import RuntimeRole, RuntimeSpec
from silisocs.runtime.types import ActionOutput, ToolCall

# --------------------------------------------------------------------------- #
# Stub agents
# --------------------------------------------------------------------------- #


class _StatefulNoSetState(Agent):
    """Agent with a real get_state but only the inherited no-op set_state."""

    def __init__(self, name: str = "ghost") -> None:
        super().__init__(model=None)  # type: ignore[arg-type]  # test stub never calls the model
        self._name = name

    @property
    def name(self) -> str:
        """Return the agent name."""
        return self._name

    def observe(self, observation: str) -> None:
        """Ignore observations (test stub)."""
        del observation

    def act(self, action_spec):
        """Return a no-op action (test stub)."""
        del action_spec
        return ActionOutput.from_text("noop")

    def get_state(self) -> dict:
        """Return non-empty episodic state."""
        return {"important": 1}


class _RealAgent(_StatefulNoSetState):
    """Agent with both get_state and set_state."""

    def __init__(self, name: str = "real") -> None:
        super().__init__(name)
        self.restored: dict | None = None

    def set_state(self, state: dict) -> None:
        """Record restored state."""
        self.restored = dict(state)


def _checkpoint(objects: dict) -> dict:
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "objects": objects,
        "checkpoint_counter": 0,
        "runtime_metadata": {},
    }


def _agent_obj(name: str, class_path: str, state: dict, compat=None) -> dict:
    return {
        "class_path": class_path,
        "role": RuntimeRole.AGENT.value,
        "compat": compat,
        "params": {"name": name},
        "state": state,
    }


# --------------------------------------------------------------------------- #
# Core staging guards
# --------------------------------------------------------------------------- #


def test_set_state_is_noop_detects_inherited_default():
    """The no-op detector distinguishes inherited vs overridden set_state."""
    assert _set_state_is_noop(_StatefulNoSetState()) is True
    assert _set_state_is_noop(_RealAgent()) is False


def test_restore_rejects_state_with_noop_set_state():
    """Restoring non-empty state onto a no-op set_state object is rejected."""
    agent = _StatefulNoSetState("ghost")
    runtime = RuntimeObjects(
        agents=[agent],
        object_specs={
            "ghost": RuntimeSpec(
                class_path="pkg.Ghost", role=RuntimeRole.AGENT, params={"name": "ghost"}
            )
        },
    )
    checkpoint = _checkpoint({"ghost": _agent_obj("ghost", "pkg.Ghost", {"important": 1})})
    with pytest.raises(ValueError, match="no-op set_state"):
        load_checkpoint_into_runtime(runtime, checkpoint, models={}, object_to_model={})


def test_restore_rejects_identity_mismatch():
    """A class_path mismatch between checkpoint and runtime is rejected."""
    agent = _RealAgent("real")
    runtime = RuntimeObjects(
        agents=[agent],
        object_specs={
            "real": RuntimeSpec(
                class_path="pkg.ClassA", role=RuntimeRole.AGENT, params={"name": "real"}
            )
        },
    )
    checkpoint = _checkpoint({"real": _agent_obj("real", "pkg.ClassB", {"x": 1})})
    with pytest.raises(ValueError, match="mismatched object identity"):
        load_checkpoint_into_runtime(runtime, checkpoint, models={}, object_to_model={})


def test_restore_applies_matching_state_and_warns_on_orphan(caplog):
    """Matching state restores, and runtime objects absent from the checkpoint warn."""
    agent = _RealAgent("real")
    orphan = _RealAgent("orphan")
    runtime = RuntimeObjects(
        agents=[agent, orphan],
        object_specs={
            "real": RuntimeSpec(
                class_path="pkg.ClassA", role=RuntimeRole.AGENT, params={"name": "real"}
            ),
            "orphan": RuntimeSpec(
                class_path="pkg.ClassA", role=RuntimeRole.AGENT, params={"name": "orphan"}
            ),
        },
    )
    checkpoint = _checkpoint({"real": _agent_obj("real", "pkg.ClassA", {"x": 7})})
    with caplog.at_level("WARNING"):
        load_checkpoint_into_runtime(runtime, checkpoint, models={}, object_to_model={})
    assert agent.restored == {"x": 7}
    assert "orphan" in caplog.text


# --------------------------------------------------------------------------- #
# Backend checkpoint-capability contract + custom restore hook
# --------------------------------------------------------------------------- #


def test_backend_capability_flags():
    """Every shipped backend self-restores via set_state (provides_checkpoint_state)."""
    for cls in (TwitterLikeApp, RedditLikeApp, ResourceMarketApp, VirtualSpaceApp):
        assert cls.provides_checkpoint_state is True
    # Mastodon now self-restores too: get_state embeds its action history and
    # set_state replays it, so it restores via the default set_state path.
    assert SocialNetworkApp.provides_checkpoint_state is True
    assert SocialNetworkApp.supports_action_replay is True
    assert TwitterLikeApp.supports_action_replay is True


def test_build_checkpoint_restore_built_in_and_class_path():
    """The restore builder resolves both built-in and custom class_path strategies."""
    built = build_checkpoint_restore(
        SimpleNamespace(built_in="social_action_event_replay", class_path=None, params={})
    )
    assert isinstance(built, SocialActionEventReplayRestore)

    via_path = build_checkpoint_restore(
        SimpleNamespace(
            built_in="",
            class_path="silisocs.runtime.checkpointing.restore.SocialActionEventReplayRestore",
            params={},
        )
    )
    assert isinstance(via_path, CheckpointRestoreStrategy)


def test_build_checkpoint_restore_rejects_unknown_and_non_strategy():
    """Unknown built-ins and non-strategy class_paths fail loudly."""
    with pytest.raises(ValueError, match="Unknown"):
        build_checkpoint_restore(SimpleNamespace(built_in="bogus", class_path=None, params={}))
    with pytest.raises(TypeError, match="must subclass"):
        build_checkpoint_restore(
            SimpleNamespace(built_in="", class_path="builtins.dict", params={})
        )


def test_replay_strategy_rejects_non_replayable_backend(tmp_path):
    """Built-in replay refuses a backend that cannot be safely replayed."""
    gm = SimpleNamespace(
        name="gm1",
        backend_type="mastodon",
        backend=SimpleNamespace(supports_action_replay=False),
    )
    with pytest.raises(ValueError, match="cannot be reconstructed"):
        SocialActionEventReplayRestore().restore(
            game_masters=[gm],
            action_events_files=[tmp_path / "missing.jsonl"],
            checkpoint_step=1,
        )


class _DummySocialBackend(SocialBackendApp):
    """Concrete SocialBackendApp for testing the default replay mapping."""

    def name(self) -> str:
        return "dummy"

    def description(self) -> str:
        return "dummy"


def test_default_backend_replay_mapping_is_microblog():
    """The base social backend maps logged labels to the microblog vocabulary."""
    backend = _DummySocialBackend()
    post = backend.event_to_replay_action("post", {"post_text": "hello"})
    assert post.tool_calls[0].name == "create_tweet"
    assert post.tool_calls[0].arguments == {"status": "hello"}
    like = backend.event_to_replay_action("like", {"post_id": "7"})
    assert like.tool_calls[0].name == "like_tweet"
    assert like.tool_calls[0].arguments == {"post_id": "7"}
    follow = backend.event_to_replay_action("follow", {"target_user": "bob"})
    assert follow.tool_calls[0].name == "follow_user"


def test_mastodon_replay_mapping_and_id_remap():
    """Mastodon maps its own vocabulary and remaps old->new toot ids for references."""
    app = SocialNetworkApp()
    app.begin_action_replay()

    post = app.event_to_replay_action("post", {"toot_id": "OLD1", "post_text": "hello"})
    assert post.tool_calls[0].name == "post_toot"
    assert post.tool_calls[0].arguments == {"status": "hello"}

    # Simulate the server assigning a new id when the post is re-created.
    app._record_replayed_post_id("NEW1")

    like = app.event_to_replay_action("like_toot", {"toot_id": "OLD1"})
    assert like.tool_calls[0].name == "like_toot"
    assert like.tool_calls[0].arguments == {"toot_id": "NEW1"}

    boost = app.event_to_replay_action("boost_toot", {"toot_id": "OLD1"})
    assert boost.tool_calls[0].name == "boost_toot"
    assert boost.tool_calls[0].arguments == {"toot_id": "NEW1"}

    # A reply answers its parent (reply_to.toot_id), uses the in_reply_to_id arg,
    # and is itself a new toot whose own id (data.toot_id) is captured for chains.
    reply = app.event_to_replay_action(
        "reply", {"reply_to": {"toot_id": "OLD1"}, "toot_id": "OLDR", "post_text": "hi"}
    )
    assert reply.tool_calls[0].name == "reply_to_toot"
    assert reply.tool_calls[0].arguments == {"in_reply_to_id": "NEW1", "status": "hi"}

    follow = app.event_to_replay_action("follow", {"target_user": "bob"})
    assert follow.tool_calls[0].name == "follow_user"

    # A reference to a toot that was never re-created is skipped, not crashed.
    assert app.event_to_replay_action("like_toot", {"toot_id": "UNSEEN"}) is None

    # Reply chain: the reply re-created above gets a new id, and a like on the
    # *reply* must remap the reply's own old id (OLDR -> NEWR) so it connects.
    app._record_replayed_post_id("NEWR")
    like_on_reply = app.event_to_replay_action("like_toot", {"toot_id": "OLDR"})
    assert like_on_reply.tool_calls[0].arguments == {"toot_id": "NEWR"}

    # begin_action_replay clears the id map for a fresh replay session.
    app.begin_action_replay()
    assert app.event_to_replay_action("like_toot", {"toot_id": "OLD1"}) is None


def test_mastodon_get_state_embeds_action_history(tmp_path):
    """get_state reads the backend's own action log into replay events (actions only)."""
    log = tmp_path / "action_events.jsonl"
    _write_action_log(
        log,
        [
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "Alice",
                "data": {"post_text": "hi"},
            },
            # Non-action bookkeeping rows are excluded.
            {
                "event_type": "timeline_retrieval",
                "episode": 0,
                "label": "get_home_feed",
                "source_user": "Alice",
                "data": {},
            },
        ],
    )
    app = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=str(log)))
    events = app.get_state()["replay_events"]
    assert [e["label"] for e in events] == ["post"]
    assert events[0] == {"label": "post", "source_user": "Alice", "data": {"post_text": "hi"}}


def test_mastodon_set_state_replays_as_original_user():
    """set_state maps each event and re-invokes it as its original user."""
    app = SocialNetworkApp(action_logger=SimpleNamespace(output_filename=""))
    calls: list[tuple[str, dict]] = []
    app.invoke_action_with_kwargs = lambda name, args: calls.append((name, dict(args))) or ""
    app.set_state(
        {
            "replay_events": [
                {"label": "post", "source_user": "Alice", "data": {"post_text": "hi"}},
                {"label": "follow", "source_user": "Bob", "data": {"target_user": "Alice"}},
            ]
        }
    )
    assert calls == [
        ("post_toot", {"status": "hi", "agent_name": "Alice"}),
        ("follow_user", {"target_user": "Alice", "agent_name": "Bob"}),
    ]
    # Empty / absent replay events are a no-op (nothing to rebuild).
    calls.clear()
    app.set_state({})
    app.set_state({"replay_events": []})
    assert calls == []


# --------------------------------------------------------------------------- #
# Recsys lazy self-heal
# --------------------------------------------------------------------------- #


class _StubRecsysBackend:
    """Minimal backend recording init/update calls and live recsys types."""

    def __init__(self) -> None:
        self.init_calls: list[dict] = []
        self.update_calls = 0
        self._active: set[str] = set()
        self.action_logger = None

    def recsys_active_types(self) -> set[str]:
        """Return the live recsys types."""
        return set(self._active)

    def init_recsys(self, *, recsys_type, **kwargs) -> None:
        """Record an init_recsys call and mark the type live."""
        del kwargs
        self.init_calls.append({"recsys_type": recsys_type})
        self._active.add(recsys_type)

    def update_recommendations(self, **kwargs) -> None:
        """Record an update_recommendations call."""
        del kwargs
        self.update_calls += 1


def _make_recsys_component(backend) -> SocialRecommendationUpdateComponent:
    return SocialRecommendationUpdateComponent(
        backend=backend,
        backend_type="twitter_like",
        default_recsys_type="twitter",
        update_every_n_steps=1,
    )


def test_recsys_cold_start_initializes():
    """A cold start initializes the configured recsys type and updates."""
    backend = _StubRecsysBackend()
    component = _make_recsys_component(backend)
    component.update_recommendations()
    assert [c["recsys_type"] for c in backend.init_calls] == ["twitter"]
    assert backend.update_calls == 1


def test_recsys_self_heals_after_restore():
    """A restored component re-inits recsys when the backend reports none live."""
    backend = _StubRecsysBackend()
    component = _make_recsys_component(backend)

    # Simulate a restore: the backend platform was rebuilt empty, but the
    # component restored a non-empty initialized-types flag from the checkpoint.
    component._initialized_recsys_types = {"twitter"}
    component._last_update_episode = None
    assert backend.recsys_active_types() == set()

    component.update_recommendations()

    # The component must reconcile against the (empty) backend and re-init, not
    # trust its stale flag and silently no-op.
    assert [c["recsys_type"] for c in backend.init_calls] == ["twitter"]
    assert backend.update_calls == 1


def test_recsys_disabled_when_no_type_configured():
    """With no configured recsys type the component disables itself."""
    backend = _StubRecsysBackend()
    component = SocialRecommendationUpdateComponent(
        backend=backend, backend_type="twitter_like", default_recsys_type=None
    )
    component.update_recommendations()
    assert backend.init_calls == []
    assert component._recsys_disabled is True


def test_reddit_init_recsys_accepts_component_kwargs():
    """Reddit init_recsys accepts the full kwarg set the component always passes."""
    params = set(inspect.signature(RedditLikeApp.init_recsys).parameters)
    assert {
        "recsys_type",
        "user_context_recent_posts",
        "include_like_trace",
        "like_trace_window",
        "like_trace_weight",
        "include_like_trace_in_context",
    } <= params


# Activity components moved to sim-level participation policies
# (simulation_engines/policies/participation.py), which are stateless by design;
# their coverage lives in tests/test_participation_policy.py.


# --------------------------------------------------------------------------- #
# Multi-GM backend isolation + collision guard
# --------------------------------------------------------------------------- #


def test_multi_gm_isolates_backend_paths():
    """Multiple GMs get distinct per-GM backend output paths."""
    entries = [
        ("alpha", {"backend_type": "twitter_like", "output_rootname": "/tmp/run"}),
        ("beta", {"backend_type": "twitter_like", "output_rootname": "/tmp/run"}),
    ]
    _isolate_backend_paths(entries)
    roots = [cfg["output_rootname"] for _, cfg in entries]
    assert roots[0].endswith("/alpha")
    assert roots[1].endswith("/beta")
    assert roots[0] != roots[1]


def test_single_gm_keeps_flat_layout():
    """A single GM keeps the flat output layout for backward compatibility."""
    entries = [("solo", {"backend_type": "twitter_like", "output_rootname": "/tmp/run"})]
    _isolate_backend_paths(entries)
    assert entries[0][1]["output_rootname"] == "/tmp/run"


def test_multi_gm_db_path_collision_raises():
    """Two GMs resolving to the same backend DB path fail loudly."""
    entries = [
        ("dup", {"backend_type": "twitter_like", "output_rootname": "/tmp/a"}),
        ("dup", {"backend_type": "twitter_like", "output_rootname": "/tmp/a"}),
    ]
    with pytest.raises(ValueError, match="same backend database path"):
        _isolate_backend_paths(entries)


# --------------------------------------------------------------------------- #
# Per-GM authoritative vs replay restore
# --------------------------------------------------------------------------- #


class _CapturingBackend:
    """Replayable backend that maps every post to create_tweet."""

    supports_action_replay = True

    def event_to_replay_action(self, label, data):
        return ActionOutput.from_tool_calls(
            [ToolCall("create_tweet", {"status": str(data.get("post_text", ""))})]
        )

    def actions(self):
        return [SimpleNamespace(name="create_tweet", selectable_name="create_tweet")]


class _CapturingGM:
    """Game master stub that records resolve_action calls."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.backend_type = "twitter_like"
        self.backend = _CapturingBackend()
        self.resolved: list = []

    def resolve_action(self, agent_name, action):
        self.resolved.append((agent_name, action))


def _write_action_log(path, rows) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def test_checkpoint_authoritative_gm_names_identifies_snapshot_gms():
    """Only game masters carrying a backend block are authoritative."""
    checkpoint = {
        "objects": {
            "gm_a": {"role": RuntimeRole.GAME_MASTER.value, "state": {"backend": {}}},
            "gm_b": {"role": RuntimeRole.GAME_MASTER.value, "state": {}},
            "alice": {"role": RuntimeRole.AGENT.value, "state": {}},
        }
    }
    assert checkpoint_authoritative_gm_names(checkpoint) == frozenset({"gm_a"})


def test_replay_skips_authoritative_gms(tmp_path):
    """A GM restored from snapshot is not replayed."""
    gm = _CapturingGM("gm_a")
    log = tmp_path / "action_events.jsonl"
    _write_action_log(
        log,
        [
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "alice",
                "data": {"post_text": "hi"},
            }
        ],
    )
    SocialActionEventReplayRestore().restore(
        game_masters=[gm],
        action_events_files=[log],
        checkpoint_step=5,
        authoritative_gm_names=frozenset({"gm_a"}),
    )
    assert gm.resolved == []


def test_replay_runs_for_non_authoritative_gm(tmp_path):
    """A GM without a snapshot replays its pre-checkpoint events only."""
    gm = _CapturingGM("gm_a")
    log = tmp_path / "action_events.jsonl"
    _write_action_log(
        log,
        [
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "alice",
                "data": {"post_text": "hi"},
            },
            # episode >= checkpoint_step is in the future and must be skipped.
            {
                "event_type": "action",
                "episode": 9,
                "label": "post",
                "source_user": "alice",
                "data": {"post_text": "future"},
            },
        ],
    )
    SocialActionEventReplayRestore().restore(
        game_masters=[gm], action_events_files=[log], checkpoint_step=5
    )
    assert len(gm.resolved) == 1
    agent_name, action = gm.resolved[0]
    assert agent_name == "alice"
    assert action.tool_calls[0].name == "create_tweet"
    assert action.tool_calls[0].arguments == {"status": "hi"}


def test_resolve_action_event_files_flat_and_per_gm(tmp_path):
    """The eval reader finds the flat log, or per-GM subdir logs for multi-GM."""
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "action_events.jsonl").write_text("{}", encoding="utf-8")
    assert resolve_action_event_files(flat) == [flat / "action_events.jsonl"]

    multi = tmp_path / "multi"
    (multi / "alpha").mkdir(parents=True)
    (multi / "beta").mkdir(parents=True)
    (multi / "alpha" / "action_events.jsonl").write_text("{}", encoding="utf-8")
    (multi / "beta" / "action_events.jsonl").write_text("{}", encoding="utf-8")
    assert resolve_action_event_files(multi) == [
        multi / "alpha" / "action_events.jsonl",
        multi / "beta" / "action_events.jsonl",
    ]


def test_per_gm_log_writer_layout_matches_reader(tmp_path):
    """The per-GM output layout the builder produces is exactly what the reader discovers.

    Closes the writer/reader contract: ``_isolate_backend_paths`` nests each
    backend under ``<run>/<gm_name>/``, the GM writes
    ``<output_rootname>/action_events.jsonl`` there, and
    ``resolve_action_event_files`` must rediscover those exact files.
    """
    entries = [
        ("gm_a", {"backend_type": "twitter_like", "output_rootname": str(tmp_path)}),
        ("gm_b", {"backend_type": "reddit_like", "output_rootname": str(tmp_path)}),
    ]
    _isolate_backend_paths(entries)
    written = []
    for _, backend_config in entries:
        # Mirror game_master.py: <output_rootname>/action_events.jsonl.
        log = Path(backend_config["output_rootname"]) / "action_events.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("{}", encoding="utf-8")
        written.append(log)
    assert resolve_action_event_files(tmp_path) == sorted(written)


def test_replay_reads_multiple_action_event_files(tmp_path):
    """Replay consumes every per-GM log file passed to it (multi-GM discovery)."""
    gm = _CapturingGM("gm_a")
    (tmp_path / "gm_a").mkdir()
    (tmp_path / "gm_b").mkdir()
    f1 = tmp_path / "gm_a" / "action_events.jsonl"
    f2 = tmp_path / "gm_b" / "action_events.jsonl"
    _write_action_log(
        f1,
        [
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "alice",
                "data": {"post_text": "one"},
            }
        ],
    )
    _write_action_log(
        f2,
        [
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "bob",
                "data": {"post_text": "two"},
            }
        ],
    )
    # Both per-GM files feed one call; with a single GM all events route to it.
    SocialActionEventReplayRestore().restore(
        game_masters=[gm], action_events_files=[f1, f2], checkpoint_step=5
    )
    assert [c[0] for c in gm.resolved] == ["alice", "bob"]


def test_replay_routes_each_event_to_owning_gm_by_gm_name(tmp_path):
    """Rows are replayed onto the GM that logged them (gm_name), not the first chain GM.

    Regression: with two flow-chained GMs that both persist the same action, the
    same agent's actions land in both per-GM logs; routing by gm_name reconstructs
    each GM's own backend instead of replaying everything onto the first.
    """
    gm_a = _CapturingGM("gm_a")
    gm_b = _CapturingGM("gm_b")
    log = tmp_path / "action_events.jsonl"
    _write_action_log(
        log,
        [
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "alice",
                "gm_name": "gm_a",
                "data": {"post_text": "to-a"},
            },
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "alice",
                "gm_name": "gm_b",
                "data": {"post_text": "to-b"},
            },
        ],
    )
    SocialActionEventReplayRestore().restore(
        game_masters=[gm_a, gm_b], action_events_files=[log], checkpoint_step=5
    )
    assert [a.tool_calls[0].arguments["status"] for _, a in gm_a.resolved] == ["to-a"]
    assert [a.tool_calls[0].arguments["status"] for _, a in gm_b.resolved] == ["to-b"]


def test_replay_skips_event_owned_by_authoritative_gm(tmp_path):
    """A row whose owning GM (gm_name) was snapshot-restored is skipped, not misrouted."""
    snap_gm = _CapturingGM("snap_gm")  # authoritative
    live_gm = _CapturingGM("live_gm")  # replay
    log = tmp_path / "action_events.jsonl"
    _write_action_log(
        log,
        [
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "alice",
                "gm_name": "snap_gm",
                "data": {"post_text": "x"},
            },
            {
                "event_type": "action",
                "episode": 0,
                "label": "post",
                "source_user": "bob",
                "gm_name": "live_gm",
                "data": {"post_text": "y"},
            },
        ],
    )
    SocialActionEventReplayRestore().restore(
        game_masters=[snap_gm, live_gm],
        action_events_files=[log],
        checkpoint_step=5,
        authoritative_gm_names=frozenset({"snap_gm"}),
    )
    assert snap_gm.resolved == []  # owned by authoritative GM -> skipped
    assert [a.tool_calls[0].arguments["status"] for _, a in live_gm.resolved] == ["y"]


class _RecordingStrategy(CheckpointRestoreStrategy):
    """Records the (gm_names, skip_set) it was invoked with."""

    def __init__(self) -> None:
        self.calls: list[tuple[frozenset[str], frozenset[str]]] = []

    def restore(
        self,
        *,
        game_masters,
        action_events_files,
        checkpoint_step,
        authoritative_gm_names=frozenset(),
    ):
        gm_names = frozenset(str(getattr(g, "name", "")) for g in game_masters)
        self.calls.append((gm_names, authoritative_gm_names))


def test_run_checkpoint_restores_applies_per_gm_override(tmp_path):
    """The default strategy skips overridden GMs; each override runs its own strategy."""
    default = _RecordingStrategy()
    custom = _RecordingStrategy()
    gm_a = SimpleNamespace(name="default_gm")
    gm_b = SimpleNamespace(name="custom_gm")
    run_checkpoint_restores(
        game_masters=[gm_a, gm_b],
        default_strategy=default,
        per_gm_strategies={"custom_gm": custom},
        action_events_files=[tmp_path / "x.jsonl"],
        checkpoint_step=5,
        authoritative_gm_names=frozenset(),
    )
    # Default got all GMs but was told to skip the overridden one.
    assert default.calls == [(frozenset({"default_gm", "custom_gm"}), frozenset({"custom_gm"}))]
    # The override strategy ran for its GM, skipping everyone else.
    assert custom.calls == [(frozenset({"default_gm", "custom_gm"}), frozenset({"default_gm"}))]


def test_run_checkpoint_restores_skips_authoritative_override(tmp_path):
    """An overridden GM that is also authoritative is not restored again."""
    default = _RecordingStrategy()
    custom = _RecordingStrategy()
    gm = SimpleNamespace(name="snap_gm")
    run_checkpoint_restores(
        game_masters=[gm],
        default_strategy=default,
        per_gm_strategies={"snap_gm": custom},
        action_events_files=[tmp_path / "x.jsonl"],
        checkpoint_step=5,
        authoritative_gm_names=frozenset({"snap_gm"}),
    )
    assert custom.calls == []  # authoritative snapshot already restored it


def test_build_per_gm_checkpoint_restores_reads_gm_orchestration():
    """Per-GM restore overrides are built only for GMs that declare one."""
    from omegaconf import OmegaConf

    cfg = OmegaConf.create(
        {
            "env": {
                "gm_orchestration": {
                    "gms": [
                        {"gm_name": "plain"},
                        {
                            "gm_name": "custom",
                            "restore": {"built_in": "social_action_event_replay"},
                        },
                    ]
                }
            }
        }
    )
    overrides = build_per_gm_checkpoint_restores(cfg)
    assert set(overrides) == {"custom"}
    assert isinstance(overrides["custom"], SocialActionEventReplayRestore)
    # No gm_orchestration (single-GM run) -> no overrides.
    assert build_per_gm_checkpoint_restores(OmegaConf.create({})) == {}


# --------------------------------------------------------------------------- #
# Concordia adapter hardening
# --------------------------------------------------------------------------- #


def test_concordia_adapter_set_state_raises_without_target_set_state():
    """The Concordia adapter refuses to silently drop un-restorable state."""
    pytest.importorskip("concordia")
    from silisocs.adapters.concordia import ConcordiaAgentAdapter  # noqa: PLC0415

    class _Target:
        name = "x"

        def get_state(self) -> dict:
            return {"memory": [1, 2, 3]}

        # Intentionally no set_state.

    adapter = ConcordiaAgentAdapter(_Target(), model=None)
    # Empty state is a safe no-op.
    adapter.set_state({})
    # Non-empty state with no way to restore must fail loudly.
    with pytest.raises(TypeError, match="set_state"):
        adapter.set_state({"memory": [1, 2, 3]})
