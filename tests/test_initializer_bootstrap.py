from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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
    def initialize(self, *, agents: Sequence[Agent], game_master: Any, context: Any) -> None:
        assert [agent.name for agent in agents] == ["Alice"]
        assert isinstance(context, InitializationContext)
        game_master.events.append("gm")


class _GameMaster:
    name = "gm"

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.app = type("_App", (), {"platform_type": "twitter_like"})()
        self._initializer = _GMOwnedInitializer()

    def initialize(self, *, agents: Sequence[Agent], context: InitializationContext) -> None:
        self._initializer.initialize(agents=agents, game_master=self, context=context)

    def resolve_action(self, agent_name: str, action: ActionOutput) -> str:
        assert agent_name == "Alice"
        self.events.append(f"simulation:{action.tool_calls[0].arguments['status']}")
        return "posted"


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
