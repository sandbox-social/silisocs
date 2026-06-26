"""writer module. Auto-generated module docstring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from silisocs.scenario_gen.specs import ScenarioSpec, StudySpec

# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

_BLOCK_THRESHOLD = 80


def _str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    """Represent long or multi-line strings as YAML block scalars."""
    if "\n" in data or len(data) > _BLOCK_THRESHOLD:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


def _make_dumper() -> type[yaml.Dumper]:
    """Build a YAML Dumper subclass that uses the block-scalar string representer."""

    class _Dumper(yaml.Dumper):
        """_Dumper."""

    _Dumper.add_representer(str, _str_representer)
    return _Dumper


def _dump(data: Any, header: str | None = None) -> str:
    """Dump data to a YAML string, optionally prefixed with a comment header."""
    text = yaml.dump(
        data,
        Dumper=_make_dumper(),
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
    )
    return f"# {header}\n{text}" if header else text


def _write(path: Path, content: str) -> None:
    """Write content to a file, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Scenario writer
# ---------------------------------------------------------------------------


def write_scenario(spec: ScenarioSpec, root: Path | str) -> None:
    """Write all scenario config files (world, agents, env, eval, sim) under root."""
    root = Path(root)
    conf = root / "conf"
    _write_scenario_yaml(spec, conf)
    _write_agents_yaml(spec, conf)
    _write_env_yaml(spec, conf)
    _write_eval_yaml(conf)
    _write_sim_yaml(conf)


def _write_scenario_yaml(spec: ScenarioSpec, conf: Path) -> None:
    """Write the world/default.yaml run params and setting/event/data block."""
    data = {
        "scenario_name": spec.scenario_name,
        "jobname_format": spec.jobname_format,
        "num_agents": spec.num_agents,
        "num_steps": spec.num_steps,
        "seed": spec.seed,
        "run_name": spec.run_name,
        "setting": {
            "name": spec.setting.name,
            "background": spec.setting.background,
        },
        "event": {
            "name": spec.event.name,
            "context": spec.event.context,
        },
        "data": spec.data or {},
    }
    _write(conf / "world" / "default.yaml", _dump(data, header="@package _global_"))


def _write_agents_yaml(spec: ScenarioSpec, conf: Path) -> None:
    """Write the agents/default.yaml persona pipeline and shared memories."""
    classes: dict[str, Any] = {}
    for ac in spec.agent_classes:
        classes[ac.sim_role_name] = {
            "count": ac.count,
            "class_path": ac.class_path,
            "sim_role_name": ac.sim_role_name,
            "data": {
                "source": "inline",
                "records": [
                    {
                        "name": a.name,
                        "username": a.username,
                        "context": a.context,
                        "style": a.style,
                        "goal": a.goal,
                        "seed_post": a.seed_post,
                    }
                    for a in ac.agents
                ],
            },
            "field_map": {
                "name": "name",
                "username": "username",
                "context": "context",
                "style": "style",
                "goal": "goal",
                "seed_post": "seed_post",
            },
        }

    data = {
        "builder": {"class_path": None, "params": {}},
        "shared_memories": ["${event.context}", "${setting.background}"],
        "persona_pipeline": {
            "defaults": {
                "params": {
                    "seed_post": "",
                    "bio": "",
                    "style": "",
                    "goal": None,
                    "world_context": "${event.context}",
                },
                "shared_memories": ["${event.context}"],
            },
            "classes": classes,
        },
        "initial_observations": [
            "{name} opens their social media feed.",
            "{name} thinks about what they want to post.",
            "{name} checks who has responded to them.",
        ],
    }
    _write(conf / "agents" / "default.yaml", _dump(data, header="@package agents"))


def _write_env_yaml(spec: ScenarioSpec, conf: Path) -> None:
    """Write the env.yaml backend and game-master component configuration."""
    activity_rates = {
        ac.sim_role_name: {
            "inactive_to_active": ac.activity.inactive_to_active,
            "active_to_inactive": ac.activity.active_to_inactive,
        }
        for ac in spec.agent_classes
    }

    data = {
        "gm": {
            "backend": {
                "type": spec.backend.type,
                "class_path": None,
                "params": {},
                "enabled_actions": spec.backend.enabled_actions,
            },
            "class_path": "silisocs.environments.gm.game_master.ComponentGameMaster",
            "name": f"{spec.backend.type}_gm",
            "components": {
                "initialize": {
                    "built_in": "social_media",
                    "class_path": None,
                    "params": {
                        "graph": {
                            "fully_connected_targets": spec.network.fully_connected_targets,
                            "base_followership_probability": spec.network.base_followership_probability,
                            "network_type": spec.network.network_type,
                            "barabasi_albert_m": spec.network.barabasi_albert_m,
                        }
                    },
                },
                "next_acting": {
                    "built_in": "activity_probability",
                    "class_path": None,
                    "params": {"activity_transition_rates": activity_rates},
                },
                "observe": {
                    "built_in": "timeline_every_turn",
                    "class_path": None,
                    "params": {
                        "episode_observation_flow": "fixed_pre",
                        "timeline_mode": spec.backend.timeline_mode,
                    },
                },
                "resolve": {
                    "built_in": "tool_calling",
                    "class_path": None,
                    "params": {},
                },
                "update": {
                    "built_in": "social_recommendation",
                    "class_path": None,
                    "params": {},
                },
                "action_prompt": {
                    "built_in": "default",
                    "class_path": None,
                    "params": {},
                },
            },
        },
    }
    _write(conf / "env.yaml", _dump(data))


def _write_eval_yaml(conf: Path) -> None:
    """Write the eval.yaml probe deployment defaults."""
    data = {
        "probes": {
            "deployment": {
                "enabled": True,
                "start_step": 1,
                "every_n_steps": 1,
                "include_agents": [],
                "exclude_agents": [],
            },
            "probes": {},
        },
    }
    _write(conf / "eval.yaml", _dump(data))


def _write_sim_yaml(conf: Path) -> None:
    """Write the sim.yaml action mode, initialization, and engine settings."""
    data = {
        "action_mode": "generic",
        "tool_calling": {"mode": "multi"},
        "initialization": {
            "agents": {"built_in": "raw_memory", "class_path": None, "params": {}},
            "game_masters": {"built_in": "default", "class_path": None, "params": {}},
            "simulation": {
                "built_in": "seed_posts",
                "class_path": None,
                "params": {"type": "llm", "params": {}},
            },
        },
        "engine": {
            "step": {
                "built_in": "base",
                "params": {
                    "flow_order": ["fixed_pre", "default"],
                    "agent_to_flow": {},
                },
            },
            "turn_policy": {
                "built_in": "open_ended",
                "params": {
                    "max_actions": 10,
                    "finished_action_signal": "FINISHED",
                },
            },
        },
    }
    _write(conf / "sim.yaml", _dump(data))


# ---------------------------------------------------------------------------
# Study writer
# ---------------------------------------------------------------------------


def write_study(spec: StudySpec, root: Path | str) -> None:
    """Write study.yaml from a StudySpec, including hypotheses and run defaults."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    study_id = spec.study_id
    summary_path = f"experiments/studies/{study_id}/SUMMARY.md"
    summary_log_path = f"experiments/studies/{study_id}/generated/summary_log.jsonl"

    hypotheses: dict[str, Any] = {}
    for h in spec.hypotheses:
        conditions: dict[str, Any] = {}
        for c in h.conditions:
            cond: dict[str, Any] = {"overrides": c.overrides}
            if c.sub_experiment is not None:
                cond["sub_experiment"] = c.sub_experiment
            if c.execution_mode == "reuse_existing":
                cond["execution"] = {"mode": "reuse_existing"}
                if c.reuse_source is not None:
                    cond["reuse"] = {"source": c.reuse_source}
            else:
                cond["execution"] = {"mode": "run"}
            conditions[c.id] = cond

        hyp: dict[str, Any] = {
            "statement": h.statement,
            "independent_variable": h.independent_variable,
            "prediction": h.prediction,
            "status": h.status,
            "analysis": {
                "rationale": "",
                "observations_summary": "",
                "interpretation": "",
                "confidence": "",
                "next_step_recommendation": "",
            },
            "conditions": conditions,
        }
        if h.follows_from is not None:
            hyp["follows_from"] = h.follows_from
        if h.motivation is not None:
            hyp["motivation"] = h.motivation
        hypotheses[h.id] = hyp

    run_defaults: dict[str, Any] = {
        "config_path": spec.run_defaults.config_path,
        "run_name_template": spec.run_defaults.run_name_template,
        "output_root_override": spec.run_defaults.output_root_override,
        "seed_start": spec.run_defaults.seed_start,
        "seed_repeats": spec.run_defaults.seed_repeats,
    }
    if spec.run_defaults.overrides:
        run_defaults["overrides"] = spec.run_defaults.overrides

    data: dict[str, Any] = {
        "schema_version": 1,
        "study": {
            "name": spec.name,
            "study_id": study_id,
            "study_version": "v1",
            "question": spec.question,
            "scenarios": spec.scenarios,
            "parent_studies": [],
            "derived_from_runs": [],
            "study_summary_path": summary_path,
            "summary_log_path": summary_log_path,
            "run_defaults": run_defaults,
            "notes": {
                "objective": spec.notes.objective,
                "context": spec.notes.context,
                "constraints": spec.notes.constraints,
            },
        },
        "evaluations": [{"id": e.id, "preset": e.preset} for e in spec.evaluations],
        "hypotheses": hypotheses,
    }

    _write(root / "study.yaml", _dump(data))
