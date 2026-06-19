from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest

from silisocs.agents.base_agent import Agent
from silisocs.initialization.agents import AgentInitializer
from silisocs.initialization.context import InitializationContext
from silisocs.initialization.game_masters import (
    DefaultGameMasterInitializerStrategy,
    GameMasterInitializer,
)
from silisocs.initialization.simulation import SeedPostsSimulationInitializer
from silisocs.initialization.simulation.seed_posts import SeedPostProvider
from silisocs.runtime.checkpointing import SocialActionEventReplayRestore
from silisocs.runtime.types import ActionOutput


@dataclass
class _Agent:
    name: str
    events: list[str]

    def initialize(self, context: Any | None = None) -> None:
        del context
        self.events.append("agent")


class _AgentInitializer(AgentInitializer):
    def initialize(self, **kwargs: Any) -> None:
        for agent in kwargs["agents"]:
            agent.initialize({})


class _GMOwnedInitializer(GameMasterInitializer):
    def initialize(self, *, agents: Sequence[Agent], gm_context: Any, context: Any) -> None:
        assert [agent.name for agent in agents] == ["Alice"]
        assert isinstance(context, InitializationContext)
        gm_context.events.append("gm")


class _GameMaster:
    name = "gm"

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.backend_type = "twitter_like"
        self._initializer = _GMOwnedInitializer()

    def initialize(self, *, agents: Sequence[Agent], context: InitializationContext) -> None:
        self._initializer.initialize(agents=agents, gm_context=self, context=context)

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        assert agent_name == "Alice"
        self.events.append(f"simulation:{action.tool_calls[0].arguments['status']}")
        return "posted"


@dataclass(frozen=True)
class _Action:
    name: str
    selectable_name: str


class _ReplayBackend:
    def __init__(self, actions: list[_Action]) -> None:
        self._actions = actions

    def actions(self) -> list[_Action]:
        return list(self._actions)


class _ReplayGameMaster:
    def __init__(
        self,
        *,
        name: str,
        actions: list[_Action],
        agent_flow_tags: dict[str, str] | None = None,
        flow_chains: dict[str, list[str]] | None = None,
        owned_flows: tuple[str, ...] = (),
    ) -> None:
        self.name = name
        self.backend = _ReplayBackend(actions)
        self.agent_flow_tags = agent_flow_tags or {}
        self.flow_chains = flow_chains or {}
        self.owned_flows = owned_flows
        self.replayed: list[tuple[str, str]] = []

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        self.replayed.append((agent_name, action.tool_calls[0].name))
        return "replayed"


class _SeedProvider(SeedPostProvider):
    def get_seed_posts(
        self,
        *,
        agents: Sequence[Agent],
        context: InitializationContext,
    ) -> dict[str, str]:
        del context
        return {agent.name: f"hello from {agent.name}" for agent in agents}


def test_initialization_phases_run_agent_gm_simulation_in_order() -> None:
    events: list[str] = []
    agents = cast(Sequence[Agent], [_Agent("Alice", events)])
    gm = _GameMaster(events)
    context = InitializationContext()

    _AgentInitializer().initialize(agents=agents, model=cast(Any, object()), context=context)
    DefaultGameMasterInitializerStrategy().initialize(
        agents=agents,
        game_masters=[gm],
        context=context,
    )
    SeedPostsSimulationInitializer(seed_post_provider=_SeedProvider()).initialize(
        agents=agents,
        game_masters=[gm],
        model=cast(Any, object()),
        context=context,
    )

    assert events == ["agent", "gm", "simulation:hello from Alice"]


def test_gm_phase_rejects_game_master_without_initialize() -> None:
    try:
        DefaultGameMasterInitializerStrategy().initialize(
            agents=cast(Sequence[Agent], [_Agent("Alice", [])]),
            game_masters=[object()],
            context=InitializationContext(),
        )
    except TypeError as exc:
        assert "initialize" in str(exc)
    else:
        raise AssertionError("GM without initialize should fail loudly")


def test_checkpoint_restore_replays_backend_action_events(tmp_path: Path) -> None:
    events_file = tmp_path / "action_events.jsonl"
    replay_row = {
        "episode": 0,
        "event_type": "action",
        "label": "post",
        "source_user": "Alice",
        "data": {"post_text": "checkpoint hello"},
    }
    events_file.write_text(json.dumps(replay_row) + "\n", encoding="utf-8")

    events: list[str] = []
    gm = _GameMaster(events)

    SocialActionEventReplayRestore().restore(
        game_masters=[gm],
        action_events_file=events_file,
        checkpoint_step=1,
    )

    assert events == ["simulation:checkpoint hello"]


def test_checkpoint_restore_routes_replay_to_matching_gm_backend(tmp_path: Path) -> None:
    events_file = tmp_path / "action_events.jsonl"
    replay_row = {
        "episode": 0,
        "event_type": "action",
        "label": "post",
        "source_user": "Alice",
        "data": {"post_text": "checkpoint hello"},
    }
    events_file.write_text(json.dumps(replay_row) + "\n", encoding="utf-8")
    router_gm = _ReplayGameMaster(
        name="router_gm",
        actions=[],
        agent_flow_tags={"Alice": "social"},
        flow_chains={"social": ["audit_gm", "social_gm"]},
        owned_flows=("social",),
    )
    audit_gm = _ReplayGameMaster(name="audit_gm", actions=[], owned_flows=("social",))
    social_gm = _ReplayGameMaster(
        name="social_gm",
        actions=[_Action(name="create_tweet", selectable_name="create_tweet")],
        owned_flows=("social",),
    )

    SocialActionEventReplayRestore().restore(
        game_masters=[router_gm, audit_gm, social_gm],
        action_events_file=events_file,
        checkpoint_step=1,
    )

    assert router_gm.replayed == []
    assert audit_gm.replayed == []
    assert social_gm.replayed == [("Alice", "create_tweet")]


def _write_replay_event(tmp_path: Path) -> Path:
    events_file = tmp_path / "action_events.jsonl"
    replay_row = {
        "episode": 0,
        "event_type": "action",
        "label": "post",
        "source_user": "Alice",
        "data": {"post_text": "checkpoint hello"},
    }
    events_file.write_text(json.dumps(replay_row) + "\n", encoding="utf-8")
    return events_file


def test_checkpoint_restore_requires_flow_metadata_for_multi_gm_replay(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="agent_flow_tags"):
        SocialActionEventReplayRestore().restore(
            game_masters=[
                _ReplayGameMaster(name="first", actions=[]),
                _ReplayGameMaster(name="second", actions=[]),
            ],
            action_events_file=_write_replay_event(tmp_path),
            checkpoint_step=1,
        )


def test_checkpoint_restore_rejects_unknown_gm_in_flow_chain(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown GM"):
        SocialActionEventReplayRestore().restore(
            game_masters=[
                _ReplayGameMaster(
                    name="router",
                    actions=[],
                    agent_flow_tags={"Alice": "social"},
                    flow_chains={"social": ["missing"]},
                ),
                _ReplayGameMaster(name="other", actions=[]),
            ],
            action_events_file=_write_replay_event(tmp_path),
            checkpoint_step=1,
        )


def test_checkpoint_restore_rejects_missing_action_match_in_chain(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not route action"):
        SocialActionEventReplayRestore().restore(
            game_masters=[
                _ReplayGameMaster(
                    name="router",
                    actions=[],
                    agent_flow_tags={"Alice": "social"},
                    flow_chains={"social": ["router", "audit"]},
                ),
                _ReplayGameMaster(name="audit", actions=[]),
            ],
            action_events_file=_write_replay_event(tmp_path),
            checkpoint_step=1,
        )


def test_checkpoint_restore_rejects_ambiguous_action_match_in_chain(tmp_path: Path) -> None:
    post_action = _Action(name="create_tweet", selectable_name="create_tweet")

    with pytest.raises(ValueError, match="ambiguous"):
        SocialActionEventReplayRestore().restore(
            game_masters=[
                _ReplayGameMaster(
                    name="router",
                    actions=[post_action],
                    agent_flow_tags={"Alice": "social"},
                    flow_chains={"social": ["router", "audit"]},
                ),
                _ReplayGameMaster(name="audit", actions=[post_action]),
            ],
            action_events_file=_write_replay_event(tmp_path),
            checkpoint_step=1,
        )
