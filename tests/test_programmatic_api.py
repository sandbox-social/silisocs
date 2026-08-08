"""The programmatic run API: compose_config + run_simulation, no Hydra CLI.

The notebook/orchestrator contract: compose a config in-process, run it to a
caller-chosen output directory, and load the artifacts back — no subprocess,
no ``@hydra.main`` context, no ``sys.argv`` preprocessing.
"""

from __future__ import annotations

from silisocs import compose_config, load_run, run_simulation


def test_compose_config_composes_base_defaults_with_overrides():
    cfg = compose_config(overrides=["num_agents=3", "num_steps=2", "sim.llm.provider=scripted"])

    assert cfg.num_agents == 3
    assert cfg.num_steps == 2
    assert cfg.sim.llm.provider == "scripted"
    assert cfg.scenario_name == "default"


def test_run_simulation_runs_in_process_and_returns_the_run_dir(tmp_path):
    cfg = compose_config(
        overrides=[
            "num_agents=2",
            "num_steps=1",
            "sim.llm.provider=scripted",
            "sim.max_concurrent_actions=2",
        ]
    )

    run_dir = run_simulation(cfg, output_dir=tmp_path / "nb_run")

    assert run_dir == (tmp_path / "nb_run").resolve()
    artifact = load_run(run_dir)
    assert artifact.status == "success"
    assert artifact.num_agents == 2
    assert (run_dir / "run_events.jsonl").is_file()


def test_run_simulation_without_output_dir_outside_hydra_is_a_clear_error(tmp_path):
    import pytest

    cfg = compose_config(overrides=["num_agents=2", "num_steps=1", "sim.llm.provider=scripted"])

    with pytest.raises(ValueError, match="output_dir"):
        run_simulation(cfg)


def test_compose_config_overrides_beat_scenario_flat_files():
    """A caller's override must survive the scenario's flat sim.yaml merge —
    and the scenario selection must not leak into later composes/runs.
    """
    import os

    from silisocs.runtime.configuration.external import merge_external_group_overrides

    cfg = compose_config(
        scenario="misinformation", overrides=["sim.tool_calling.mode=none", "num_steps=1"]
    )
    assert cfg.sim.tool_calling.mode == "none"
    assert cfg.scenario_name == "misinformation"

    # The run-time merge is a no-op now (selection cleared after compose), so
    # run_simulation cannot revert the override...
    assert merge_external_group_overrides(cfg).sim.tool_calling.mode == "none"
    assert os.environ.get("SILISOCS_EXTERNAL_CONFIG_DIRS", "") == ""
    # ...and a later compose without a scenario is untouched by the earlier one.
    assert compose_config(overrides=["num_steps=1"]).scenario_name == "default"
