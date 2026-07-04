"""Pure config assembly for the dashboard, decoupled from Streamlit.

The dashboard's Save-Scenario and Launch paths turn the UI's session state into
world/sim/env config dicts and Hydra override strings. That transformation is pure
data-shaping, so it lives here parameterized by an explicit ``state`` mapping (a
snapshot of ``st.session_state``) rather than reading the Streamlit global. That
keeps ``launch_app`` a thin UI shell and makes the config logic importable and
unit-testable without launching Streamlit (which is what kept it untested before).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import yaml

from silisocs.dashboard.defaults import (
    default_initial_observations,
    default_jobname_format,
    default_persona_defaults,
)

# Backends that default to activity-gated participation (vs. deterministic "all").
_SOCIAL_BACKENDS = {"twitter_like", "reddit_like", "mastodon"}


def _as_int(value: object, default: int) -> int:
    """Best-effort int coercion that tolerates Hydra interpolations like ${...}."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith("${"):
            return default
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return default
    return default


def collect_activity_rates(state: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Per-role activity transition rates from the activity sliders in ``state``."""
    rates: dict[str, dict[str, float]] = {}
    for cls in state.get("_agent_classes", []):
        role = cls.get("sim_role_name") or cls.get("name", "")
        if role:
            rates[role] = {
                "inactive_to_active": state.get(f"act_{role}_i2a", 0.3),
                "active_to_inactive": state.get(f"act_{role}_a2i", 0.3),
            }
    return rates


def participation_sim_data(state: Mapping[str, Any], backend_type: str) -> dict[str, object]:
    """Dotted-key ``sim.engine.participation`` config for save/launch.

    Emits the built-in plus the per-role ``activity_transition_rates`` collected
    from the sliders; ``min_active_agents`` / ``active_probability`` are inherited
    from ``sim/base.yaml`` via merge (no dashboard control yet).
    """
    built_in = state.get(
        "engine_participation_built_in",
        "activity_probability" if backend_type in _SOCIAL_BACKENDS else "all",
    )
    data: dict[str, object] = {"engine.participation.built_in": built_in}
    rates = collect_activity_rates(state)
    if rates:
        data["engine.participation.params.activity_transition_rates"] = rates
    return data


def build_world_config(state: Mapping[str, Any]) -> dict:
    """Build a complete world config dict from a session-state snapshot."""
    scenario_name = state.get("scenario_name_edit", state.get("_loaded_scenario_name", "default"))
    _sc_cfg = state.get("_loaded_world", {})

    # Build classes config from session state.
    classes_dict: dict[str, dict] = {}
    for cls in state.get("_agent_classes", []):
        cls_cfg: dict = {}
        if cls.get("count"):
            cls_cfg["count"] = cls["count"]
        if cls.get("class_path"):
            cls_cfg["class_path"] = cls["class_path"]
        if cls.get("sim_role_name"):
            cls_cfg["sim_role_name"] = cls["sim_role_name"]
        if cls.get("flow_tag"):
            cls_cfg["flow_tag"] = cls["flow_tag"]
        if cls.get("data"):
            cls_cfg["data"] = cls["data"]
        if cls.get("field_map"):
            cls_cfg["field_map"] = cls["field_map"]
        if cls.get("model"):
            cls_cfg["model"] = cls["model"]
        if isinstance(cls.get("fixed_action"), dict):
            fixed_cfg = dict(cls["fixed_action"])
            if fixed_cfg.get("enabled"):
                cls_cfg["fixed_action"] = fixed_cfg
        classes_dict[cls.get("name", f"class_{len(classes_dict)}")] = cls_cfg

    fixed_action_sets: dict = {}
    fixed_action_file = str(state.get("fixed_action_sets_file", "") or "").strip()
    if fixed_action_file:
        fixed_action_sets["file"] = fixed_action_file

    inline_text = str(state.get("fixed_action_sets_inline_yaml", "") or "").strip()
    if inline_text:
        try:
            inline_sets = yaml.safe_load(inline_text) or {}
            if isinstance(inline_sets, dict):
                fixed_action_sets["inline"] = inline_sets
        except yaml.YAMLError:
            pass

    # Parse shared memories.
    shared_text = state.get("shared_memories_edit", "")
    shared_list = [line.strip() for line in shared_text.splitlines() if line.strip()]

    # Assemble world data.
    bg_text = state.get("setting_background", "")
    bg_list = [line.strip() for line in bg_text.splitlines() if line.strip()]

    # jobname_format / persona defaults / initial_observations are sourced from the
    # packaged base config (dashboard/defaults.py) so they can't drift from conf/.
    config = {
        "scenario_name": scenario_name,
        "jobname_format": default_jobname_format(),
        "setting": {
            "name": state.get("setting_name", ""),
            "background": bg_list,
        },
        "event": {
            "name": state.get("event_name", ""),
            "context": state.get("event_context", ""),
        },
        "builder": {"class_path": None, "params": {}},
        "persona_pipeline": {
            "defaults": {
                "params": default_persona_defaults(),
                "shared_memories": shared_list,
            },
            "classes": classes_dict,
        },
        "shared_memories": shared_list,
        "initial_observations": default_initial_observations(),
    }

    # Build probes from dashboard state (generalist structure).
    probe_items = state.get("_probe_items", [])
    configured_probes: dict[str, dict] = {}
    for idx, item in enumerate(probe_items):
        if not isinstance(item, dict):
            continue
        probe_name = str(item.get("probe_name") or f"probe_{idx}")
        probe_type = str(item.get("probe_type") or "FreeTextProbe")
        probe_data = item.get("probe_data", {})
        if not isinstance(probe_data, dict):
            probe_data = {}
        if "name" not in probe_data or not probe_data.get("name"):
            probe_data["name"] = probe_name
        configured_probes[probe_name] = {
            "probe_name": probe_name,
            "probe_type": probe_type,
            "probe_data": probe_data,
        }

    config["probes"] = {
        "deployment": {
            "enabled": bool(state.get("probes_enabled", True)),
            "start_step": _as_int(state.get("probe_start", 1), 1),
            "every_n_steps": max(1, _as_int(state.get("probe_interval", 1), 1)),
            "include_agents": state.get("probes_include_agents", []),
            "exclude_agents": state.get("probes_exclude_agents", []),
        },
        "probes": configured_probes,
    }

    # Copy any extra sections from loaded config (candidates, news_account, etc.)
    for key in ("candidates", "news_account", "data", "partisan_types"):
        if key in _sc_cfg:
            config[key] = _sc_cfg[key]

    if fixed_action_sets:
        config["fixed_action_sets"] = fixed_action_sets

    return config


def _override_value(prefix: str, key: str, val: Any) -> str:
    """Serialize one config value to a Hydra ``prefix.key=...`` override string.

    None -> ``=null``; bool -> lowercase; list -> ``[a,b]`` (``[]`` when empty);
    a str with spaces is quoted; everything else is emitted verbatim. Shared by the
    ``sim``/``env``/``eval`` sections (formerly three identical copies).
    """
    if val is None:
        return f"{prefix}.{key}=null"
    if isinstance(val, bool):
        return f"{prefix}.{key}={'true' if val else 'false'}"
    if isinstance(val, list):
        if not val:
            return f"{prefix}.{key}=[]"
        inner = ",".join(str(item) for item in val)
        return f"{prefix}.{key}=[{inner}]"
    if isinstance(val, str) and " " in val:
        return f'{prefix}.{key}="{val}"'
    return f"{prefix}.{key}={val}"


def build_hydra_overrides(
    sim: dict,
    env: dict,
    backend_group: str,
    world: dict,
    eval_cfg: dict | None = None,
) -> list[str]:
    """Build Hydra CLI override strings from sim, env, eval, and world dicts."""
    overrides: list[str] = [_override_value("sim", key, val) for key, val in sim.items()]
    overrides.append(f"env={backend_group}")
    overrides += [_override_value("env", key, val) for key, val in env.items()]
    overrides += [_override_value("eval", key, val) for key, val in (eval_cfg or {}).items()]
    for key, val in world.items():
        # World keys route to env when they address the game master, else sim; None
        # and empty/blank strings are dropped rather than emitted.
        if val is None:
            continue
        prefix = "env" if key.startswith("gm.") else "sim"
        if isinstance(val, bool):
            overrides.append(f"{prefix}.{key}={'true' if val else 'false'}")
        elif isinstance(val, (int, float)):
            overrides.append(f"{prefix}.{key}={val}")
        elif isinstance(val, str) and val:
            overrides.append(f'{prefix}.{key}="{val}"' if " " in val else f"{prefix}.{key}={val}")
    return overrides


def world_config_warnings(sim_params: dict, classes: list[dict]) -> list[str]:
    """Compute the Launch-tab validation warnings for the given sim params + classes."""
    warnings: list[str] = []
    if not classes:
        warnings.append("No agent classes defined. Add at least one in the Agent Classes tab.")
    for cls in classes:
        if not cls.get("class_path"):
            warnings.append(f"Class '{cls.get('name', '?')}' has no agent class set.")
        if not cls.get("data", {}).get("source"):
            warnings.append(f"Class '{cls.get('name', '?')}' has no data source.")
    total_count = sum(_as_int(c.get("count", 0), 0) for c in classes)
    declared = _as_int(sim_params.get("num_agents", 0), 0)
    if total_count and declared and total_count != declared:
        warnings.append(
            f"Agent classes sum to {total_count}, but num_agents is {declared}. "
            f"The actual number of agents built is the sum of the class counts "
            f"({total_count}); num_agents does not cap or pad the total. Set unused "
            f"classes to count 0, or align num_agents with the class counts."
        )
    return warnings
