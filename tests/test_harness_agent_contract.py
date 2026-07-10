# mypy: disable-error-code="arg-type, index"
# ^ Relaxed for this experimental harness suite's deliberately-minimal test doubles
#   (fake models that don't subclass LanguageModel; indexing the optional
#   ActionOutput.structured payload that a harness turn always populates).
"""Contract tests for harness-backed agents (fake adapter, no external deps).

These exercise the whole harness seam in-process: the Tool Bridge against real
twitter_like AND reddit_like backends (backend-agnosticism), the ``HarnessAgent``
turn/probe/checkpoint contract, the zero-config GM integration (the default action-prompt
binds the surface for harness agents and leaves native agents untouched; the shared
resolve base records self-describing harness turns), and the persona composition. The
``FakeHarnessAdapter`` IS the reference harness, so these tests double as the public spec
every real adapter must satisfy.

A full run through the real runner (session, artifacts, run manifest) lives in
``tests/test_harness_e2e.py`` (subprocess).
"""

from __future__ import annotations

from typing import Any

import pytest

from silisocs.agents.harness.adapter import HarnessProbeRequest, HarnessTurnRequest
from silisocs.agents.harness.base import HarnessAgent, compose_persona
from silisocs.agents.harness.bridge import (
    HARNESS_TOOL_FAILURES_COUNTER,
    ToolSurface,
)
from silisocs.agents.harness.fake import FakeHarnessAdapter, FakeHarnessAgent
from silisocs.agents.harness.types import HarnessTurnResult
from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.environments.gm.components.factory import build_action_prompt_component
from silisocs.environments.gm.components.resolve import (
    HARNESS_TURNS_COUNTER,
    ParsedActionResolveComponent,
)
from silisocs.runtime.telemetry.collector import SimMetricsCollector
from silisocs.runtime.types import HARNESS_TURN_KEY, ActionOutput, ActionSpec, OutputType


class _NoModel:
    """Minimal LanguageModel stand-in for the probe (model-mode) path."""

    def sample_text(self, prompt: str, **kwargs: Any) -> str:
        return "model probe answer"

    def sample_choice(self, prompt: str, responses: Any, **kwargs: Any) -> tuple[int, str, dict]:
        options = list(responses)
        return 0, str(options[0]) if options else "", {}

    def sample_structured(self, prompt: str, schema: Any, **kwargs: Any) -> dict:
        return {"answer": "model structured probe"}


class _CaptureLogger:
    """EventLogger stand-in that records ``log`` calls in memory."""

    def __init__(self) -> None:
        self.episode_idx = 0
        self.rows: list[dict[str, Any]] = []

    def log(self, row: dict[str, Any]) -> None:
        self.rows.append(dict(row))


class _Ctx:
    """Lightweight GameMasterContext stand-in for factory builds."""

    def __init__(self, backend: Any, agents: list[Any], flow_tags: dict[str, str]) -> None:
        self.backend = backend
        self.agents = agents
        self.agent_names = [a.name for a in agents]
        self.model = _NoModel()
        self.agent_flow_tags = dict(flow_tags)


@pytest.fixture
def twitter(tmp_path: Any) -> Any:
    app = TwitterLikeApp(db_path=str(tmp_path / "tw.db"))
    app.setup_social_state(agent_names=["Alice", "Bob"], following_graph={})
    yield app
    app.shutdown()


@pytest.fixture
def reddit(tmp_path: Any) -> Any:
    app = RedditLikeApp(db_path=str(tmp_path / "rd.db"))
    app.setup_social_state(agent_names=["Alice", "Bob"])
    yield app
    app.shutdown()


@pytest.fixture(autouse=True)
def _reset_metrics() -> Any:
    SimMetricsCollector.reset()
    yield
    SimMetricsCollector.reset()


# --------------------------------------------------------------------------- #
# Tool Bridge
# --------------------------------------------------------------------------- #


def test_tool_surface_executes_and_logs_harness_event(twitter: Any) -> None:
    logger = _CaptureLogger()
    surface = ToolSurface(backend=twitter, agent_name="Alice", harness_logger=logger)
    call = surface.execute("create_tweet", {"status": "bridge post"})
    assert call.ok
    assert "posted a tweet" in call.result
    assert surface.executed == (call,)
    assert logger.rows and logger.rows[0]["kind"] == "tool_executed"
    assert logger.rows[0]["action"] == "create_tweet"
    assert logger.rows[0]["agent_name"] == "Alice"


def test_tool_surface_rejects_reserved_actor_argument(twitter: Any) -> None:
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    call = surface.execute("create_tweet", {"status": "x", "agent_name": "Mallory"})
    assert not call.ok
    assert call.error == "reserved-argument"
    # No post should have been created by the rejected call.
    assert "posted a tweet" not in call.result


def test_tool_surface_respects_backend_action_filters(twitter: Any) -> None:
    # The surface adds no filtering of its own — it inherits the backend's own
    # enabled/excluded catalog, so an excluded action is neither offered nor executable.
    twitter.set_action_filters(enabled_actions=None, excluded_actions=["like_tweet"])
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    assert "create_tweet" in surface.tool_names()
    assert "like_tweet" not in surface.tool_names()
    call = surface.execute("like_tweet", {"post_id": "1"})
    assert "Unknown action" in call.result


def test_tool_surface_backend_exception_becomes_result(monkeypatch: Any, twitter: Any) -> None:
    def _boom(name: str, args: dict) -> str:
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(twitter, "invoke_action_with_kwargs", _boom)
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    call = surface.execute("create_tweet", {"status": "x"})
    assert not call.ok
    assert "backend exploded" in call.result
    assert SimMetricsCollector.get().counter(HARNESS_TOOL_FAILURES_COUNTER) == 1


# --------------------------------------------------------------------------- #
# HarnessAgent turns (backend-agnostic)
# --------------------------------------------------------------------------- #


def _harness_turn(agent: HarnessAgent, surface: ToolSurface, prompt: str = "Act.") -> ActionOutput:
    spec = ActionSpec(
        prompt=prompt, output_type=OutputType.TEXT, extra_args={"tool_surface": surface}
    )
    return agent.act(spec)


def test_harness_agent_turn_on_twitter(twitter: Any) -> None:
    agent = FakeHarnessAgent(
        _NoModel(),
        name="Alice",
        script=[{"name": "create_tweet", "arguments": {"status": "harness on twitter"}}],
    )
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    out = _harness_turn(agent, surface)
    assert out.output_type == OutputType.TEXT
    payload = out.structured[HARNESS_TURN_KEY]
    assert payload["tool_calls"] == 1 and payload["failures"] == 0
    # The action really landed in the backend (its confirmation echoes the content).
    assert surface.executed[0].ok
    assert "harness on twitter" in surface.executed[0].result


def test_harness_agent_turn_on_reddit(reddit: Any) -> None:
    agent = FakeHarnessAgent(
        _NoModel(),
        name="Alice",
        script=[
            {
                "name": "create_reddit_post",
                "arguments": {
                    "subreddit": "general",
                    "title": "Hi",
                    "content": "harness on reddit",
                },
            }
        ],
    )
    surface = ToolSurface(backend=reddit, agent_name="Alice")
    out = _harness_turn(agent, surface)
    payload = out.structured[HARNESS_TURN_KEY]
    assert payload["tool_calls"] == 1 and payload["failures"] == 0
    assert surface.executed[0].ok
    # Reddit's post confirmation references the title; the feed read proves persistence.
    assert "harness on reddit" in reddit.get_home_feed(agent_name="Alice", limit=10)


def test_harness_agent_auto_post_fills_first_string_param(twitter: Any) -> None:
    # No script: the fake adapter auto-detects a post action and fills its content.
    agent = FakeHarnessAgent(_NoModel(), name="Alice", post_content="auto content")
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    out = _harness_turn(agent, surface)
    assert out.structured[HARNESS_TURN_KEY]["tool_calls"] >= 1
    posted = [call for call in surface.executed if call.name == "create_tweet"]
    assert posted and "auto content" in posted[0].result


# --------------------------------------------------------------------------- #
# Self-describing resolve: ANY resolve records harness turns; native output is
# handled normally by the same component (zero-config, no harness resolve built-in).
# --------------------------------------------------------------------------- #


def test_any_resolve_records_harness_turn(twitter: Any) -> None:
    twitter.harness_logger = _CaptureLogger()
    resolver = ParsedActionResolveComponent(backend=twitter)  # the DEFAULT resolve
    output = ActionOutput(
        output_type=OutputType.TEXT,
        text="did stuff",
        structured={HARNESS_TURN_KEY: {"final_text": "did stuff", "tool_calls": 2, "failures": 0}},
    )
    resolved = resolver.resolve_action("Alice", output)
    assert "FINISHED" in resolved
    assert SimMetricsCollector.get().counter(HARNESS_TURNS_COUNTER) == 1
    assert twitter.harness_logger.rows[-1]["kind"] == "turn_completed"


def test_same_resolve_handles_native_output_normally(twitter: Any) -> None:
    # A native agent's output (no harness_turn payload) flows to the normal resolve,
    # so one GM hosts a mixed native+harness population with no special config.
    resolver = ParsedActionResolveComponent(backend=twitter)
    native = ActionOutput.from_text("ACTION TYPE: POST\nCONTENT: native mixed pop\nREASONING: x")
    resolved = resolver.resolve_action("Alice", native)
    assert "posted a tweet" in resolved
    assert "native mixed pop" in resolved
    assert SimMetricsCollector.get().counter(HARNESS_TURNS_COUNTER) == 0


# --------------------------------------------------------------------------- #
# Probes
# --------------------------------------------------------------------------- #


def test_probe_model_mode_uses_language_model(twitter: Any) -> None:
    agent = FakeHarnessAgent(_NoModel(), name="Alice", probe_mode="model")
    spec = ActionSpec(prompt="Pick one.", output_type=OutputType.CHOICE, options=["yes", "no"])
    out = agent.act(spec)
    assert out.output_type == OutputType.CHOICE
    assert out.choice == "yes"


def test_probe_harness_mode_uses_adapter(twitter: Any) -> None:
    agent = FakeHarnessAgent(_NoModel(), name="Alice", probe_mode="harness")
    spec = ActionSpec(prompt="Pick one.", output_type=OutputType.CHOICE, options=["yes", "no"])
    out = agent.act(spec)
    # FakeHarnessAdapter.run_probe returns the first option for choice probes.
    assert out.choice == "yes"


# --------------------------------------------------------------------------- #
# Checkpoint state round-trip
# --------------------------------------------------------------------------- #


def test_harness_agent_state_roundtrip() -> None:
    agent = FakeHarnessAgent(_NoModel(), name="Alice")
    agent.observe("pending observation")
    # Run one (surface-less) turn to advance adapter state.
    agent.act(ActionSpec(prompt="one-shot", output_type=OutputType.TEXT))
    state = agent.get_state()
    assert state["adapter"]["turns"] == 1

    restored = FakeHarnessAgent(_NoModel(), name="Alice")
    restored.observe("stale")
    restored.set_state(state)
    assert restored.get_state()["adapter"]["turns"] == 1
    assert restored.get_state()["observations"] == state["observations"]


# --------------------------------------------------------------------------- #
# Zero-config: the DEFAULT action_prompt binds the surface for harness agents and
# leaves native agents untouched (no `harness` built-in exists).
# --------------------------------------------------------------------------- #


def test_default_action_prompt_binds_surface_for_harness_agent(twitter: Any) -> None:
    agent = FakeHarnessAgent(_NoModel(), name="Alice")
    ctx = _Ctx(twitter, [agent], {"Alice": "default"})
    component = build_action_prompt_component(
        {}, context=ctx, action_prompt_template="Hello {name}", enable_tool_calling=False
    )
    spec = component.action_prompt("Alice")
    assert spec.prompt == "Hello Alice"
    assert isinstance(spec.extra_args.get("tool_surface"), ToolSurface)
    assert spec.extra_args["tool_surface"].agent_name == "Alice"


def test_default_action_prompt_gives_native_agent_no_surface(twitter: Any) -> None:
    class _Native:
        name = "Bob"

    ctx = _Ctx(twitter, [_Native()], {})
    component = build_action_prompt_component(
        {}, context=ctx, action_prompt_template="hi", enable_tool_calling=False
    )
    assert component.action_prompt("Bob").extra_args.get("tool_surface") is None


def test_surface_rebinds_per_backend(twitter: Any, reddit: Any) -> None:
    # A harness agent hopping GMs in a multi-GM chain acts on each backend: the default
    # action_prompt rebuilds the surface from ITS backend each turn.
    agent = FakeHarnessAgent(_NoModel(), name="Alice")
    tw_component = build_action_prompt_component(
        {},
        context=_Ctx(twitter, [agent], {"Alice": "default"}),
        action_prompt_template="",
        enable_tool_calling=False,
    )
    rd_component = build_action_prompt_component(
        {},
        context=_Ctx(reddit, [agent], {"Alice": "default"}),
        action_prompt_template="",
        enable_tool_calling=False,
    )
    tw_surface = tw_component.action_prompt("Alice").extra_args["tool_surface"]
    rd_surface = rd_component.action_prompt("Alice").extra_args["tool_surface"]
    assert "create_tweet" in tw_surface.tool_names()
    assert "create_reddit_post" in rd_surface.tool_names()


# --------------------------------------------------------------------------- #
# Persona composition + adapter contract shape
# --------------------------------------------------------------------------- #


def test_compose_persona_folds_pipeline_fields() -> None:
    persona = compose_persona(
        "",
        context="Alice is a teacher.",
        bio="Loves civics.",
        goal="Inform neighbors.",
        shared_memories=["On a social platform.", "On a social platform."],
    )
    assert "Alice is a teacher." in persona
    assert "Loves civics." in persona
    assert persona.count("On a social platform.") == 1  # deduped


def test_fake_adapter_turn_result_shape(twitter: Any) -> None:
    adapter = FakeHarnessAdapter(script=[{"name": "create_tweet", "arguments": {"status": "x"}}])
    surface = ToolSurface(backend=twitter, agent_name="Alice")
    result = adapter.run_turn(HarnessTurnRequest(agent_name="Alice", prompt="go", surface=surface))
    assert isinstance(result, HarnessTurnResult)
    assert result.finished
    assert len(surface.executed) == 1
    probe = adapter.run_probe(
        HarnessProbeRequest(agent_name="Alice", prompt="?", options=("a", "b"))
    )
    assert isinstance(probe, str)
    assert probe == "a"
