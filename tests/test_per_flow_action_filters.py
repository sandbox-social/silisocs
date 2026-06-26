"""Tests for per-flow enforced action filters in resolve components."""

from __future__ import annotations

import logging

import pytest

from silisocs.environments.backends.base import RUNTIME_AGENT_PARAM
from silisocs.environments.gm.components.resolve import (
    ACTION_FLOW_FILTERED_COUNTER,
    GenericActionResolveComponent,
    ParsedActionResolveComponent,
    ToolCallingResolveComponent,
)
from silisocs.runtime.telemetry.collector import SimMetricsCollector
from silisocs.runtime.types import ActionOutput


class _Descriptor:
    def __init__(self, name: str, selectable_name: str) -> None:
        self.name = name
        self.selectable_name = selectable_name
        self.parameters: list = []


class _FakeBackend:
    """Backend stub exposing the action surface + the three dispatch entrypoints."""

    def __init__(self) -> None:
        self.parse_calls: list[tuple[str, dict]] = []
        self.generic_calls: list[tuple[str, str]] = []
        self.tool_calls: list[tuple[str, dict]] = []

    def _all_actions(self) -> list[_Descriptor]:
        return [
            _Descriptor("create_tweet", "post"),
            _Descriptor("like_tweet", "like"),
            _Descriptor("repost_tweet", "repost"),
            _Descriptor("follow_user", "follow"),
        ]

    def actions(self) -> list[_Descriptor]:
        return self._all_actions()

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        self.parse_calls.append((user_name, action_data))
        return f"did:{action_data['action_type']}"

    def invoke_action_by_name(self, action_name: str, args_text: str) -> str:
        self.generic_calls.append((action_name, args_text))
        return f"did:{action_name}"

    def invoke_action_with_kwargs(self, tool_name: str, payload: dict) -> str:
        self.tool_calls.append((tool_name, dict(payload)))
        return f"did:{tool_name}"


class _VerbBackend(_FakeBackend):
    """Mirrors a real social backend where selectable_name == canonical name (no
    verb bridging via descriptors), so the verb<->method bridge must come from
    action_aliases().
    """

    def _all_actions(self) -> list[_Descriptor]:
        return [
            _Descriptor("create_tweet", "create_tweet"),
            _Descriptor("like_tweet", "like_tweet"),
            _Descriptor("repost_tweet", "repost_tweet"),
            _Descriptor("follow_user", "follow_user"),
        ]

    def action_aliases(self) -> list[set[str]]:
        return [
            {"post", "create_tweet"},
            {"like", "like_tweet"},
            {"repost", "retweet", "boost", "repost_tweet"},
        ]


def _parsed_action(action_type: str, target_id: str = "") -> str:
    return (
        f"ACTION TYPE: {action_type}\nTARGET ID: {target_id}\nCONTENT: hello\nREASONING: because\n"
    )


def _counter() -> int:
    return SimMetricsCollector.get().counter(ACTION_FLOW_FILTERED_COUNTER)


# --------------------------------------------------------------------------- #
# Backward compatibility: no filter => pure pass-through
# --------------------------------------------------------------------------- #


def test_no_filter_is_pass_through() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = ParsedActionResolveComponent(backend=backend)
    result = component.resolve(active_agent="Alice", action=_parsed_action("post"))
    assert result == "did:post"
    assert len(backend.parse_calls) == 1
    assert _counter() == 0


def test_empty_filter_map_is_pass_through() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = ParsedActionResolveComponent(
        backend=backend, flow_action_filters={}, agent_flow_tags={"Alice": "lurker"}
    )
    component.resolve(active_agent="Alice", action=_parsed_action("post"))
    assert len(backend.parse_calls) == 1
    assert _counter() == 0


# --------------------------------------------------------------------------- #
# ParsedAction enforcement
# --------------------------------------------------------------------------- #


def test_enabled_allowlist_blocks_unlisted_action() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = ParsedActionResolveComponent(
        backend=backend,
        flow_action_filters={"lurker": {"enabled_actions": ["like"]}},
        agent_flow_tags={"Bob": "lurker"},
    )
    blocked = component.resolve(active_agent="Bob", action=_parsed_action("post"))
    assert "not permitted" in blocked
    assert backend.parse_calls == []
    assert _counter() == 1

    allowed = component.resolve(active_agent="Bob", action=_parsed_action("like", target_id="5"))
    assert allowed == "did:like"
    assert len(backend.parse_calls) == 1


def test_excluded_denylist_blocks_listed_action() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = ParsedActionResolveComponent(
        backend=backend,
        flow_action_filters={"poster": {"excluded_actions": ["follow"]}},
        agent_flow_tags={"Alice": "poster"},
    )
    blocked = component.resolve(active_agent="Alice", action=_parsed_action("follow"))
    assert "not permitted" in blocked
    assert backend.parse_calls == []

    allowed = component.resolve(active_agent="Alice", action=_parsed_action("post"))
    assert allowed == "did:post"


def test_alias_matching_bridges_canonical_and_selectable_names() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    # Filter is written with the canonical method name; agent uses the selectable verb.
    component = ParsedActionResolveComponent(
        backend=backend,
        flow_action_filters={"lurker": {"enabled_actions": ["create_tweet"]}},
        agent_flow_tags={"Bob": "lurker"},
    )
    allowed = component.resolve(active_agent="Bob", action=_parsed_action("post"))
    assert allowed == "did:post"

    blocked = component.resolve(active_agent="Bob", action=_parsed_action("like", target_id="5"))
    assert "not permitted" in blocked


def test_default_flow_filter_is_fallback_for_unmapped_flows() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = ParsedActionResolveComponent(
        backend=backend,
        flow_action_filters={"default": {"enabled_actions": ["like"]}},
        agent_flow_tags={"Bob": "some_unmapped_flow"},
    )
    blocked = component.resolve(active_agent="Bob", action=_parsed_action("post"))
    assert "not permitted" in blocked


def test_agent_without_flow_tag_uses_default_flow() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = ParsedActionResolveComponent(
        backend=backend,
        flow_action_filters={"default": {"enabled_actions": ["like"]}},
        agent_flow_tags={},  # no mapping -> agent resolves to "default"
    )
    blocked = component.resolve(active_agent="Ghost", action=_parsed_action("post"))
    assert "not permitted" in blocked


# --------------------------------------------------------------------------- #
# Isolation: one shared component enforces different filters per flow
# --------------------------------------------------------------------------- #


def test_per_flow_isolation_within_one_component() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = ParsedActionResolveComponent(
        backend=backend,
        flow_action_filters={
            "lurker": {"enabled_actions": ["like"]},
            "poster": {"enabled_actions": ["post"]},
        },
        agent_flow_tags={"Bob": "lurker", "Alice": "poster"},
    )
    # Bob (lurker) may not post; Alice (poster) may.
    assert "not permitted" in component.resolve(active_agent="Bob", action=_parsed_action("post"))
    assert component.resolve(active_agent="Alice", action=_parsed_action("post")) == "did:post"
    # Bob may like; Alice may not.
    assert (
        component.resolve(active_agent="Bob", action=_parsed_action("like", target_id="5"))
        == "did:like"
    )
    assert "not permitted" in component.resolve(
        active_agent="Alice", action=_parsed_action("like", target_id="5")
    )


# --------------------------------------------------------------------------- #
# Generic + ToolCalling modes
# --------------------------------------------------------------------------- #


def test_generic_mode_enforces_filter() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = GenericActionResolveComponent(
        backend=backend,
        flow_action_filters={"x": {"excluded_actions": ["follow"]}},
        agent_flow_tags={"Bob": "x"},
    )
    blocked = component.resolve(active_agent="Bob", action="ACTION: follow_user")
    assert "not permitted" in blocked
    assert backend.generic_calls == []

    allowed = component.resolve(active_agent="Bob", action="ACTION: create_tweet")
    assert allowed == "did:create_tweet"
    assert backend.generic_calls == [("create_tweet", "")]


def test_tool_calling_mode_filters_per_call_and_allows_finished() -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    component = ToolCallingResolveComponent(
        backend=backend,
        flow_action_filters={"x": {"enabled_actions": ["like_tweet"]}},
        agent_flow_tags={"Bob": "x"},
    )
    action = ActionOutput.from_tool_calls(
        [("like_tweet", {"post_id": 5}), ("follow_user", {"target": "x"}), ("FINISHED", {})]
    )
    result = component.resolve(active_agent="Bob", action=action)
    # like_tweet allowed + FINISHED always allowed -> both invoked; follow_user blocked.
    invoked = [name for name, _ in backend.tool_calls]
    assert invoked == ["like_tweet", "FINISHED"]
    assert "not permitted" in result
    assert _counter() == 1


# --------------------------------------------------------------------------- #
# Build-time validation
# --------------------------------------------------------------------------- #


def test_unknown_action_name_warns_but_does_not_raise(caplog) -> None:
    SimMetricsCollector.reset()
    backend = _FakeBackend()
    with caplog.at_level(logging.WARNING):
        ParsedActionResolveComponent(
            backend=backend,
            flow_action_filters={"x": {"enabled_actions": ["totally_made_up"]}},
            agent_flow_tags={"Bob": "x"},
        )
    assert any("not declared on backend" in record.message for record in caplog.records)


def test_build_resolve_component_threads_filter_and_flow_tags() -> None:
    # Integration: params.flow_action_filters + context.agent_flow_tags must reach
    # the resolve component through the factory seam.
    from silisocs.environments.gm.components.factory import build_resolve_component
    from silisocs.environments.gm.context import GameMasterContext

    SimMetricsCollector.reset()
    backend = _FakeBackend()
    context = GameMasterContext(
        gm_name="gm",
        backend=backend,
        agents=(),
        agent_names=("Bob",),
        agent_flow_tags={"Bob": "lurker"},
        model=None,
        event_logger=None,
    )
    component = build_resolve_component(
        {
            "built_in": "parsed_action",
            "params": {"flow_action_filters": {"lurker": {"enabled_actions": ["like"]}}},
        },
        context=context,
        action_prompt_template="",
    )
    blocked = component.resolve_action("Bob", ActionOutput.from_text(_parsed_action("post")))
    assert "not permitted" in blocked
    assert backend.parse_calls == []


# --------------------------------------------------------------------------- #
# Vocabulary-agnostic matching via backend action_aliases() (custom-mode verbs
# vs backend method names) — regression guard for the silent over-block/leak.
# --------------------------------------------------------------------------- #


def test_method_name_denylist_blocks_synonymous_verb() -> None:
    # excluded by method name; agent emits the domain verb -> must still be blocked
    # (was a silent denylist LEAK before action_aliases bridging).
    component = ParsedActionResolveComponent(
        backend=_VerbBackend(),
        flow_action_filters={"x": {"excluded_actions": ["like_tweet"]}},
        agent_flow_tags={"Bob": "x"},
    )
    assert component._action_allowed("Bob", "like") is False
    assert component._action_allowed("Bob", "post") is True


def test_method_name_allowlist_admits_synonymous_verb() -> None:
    # allowlisted by method name; agent emits the domain verb -> must be allowed
    # (was a silent ALLOWLIST OVER-BLOCK before action_aliases bridging).
    component = ParsedActionResolveComponent(
        backend=_VerbBackend(),
        flow_action_filters={"x": {"enabled_actions": ["create_tweet"]}},
        agent_flow_tags={"Bob": "x"},
    )
    assert component._action_allowed("Bob", "post") is True
    assert component._action_allowed("Bob", "like") is False


def test_verb_filter_admits_synonymous_method_name() -> None:
    # The reverse direction: filter written with verbs, agent (e.g. generic mode)
    # emits the canonical method name.
    component = ParsedActionResolveComponent(
        backend=_VerbBackend(),
        flow_action_filters={"x": {"enabled_actions": ["post", "repost"]}},
        agent_flow_tags={"Bob": "x"},
    )
    assert component._action_allowed("Bob", "create_tweet") is True
    assert component._action_allowed("Bob", "boost") is True  # repost synonym
    assert component._action_allowed("Bob", "like_tweet") is False


# --------------------------------------------------------------------------- #
# Tool-calling atomicity: a forbidden actor-arg later in a batch must not leave
# earlier valid calls partially committed.
# --------------------------------------------------------------------------- #


def test_tool_calling_is_atomic_on_forbidden_actor_arg() -> None:
    backend = _FakeBackend()
    component = ToolCallingResolveComponent(backend=backend)
    action = ActionOutput.from_tool_calls(
        [
            ("create_tweet", {"status": "hi"}),  # valid, would run first
            ("like_tweet", {RUNTIME_AGENT_PARAM: "Bob", "post_id": 1}),  # forbidden actor arg
        ]
    )
    with pytest.raises(ValueError, match="runtime-owned actor"):
        component.resolve(active_agent="Alice", action=action)
    # No call committed before the ValueError — the batch is atomic.
    assert backend.tool_calls == []


class _Entity:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def _agent_name(self) -> str:
        return self.name


class _GmApp(_FakeBackend):
    """Dummy backend sufficient to build a MultiFlowGameMaster and resolve actions."""

    action_logger = None
    backend_type = "twitter_like"

    def action_aliases(self) -> list[set[str]]:
        return [{"post", "create_tweet"}, {"like", "like_tweet"}]

    def set_action_filters(self, *, enabled_actions=None, excluded_actions=None) -> None:
        del enabled_actions, excluded_actions

    def action_catalog(self) -> list[dict[str, str]]:
        return [{"selectable_name": "POST"}, {"selectable_name": "LIKE"}]

    def initialize(self, agent_names, **kwargs) -> None:
        del agent_names, kwargs

    def get_state(self) -> dict:
        return {}


def test_multiflow_gm_resolve_action_enforces_per_flow_filter(tmp_path, monkeypatch) -> None:
    # End-to-end through the GM per-flow component-routing seam: the filter on the
    # resolve slot params must be enforced by gm.resolve_action.
    from silisocs.environments.gm import game_master as gm_module
    from silisocs.environments.gm.game_master import MultiFlowGameMaster

    SimMetricsCollector.reset()
    app = _GmApp()
    monkeypatch.setattr(gm_module, "create_backend_app", lambda **kwargs: app)

    gm = MultiFlowGameMaster(
        model=object(),
        agents=[_Entity("Bob")],
        name="gm",
        agent_flow_tags={"Bob": "lurker"},
        backend_config={"backend_type": "twitter_like", "output_rootname": str(tmp_path)},
        components={
            "initialize": {"built_in": "social_media"},
            "next_acting": {"built_in": "all_agents"},
            "observe": {"built_in": "timeline_every_turn"},
            "resolve": {
                "built_in": "parsed_action",
                "params": {"flow_action_filters": {"lurker": {"enabled_actions": ["like"]}}},
            },
            "update": {"built_in": "social_recommendation"},
        },
        action_prompt_template="Act",
        action_mode="custom",
        tool_calling_mode="none",
    )

    blocked = gm.resolve_action("Bob", ActionOutput.from_text(_parsed_action("post")))
    assert "not permitted" in blocked
    assert app.parse_calls == []

    allowed = gm.resolve_action(
        "Bob", ActionOutput.from_text(_parsed_action("like", target_id="5"))
    )
    assert allowed == "did:like"
    assert len(app.parse_calls) == 1


def test_invalid_filter_shapes_raise() -> None:
    backend = _FakeBackend()
    with pytest.raises(ValueError, match="must be a mapping"):
        ParsedActionResolveComponent(backend=backend, flow_action_filters=["lurker"])
    with pytest.raises(ValueError, match="empty flow name"):
        ParsedActionResolveComponent(
            backend=backend, flow_action_filters={"  ": {"enabled_actions": []}}
        )
    with pytest.raises(ValueError, match="must be a mapping"):
        ParsedActionResolveComponent(
            backend=backend, flow_action_filters={"x": ["enabled_actions"]}
        )
