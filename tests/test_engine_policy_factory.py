from __future__ import annotations

import json

from mastodon_sim.environments.engines.policies.action_chunk import (
    FixedCountActionChunkPolicy,
    OpenEndedActionChunkPolicy,
    SingleActionChunkPolicy,
)
from mastodon_sim.environments.engines.policies.factory import (
    build_action_loop_policy,
    build_probe_schedule_policy,
)
from mastodon_sim.environments.engines.policies.probe_schedule import (
    DisabledProbeSchedulePolicy,
    FixedIntervalProbeSchedulePolicy,
    StepProbeSchedulePolicy,
)


class _FakeEngine:
    def __init__(self, responses: list[object] | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self._responses = list(responses or [])

    def _run_single_entity_action(self, **kwargs):
        self.calls.append(kwargs)
        if self._responses:
            return self._responses.pop(0)
        return "rendered"


def test_build_action_loop_policy_defaults_to_single_action() -> None:
    policy = build_action_loop_policy(None)
    assert isinstance(policy, SingleActionChunkPolicy)


def test_build_action_loop_policy_supports_fixed_count_params() -> None:
    policy = build_action_loop_policy({"built_in": "fixed_count", "params": {"count": 4}})
    assert isinstance(policy, FixedCountActionChunkPolicy)
    assert policy.count == 4


def test_build_action_loop_policy_supports_class_path() -> None:
    policy = build_action_loop_policy(
        {
            "class_path": (
                "mastodon_sim.environments.engines.policies.action_chunk.FixedCountActionChunkPolicy"
            ),
            "params": {"count": 3},
        }
    )
    assert isinstance(policy, FixedCountActionChunkPolicy)
    assert policy.count == 3


def test_build_probe_schedule_policy_defaults_and_fixed_interval() -> None:
    default_policy = build_probe_schedule_policy(None)
    assert isinstance(default_policy, StepProbeSchedulePolicy)

    fixed = build_probe_schedule_policy(
        {
            "built_in": "fixed_interval",
            "params": {"start_step": 2, "every_n_steps": 3},
        }
    )
    assert isinstance(fixed, FixedIntervalProbeSchedulePolicy)
    assert fixed.should_run_probe_phase(step=1, orchestrator=None) is False
    assert fixed.should_run_probe_phase(step=2, orchestrator=None) is True
    assert fixed.should_run_probe_phase(step=5, orchestrator=None) is True


def test_build_probe_schedule_policy_supports_disabled() -> None:
    policy = build_probe_schedule_policy({"built_in": "disabled"})
    assert isinstance(policy, DisabledProbeSchedulePolicy)
    assert policy.should_run_probe_phase(step=99, orchestrator=None) is False


def test_fixed_count_policy_runs_exact_number_of_actions() -> None:
    engine = _FakeEngine(["a1", "a2", "a3"])
    policy = FixedCountActionChunkPolicy(count=3)
    result = policy.run(
        engine=engine,
        game_master=object(),
        entity=object(),
        action_spec=object(),
        skip_actions=False,
        verbose=False,
    )

    assert result == "a3"
    assert len(engine.calls) == 3


def test_open_ended_policy_stops_on_finished_action() -> None:
    """Test that open-ended policy stops when agent outputs 'Finished action episode'."""
    engine = _FakeEngine(
        [
            {
                "raw": "ACTION TYPE: POST\nTARGET ID: \nCONTENT: first\nREASONING: test",
                "rendered": "agent: first",
                "resolved": "Alice posted",
            },
            {
                "raw": "ACTION TYPE: FINISHED\nTARGET ID: \nCONTENT: \nREASONING: done",
                "rendered": "agent: FINISHED",
                "resolved": "Finished action episode",
            },
            "SHOULD_NOT_RUN",
        ]
    )
    policy = OpenEndedActionChunkPolicy(max_actions=5, finished_action_signal="FINISHED")
    result = policy.run(
        engine=engine,
        game_master=object(),
        entity=object(),
        action_spec=object(),
        skip_actions=False,
        verbose=False,
    )

    # Should have executed 2 actions (POST, then explicit FINISHED signal stops the loop).
    assert result == "agent: first"
    assert len(engine.calls) == 2


def test_open_ended_policy_does_not_stop_on_finish_word_in_content() -> None:
    engine = _FakeEngine(
        [
            {
                "raw": (
                    "ACTION TYPE: POST\n"
                    "TARGET ID: \n"
                    "CONTENT: We should finish this policy debate tomorrow.\n"
                    "REASONING: includes finish word but not finish action"
                ),
                "rendered": "agent: post-1",
                "resolved": "Alice posted",
            },
            {
                "raw": "ACTION TYPE: POST\nTARGET ID: \nCONTENT: second post\nREASONING: keep going",
                "rendered": "agent: post-2",
                "resolved": "Alice posted",
            },
            {
                "raw": "ACTION TYPE: FINISH\nTARGET ID: \nCONTENT: \nREASONING: done",
                "rendered": "agent: finish",
                "resolved": "Finished action episode",
            },
        ]
    )
    policy = OpenEndedActionChunkPolicy(max_actions=5, finished_action_signal="FINISHED")
    result = policy.run(
        engine=engine,
        game_master=object(),
        entity=object(),
        action_spec=object(),
        skip_actions=False,
        verbose=False,
    )

    assert result == "agent: post-2"
    assert len(engine.calls) == 3


def test_open_ended_policy_accepts_legacy_done_token() -> None:
    policy = build_action_loop_policy(
        {
            "built_in": "open_ended",
            "params": {"max_actions": 4, "done_token": "FINISHED"},
        }
    )

    assert isinstance(policy, OpenEndedActionChunkPolicy)
    assert policy.finished_action_signal == "FINISHED"


def test_open_ended_policy_counts_multi_tool_calls_toward_cap() -> None:
    engine = _FakeEngine(
        [
            {
                "raw": json.dumps(
                    {
                        "tool_calls": [
                            {"name": "post", "arguments": {"content": "a"}},
                            {"name": "like", "arguments": {"post_id": "1"}},
                        ]
                    }
                ),
                "rendered": "agent: multi-1",
                "resolved": "posted\nliked",
            },
            {
                "raw": json.dumps(
                    {
                        "tool_call": {"name": "post", "arguments": {"content": "b"}},
                    }
                ),
                "rendered": "agent: single-2",
                "resolved": "posted",
            },
            "SHOULD_NOT_RUN",
        ]
    )

    policy = OpenEndedActionChunkPolicy(max_actions=3, finished_action_signal="FINISHED")
    result = policy.run(
        engine=engine,
        game_master=object(),
        entity=object(),
        action_spec=object(),
        skip_actions=False,
        verbose=False,
    )

    assert result == "agent: single-2"
    assert len(engine.calls) == 2


def test_fixed_count_policy_counts_multi_tool_calls_toward_budget() -> None:
    engine = _FakeEngine(
        [
            {
                "raw": json.dumps(
                    {
                        "tool_calls": [
                            {"name": "post", "arguments": {"content": "a"}},
                            {"name": "like", "arguments": {"post_id": "1"}},
                        ]
                    }
                ),
                "rendered": "agent: multi-1",
                "resolved": "posted\nliked",
            },
            "SHOULD_NOT_RUN",
        ]
    )

    policy = FixedCountActionChunkPolicy(count=2)
    result = policy.run(
        engine=engine,
        game_master=object(),
        entity=object(),
        action_spec=object(),
        skip_actions=False,
        verbose=False,
    )

    assert result == "agent: multi-1"
    assert len(engine.calls) == 1
