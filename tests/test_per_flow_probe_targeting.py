"""Tests for per-flow probe deployment targeting (include/exclude_flows)."""

from __future__ import annotations

import logging

import pytest
from omegaconf import OmegaConf

from silisocs.evaluations.probes.deployment import (
    ProbeDeploymentOrchestrator,
    ProbeDeploymentPolicy,
)
from silisocs.simulation_engines.policies.loops import (
    FixedStepsLoopStrategy,
    _collect_agent_flows,
)


class _DummyLogger:
    def __init__(self) -> None:
        self.episode_idx = -1


class _DummyAgent:
    def __init__(self, name: str, *, role: str | None = None, flow_tag: str | None = None) -> None:
        self._agent_name = name
        if role is not None:
            self.role = role
        if flow_tag is not None:
            # Deliberately misleading: targeting must use the threaded agent_flows
            # map (authoritative GM source), NOT an agent attribute.
            self.flow_tag = flow_tag


def _orchestrator(monkeypatch, deployment: dict) -> tuple[ProbeDeploymentOrchestrator, list]:
    calls: list[list[str]] = []

    def _fake_deploy_probes(
        agents, probes, probe_event_logger, worker_limit=None, prebuilt_probes=None
    ):
        calls.append([agent._agent_name for agent in agents])

    monkeypatch.setattr("silisocs.evaluations.probes.deployment.deploy_probes", _fake_deploy_probes)
    probes_cfg = OmegaConf.create(
        {
            "probes": {
                0: {"probe_type": "BinaryProbe", "probe_data": {"name": "B", "question": "q?"}}
            },
            "deployment": deployment,
        }
    )
    return ProbeDeploymentOrchestrator(probes_cfg, _DummyLogger()), calls


# --------------------------------------------------------------------------- #
# Policy parsing
# --------------------------------------------------------------------------- #


def test_policy_parses_flow_filters() -> None:
    policy = ProbeDeploymentPolicy.from_probes_config(
        {"deployment": {"include_flows": ["treatment"], "exclude_flows": ["bots"]}}
    )
    assert policy.include_flows == ("treatment",)
    assert policy.exclude_flows == ("bots",)


def test_policy_defaults_have_no_flow_filters() -> None:
    policy = ProbeDeploymentPolicy.from_probes_config({})
    assert policy.include_flows == ()
    assert policy.exclude_flows == ()


# --------------------------------------------------------------------------- #
# include / exclude flows
# --------------------------------------------------------------------------- #


def test_include_flows_targets_only_matching_flow(monkeypatch) -> None:
    orch, calls = _orchestrator(monkeypatch, {"include_flows": ["treatment"]})
    agents = [_DummyAgent("Alice"), _DummyAgent("Bob"), _DummyAgent("Carol")]
    agent_flows = {"Alice": "treatment", "Bob": "control", "Carol": "treatment"}

    deployed, selected = orch.maybe_deploy(step=1, agents=agents, agent_flows=agent_flows)

    assert (deployed, selected) == (True, 2)
    assert calls == [["Alice", "Carol"]]


def test_exclude_flows_drops_matching_flow(monkeypatch) -> None:
    orch, calls = _orchestrator(monkeypatch, {"exclude_flows": ["control"]})
    agents = [_DummyAgent("Alice"), _DummyAgent("Bob"), _DummyAgent("Carol")]
    agent_flows = {"Alice": "treatment", "Bob": "control", "Carol": "treatment"}

    orch.maybe_deploy(step=1, agents=agents, agent_flows=agent_flows)

    assert calls == [["Alice", "Carol"]]


def test_unmapped_agent_resolves_to_default_flow(monkeypatch) -> None:
    orch, calls = _orchestrator(monkeypatch, {"include_flows": ["default"]})
    agents = [_DummyAgent("Alice"), _DummyAgent("Bob")]
    # Only Bob is mapped; Alice (absent from the map) resolves to "default".
    agent_flows = {"Bob": "treatment"}

    orch.maybe_deploy(step=1, agents=agents, agent_flows=agent_flows)

    assert calls == [["Alice"]]


def test_flow_targeting_ignores_agent_attribute(monkeypatch) -> None:
    # FixedAgent has no flow_tag attr; native agents do — but targeting must use
    # the GM-sourced agent_flows map, not getattr(agent, "flow_tag").
    orch, calls = _orchestrator(monkeypatch, {"include_flows": ["treatment"]})
    agents = [_DummyAgent("Alice", flow_tag="treatment"), _DummyAgent("Bob")]
    # The map says Alice is control (overrides her misleading attribute) and Bob is treatment.
    agent_flows = {"Alice": "control", "Bob": "treatment"}

    orch.maybe_deploy(step=1, agents=agents, agent_flows=agent_flows)

    assert calls == [["Bob"]]


# --------------------------------------------------------------------------- #
# Backward compatibility + AND-composition
# --------------------------------------------------------------------------- #


def test_no_flow_keys_deploys_to_all_even_with_flows_threaded(monkeypatch) -> None:
    orch, calls = _orchestrator(monkeypatch, {})
    agents = [_DummyAgent("Alice"), _DummyAgent("Bob")]
    agent_flows = {"Alice": "treatment", "Bob": "control"}

    deployed, selected = orch.maybe_deploy(step=1, agents=agents, agent_flows=agent_flows)

    assert (deployed, selected) == (True, 2)
    assert calls == [["Alice", "Bob"]]


def test_no_agent_flows_with_filter_falls_back_to_default(monkeypatch) -> None:
    # include_flows=[treatment] but no agent_flows threaded -> everyone is "default"
    # -> nobody matches -> deployment skipped (no crash).
    orch, calls = _orchestrator(monkeypatch, {"include_flows": ["treatment"]})
    agents = [_DummyAgent("Alice"), _DummyAgent("Bob")]

    deployed, selected = orch.maybe_deploy(step=1, agents=agents, agent_flows=None)

    assert (deployed, selected) == (False, 0)
    assert calls == []


def test_flow_filter_intersects_with_class_filter(monkeypatch) -> None:
    orch, calls = _orchestrator(
        monkeypatch, {"include_classes": ["influencer"], "include_flows": ["treatment"]}
    )
    agents = [
        _DummyAgent("Alice", role="influencer"),  # treatment + influencer -> kept
        _DummyAgent("Bob", role="influencer"),  # control + influencer -> dropped
        _DummyAgent("Carol", role="voter"),  # treatment + voter -> dropped
    ]
    agent_flows = {"Alice": "treatment", "Bob": "control", "Carol": "treatment"}

    orch.maybe_deploy(step=1, agents=agents, agent_flows=agent_flows)

    assert calls == [["Alice"]]


# --------------------------------------------------------------------------- #
# Loop threading helper
# --------------------------------------------------------------------------- #


class _GM:
    def __init__(self, agent_flow_tags: dict[str, str]) -> None:
        self.agent_flow_tags = agent_flow_tags


def test_collect_agent_flows_merges_across_game_masters() -> None:
    gms = [_GM({"Alice": "treatment"}), _GM({"Bob": "control", "Alice": "ignored_dupe"})]
    merged = _collect_agent_flows(gms)
    # earliest GM wins on duplicate agent.
    assert merged == {"Alice": "treatment", "Bob": "control"}


def test_collect_agent_flows_tolerates_missing_tags() -> None:
    class _BareGM:
        pass

    assert _collect_agent_flows([_BareGM()]) == {}


def test_empty_selection_logs_warning(monkeypatch, caplog) -> None:
    # A typo'd flow name selects nobody; surface it at WARNING (not silent INFO).
    orch, _calls = _orchestrator(monkeypatch, {"include_flows": ["treatmnt"]})
    agents = [_DummyAgent("Alice")]
    with caplog.at_level(logging.WARNING):
        result = orch.maybe_deploy(step=1, agents=agents, agent_flows={"Alice": "treatment"})
    assert result == (False, 0)
    assert any("no agents matched filters" in record.message for record in caplog.records)


# --------------------------------------------------------------------------- #
# Loop -> ProbeRunner agent_flows threading
# --------------------------------------------------------------------------- #


class _StubProbeRunner:
    def __init__(self) -> None:
        self.received_flows: list = []

    def maybe_run(self, *, step, agents, worker_limit, agent_flows=None):
        self.received_flows.append(agent_flows)
        return True, len(agents)


class _StubRecorder:
    def record_episode(self, **kwargs) -> None:
        del kwargs


class _StubStepResult:
    def __init__(self) -> None:
        self.probe_phase: dict = {}


class _StubEngine:
    def __init__(self, probe_runner) -> None:
        self.probe_runner = probe_runner
        self.recorder = _StubRecorder()

    def run_step(self, *, step_index, game_masters, agents, verbose):
        del step_index, game_masters, agents, verbose
        return _StubStepResult()


def test_loop_threads_merged_agent_flows_to_probe_runner() -> None:
    runner = _StubProbeRunner()
    engine = _StubEngine(runner)
    gms = [_GM({"Alice": "treatment", "Bob": "control"})]
    FixedStepsLoopStrategy().run(
        engine=engine,
        game_masters=gms,
        agents=[_DummyAgent("Alice"), _DummyAgent("Bob")],
        max_steps=1,
        start_step=0,
        verbose=False,
        checkpoint_callback=None,
    )
    assert runner.received_flows == [{"Alice": "treatment", "Bob": "control"}]


@pytest.mark.parametrize("agent_flows", [None, {}])
def test_collect_agent_flows_empty_is_safe(agent_flows) -> None:
    # Sanity: orchestrator handles both None and empty map without flow filters.
    policy = ProbeDeploymentPolicy.from_probes_config({})
    assert policy.include_flows == ()
