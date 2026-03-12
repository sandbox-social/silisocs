"""Tests for probe deployment orchestration."""

from __future__ import annotations

from omegaconf import OmegaConf

from mastodon_sim.evaluations.probes.deployment import ProbeDeploymentOrchestrator


class _DummyLogger:
    def __init__(self):
        self.episode_idx = -1


class _DummyAgent:
    def __init__(self, name: str):
        self._agent_name = name


def test_probe_orchestrator_default_schedule(monkeypatch) -> None:
    calls = []

    def _fake_deploy_probes(
        agents, probes, probe_event_logger, worker_limit=None, prebuilt_queries=None
    ):
        calls.append(
            {
                "agents": [agent._agent_name for agent in agents],
                "episode_idx": probe_event_logger.episode_idx,
                "worker_limit": worker_limit,
                "prebuilt_queries": prebuilt_queries,
            }
        )

    monkeypatch.setattr(
        "mastodon_sim.evaluations.probes.deployment.deploy_probes", _fake_deploy_probes
    )

    probes_cfg = OmegaConf.create(
        {
            "queries": {
                0: {
                    "query_type": "BinaryProbe",
                    "query_data": {
                        "name": "TestBinary",
                        "question": "Do you like tests?",
                    },
                },
            },
        }
    )
    logger = _DummyLogger()
    orchestrator = ProbeDeploymentOrchestrator(probes_cfg, logger)
    agents = [_DummyAgent("Alice"), _DummyAgent("Bob")]

    assert orchestrator.maybe_deploy(step=0, agents=agents) == (False, 0)
    assert orchestrator.maybe_deploy(step=1, agents=agents) == (True, 2)
    assert len(calls) == 1
    assert calls[0]["agents"] == ["Alice", "Bob"]
    assert calls[0]["episode_idx"] == 1
    # Cached queries should be passed through
    assert calls[0]["prebuilt_queries"] is not None
    assert len(calls[0]["prebuilt_queries"]) == 1


def test_probe_orchestrator_filters_and_cadence(monkeypatch) -> None:
    calls = []

    def _fake_deploy_probes(
        agents, probes, probe_event_logger, worker_limit=None, prebuilt_queries=None
    ):
        calls.append([agent._agent_name for agent in agents])

    monkeypatch.setattr(
        "mastodon_sim.evaluations.probes.deployment.deploy_probes", _fake_deploy_probes
    )

    probes_cfg = OmegaConf.create(
        {
            "queries": {
                0: {
                    "query_type": "BinaryProbe",
                    "query_data": {
                        "name": "TestBinary",
                        "question": "Do you like tests?",
                    },
                },
            },
            "deployment": {
                "enabled": True,
                "start_step": 2,
                "every_n_steps": 2,
                "include_entities": ["Alice", "Bob"],
                "exclude_entities": ["Bob"],
            },
        }
    )
    logger = _DummyLogger()
    orchestrator = ProbeDeploymentOrchestrator(probes_cfg, logger)
    agents = [_DummyAgent("Alice"), _DummyAgent("Bob"), _DummyAgent("Carol")]

    assert orchestrator.maybe_deploy(step=1, agents=agents) == (False, 0)
    assert orchestrator.maybe_deploy(step=2, agents=agents) == (True, 1)
    assert orchestrator.maybe_deploy(step=3, agents=agents) == (False, 0)
    assert orchestrator.maybe_deploy(step=4, agents=agents) == (True, 1)
    assert calls == [["Alice"], ["Alice"]]
