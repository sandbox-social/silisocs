# mypy: disable-error-code="arg-type"
# ^ Relaxed for this experimental suite's minimal fake model (not a LanguageModel subclass).
"""Contract tests for the Hermes adapter (fake ``AIAgent`` injected via factory seam).

The real ``hermes-agent`` library can't be co-installed (its ``openai==2.24.0`` pin
conflicts with the silisocs ``openai<2.0.0`` pin), so these verify the silisocs-side
contract with a fake ``AIAgent``: proxy-bound construction, tool routing through the
current ToolSurface, history snapshot/restore, and the probe path. A live test against
real Hermes is opt-in behind ``HERMES_LIVE=1`` (not run here).
"""

from __future__ import annotations

from typing import Any

import pytest

from silisocs.agents.harness.adapter import HarnessProbeRequest, HarnessTurnRequest
from silisocs.agents.harness.base import HarnessAgent
from silisocs.agents.harness.bridge import ToolSurface
from silisocs.agents.harness.hermes import HermesAdapter, HermesAgent, _execute_current_surface
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp


class _FakeAIAgent:
    """Simulates Hermes: one run_conversation calls the silisocs tool, then finishes."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.messages: list[str] = []

    def run_conversation(
        self, message: str, conversation_history: list[Any] | None = None
    ) -> dict[str, Any]:
        self.messages.append(message)
        # Simulate the harness deciding to call one backend action via the tool.
        tool_result = _execute_current_surface("create_tweet", {"status": "hermes post"})
        history = list(conversation_history or [])
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": f"done: {tool_result[:20]}"})
        return {
            "messages": history,
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }


@pytest.fixture
def twitter(tmp_path: Any) -> Any:
    app = TwitterLikeApp(db_path=str(tmp_path / "tw.db"))
    app.setup_social_state(agent_names=["Alice"], following_graph={})
    yield app
    app.shutdown()


def _adapter(captured: dict[str, Any] | None = None) -> HermesAdapter:
    def factory(**kwargs: Any) -> _FakeAIAgent:
        if captured is not None:
            captured.update(kwargs)
        return _FakeAIAgent(**kwargs)

    return HermesAdapter(
        name="Alice",
        persona="Alice is a teacher.",
        model_name="gpt-4o-mini",
        aiagent_factory=factory,
    )


def test_adapter_builds_against_proxy_and_routes_tools(twitter: Any) -> None:
    captured: dict[str, Any] = {}
    adapter = _adapter(captured)
    adapter.bind_model_proxy("http://127.0.0.1:9/v1", "routing-token")
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    result = adapter.run_turn(
        HarnessTurnRequest(agent_name="Alice", prompt="Take your turn.", surface=surface)
    )
    # AIAgent built against the proxy with the persona as the ephemeral system prompt.
    assert captured["base_url"] == "http://127.0.0.1:9/v1"
    assert captured["api_key"] == "routing-token"
    assert captured["ephemeral_system_prompt"] == "Alice is a teacher."
    # The tool routed to the surface and landed a real backend action.
    assert len(surface.executed) == 1
    assert surface.executed[0].name == "create_tweet"
    assert surface.executed[0].ok
    assert result.final_text.startswith("done")
    assert result.usage["prompt_tokens"] == 5


def test_message_lists_available_actions(twitter: Any) -> None:
    adapter = _adapter()
    adapter.bind_model_proxy("http://p/v1", "t")
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    adapter.run_turn(HarnessTurnRequest(agent_name="Alice", prompt="go", surface=surface))
    sent = adapter._agent.messages[0]
    assert "silisocs_action" in sent
    assert "create_tweet" in sent


def test_history_snapshot_and_restore(twitter: Any) -> None:
    adapter = _adapter()
    adapter.bind_model_proxy("http://p/v1", "t")
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    adapter.run_turn(HarnessTurnRequest(agent_name="Alice", prompt="go", surface=surface))
    snap = adapter.snapshot()
    assert snap["history"]  # conversation carried forward

    restored = _adapter()
    restored.bind_model_proxy("http://p/v1", "t")
    restored.restore(snap)
    assert restored.snapshot()["history"] == snap["history"]


def test_probe_runs_without_tools() -> None:
    adapter = _adapter()
    adapter.bind_model_proxy("http://p/v1", "t")
    result = adapter.run_probe(
        HarnessProbeRequest(agent_name="Alice", prompt="Pick", options=("yes", "no"))
    )
    assert result.startswith("done")


def test_tool_handler_without_active_surface_is_safe() -> None:
    # Called outside a turn (no ContextVar set) -> guarded message, no crash.
    assert "No active tool surface" in _execute_current_surface("create_tweet", {})


def test_hermes_agent_is_harness_agent_and_composes_persona() -> None:
    class _Model:
        _model_name = "gpt-4o-mini"

    agent = HermesAgent(
        _Model(),
        name="Alice",
        aiagent_factory=lambda **k: _FakeAIAgent(**k),
        context="Alice is a teacher.",
        bio="Loves civics.",
    )
    assert isinstance(agent, HarnessAgent)
    assert "Alice is a teacher." in agent._persona
    assert "Loves civics." in agent._persona
