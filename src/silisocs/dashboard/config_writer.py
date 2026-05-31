"""Dashboard scenario config writer."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml


def set_nested_value(payload: dict, dotted_key: str, value: object) -> None:
    """Set nested dictionary keys from dot notation."""
    cursor = payload
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        next_cursor = cursor.get(part)
        if not isinstance(next_cursor, dict):
            next_cursor = {}
            cursor[part] = next_cursor
        cursor = next_cursor
    cursor[parts[-1]] = value


def _default_env_payload(backend_type: str) -> dict[str, Any]:
    """Load the packaged env default for a backend."""
    env_file = Path(__file__).resolve().parents[1] / "conf" / "env" / f"{backend_type}.yaml"
    if not env_file.exists():
        return {
            "gm": {
                "backend": {
                    "type": backend_type,
                    "class_path": None,
                    "params": {},
                    "enabled_actions": None,
                },
                "class_path": "silisocs.environments.gm.game_master.ComponentGameMaster",
                "name": f"{backend_type}_gm",
                "components": {
                    "initialize": {"built_in": "app_initialize", "class_path": None, "params": {}},
                    "next_acting": {"built_in": "fixed_order", "class_path": None, "params": {}},
                    "observe": {"built_in": "app_observation", "class_path": None, "params": {}},
                    "resolve": {"built_in": "tool_calling", "class_path": None, "params": {}},
                    "update": {"built_in": "disabled", "class_path": None, "params": {}},
                    "action_prompt": {"built_in": "default", "class_path": None, "params": {}},
                },
            }
        }
    loaded = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    return deepcopy(loaded)


def save_scenario(
    name: str,
    scenario_data: dict[str, Any],
    sim_data: dict[str, Any],
    env_data: dict[str, Any],
    backend_type: str,
    scenarios_root: Path,
    evals_data: dict[str, Any] | None = None,
) -> Path:
    """Save scenario config to Hydra group layout in scenarios/<name>/conf/."""
    conf_dir = scenarios_root / name / "conf"
    (conf_dir / "scenario").mkdir(parents=True, exist_ok=True)
    (conf_dir / "agents").mkdir(parents=True, exist_ok=True)

    sim_payload = {
        "scenario_name": scenario_data.get("scenario_name", name),
        "jobname_format": scenario_data.get("jobname_format"),
        "setting": scenario_data.get("setting", {}),
        "event": scenario_data.get("event", {}),
        "data": scenario_data.get("data", {}),
    }
    scenario_keys = {
        "num_agents",
        "num_steps",
        "seed",
        "experiment_name",
        "run_name",
        "output_rootname",
    }
    sim_section: dict[str, Any] = {}
    for key, value in sim_data.items():
        if value is not None:
            if key in scenario_keys:
                set_nested_value(sim_payload, key, value)
            else:
                set_nested_value(sim_section, key, value)

    agent_payload = {
        "builder": scenario_data.get("builder", {"class_path": None, "params": {}}),
        "persona_pipeline": scenario_data.get("persona_pipeline", {}),
        "shared_memories": scenario_data.get("shared_memories", []),
        "initial_observations": scenario_data.get("initial_observations", []),
    }
    if isinstance(scenario_data.get("fixed_action_sets"), dict):
        agent_payload["fixed_action_sets"] = scenario_data.get("fixed_action_sets", {})

    env_payload = _default_env_payload(backend_type or "twitter_like")
    for key, value in env_data.items():
        if value is not None:
            set_nested_value(env_payload, key, value)

    evals_payload = {"probes": scenario_data.get("probes", {})}
    for key, value in (evals_data or {}).items():
        if value is not None:
            set_nested_value(evals_payload, key, value)

    files_to_write = [
        (conf_dir / "scenario" / "default.yaml", "# @package _global_\n\n", sim_payload),
        (conf_dir / "agents" / "default.yaml", "# @package agents\n\n", agent_payload),
        (conf_dir / "env.yaml", "# @package env\n\n", env_payload),
        (conf_dir / "evals.yaml", "# @package evals\n\n", evals_payload),
    ]
    if sim_section:
        files_to_write.append((conf_dir / "sim.yaml", "# @package sim\n\n", sim_section))

    for file_path, header, payload in files_to_write:
        file_path.write_text(
            header
            + yaml.dump(payload, default_flow_style=False, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    return conf_dir
