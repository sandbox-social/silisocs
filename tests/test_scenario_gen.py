from __future__ import annotations

import yaml

from silisocs.scenario_gen.specs import ScenarioSpec
from silisocs.scenario_gen.writer import write_scenario


def _minimal_spec() -> dict:
    return {
        "name": "tiny_world",
        "scenario_name": "tiny_world",
        "jobname_format": "Tiny_N${num_agents}_T${num_steps}_${run_name}",
        "num_agents": 1,
        "num_steps": 1,
        "run_name": "tiny_world",
        "setting": {"name": "Tiny", "background": ["A small test setting."]},
        "event": {"name": "Test", "context": "A small test event."},
        "agent_classes": [
            {
                "role": "user",
                "sim_role_name": "user",
                "count": 1,
                "agents": [
                    {
                        "name": "Alice",
                        "username": "alice",
                        "context": "Alice tests the generated scenario.",
                        "style": "Brief.",
                        "goal": "Run the smoke test.",
                        "seed_post": "",
                    }
                ],
            }
        ],
        "backend": {"type": "twitter_like", "timeline_mode": "follower_chronological"},
        "custom_agent_stubs": [],
    }


def test_scenario_spec_accepts_current_public_backend_and_agent_stub_fields() -> None:
    spec = ScenarioSpec.model_validate(_minimal_spec())

    assert spec.backend.type == "twitter_like"
    assert spec.custom_agent_stubs == []


def test_scenario_writer_emits_component_owned_env_config(tmp_path) -> None:
    spec = ScenarioSpec.model_validate(_minimal_spec())
    write_scenario(spec, tmp_path / spec.name)

    env_cfg = yaml.safe_load((tmp_path / spec.name / "conf" / "env.yaml").read_text())

    assert env_cfg["gm"]["backend"]["type"] == "twitter_like"
    assert env_cfg["gm"]["components"]["initialize"]["params"]["graph"]
    assert "activity_transition_rates" in env_cfg["gm"]["components"]["next_acting"]["params"]
