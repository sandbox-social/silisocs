"""Tests for probe deployment orchestration."""

from __future__ import annotations

import pytest
from omegaconf import OmegaConf

from silisocs.evaluations.probes.deployment import (
    ProbeDeploymentOrchestrator,
    ProbeDeploymentPolicy,
)


class _DummyLogger:
    def __init__(self):
        self.episode_idx = -1


class _DummyAgent:
    def __init__(self, name: str):
        self._agent_name = name


def test_probe_orchestrator_default_schedule(monkeypatch) -> None:
    calls = []

    def _fake_deploy_probes(
        agents,
        probes,
        probe_event_logger,
        worker_limit=None,
        prebuilt_probes=None,
        anchor="pre_step",
    ):
        calls.append(
            {
                "agents": [agent._agent_name for agent in agents],
                "episode_idx": probe_event_logger.episode_idx,
                "worker_limit": worker_limit,
                "prebuilt_probes": prebuilt_probes,
            }
        )

    monkeypatch.setattr("silisocs.evaluations.probes.deployment.deploy_probes", _fake_deploy_probes)

    probes_cfg = OmegaConf.create(
        {
            "probes": {
                0: {
                    "probe_type": "BinaryProbe",
                    "probe_data": {
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
    # Cached probes should be passed through
    assert calls[0]["prebuilt_probes"] is not None
    assert len(calls[0]["prebuilt_probes"]) == 1


def test_probe_orchestrator_filters_and_cadence(monkeypatch) -> None:
    calls = []

    def _fake_deploy_probes(
        agents,
        probes,
        probe_event_logger,
        worker_limit=None,
        prebuilt_probes=None,
        anchor="pre_step",
    ):
        calls.append([agent._agent_name for agent in agents])

    monkeypatch.setattr("silisocs.evaluations.probes.deployment.deploy_probes", _fake_deploy_probes)

    probes_cfg = OmegaConf.create(
        {
            "probes": {
                0: {
                    "probe_type": "BinaryProbe",
                    "probe_data": {
                        "name": "TestBinary",
                        "question": "Do you like tests?",
                    },
                },
            },
            "deployment": {
                "enabled": True,
                "start_step": 2,
                "every_n_steps": 2,
                "include_agents": ["Alice", "Bob"],
                "exclude_agents": ["Bob"],
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


def test_probe_orchestrator_supports_list_query_configs(monkeypatch) -> None:
    calls = []

    def _fake_deploy_probes(
        agents,
        probes,
        probe_event_logger,
        worker_limit=None,
        prebuilt_probes=None,
        anchor="pre_step",
    ):
        calls.append(
            {
                "agents": [agent._agent_name for agent in agents],
                "prebuilt_probes": prebuilt_probes,
            }
        )

    monkeypatch.setattr("silisocs.evaluations.probes.deployment.deploy_probes", _fake_deploy_probes)

    probes_cfg = OmegaConf.create(
        {
            "probes": [
                {
                    "probe_name": "vote_intent",
                    "probe_type": "BinaryProbe",
                    "probe_data": {"name": "VoteIntent", "question": "Will you vote?"},
                }
            ],
        }
    )
    logger = _DummyLogger()
    orchestrator = ProbeDeploymentOrchestrator(probes_cfg, logger)
    agents = [_DummyAgent("Alice")]

    assert orchestrator.maybe_deploy(step=1, agents=agents) == (True, 1)
    assert len(calls) == 1
    assert calls[0]["prebuilt_probes"] is not None
    assert getattr(calls[0]["prebuilt_probes"][0], "probe_name", "") == "vote_intent"


# --------------------------------------------------------------------------- #
# Per-probe deployment overrides + loop anchors
# --------------------------------------------------------------------------- #


def _capture_deploy(monkeypatch) -> list[dict]:
    """Monkeypatch deploy_probes to record each group's agents/probes/anchor."""
    calls: list[dict] = []

    def _fake(agents, probes, logger, worker_limit=None, prebuilt_probes=None, anchor="pre_step"):
        calls.append(
            {
                "agents": [a._agent_name for a in agents],
                "probes": [getattr(p, "probe_name", "") for p in (prebuilt_probes or [])],
                "anchor": anchor,
            }
        )

    monkeypatch.setattr("silisocs.evaluations.probes.deployment.deploy_probes", _fake)
    return calls


def _probe(name: str, deployment: dict | None = None) -> dict:
    entry = {
        "probe_type": "BinaryProbe",
        "probe_name": name,
        "probe_data": {"name": name, "question": "?"},
    }
    if deployment is not None:
        entry["deployment"] = deployment
    return entry


def test_per_probe_independent_schedules_still_batch_per_agent(monkeypatch) -> None:
    calls = _capture_deploy(monkeypatch)
    cfg = OmegaConf.create(
        {
            "deployment": {"every_n_steps": 1},
            "probes": {
                "fast": _probe("fast"),
                "slow": _probe("slow", {"start_step": 2, "every_n_steps": 2}),
            },
        }
    )
    orch = ProbeDeploymentOrchestrator(cfg, _DummyLogger())
    agents = [_DummyAgent("Alice")]

    orch.maybe_deploy(step=1, agents=agents)
    orch.maybe_deploy(step=2, agents=agents)

    # step 1: only fast due. step 2: fast + slow due, identical targets -> ONE
    # questionnaire call carrying both probes (batching preserved).
    assert [c["probes"] for c in calls] == [["fast"], ["fast", "slow"]]


def test_per_probe_override_inherits_global_filters(monkeypatch) -> None:
    calls = _capture_deploy(monkeypatch)
    cfg = OmegaConf.create(
        {
            "deployment": {"include_agents": ["Alice"]},
            "probes": {"p": _probe("p", {"every_n_steps": 1})},
        }
    )
    orch = ProbeDeploymentOrchestrator(cfg, _DummyLogger())
    orch.maybe_deploy(step=1, agents=[_DummyAgent("Alice"), _DummyAgent("Bob")])
    # The override sets only every_n_steps; include_agents falls back to global.
    assert calls[0]["agents"] == ["Alice"]


def test_per_probe_unknown_key_raises_naming_probe() -> None:
    cfg = {"probes": {"p": _probe("myprobe", {"evry_n_steps": 2})}}
    orch = ProbeDeploymentOrchestrator(cfg, _DummyLogger())
    with pytest.raises(ValueError, match=r"probes\.probes\.myprobe\.deployment: unknown key"):
        orch.maybe_deploy(step=1, agents=[_DummyAgent("Alice")])


def test_global_unknown_deployment_key_raises() -> None:
    with pytest.raises(ValueError, match="unknown key"):
        ProbeDeploymentPolicy.from_probes_config({"deployment": {"start_stp": 2}})


def test_per_probe_sampling_independent_and_deterministic() -> None:
    orch = ProbeDeploymentOrchestrator({}, _DummyLogger(), seed=1)
    agents = [_DummyAgent(c) for c in "ABCDEFGH"]
    policy = ProbeDeploymentPolicy(sample_k=3)

    pick_a = [
        a._agent_name
        for a in orch._sample_agents(list(agents), step=1, policy=policy, sample_salt="probeA")
    ]
    pick_b = [
        a._agent_name
        for a in orch._sample_agents(list(agents), step=1, policy=policy, sample_salt="probeB")
    ]
    pick_a2 = [
        a._agent_name
        for a in orch._sample_agents(list(agents), step=1, policy=policy, sample_salt="probeA")
    ]

    assert len(pick_a) == len(pick_b) == 3
    assert pick_a != pick_b  # independent salts -> independent subsets (8C3=56)
    assert pick_a == pick_a2  # same (seed, step, salt) -> identical subset


def test_anchor_filtering_and_reporting(monkeypatch) -> None:
    calls = _capture_deploy(monkeypatch)
    cfg = OmegaConf.create(
        {
            "probes": {
                "pre": _probe("pre"),
                "post": _probe("post", {"at": "post_step"}),
                "end": _probe("end", {"at": "run_end"}),
            }
        }
    )
    orch = ProbeDeploymentOrchestrator(cfg, _DummyLogger())
    agents = [_DummyAgent("Alice")]

    assert orch.anchors_in_use() == {"pre_step", "post_step", "run_end"}
    for anchor, expected in [("pre_step", "pre"), ("post_step", "post"), ("run_end", "end")]:
        orch.maybe_deploy(step=1, agents=agents, anchor=anchor)
        assert calls[-1]["probes"] == [expected]
        assert calls[-1]["anchor"] == anchor


def test_invalid_anchor_raises() -> None:
    with pytest.raises(ValueError, match="at must be one of"):
        ProbeDeploymentPolicy.from_probes_config({"deployment": {"at": "midnight"}})


def test_run_end_ignores_step_cadence() -> None:
    # A run_end probe is one-shot at loop end: due even with a future start_step.
    policy = ProbeDeploymentPolicy(at="run_end", start_step=99, every_n_steps=5)
    assert ProbeDeploymentOrchestrator._entry_due(policy, step=1) is True


def test_disabled_probe_with_bad_type_is_dormant(monkeypatch) -> None:
    # A deliberately disabled probe referencing an unimportable type must NOT be
    # built (so it can't abort the run at loop start via anchors_in_use).
    calls = _capture_deploy(monkeypatch)
    cfg = OmegaConf.create(
        {
            "probes": {
                "live": _probe("live"),
                "dead": {
                    "probe_type": "nonexistent.NoSuchProbe",
                    "probe_name": "dead",
                    "probe_data": {"name": "Dead"},
                    "deployment": {"enabled": False},
                },
            }
        }
    )
    orch = ProbeDeploymentOrchestrator(cfg, _DummyLogger())
    assert orch.anchors_in_use() == {"pre_step"}  # does not import the dead probe
    orch.maybe_deploy(step=1, agents=[_DummyAgent("Alice")])
    assert [c["probes"] for c in calls] == [["live"]]


def test_per_probe_empty_selection_warns_naming_probe(monkeypatch, caplog) -> None:
    _capture_deploy(monkeypatch)
    cfg = OmegaConf.create(
        {
            "probes": {
                "hit": _probe("hit"),
                "miss": _probe("miss", {"include_agents": ["Nobody"]}),  # typo'd filter
            }
        }
    )
    orch = ProbeDeploymentOrchestrator(cfg, _DummyLogger())
    with caplog.at_level("WARNING"):
        deployed, selected = orch.maybe_deploy(step=1, agents=[_DummyAgent("Alice")])
    assert (deployed, selected) == (True, 1)  # 'hit' still deployed
    assert any("'miss'" in r.message and "no agents matched" in r.message for r in caplog.records)


def test_run_end_bypasses_fixed_interval_schedule_gate(monkeypatch, tmp_path) -> None:
    from silisocs.evaluations.probes.deployment import DefaultProbeRunner

    calls = _capture_deploy(monkeypatch)
    cfg = {
        "schedule": {"built_in": "fixed_interval", "params": {"start_step": 0, "every_n_steps": 2}},
        "probes": {"terminal": _probe("terminal", {"at": "run_end"})},
    }
    runner = DefaultProbeRunner(cfg, str(tmp_path), seed=1)
    agents = [_DummyAgent("Alice")]
    # step 3 is off the every-2 cadence: a pre_step probe would be gated out...
    assert runner.maybe_run(step=3, agents=agents, worker_limit=None, anchor="pre_step") == (
        False,
        0,
    )
    # ...but run_end bypasses the step-cadence gate and still fires.
    assert runner.maybe_run(step=3, agents=agents, worker_limit=None, anchor="run_end") == (True, 1)
    assert calls[-1]["probes"] == ["terminal"]
