"""Streamlit dashboard for configuring and launching Silisocs simulations.

Run with:
    streamlit run src/silisocs/dashboard/launch_app.py
"""

from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from typing import Any

import streamlit as st
import yaml

from silisocs.dashboard.config_writer import (
    save_scenario as _save_scenario,
)
from silisocs.environments.backends.base import ActionDescriptor

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_CONF_DIR = _PACKAGE_ROOT / "conf"
_SCENARIOS_DIR = _PACKAGE_ROOT.parents[2] / "scenarios"
_PROJECT_ROOT = _PACKAGE_ROOT.parents[2]

_BACKEND_OPTIONS = ["twitter_like", "reddit_like", "mastodon", "resource_market", "virtual_space"]
_SOCIAL_BACKENDS = {"twitter_like", "reddit_like", "mastodon"}
_MEMORY_BACKENDS = ["list", "associative"]
_ACTION_MODES = ["custom", "generic", "tool_calling"]
_NETWORK_TYPES = ["barabasi_albert", "random", "lfr_benchmark"]
_RUNTIME_INITIALIZERS = ["raw_memory", "formative_memory", "none"]
_PERSONA_SOURCES = ["inline", "local_json", "config_path", "hf_dataset"]
_GM_NEXT_ACTING_OPTIONS = [
    "activity_probability",
    "activity_markov",
    "all_agents",
    "fixed_order",
]
_GM_OBSERVE_OPTIONS = ["timeline_every_turn", "app_observation", "episode_only"]
_GM_RESOLVE_OPTIONS = ["parsed_action", "generic_action", "tool_calling"]
_BACKEND_INITIALIZER_OPTIONS = ["social_media", "app_initialize", "none", "disabled"]
_ENGINE_TURN_POLICY_OPTIONS = ["single_action", "fixed_count", "open_ended"]
_ENGINE_PROBE_SCHEDULE_OPTIONS = ["step_schedule", "fixed_interval", "disabled"]
_PROBE_TYPE_OPTIONS = ["NumericRatingProbe", "BinaryProbe", "ChoiceProbe", "FreeTextProbe"]

# ---------------------------------------------------------------------------
# Theme & page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Silisocs Launcher",
    page_icon="\U0001f30d",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    section[data-testid="stSidebar"] { background-color: #0e1117; }
    div[data-testid="stExpander"] details {
        border: 1px solid #30363d; border-radius: 8px; padding: 4px 8px;
    }
    div.stButton > button[kind="primary"] {
        background-color: #238636; color: white;
        font-size: 1.1rem; padding: 0.6rem 2.4rem; border-radius: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_yaml(path: Path) -> dict:
    """_load_yaml.

    :param Path path:
    :type path: Path

    :returns: dict
    :rtype: dict
    """
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


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


def _as_float(value: object, default: float) -> float:
    """Best-effort float coercion that tolerates Hydra interpolations like ${...}."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text.startswith("${"):
            return default
        try:
            return float(text)
        except ValueError:
            return default
    return default


def _normalize_probe_items(probes_cfg: dict) -> list[dict]:
    """Normalize probes.probes to editable dashboard rows."""
    configured_probes = probes_cfg.get("probes", {}) if isinstance(probes_cfg, dict) else {}
    rows: list[dict] = []

    if isinstance(configured_probes, dict):
        iterator = list(configured_probes.items())
    elif isinstance(configured_probes, list):
        iterator = [(str(i), q) for i, q in enumerate(configured_probes)]
    else:
        iterator = []

    for idx, probe_cfg in iterator:
        if not isinstance(probe_cfg, dict):
            continue
        probe_data = probe_cfg.get("probe_data", {})
        if not isinstance(probe_data, dict):
            probe_data = {}
        probe_name = str(probe_cfg.get("probe_name") or probe_data.get("name") or idx)
        probe_type = str(probe_cfg.get("probe_type", "FreeTextProbe"))
        rows.append(
            {
                "probe_name": probe_name,
                "probe_type": probe_type,
                "probe_data": dict(probe_data),
            }
        )

    return rows


def _deep_merge_dict(base: dict, overrides: dict) -> dict:
    """Recursively merge two dictionaries."""
    merged = dict(base)
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _scenario_root_candidates() -> list[Path]:
    """Return likely scenario-root candidates in priority order."""
    candidates = [
        _PROJECT_ROOT / "scenarios",
        Path.cwd() / "scenarios",
        _SCENARIOS_DIR,
    ]
    unique: list[Path] = []
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _resolve_scenarios_root(path_text: str | None) -> Path:
    """Resolve user-provided scenarios root.

    If the provided path is a project root that contains a nested
    `scenarios/` directory, use that nested directory.
    """
    text = (path_text or "").strip()
    if text:
        candidate = Path(text).expanduser()
        candidate = (
            (Path.cwd() / candidate).resolve()
            if not candidate.is_absolute()
            else candidate.resolve()
        )
    else:
        candidates = _scenario_root_candidates()
        candidate = next((p for p in candidates if p.is_dir()), candidates[0])

    nested = candidate / "scenarios"
    if nested.is_dir():
        return nested
    return candidate


def _discover_external_scenarios(scenarios_root: Path) -> dict[str, Path]:
    """Discover external scenario YAML files from a scenarios root directory."""
    found: dict[str, Path] = {}
    if scenarios_root.is_dir():
        for d in sorted(scenarios_root.iterdir()):
            if not d.is_dir():
                continue
            # Preferred layout: scenarios/<name>/conf/world/default.yaml
            flat_conf = d / "conf"
            world_default = flat_conf / "world" / "default.yaml"
            if world_default.is_file():
                found[d.name] = world_default
                continue

            # Fallback: any yaml in scenarios/<name>/conf/world/
            hydra_path = flat_conf / "world"
            if hydra_path.is_dir():
                any_yaml = sorted(hydra_path.glob("*.yaml"))
                if any_yaml:
                    found[d.name] = any_yaml[0]

    return found


def _discover_run_configs_for_scenario(scenarios_root: Path, scenario_key: str) -> dict[str, Path]:
    """Discover output-run config snapshots for a given scenario key."""
    base = scenario_key.split("/", maxsplit=1)[0]
    outputs_dir = scenarios_root / base / "outputs"
    found: dict[str, Path] = {}
    if not outputs_dir.is_dir():
        return found

    for run_dir in sorted(outputs_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        for cfg in sorted(run_dir.glob("configs/*/config.yaml")):
            label = run_dir.name
            # Disambiguate rare duplicates.
            if label in found:
                label = f"{run_dir.name} :: {cfg.parent.name}"
            found[label] = cfg
            break
    return found


def _split_loaded_config(
    loaded_cfg: object,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Split loaded YAML into (world_view, sim, env)."""
    if not isinstance(loaded_cfg, dict):
        return {}, {}, {}

    if any(isinstance(loaded_cfg.get(k), dict) for k in ("agents", "sim", "env")):
        loaded_agent = (
            loaded_cfg.get("agents", {}) if isinstance(loaded_cfg.get("agents"), dict) else {}
        )
        loaded_sim = loaded_cfg.get("sim", {}) if isinstance(loaded_cfg.get("sim"), dict) else {}
        loaded_env = loaded_cfg.get("env", {}) if isinstance(loaded_cfg.get("env"), dict) else {}
        loaded_eval = loaded_cfg.get("eval", {}) if isinstance(loaded_cfg.get("eval"), dict) else {}
        gm_components = (loaded_env.get("gm") or {}).get("components") or {}
        graph_cfg = ((gm_components.get("initialize") or {}).get("params") or {}).get("graph")

        world_view: dict = {
            "scenario_name": loaded_sim.get("scenario_name", ""),
            "jobname_format": loaded_sim.get("jobname_format", ""),
            "setting": loaded_sim.get("setting", {}),
            "event": loaded_sim.get("event", {}),
            "data": loaded_sim.get("data", {}),
            "graph": graph_cfg or {},
            "persona_pipeline": loaded_agent.get("persona_pipeline", {}),
            "shared_memories": loaded_agent.get("shared_memories", []),
            "initial_observations": loaded_agent.get("initial_observations", []),
            "fixed_action_sets": loaded_agent.get("fixed_action_sets", {}),
            "probes": loaded_eval.get("probes", {}),
            "candidates": loaded_env.get("candidates", loaded_sim.get("candidates", {})),
            "news_account": loaded_env.get("news_account", loaded_sim.get("news_account", {})),
            "partisan_types": loaded_env.get(
                "partisan_types", loaded_sim.get("partisan_types", [])
            ),
        }
        return world_view, loaded_sim, loaded_env

    return {}, {}, {}


def _backend_class(backend_type: str):
    """_backend_class.

    :param str backend_type:
    :type backend_type: str
    """
    if backend_type == "twitter_like":
        from silisocs.environments.backends.twitter_like.app import TwitterLikeApp

        return TwitterLikeApp
    if backend_type == "reddit_like":
        from silisocs.environments.backends.reddit_like.app import RedditLikeApp

        return RedditLikeApp
    if backend_type == "mastodon":
        from silisocs.environments.backends.mastodon.apps import SocialNetworkApp

        return SocialNetworkApp
    if backend_type == "resource_market":
        from silisocs.environments.backends.resource_market.app import ResourceMarketApp

        return ResourceMarketApp
    if backend_type == "virtual_space":
        from silisocs.environments.backends.virtual_space.app import VirtualSpaceApp

        return VirtualSpaceApp
    raise ValueError(f"Unknown backend_type: {backend_type}")


def _backend_action_catalog(backend_type: str) -> list[dict]:
    """Build backend action catalog without creating live backend instances."""
    cls = _backend_class(backend_type)
    actions = []
    for _, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
        if getattr(fn, "__app_action__", False):
            descriptor = ActionDescriptor.from_method(fn)
            actions.append(
                {
                    "name": descriptor.name,
                    "selectable_name": descriptor.selectable_name,
                    "description": descriptor.description.strip(),
                }
            )
    actions.sort(key=lambda item: item["selectable_name"])
    return actions


def _discover_agent_modules() -> list[str]:
    """Discover available agent modules by scanning the package."""
    modules: list[str] = []
    modules.append("silisocs.agents.native.NativeAgent")
    fixed_file = _PACKAGE_ROOT / "agents" / "fixed.py"
    if fixed_file.exists():
        modules.append("silisocs.agents.fixed.FixedAgent")
    # Scenario-specific compatibility agent modules.
    for scenario_dir in sorted((_PROJECT_ROOT / "scenarios").glob("*/input/entity_lib")):
        for py_file in sorted(scenario_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            rel = py_file.relative_to(_PROJECT_ROOT)
            mod_path = str(rel.with_suffix("")).replace("/", ".")
            modules.append(mod_path)
    return modules


def _get_config_path_for_scenario(scenarios_root: Path, scenario_key: str) -> str | None:
    """Return the --config-path dir for an external scenario, or None for package-bundled."""
    candidate = scenarios_root / scenario_key / "conf"
    if candidate.is_dir():
        return str(candidate)
    # Handle compound keys like "election/variant"
    parts = scenario_key.split("/")
    if len(parts) > 1:
        candidate = scenarios_root / parts[0] / "conf"
        if candidate.is_dir():
            return str(candidate)
    return None


def _build_world_config() -> dict:
    """Build a complete world config dict from session state."""
    scenario_name = st.session_state.get(
        "scenario_name_edit", st.session_state.get("_loaded_scenario_name", "default")
    )
    _sc_cfg = st.session_state.get("_loaded_world", {})

    # Build classes config from session state.
    classes_dict: dict[str, dict] = {}
    for cls in st.session_state.get("_agent_classes", []):
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
    fixed_action_file = str(st.session_state.get("fixed_action_sets_file", "") or "").strip()
    if fixed_action_file:
        fixed_action_sets["file"] = fixed_action_file

    inline_text = str(st.session_state.get("fixed_action_sets_inline_yaml", "") or "").strip()
    if inline_text:
        try:
            inline_sets = yaml.safe_load(inline_text) or {}
            if isinstance(inline_sets, dict):
                fixed_action_sets["inline"] = inline_sets
        except yaml.YAMLError:
            pass

    # Parse shared memories.
    shared_text = st.session_state.get("shared_memories_edit", "")
    shared_list = [line.strip() for line in shared_text.splitlines() if line.strip()]

    # Assemble world data.
    bg_text = st.session_state.get("setting_background", "")
    bg_list = [line.strip() for line in bg_text.splitlines() if line.strip()]

    # Collect activity rates from session state.
    activity_rates = {}
    for cls in st.session_state.get("_agent_classes", []):
        role = cls.get("sim_role_name") or cls.get("name", "")
        if role:
            i2a = st.session_state.get(f"act_{role}_i2a", 0.3)
            a2i = st.session_state.get(f"act_{role}_a2i", 0.3)
            activity_rates[role] = {
                "inactive_to_active": i2a,
                "active_to_inactive": a2i,
            }

    config = {
        "scenario_name": scenario_name,
        "jobname_format": "N${num_agents}_T${num_steps}_${experiment_name}_${run_name}",
        "setting": {
            "name": st.session_state.get("setting_name", ""),
            "background": bg_list,
        },
        "event": {
            "name": st.session_state.get("event_name", ""),
            "context": st.session_state.get("event_context", ""),
        },
        "builder": {"class_path": None, "params": {}},
        "persona_pipeline": {
            "defaults": {
                "params": {
                    "seed_post": "",
                    "bio": "",
                    "style": "",
                    "goal": None,
                    "world_context": "${event.context}",
                },
                "shared_memories": shared_list,
            },
            "classes": classes_dict,
        },
        "shared_memories": shared_list,
        "initial_observations": [
            '"{name} is at home checking their social media feed."',
            '"{name} decides to browse and maybe post something."',
        ],
    }

    # Build probes from dashboard state (generalist structure).
    probe_items = st.session_state.get("_probe_items", [])
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
            "enabled": bool(st.session_state.get("probes_enabled", True)),
            "start_step": _as_int(st.session_state.get("probe_start", 1), 1),
            "every_n_steps": max(1, _as_int(st.session_state.get("probe_interval", 1), 1)),
            "include_agents": st.session_state.get("probes_include_agents", []),
            "exclude_agents": st.session_state.get("probes_exclude_agents", []),
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


def _build_hydra_overrides(
    sim: dict,
    env: dict,
    backend_group: str,
    world: dict,
    eval_cfg: dict | None = None,
) -> list[str]:
    """_build_hydra_overrides.

    :param dict sim:
    :type sim: dict
    :param dict env:
    :type env: dict
    :param str backend_group:
    :type backend_group: str
    :param dict world:
    :type world: dict

    :returns: list[str]
    :rtype: list[str]
    """
    overrides: list[str] = []
    for key, val in sim.items():
        if val is None:
            overrides.append(f"sim.{key}=null")
        elif isinstance(val, bool):
            overrides.append(f"sim.{key}={'true' if val else 'false'}")
        elif isinstance(val, list):
            if not val:
                overrides.append(f"sim.{key}=[]")
            else:
                inner = ",".join(str(item) for item in val)
                overrides.append(f"sim.{key}=[{inner}]")
        elif isinstance(val, str) and " " in val:
            overrides.append(f'sim.{key}="{val}"')
        else:
            overrides.append(f"sim.{key}={val}")
    overrides.append(f"env={backend_group}")
    for key, val in env.items():
        if val is None:
            overrides.append(f"env.{key}=null")
        elif isinstance(val, bool):
            overrides.append(f"env.{key}={'true' if val else 'false'}")
        elif isinstance(val, list):
            if not val:
                overrides.append(f"env.{key}=[]")
            else:
                inner = ",".join(str(item) for item in val)
                overrides.append(f"env.{key}=[{inner}]")
        elif isinstance(val, str) and " " in val:
            overrides.append(f'env.{key}="{val}"')
        else:
            overrides.append(f"env.{key}={val}")
    for key, val in (eval_cfg or {}).items():
        if val is None:
            overrides.append(f"eval.{key}=null")
        elif isinstance(val, bool):
            overrides.append(f"eval.{key}={'true' if val else 'false'}")
        elif isinstance(val, list):
            if not val:
                overrides.append(f"eval.{key}=[]")
            else:
                inner = ",".join(str(item) for item in val)
                overrides.append(f"eval.{key}=[{inner}]")
        elif isinstance(val, str) and " " in val:
            overrides.append(f'eval.{key}="{val}"')
        else:
            overrides.append(f"eval.{key}={val}")
    for key, val in world.items():
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


def _validate_config(sim_params: dict, classes: list[dict]) -> None:
    """Show validation warnings in the Launch tab."""
    warnings = []
    if not classes:
        warnings.append("No agent classes defined. Add at least one in the Agent Classes tab.")
    for cls in classes:
        if not cls.get("class_path"):
            warnings.append(f"Class '{cls.get('name', '?')}' has no agent class set.")
        if not cls.get("data", {}).get("source"):
            warnings.append(f"Class '{cls.get('name', '?')}' has no data source.")
    total_count = sum(c.get("count", 0) for c in classes)
    if total_count > sim_params.get("num_agents", 0):
        warnings.append(
            f"Total class count ({total_count}) exceeds num_agents ({sim_params['num_agents']}). "
            f"Some agents may be truncated."
        )
    for w in warnings:
        st.warning(w)


# ---------------------------------------------------------------------------
# Sidebar — scenario management
# ---------------------------------------------------------------------------
with st.sidebar:
    st.title("\U0001f30d Silisocs")
    st.caption("Social Simulation Framework")
    st.divider()

    st.markdown("**Configuration Preset**")
    preset_options = ["default", "recommendation"]
    selected_preset = st.selectbox(
        "Load preset",
        preset_options,
        index=0,
        key="sidebar_preset_select",
        help=(
            "Select a preset configuration:\n"
            "- **default**: Standard mastodon-sim settings\n"
            "- **recommendation**: Tool-calling actions with recommendation timelines enabled"
        ),
    )

    # Store preset selection for later use
    st.session_state["_selected_preset"] = selected_preset

    st.divider()

    # Show preset details
    if selected_preset == "default":
        st.success("**Default Preset Active**")
        st.caption(
            "Standard mastodon-sim configuration with custom action parsing and optional recommendation system."
        )
    else:
        st.info("**Recommendation Preset Active**")
        st.markdown(
            """
            **Recommendation Features:**
            - ✅ Tool-calling action mode
            - ✅ Recommendation system enabled
            - ✅ Central batch recommendation computation
            - ✅ Full selected backend action surface available
            - ✅ Probe and artifact logging support
            """
        )

    st.markdown("**Quick Links**")
    st.markdown("- 📖 [Documentation](../../../docs/)")
    st.markdown("- 📁 [Scenario Folder](../../../scenarios/)")
    st.divider()
    default_scenarios_root = _resolve_scenarios_root(
        str(st.session_state.get("_loaded_scenarios_root") or "")
    )
    scenarios_root_text = st.text_input(
        "Scenarios directory",
        value=str(default_scenarios_root),
        key="scenarios_root_path",
        help=(
            "Directory containing scenario folders (for example, election). "
            "You can also provide a project root; if it has a nested scenarios/ folder, it will be used."
        ),
    )
    selected_scenarios_root = _resolve_scenarios_root(scenarios_root_text)
    if not selected_scenarios_root.is_dir():
        st.warning(f"Scenarios directory not found: {selected_scenarios_root}")

    st.markdown("**Scenario**")
    external_scenarios = _discover_external_scenarios(selected_scenarios_root)
    pkg_default = _CONF_DIR / "sim" / "base.yaml"
    available_scenarios = {"default": pkg_default, **external_scenarios}
    scenario_names = list(available_scenarios.keys())

    selected_scenario = st.selectbox(
        "Load scenario",
        scenario_names,
        index=0,
        key="sidebar_scenario_select",
        help="Select a scenario package to load.",
    )

    run_configs = _discover_run_configs_for_scenario(selected_scenarios_root, selected_scenario)
    run_options = ["Scenario definition"] + list(run_configs.keys())
    selected_run_source = st.selectbox(
        "Start from",
        run_options,
        index=0,
        key="sidebar_run_source_select",
        help="Choose base scenario config or start from a prior run snapshot.",
    )

    if selected_scenario in available_scenarios:
        if selected_run_source == "Scenario definition":
            selected_path = available_scenarios[selected_scenario]
            source_kind = "scenario"
            source_label = f"{selected_scenario} :: definition"
        else:
            selected_path = run_configs[selected_run_source]
            source_kind = "output_run"
            source_label = f"{selected_scenario} :: run/{selected_run_source}"

        loaded_cfg = _load_yaml(selected_path)
        loaded_world, loaded_sim, loaded_environment = _split_loaded_config(loaded_cfg)

        scenario_name = str(loaded_world.get("scenario_name") or selected_scenario)
        st.session_state["_loaded_world"] = loaded_world
        st.session_state["_loaded_scenario_name"] = scenario_name
        st.session_state["_loaded_sim_defaults"] = loaded_sim
        st.session_state["_loaded_environment_defaults"] = loaded_environment
        st.session_state["_loaded_source_label"] = source_label
        st.session_state["_loaded_source_kind"] = source_kind
        st.session_state["_loaded_source_scenario_key"] = selected_scenario.split("/")[0]
        st.session_state["_loaded_scenarios_root"] = str(selected_scenarios_root)
    else:
        loaded_world = {}

    st.divider()

    # New scenario creation.
    st.markdown("**Create New Scenario**")
    new_name = st.text_input(
        "Scenario name", key="new_scenario_name_input", placeholder="my_scenario"
    )
    if st.button("Create", key="create_new_scenario", use_container_width=True):
        if new_name and new_name.strip():
            clean_name = new_name.strip().lower().replace(" ", "_")
            # Create from selected preset default.
            default_cfg = {
                "scenario_name": clean_name,
                "jobname_format": "N${num_agents}_T${num_steps}_${experiment_name}_${run_name}",
                "setting": {"name": "", "background": []},
                "event": {"name": "", "context": ""},
                "persona_pipeline": {},
                "graph": {},
                "shared_memories": [],
                "initial_observations": [],
                "probes": {},
            }
            save_path = _save_scenario(
                clean_name,
                default_cfg,
                {},
                {},
                "twitter_like",
                selected_scenarios_root,
            )
            st.success(f"Created: `{save_path}`")
            st.rerun()
        else:
            st.error("Enter a scenario name.")

    st.divider()

    # Status placeholder for run messages
    status_placeholder = st.empty()


# ---------------------------------------------------------------------------
# Load defaults
# ---------------------------------------------------------------------------
_world_cfg = st.session_state.get("_loaded_world", {})
_selected_preset_for_defaults = st.session_state.get("_selected_preset", "default")
_sim_base_defaults = _load_yaml(_CONF_DIR / "sim" / "base.yaml")
_sim_loaded_defaults = st.session_state.get("_loaded_sim_defaults", {})
if not isinstance(_sim_loaded_defaults, dict):
    _sim_loaded_defaults = {}
_sim_defaults = _deep_merge_dict(_sim_base_defaults, _sim_loaded_defaults)
_eval_base_defaults = _load_yaml(_CONF_DIR / "eval" / "base.yaml")
_environment_defaults = st.session_state.get("_loaded_environment_defaults", {})
if not isinstance(_environment_defaults, dict):
    _environment_defaults = {}
_agent_modules = _discover_agent_modules()
_gm_defaults = (
    _environment_defaults.get("gm", {})
    if isinstance(_environment_defaults.get("gm", {}), dict)
    else _sim_defaults.get("gm", {})
    if isinstance(_sim_defaults.get("gm", {}), dict)
    else {}
)
_gm_components_defaults = (
    _gm_defaults.get("components", {})
    if isinstance(_gm_defaults.get("components", {}), dict)
    else {}
)
_engine_defaults = (
    _sim_defaults.get("engine", {}) if isinstance(_sim_defaults.get("engine", {}), dict) else {}
)
_engine_action_defaults = (
    _engine_defaults.get("turn_policy", {})
    if isinstance(_engine_defaults.get("turn_policy", {}), dict)
    else {}
)
_engine_probe_defaults = (
    (_world_cfg.get("probes", {}) or {}).get("schedule", {})
    if isinstance((_world_cfg.get("probes", {}) or {}).get("schedule", {}), dict)
    else (_eval_base_defaults.get("probes", {}) or {}).get("schedule", {})
    if isinstance((_eval_base_defaults.get("probes", {}) or {}).get("schedule", {}), dict)
    else {}
)
_llm_defaults = (
    _sim_defaults.get("llm", {}) if isinstance(_sim_defaults.get("llm", {}), dict) else {}
)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
st.title("Simulation Configuration")
scenario_display = st.session_state.get("_loaded_scenario_name", "default")
st.markdown(f"Editing scenario: **{scenario_display}**")
source_label = st.session_state.get("_loaded_source_label", scenario_display)
st.caption(f"Loaded from: {source_label}")

tab_sim, tab_world, tab_classes, tab_env, tab_probes, tab_launch = st.tabs(
    ["Simulation", "World", "Agent Classes", "Environment", "Probes", "Launch"],
)


# ---------------------------------------------------------------------------
# TAB: Simulation
# ---------------------------------------------------------------------------
with tab_sim:
    st.subheader("Core Simulation Parameters")
    col1, col2 = st.columns(2)
    with col1:
        st.number_input(
            "Number of agents",
            min_value=1,
            max_value=1_000_000,
            value=max(1, _as_int(_sim_defaults.get("num_agents", 20), 20)),
            step=10,
            key="num_agents",
        )
        st.number_input(
            "Number of episodes (steps)",
            min_value=1,
            max_value=500,
            value=max(1, _as_int(_sim_defaults.get("num_steps", 50), 50)),
            key="num_steps",
        )
        st.number_input(
            "Random seed",
            min_value=0,
            value=max(0, _as_int(_sim_defaults.get("seed", 1), 1)),
            key="seed",
        )
        st.text_input("Run name", value=str(_sim_defaults.get("run_name", "run1")), key="run_name")

    with col2:
        st.text_input(
            "Default LLM model",
            value=str(_llm_defaults.get("name", "gpt-4o-mini")),
            key="llm_name",
            help="Default model. Per-class overrides in Agent Classes tab.",
        )
        llm_provider_options = ["openai", "openai_compatible", "scripted", "disabled"]
        llm_provider_default = str(_llm_defaults.get("provider", "openai"))
        st.selectbox(
            "LLM provider",
            llm_provider_options,
            index=llm_provider_options.index(llm_provider_default)
            if llm_provider_default in llm_provider_options
            else 0,
            key="llm_provider",
        )
        st.text_input(
            "LLM API base URL",
            value=str(_llm_defaults.get("api_base") or ""),
            key="llm_api_base",
            help="Required for provider=openai_compatible.",
        )
        st.text_input(
            "LLM API key",
            value=str(_llm_defaults.get("api_key") or ""),
            type="password",
            key="llm_api_key",
            help="Uses OPENAI_API_KEY env var if blank.",
        )
        st.number_input(
            "Max concurrent actions",
            min_value=1,
            max_value=10_000,
            value=max(1, _as_int(_sim_defaults.get("max_concurrent_actions", 1000), 1000)),
            key="max_concurrent_actions",
        )

    with st.expander("Advanced settings", expanded=False):
        ac1, ac2 = st.columns(2)
        with ac1:
            st.selectbox(
                "Action mode",
                _ACTION_MODES,
                index=_ACTION_MODES.index(_sim_defaults.get("action_mode", "custom")),
                key="action_mode",
            )
        with ac2:
            st.number_input(
                "Timeline posts",
                min_value=1,
                max_value=100,
                value=max(
                    1,
                    _as_int(
                        _environment_defaults.get(
                            "timeline_posts", _sim_defaults.get("timeline_posts", 10)
                        ),
                        10,
                    ),
                ),
                key="timeline_posts",
            )
            st.number_input(
                "Observation history",
                min_value=10,
                max_value=1000,
                value=max(
                    10,
                    _as_int(
                        _environment_defaults.get(
                            "observation_history", _sim_defaults.get("observation_history", 100)
                        ),
                        100,
                    ),
                ),
                key="observation_history",
            )
            st.checkbox(
                "Disable language model (dry run)",
                value=bool((_sim_defaults.get("llm") or {}).get("disabled", False)),
                key="llm_disabled",
            )

    # Multi-flow and multi-GM orchestration (advanced)
    with st.expander("🔀 Multi-Flow & Orchestration (Advanced)", expanded=False):
        mf1, mf2 = st.columns(2)
        with mf1:
            st.caption(
                "GM flow routing is configured by selecting a routed GM class "
                "and component instances in YAML."
            )
            st.checkbox(
                "Enable Engine Multi-Flow",
                value=str(
                    ((_sim_defaults.get("engine", {}) or {}).get("step", {}) or {}).get(
                        "built_in", "base"
                    )
                )
                == "flow",
                key="enable_engine_multi_flow",
                help="Use the flow-aware engine step strategy.",
            )
        with mf2:
            st.markdown(
                "**When enabled together**: GM routes agents to flows → Engine schedules flows"
            )

        # Multi-GM orchestration (gm_orchestration block)
        st.markdown("**GM Orchestration** (advanced: multiple GMs managing different aspects)")
        gm_orch_default = _environment_defaults.get(
            "gm_orchestration", _sim_defaults.get("gm_orchestration", {})
        )
        gm_orch_yaml = st.text_area(
            "gm_orchestration (YAML)",
            value=yaml.dump(gm_orch_default, default_flow_style=False)
            if gm_orch_default
            else "gms: []\nflow_bindings:\n  flow_to_gm: {}\n  flow_to_gms: {}",
            key="gm_orchestration_yaml",
            height=200,
            help="Define multiple GMs and their flow assignments. Leave empty to disable.",
        )
        try:
            parsed_gm_orch = yaml.safe_load(gm_orch_yaml) or {}
            st.session_state["gm_orchestration_yaml_parsed"] = parsed_gm_orch
        except yaml.YAMLError as e:
            st.error(f"Invalid YAML in gm_orchestration: {e}")
            st.session_state["gm_orchestration_yaml_parsed"] = {}


# ---------------------------------------------------------------------------
# TAB: World
# ---------------------------------------------------------------------------
with tab_world:
    st.subheader("World Configuration")

    col1, col2 = st.columns(2)
    with col1:
        st.text_input(
            "Scenario name",
            value=_world_cfg.get("scenario_name", scenario_display),
            key="scenario_name_edit",
            help="Used for output directory and config resolution.",
        )
        setting = _world_cfg.get("setting", {})
        st.text_input(
            "Setting name",
            value=setting.get("name", ""),
            key="setting_name",
            help="e.g. 'Storhampton', 'Online Forum', etc.",
        )
        bg = setting.get("background", [])
        bg_text = "\n".join(bg) if isinstance(bg, list) else str(bg or "")
        st.text_area("Setting background", value=bg_text, key="setting_background", height=100)

    with col2:
        event = _world_cfg.get("event", {})
        st.text_input("Event name", value=event.get("name", ""), key="event_name")
        st.text_area(
            "Event context",
            value=event.get("context", ""),
            key="event_context",
            height=150,
            help="Main world context injected into agent memories.",
        )

    # Agent initializer.
    pipeline = _world_cfg.get("persona_pipeline", {})
    initialization_cfg = (
        _sim_defaults.get("initialization", {})
        if isinstance(_sim_defaults.get("initialization", {}), dict)
        else {}
    )
    agent_memory_cfg = (
        initialization_cfg.get("agents", {})
        if isinstance(initialization_cfg.get("agents", {}), dict)
        else {}
    )
    initializer_default = str(agent_memory_cfg.get("built_in", "raw_memory"))
    initializer_idx = (
        _RUNTIME_INITIALIZERS.index(initializer_default)
        if initializer_default in _RUNTIME_INITIALIZERS
        else 0
    )
    st.selectbox(
        "Runtime initializer",
        _RUNTIME_INITIALIZERS,
        index=initializer_idx,
        key="runtime_initializer_built_in",
        help="'raw_memory' = direct injection. 'formative_memory' = LLM-generated backstories.",
    )

    # Shared memories.
    defaults_cfg = pipeline.get("defaults", {})
    shared = defaults_cfg.get("shared_memories", [])
    shared_text = "\n".join(str(s) for s in shared) if isinstance(shared, list) else str(shared)
    st.text_area(
        "Shared memories (one per line)",
        value=shared_text,
        key="shared_memories_edit",
        height=100,
        help="Injected into every agent's observation stream at init.",
    )


# ---------------------------------------------------------------------------
# TAB: Agent Classes
# ---------------------------------------------------------------------------
with tab_classes:
    st.subheader("Agent Class Configuration")
    st.caption(
        "Define agent classes. Each class loads personas from a data source "
        "and maps fields to agent parameters."
    )

    pipeline_cfg = _world_cfg.get("persona_pipeline", {})
    classes_cfg = pipeline_cfg.get("classes", {})
    fixed_sets_cfg = _world_cfg.get("fixed_action_sets", {})

    if "fixed_action_sets_file" not in st.session_state:
        st.session_state["fixed_action_sets_file"] = str(fixed_sets_cfg.get("file", "") or "")
    if "fixed_action_sets_inline_yaml" not in st.session_state:
        inline_sets = fixed_sets_cfg.get("inline", {}) if isinstance(fixed_sets_cfg, dict) else {}
        st.session_state["fixed_action_sets_inline_yaml"] = (
            yaml.dump(inline_sets, default_flow_style=False) if inline_sets else ""
        )

    # Initialize session state for classes if not already set.
    if "_agent_classes" not in st.session_state:
        # Convert loaded config to editable list.
        cls_list = []
        for cls_name, cls_data in classes_cfg.items():
            if not isinstance(cls_data, dict):
                continue
            cls_list.append({"name": cls_name, **cls_data})
        if not cls_list:
            cls_list = [
                {
                    "name": "user",
                    "count": 10,
                    "class_path": "silisocs.agents.native.NativeAgent",
                    "data": {
                        "source": "hf_dataset",
                        "dataset": "nvidia/Nemotron-Personas-USA",
                        "split": "train",
                    },
                    "field_map": {"context": "persona"},
                }
            ]
        st.session_state["_agent_classes"] = cls_list

    # Add class button.
    if st.button("+ Add Agent Class", key="add_class"):
        st.session_state["_agent_classes"].append(
            {
                "name": f"class_{len(st.session_state['_agent_classes']) + 1}",
                "count": 10,
                "class_path": "silisocs.agents.native.NativeAgent",
                "data": {
                    "source": "hf_dataset",
                    "dataset": "nvidia/Nemotron-Personas-USA",
                    "split": "train",
                },
                "field_map": {"context": "persona"},
            }
        )
        st.rerun()

    # Render each class.
    classes_to_remove = []
    for i, cls in enumerate(st.session_state["_agent_classes"]):
        with st.expander(f"Class: **{cls.get('name', f'class_{i}')}**", expanded=(i == 0)):
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                cls["name"] = st.text_input(
                    "Class name", value=cls.get("name", ""), key=f"cls_name_{i}"
                )
                cls["count"] = st.number_input(
                    "Count",
                    min_value=1,
                    max_value=100_000,
                    value=max(1, _as_int(cls.get("count", 10), 10)),
                    key=f"cls_count_{i}",
                )
            with c2:
                # Agent class dropdown.
                current_mod = cls.get("class_path", "silisocs.agents.native.NativeAgent")
                mod_options = list(_agent_modules)
                if current_mod not in mod_options:
                    mod_options.append(current_mod)
                mod_idx = mod_options.index(current_mod) if current_mod in mod_options else 0
                cls["class_path"] = st.selectbox(
                    "Agent class",
                    mod_options,
                    index=mod_idx,
                    key=f"cls_mod_{i}",
                    help="Python class path for the agent implementation.",
                )
                # Per-class model override.
                cls_model = cls.get("model", "")
                cls["model"] = st.text_input(
                    "LLM model override",
                    value=str(cls_model or ""),
                    key=f"cls_model_{i}",
                    help="Leave blank to use the default model from Simulation tab.",
                )
            with c3:
                st.markdown("")
                st.markdown("")
                if st.button("Remove", key=f"cls_remove_{i}", use_container_width=True):
                    classes_to_remove.append(i)

            # Data source.
            st.markdown("**Data Source**")
            data = cls.get("data", {}) or {}
            dc1, dc2 = st.columns(2)
            with dc1:
                source = data.get("source", "hf_dataset")
                src_idx = _PERSONA_SOURCES.index(source) if source in _PERSONA_SOURCES else 0
                new_source = st.selectbox(
                    "Source", _PERSONA_SOURCES, index=src_idx, key=f"cls_src_{i}"
                )
                data["source"] = new_source
            with dc2:
                if new_source == "hf_dataset":
                    data["dataset"] = st.text_input(
                        "Dataset",
                        value=data.get("dataset", "nvidia/Nemotron-Personas-USA"),
                        key=f"cls_ds_{i}",
                    )
                    data["split"] = st.text_input(
                        "Split",
                        value=data.get("split", "train"),
                        key=f"cls_split_{i}",
                    )
                elif new_source == "local_json":
                    data["path"] = st.text_input(
                        "JSON path",
                        value=data.get("path", ""),
                        key=f"cls_path_{i}",
                        help="Relative to scenario directory.",
                    )
                    # Verify path exists.
                    if data.get("path"):
                        scenario_name = _world_cfg.get("scenario_name", scenario_display)
                        check_path = _SCENARIOS_DIR / scenario_name / data["path"]
                        if check_path.exists():
                            st.success(f"File found: {check_path.name}")
                        else:
                            st.warning(f"File not found: {check_path}")
                elif new_source == "config_path":
                    data["path"] = st.text_input(
                        "Config dot-path",
                        value=data.get("path", ""),
                        key=f"cls_cfgpath_{i}",
                        help="Dot-path into another config section, e.g. 'candidates'.",
                    )
            cls["data"] = data

            # Field map.
            st.markdown("**Field Mapping**")
            fm = cls.get("field_map", {}) or {}
            fm_text = yaml.dump(fm, default_flow_style=False) if fm else "context: persona"
            new_fm_text = st.text_area(
                "field_map (YAML)",
                value=fm_text,
                key=f"cls_fm_{i}",
                height=80,
                help="Maps data record fields to agent params.",
            )
            try:
                cls["field_map"] = yaml.safe_load(new_fm_text) or {}
            except yaml.YAMLError:
                st.error("Invalid YAML in field_map")

            # Sim role.
            cls["sim_role_name"] = st.text_input(
                "Sim role name",
                value=cls.get("sim_role_name", cls.get("name", "")),
                key=f"cls_role_{i}",
                help="Role name for activity rates and social network config.",
            )
            if bool(st.session_state.get("advanced_config_enabled", False)):
                cls["flow_tag"] = st.text_input(
                    "Flow tag",
                    value=str(cls.get("flow_tag", cls.get("params", {}).get("action_flow", ""))),
                    key=f"cls_flow_tag_{i}",
                    help="Class-level action flow tag used by GM/engine orchestration.",
                )
            else:
                cls.pop("flow_tag", None)

            st.markdown("**Fixed Action Agent (optional)**")
            fixed_cfg = (
                cls.get("fixed_action", {}) if isinstance(cls.get("fixed_action"), dict) else {}
            )
            enabled = st.checkbox(
                "Enable fixed action execution for this class",
                value=bool(fixed_cfg.get("enabled", False)),
                key=f"cls_fixed_enabled_{i}",
            )
            if enabled:
                available_sets = []
                inline_yaml_text = st.session_state.get("fixed_action_sets_inline_yaml", "")
                try:
                    inline_parsed = yaml.safe_load(inline_yaml_text) or {}
                    if isinstance(inline_parsed, dict):
                        available_sets = sorted(inline_parsed.keys())
                except yaml.YAMLError:
                    available_sets = []

                set_default = str(fixed_cfg.get("action_set_ref", "") or "")
                if set_default and set_default not in available_sets:
                    available_sets = [*available_sets, set_default]

                if available_sets:
                    set_ref = st.selectbox(
                        "Action set reference",
                        available_sets,
                        index=available_sets.index(set_default)
                        if set_default in available_sets
                        else 0,
                        key=f"cls_fixed_set_ref_{i}",
                    )
                else:
                    set_ref = st.text_input(
                        "Action set reference",
                        value=set_default,
                        key=f"cls_fixed_set_ref_{i}",
                        help="Define action sets below or in a file and reference the set id here.",
                    )

                policy = st.selectbox(
                    "Selection policy",
                    ["round_robin", "weighted_random", "scripted_sequence"],
                    index=["round_robin", "weighted_random", "scripted_sequence"].index(
                        str(fixed_cfg.get("selection_policy", "round_robin"))
                    )
                    if str(fixed_cfg.get("selection_policy", "round_robin"))
                    in ["round_robin", "weighted_random", "scripted_sequence"]
                    else 0,
                    key=f"cls_fixed_policy_{i}",
                )
                on_exhaustion = st.selectbox(
                    "When action set is exhausted",
                    ["loop", "stop", "fallback_to_llm"],
                    index=["loop", "stop", "fallback_to_llm"].index(
                        str(fixed_cfg.get("on_exhaustion", "loop"))
                    )
                    if str(fixed_cfg.get("on_exhaustion", "loop"))
                    in ["loop", "stop", "fallback_to_llm"]
                    else 0,
                    key=f"cls_fixed_exhaustion_{i}",
                )
                cls["fixed_action"] = {
                    "enabled": True,
                    "action_set_ref": set_ref,
                    "selection_policy": policy,
                    "on_exhaustion": on_exhaustion,
                }
            else:
                cls.pop("fixed_action", None)

    # Process removals.
    if classes_to_remove:
        for idx in reversed(classes_to_remove):
            st.session_state["_agent_classes"].pop(idx)
        st.rerun()

    st.divider()
    st.markdown("**Fixed Action Set Registry**")
    st.caption(
        "Define reusable action sets inline or provide a file path. Class fixed-action settings can reference these set ids."
    )
    st.text_input(
        "Action sets file path (optional)",
        key="fixed_action_sets_file",
        help="Path relative to scenario directory, e.g. input/fixed_actions/sets.yaml",
    )
    st.text_area(
        "Inline action sets (YAML)",
        key="fixed_action_sets_inline_yaml",
        height=180,
        help=(
            'Example:\nnews_cycle:\n  actions:\n    - action: create_tweet\n      args:\n        status: "Breaking: {name} update"'
        ),
    )


# ---------------------------------------------------------------------------
# TAB: Environment
# ---------------------------------------------------------------------------
with tab_env:
    st.subheader("Environment Configuration")

    st.checkbox(
        "Enable advanced configuration",
        value=bool(st.session_state.get("advanced_config_enabled", False)),
        key="advanced_config_enabled",
        help="Shows advanced flow and multi-GM controls. Keep disabled for the simple workflow.",
    )

    st.markdown("**Runtime Environment**")
    ec1, ec2 = st.columns(2)
    with ec1:
        backend_default = str(
            ((_environment_defaults.get("gm") or {}).get("backend") or {}).get(
                "type", "twitter_like"
            )
        )
        st.selectbox(
            "Backend",
            _BACKEND_OPTIONS,
            index=_BACKEND_OPTIONS.index(backend_default)
            if backend_default in _BACKEND_OPTIONS
            else 0,
            key="backend_type",
            help="twitter_like/reddit_like are local. mastodon requires a server.",
        )
    with ec2:
        st.caption("GM and engine policy controls are below.")

    selected_backend_for_actions = st.session_state.get("backend_type", backend_default)
    action_catalog = _backend_action_catalog(selected_backend_for_actions)
    action_labels = [item["selectable_name"] for item in action_catalog]
    configured_enabled = ((_environment_defaults.get("gm") or {}).get("backend") or {}).get(
        "enabled_actions"
    )
    configured_excluded = ((_environment_defaults.get("gm") or {}).get("backend") or {}).get(
        "excluded_actions"
    )
    default_enabled = configured_enabled if isinstance(configured_enabled, list) else []
    default_excluded = configured_excluded if isinstance(configured_excluded, list) else []
    st.multiselect(
        "Enabled backend actions (leave empty to allow all)",
        action_labels,
        default=[name for name in default_enabled if name in action_labels],
        key="enabled_actions",
        help="Constrains action prompts, parser/tool choices, and fixed-action agent execution.",
    )
    st.multiselect(
        "Excluded backend actions",
        action_labels,
        default=[name for name in default_excluded if name in action_labels],
        key="excluded_actions",
        help="Removes actions from prompts, parser/tool choices, and fixed-action execution.",
    )

    if selected_backend_for_actions in _SOCIAL_BACKENDS:
        st.markdown("**Timeline Configuration**")
        observe_params = (
            ((_gm_components_defaults.get("observe") or {}).get("params") or {})
            if isinstance(_gm_components_defaults.get("observe"), dict)
            else {}
        )
        timeline_strategy_default = observe_params.get("timeline_mode", "follower_chronological")
        timeline_config_default = observe_params.get("timeline_config", {})

        timeline_strategies_by_backend = {
            "twitter_like": [
                "follower_chronological",
                "pure_recsys",
                "hybrid_recsys_follower",
                "curated_global",
            ],
            "reddit_like": ["follower_chronological", "pure_recsys", "hybrid_recsys_follower"],
            "mastodon": ["follower_chronological"],
        }

        available_strategies = timeline_strategies_by_backend.get(
            selected_backend_for_actions, ["follower_chronological"]
        )
        strategy_idx = (
            available_strategies.index(timeline_strategy_default)
            if timeline_strategy_default in available_strategies
            else 0
        )

        selected_timeline_strategy = st.selectbox(
            "Timeline strategy",
            available_strategies,
            index=strategy_idx,
            key="timeline_strategy",
            help=(
                "follower_chronological: posts from followed users\n"
                "pure_recsys: recommendation feed\n"
                "hybrid_recsys_follower: recommendations plus followed users\n"
                "curated_global: global and network mix for Twitter-like runs"
            ),
        )

        if selected_timeline_strategy == "hybrid_recsys_follower":
            tc_col1, tc_col2 = st.columns(2)
            with tc_col1:
                recsys_ratio = st.slider(
                    "Recommendation ratio",
                    0.0,
                    1.0,
                    float(timeline_config_default.get("recsys_ratio", 0.6)),
                    0.1,
                    key="timeline_recsys_ratio",
                    help="Fraction of timeline to fill with recommendations",
                )
            with tc_col2:
                follower_ratio = 1.0 - recsys_ratio
                st.metric("Follower ratio", f"{follower_ratio:.1%}")

        st.markdown("**Seed Posts Configuration**")
        seed_posts_type = st.selectbox(
            "Seed post provider",
            ["agent", "csv", "json", "none", "fallback"],
            index=0,
            key="seed_posts_type",
            help=(
                "agent: ask agents for starting posts\n"
                "csv: load from CSV file (agent_name,post_text)\n"
                "json: load from JSON file ({agent_name: post_text})\n"
                "none: no seed posts\n"
                "fallback: use configured file rows first, then ask agents for missing posts"
            ),
        )

        if seed_posts_type in ("csv", "json", "fallback"):
            st.text_input(
                "Seed posts file path",
                value="",
                key="seed_posts_file",
                help="Path to CSV or JSON file with agent posts",
                placeholder="agents_posts.csv or agents_posts.json",
            )

    with st.expander("GM Components", expanded=False):
        gc1, gc2 = st.columns(2)

        next_acting_defaults = _gm_components_defaults.get("next_acting", {})
        observe_defaults = _gm_components_defaults.get("observe", {})
        resolve_defaults = _gm_components_defaults.get("resolve", {})
        initialize_defaults = _gm_components_defaults.get("initialize", {})
        if selected_backend_for_actions in _SOCIAL_BACKENDS:
            next_acting_default = (
                next_acting_defaults.get("built_in", "activity_probability")
                if isinstance(next_acting_defaults, dict)
                else "activity_probability"
            )
            observe_default = (
                observe_defaults.get("built_in", "timeline_every_turn")
                if isinstance(observe_defaults, dict)
                else "timeline_every_turn"
            )
            resolve_default = (
                resolve_defaults.get("built_in", "tool_calling")
                if isinstance(resolve_defaults, dict)
                else "tool_calling"
            )
            gm_initializer_default = (
                initialize_defaults.get("built_in", "social_media")
                if isinstance(initialize_defaults, dict)
                else "social_media"
            )
        else:
            next_acting_default = "fixed_order"
            observe_default = "app_observation"
            resolve_default = "tool_calling"
            gm_initializer_default = "app_initialize"

        with gc1:
            st.selectbox(
                "Next-acting policy",
                _GM_NEXT_ACTING_OPTIONS,
                index=_GM_NEXT_ACTING_OPTIONS.index(next_acting_default)
                if next_acting_default in _GM_NEXT_ACTING_OPTIONS
                else 0,
                key="gm_next_acting_built_in",
            )
            st.selectbox(
                "Observation component",
                _GM_OBSERVE_OPTIONS,
                index=_GM_OBSERVE_OPTIONS.index(observe_default)
                if observe_default in _GM_OBSERVE_OPTIONS
                else 0,
                key="gm_observe_built_in",
            )
            st.selectbox(
                "Resolve component",
                _GM_RESOLVE_OPTIONS,
                index=_GM_RESOLVE_OPTIONS.index(resolve_default)
                if resolve_default in _GM_RESOLVE_OPTIONS
                else 0,
                key="gm_resolve_built_in",
            )
            st.selectbox(
                "Game Master initializer",
                _BACKEND_INITIALIZER_OPTIONS,
                index=_BACKEND_INITIALIZER_OPTIONS.index(gm_initializer_default)
                if gm_initializer_default in _BACKEND_INITIALIZER_OPTIONS
                else 0,
                key="gm_initializer_built_in",
            )

        with gc2:
            st.text_input(
                "Custom next-acting class path",
                value=str(next_acting_defaults.get("class_path") or "")
                if isinstance(next_acting_defaults, dict)
                else "",
                key="gm_next_acting_class_path",
                help="Optional fully-qualified class path to override built-in choice.",
            )
            st.text_input(
                "Custom observe class path",
                value=str(observe_defaults.get("class_path") or "")
                if isinstance(observe_defaults, dict)
                else "",
                key="gm_observe_class_path",
                help="Optional fully-qualified class path to override built-in choice.",
            )
            st.text_input(
                "Custom resolve class path",
                value=str(resolve_defaults.get("class_path") or "")
                if isinstance(resolve_defaults, dict)
                else "",
                key="gm_resolve_class_path",
                help="Optional fully-qualified class path to override built-in choice.",
            )
            st.text_input(
                "Custom Game Master initializer class path",
                value="",
                key="gm_initializer_class_path",
                help="Optional fully-qualified class path to override built-in choice.",
            )

    with st.expander("Engine Policies", expanded=False):
        ep1, ep2 = st.columns(2)

        turn_policy_default = str(_engine_action_defaults.get("built_in", "single_action"))
        probe_schedule_default = str(_engine_probe_defaults.get("built_in", "step_schedule"))
        turn_policy_params = (
            _engine_action_defaults.get("params", {})
            if isinstance(_engine_action_defaults.get("params", {}), dict)
            else {}
        )
        probe_schedule_params = (
            _engine_probe_defaults.get("params", {})
            if isinstance(_engine_probe_defaults.get("params", {}), dict)
            else {}
        )

        with ep1:
            st.selectbox(
                "Turn policy",
                _ENGINE_TURN_POLICY_OPTIONS,
                index=_ENGINE_TURN_POLICY_OPTIONS.index(turn_policy_default)
                if turn_policy_default in _ENGINE_TURN_POLICY_OPTIONS
                else 0,
                key="engine_turn_policy_built_in",
            )
            st.number_input(
                "Fixed-count actions per agent",
                min_value=1,
                max_value=20,
                value=max(1, _as_int(turn_policy_params.get("count", 2), 2)),
                key="engine_turn_policy_count",
            )
            st.number_input(
                "Open-ended max actions per agent",
                min_value=1,
                max_value=50,
                value=max(1, _as_int(turn_policy_params.get("max_actions", 3), 3)),
                key="engine_turn_policy_max_actions",
            )
            st.text_input(
                "Open-ended finished action signal",
                value=str(turn_policy_params.get("finished_action_signal", "DONE")),
                key="engine_turn_policy_finished_action_signal",
            )
            observe_default = str(turn_policy_params.get("observe_before_act", "first"))
            observe_options = ["first", "always", "never"]
            st.selectbox(
                "Observe before act",
                observe_options,
                index=observe_options.index(observe_default)
                if observe_default in observe_options
                else 0,
                key="engine_turn_policy_observe_before_act",
                help="first preserves current behavior; always refreshes context before every repeated action; never disables turn-policy observations.",
            )
            st.text_input(
                "Custom turn-policy class path",
                value=str(_engine_action_defaults.get("class_path") or ""),
                key="engine_turn_policy_class_path",
                help="Optional fully-qualified class path to override built-in choice.",
            )

        with ep2:
            st.selectbox(
                "Probe schedule policy",
                _ENGINE_PROBE_SCHEDULE_OPTIONS,
                index=_ENGINE_PROBE_SCHEDULE_OPTIONS.index(probe_schedule_default)
                if probe_schedule_default in _ENGINE_PROBE_SCHEDULE_OPTIONS
                else 0,
                key="engine_probe_schedule_built_in",
            )
            st.number_input(
                "Probe schedule start step",
                min_value=0,
                max_value=1_000_000,
                value=max(0, _as_int(probe_schedule_params.get("start_step", 0), 0)),
                key="engine_probe_schedule_start_step",
            )
            st.number_input(
                "Probe schedule interval",
                min_value=1,
                max_value=1_000_000,
                value=max(1, _as_int(probe_schedule_params.get("every_n_steps", 1), 1)),
                key="engine_probe_schedule_every_n_steps",
            )
            st.text_input(
                "Custom probe-schedule class path",
                value=str(_engine_probe_defaults.get("class_path") or ""),
                key="engine_probe_schedule_class_path",
                help="Optional fully-qualified class path to override built-in choice.",
            )

    if bool(st.session_state.get("advanced_config_enabled", False)):
        with st.expander("Advanced GM Orchestration", expanded=False):
            st.caption(
                "Advanced: multi-GM and flow orchestration. Leave disabled for the default easy workflow."
            )
            st.info(
                "Advanced orchestration is intended for expert YAML workflows. "
                "Use this section to copy/edit settings, then apply through config files or explicit Hydra overrides."
            )
            default_orchestration = _environment_defaults.get(
                "gm_orchestration", _sim_defaults.get("gm_orchestration", {})
            )
            orchestration_yaml = (
                yaml.dump(default_orchestration, default_flow_style=False)
                if isinstance(default_orchestration, dict)
                else "{}\n"
            )
            st.text_area(
                "gm_orchestration (YAML)",
                value=orchestration_yaml,
                key="gm_orchestration_yaml",
                height=180,
                help="Supports gms, flow_to_gm, flow_to_gms, gm_to_flows.",
            )

    with st.expander(
        "Recommendation System (Recsys)",
        expanded=(_selected_preset_for_defaults == "recommendation"),
    ):
        st.markdown("**Recommendation Engine**")
        st.caption(
            "Central batch recommendation algorithm with embedding caching and lazy evaluation."
        )

        update_cfg = (
            _gm_defaults.get("components", {}).get("update", {})
            if isinstance(_gm_defaults, dict)
            else {}
        )
        update_params = update_cfg.get("params", {}) if isinstance(update_cfg, dict) else {}
        observe_cfg = (
            _gm_defaults.get("components", {}).get("observe", {})
            if isinstance(_gm_defaults, dict)
            else {}
        )
        observe_flows = observe_cfg.get("flows", {}) if isinstance(observe_cfg, dict) else {}
        update_flows = update_cfg.get("flows", {}) if isinstance(update_cfg, dict) else {}
        flow_defaults = observe_flows or update_flows

        rc1, rc2, rc3 = st.columns(3)
        with rc1:
            recsys_enabled = (
                _environment_defaults.get(
                    "timeline_mode",
                    _environment_defaults.get(
                        "timeline_strategy",
                        _sim_defaults.get("timeline_strategy", "follower_chronological"),
                    ),
                )
                != "follower_chronological"
            )
            st.checkbox(
                "Enable recommendations",
                value=recsys_enabled,
                key="engine_recsys_enabled",
                help="When enabled, agents see ~60% recommended posts + 40% followed user posts",
            )

        with rc2:
            recsys_type_default = "reddit"
            if isinstance(flow_defaults, dict):
                recsys_type_default = (
                    flow_defaults.get("default", {}).get("recsys_type")
                    or next(
                        (
                            cfg.get("recsys_type")
                            for cfg in flow_defaults.values()
                            if isinstance(cfg, dict) and cfg.get("recsys_type")
                        ),
                        None,
                    )
                    or recsys_type_default
                )
            _RECSYS_TYPES = ["reddit", "twitter", "twhin"]
            st.selectbox(
                "Algorithm type",
                _RECSYS_TYPES,
                index=_RECSYS_TYPES.index(recsys_type_default)
                if recsys_type_default in _RECSYS_TYPES
                else 0,
                key="engine_recsys_type",
                help=(
                    "reddit: Hot-score (engagement+recency) | "
                    "twitter: TF-IDF (bio-to-content) | "
                    "twhin: Deep embeddings"
                ),
            )

        with rc3:
            st.markdown("")
            st.markdown("")
            if st.checkbox("Show algorithm details", key="show_recsys_details"):
                algo_selection = st.session_state.get("engine_recsys_type", "reddit")
                if algo_selection == "reddit":
                    st.info(
                        "**Reddit Hot-Score**: `sign(score) × log(|score|) + (age_seconds / 45000)`\n\n"
                        "Boosts posts with high engagement and recent posts. Creates feedback loops."
                    )
                elif algo_selection == "twitter":
                    st.info(
                        "**Twitter TF-IDF**: Cosine similarity between user bio and post content\n\n"
                        "Personalizes feed to user interests. Creates filter bubbles and echo chambers."
                    )
                elif algo_selection == "twhin":
                    st.info(
                        "**TWHIN-BERT**: Deep learning embeddings via Twitter model\n\n"
                        "Most accurate but compute-intensive. Requires transformers library."
                    )

        st.divider()

        rp1, rp2, rp3 = st.columns(3)

        with rp1:
            st.number_input(
                "Max recommended posts",
                min_value=1,
                max_value=50,
                value=max(1, _as_int(update_params.get("max_posts", 10), 10)),
                key="engine_recsys_max_rec_posts",
                help="Number of posts to recommend to each agent",
            )

        with rp2:
            st.number_input(
                "Update every N steps",
                min_value=1,
                max_value=100,
                value=max(1, _as_int(update_params.get("update_every_n_steps", 1), 1)),
                key="engine_recsys_update_every_n_steps",
                help="Frequency of recomputing recommendations (1=every step)",
            )

        with rp3:
            st.checkbox(
                "Lazy evaluation",
                value=True,
                key="engine_recsys_lazy",
                help="Only compute recs for active agents (faster for sparse activity)",
            )

        if st.checkbox("📊 Timeline Composition Details", key="show_timeline_composition"):
            st.markdown("""
            #### How Agents See the Timeline

            The combined feed merges two sources:
            1. **Personalized Recommendations** (~60%): Generated by the recsys algorithm, updates centrally
            2. **Followed User Posts** (~40%): Chronological feed from users the agent follows

            This combination mirrors real social media behavior:
            - Recsys posts drive engagement and create feedback loops
            - Followed posts maintain social identity and network influence
            """)

    st.markdown("**Social Graph Configuration**")
    net_cfg = _world_cfg.get("graph", {})

    nc1, nc2 = st.columns(2)
    with nc1:
        nt_default = net_cfg.get("network_type", "barabasi_albert")
        nt_idx = _NETWORK_TYPES.index(nt_default) if nt_default in _NETWORK_TYPES else 0
        st.selectbox(
            "Network topology",
            _NETWORK_TYPES,
            index=nt_idx,
            key="network_type",
            help="barabasi_albert = scale-free, random = Erdos-Renyi.",
        )
        st.number_input(
            "BA model edges per node (m)",
            min_value=1,
            max_value=200,
            value=max(1, _as_int(net_cfg.get("barabasi_albert_m", 10), 10)),
            key="ba_m",
        )
        st.slider(
            "Base follow probability",
            0.0,
            1.0,
            _as_float(net_cfg.get("base_followership_probability", 0.3), 0.3),
            0.05,
            key="follow_prob",
        )

    with nc2:
        st.markdown("**Activity transition rates**")
        st.caption("Two-state Markov process: P(inactive->active) and P(active->inactive).")
        activity = net_cfg.get("activity_transition_rates", {})
        # Show rates for known classes, plus allow editing.
        all_roles = set(activity.keys())
        for cls in st.session_state.get("_agent_classes", []):
            role = cls.get("sim_role_name") or cls.get("name", "")
            if role:
                all_roles.add(role)
        for role in sorted(all_roles):
            rates = activity.get(role, {})
            if not isinstance(rates, dict):
                rates = {}
            with st.expander(role, expanded=False):
                st.slider(
                    f"{role}: inactive -> active",
                    0.0,
                    1.0,
                    _as_float(rates.get("inactive_to_active", 0.3), 0.3),
                    0.05,
                    key=f"act_{role}_i2a",
                )
                st.slider(
                    f"{role}: active -> inactive",
                    0.0,
                    1.0,
                    _as_float(rates.get("active_to_inactive", 0.3), 0.3),
                    0.05,
                    key=f"act_{role}_a2i",
                )


# ---------------------------------------------------------------------------
# TAB: Probes
# ---------------------------------------------------------------------------
with tab_probes:
    st.subheader("Probe Configuration")
    probes_cfg = _world_cfg.get("probes", {})
    deploy_cfg = probes_cfg.get("deployment", {}) if isinstance(probes_cfg, dict) else {}

    active_probe_world = st.session_state.get("_loaded_scenario_name", "default")
    if (
        "_probe_items" not in st.session_state
        or st.session_state.get("_probe_items_world") != active_probe_world
    ):
        st.session_state["_probe_items"] = _normalize_probe_items(probes_cfg)
        st.session_state["_probe_items_world"] = active_probe_world

    pc1, pc2 = st.columns(2)
    with pc1:
        st.checkbox(
            "Enable probes", value=bool(deploy_cfg.get("enabled", True)), key="probes_enabled"
        )
        st.number_input(
            "Start at episode",
            min_value=0,
            value=max(0, _as_int(deploy_cfg.get("start_step", 1), 1)),
            key="probe_start",
        )
        st.number_input(
            "Deploy every N episodes",
            min_value=1,
            value=max(1, _as_int(deploy_cfg.get("every_n_steps", 1), 1)),
            key="probe_interval",
        )
        probe_role_options = sorted(
            {
                (cls.get("sim_role_name") or cls.get("name", ""))
                for cls in st.session_state.get("_agent_classes", [])
                if (cls.get("sim_role_name") or cls.get("name", ""))
            }
        )
        default_include = (
            deploy_cfg.get("include_agents", []) if isinstance(deploy_cfg, dict) else []
        )
        default_exclude = (
            deploy_cfg.get("exclude_agents", []) if isinstance(deploy_cfg, dict) else []
        )
        st.multiselect(
            "Include roles/agents (optional)",
            options=probe_role_options,
            default=[x for x in default_include if x in probe_role_options],
            key="probes_include_agents",
        )
        st.multiselect(
            "Exclude roles/agents (optional)",
            options=probe_role_options,
            default=[x for x in default_exclude if x in probe_role_options],
            key="probes_exclude_agents",
        )
    with pc2:
        st.markdown("**Probe list**")
        if st.button("+ Add Probe", key="probe_add"):
            st.session_state["_probe_items"].append(
                {
                    "probe_name": f"probe_{len(st.session_state['_probe_items']) + 1}",
                    "probe_type": "FreeTextProbe",
                    "probe_data": {
                        "name": f"probe_{len(st.session_state['_probe_items']) + 1}",
                        "question": "What do you think right now?",
                        "context": "{agentname} reflects on the current situation.",
                    },
                }
            )
            st.rerun()

    items_to_remove: list[int] = []
    for i, item in enumerate(st.session_state.get("_probe_items", [])):
        probe_name = str(item.get("probe_name") or f"probe_{i}")
        with st.expander(f"Probe: {probe_name}", expanded=False):
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                item["probe_name"] = st.text_input(
                    "Probe name",
                    value=probe_name,
                    key=f"probe_name_{i}",
                    help="Used as stable identifier and log label.",
                )
                current_type = str(item.get("probe_type", "FreeTextProbe"))
                type_options = list(_PROBE_TYPE_OPTIONS)
                if current_type not in type_options:
                    type_options.append(current_type)
                item["probe_type"] = st.selectbox(
                    "Probe type",
                    options=type_options,
                    index=type_options.index(current_type),
                    key=f"probe_type_{i}",
                )
            with c2:
                qd = item.get("probe_data", {})
                if not isinstance(qd, dict):
                    qd = {}
                qd["name"] = st.text_input(
                    "Display name",
                    value=str(qd.get("name") or item["probe_name"]),
                    key=f"probe_display_name_{i}",
                )
                qd["context"] = st.text_area(
                    "Context",
                    value=str(qd.get("context", "")),
                    key=f"probe_context_{i}",
                    height=80,
                )
                qd["question"] = st.text_area(
                    "Question",
                    value=str(qd.get("question", "")),
                    key=f"probe_question_{i}",
                    height=80,
                )

                if item["probe_type"] == "NumericRatingProbe":
                    qd["lo"] = st.number_input(
                        "Min rating",
                        min_value=0,
                        value=max(0, _as_int(qd.get("lo", 1), 1)),
                        key=f"probe_lo_{i}",
                    )
                    qd["hi"] = st.number_input(
                        "Max rating",
                        min_value=int(qd["lo"]),
                        value=max(int(qd["lo"]), _as_int(qd.get("hi", 10), 10)),
                        key=f"probe_hi_{i}",
                    )
                if item["probe_type"] == "ChoiceProbe":
                    choices_text = "\n".join(str(x) for x in qd.get("choices", []))
                    new_choices_text = st.text_area(
                        "Choices (one per line)",
                        value=choices_text,
                        key=f"probe_choices_{i}",
                        height=90,
                    )
                    qd["choices"] = [x.strip() for x in new_choices_text.splitlines() if x.strip()]

                labels_text = yaml.dump(qd.get("labels", {}), default_flow_style=False)
                new_labels_text = st.text_area(
                    "Labels (YAML, optional)",
                    value=labels_text,
                    key=f"probe_labels_{i}",
                    height=70,
                )
                try:
                    qd["labels"] = yaml.safe_load(new_labels_text) or {}
                except yaml.YAMLError:
                    st.error("Invalid YAML in labels")

                item["probe_data"] = qd
            with c3:
                st.markdown("\n")
                if st.button("Remove", key=f"probe_remove_{i}", use_container_width=True):
                    items_to_remove.append(i)

    if items_to_remove:
        for idx in reversed(items_to_remove):
            st.session_state["_probe_items"].pop(idx)
        st.rerun()


# ---------------------------------------------------------------------------
# TAB: Launch
# ---------------------------------------------------------------------------
with tab_launch:
    st.subheader("Review & Launch")

    # Collect sim params.
    sim_params = {
        "num_agents": st.session_state.get("num_agents", 20),
        "num_steps": st.session_state.get("num_steps", 50),
        "seed": st.session_state.get("seed", 1),
        "run_name": st.session_state.get("run_name", "run1"),
        "llm.provider": st.session_state.get("llm_provider", "openai"),
        "llm.name": st.session_state.get("llm_name", "gpt-4o-mini"),
        "llm.api_base": st.session_state.get("llm_api_base") or None,
        "llm.api_key": st.session_state.get("llm_api_key") or None,
        "max_concurrent_actions": st.session_state.get("max_concurrent_actions", 1000),
        "action_mode": st.session_state.get("action_mode", "custom"),
        "initialization.agents.built_in": st.session_state.get(
            "runtime_initializer_built_in", "raw_memory"
        ),
        "initialization.agents.class_path": None,
        "initialization.agents.params": {},
        "initialization.game_masters.built_in": "default",
        "initialization.game_masters.class_path": None,
        "initialization.game_masters.params": {},
        "initialization.simulation.built_in": (
            "none" if st.session_state.get("seed_posts_type", "none") == "none" else "seed_posts"
        ),
        "initialization.simulation.class_path": None,
        "initialization.simulation.params.type": st.session_state.get("seed_posts_type", "none"),
        "initialization.simulation.params.params.file_path": (
            st.session_state.get("seed_posts_file")
            if st.session_state.get("seed_posts_file")
            else None
        ),
        "llm.disabled": st.session_state.get("llm_disabled", False),
        "engine.step.built_in": (
            "flow" if st.session_state.get("enable_engine_multi_flow", False) else "base"
        ),
        "engine.turn_policy.built_in": st.session_state.get(
            "engine_turn_policy_built_in", "single_action"
        ),
        "engine.turn_policy.class_path": (
            st.session_state.get("engine_turn_policy_class_path") or None
        ),
        "engine.turn_policy.params.count": st.session_state.get("engine_turn_policy_count", 2),
        "engine.turn_policy.params.max_actions": st.session_state.get(
            "engine_turn_policy_max_actions", 3
        ),
        "engine.turn_policy.params.finished_action_signal": st.session_state.get(
            "engine_turn_policy_finished_action_signal", "DONE"
        ),
        "engine.turn_policy.params.observe_before_act": st.session_state.get(
            "engine_turn_policy_observe_before_act", "first"
        ),
    }
    eval_params = {
        "probes.schedule.built_in": st.session_state.get(
            "engine_probe_schedule_built_in", "step_schedule"
        ),
        "probes.schedule.class_path": (
            st.session_state.get("engine_probe_schedule_class_path") or None
        ),
        "probes.schedule.params.start_step": st.session_state.get(
            "engine_probe_schedule_start_step", 0
        ),
        "probes.schedule.params.every_n_steps": st.session_state.get(
            "engine_probe_schedule_every_n_steps", 1
        ),
    }
    selected_backend = st.session_state.get("backend_type", "twitter_like")
    env_params = {
        "gm.backend.enabled_actions": (
            st.session_state.get("enabled_actions")
            if st.session_state.get("enabled_actions")
            else None
        ),
        "gm.backend.excluded_actions": (
            st.session_state.get("excluded_actions")
            if st.session_state.get("excluded_actions")
            else None
        ),
        "observation_history": st.session_state.get("observation_history", 100),
        "gm_orchestration": st.session_state.get("gm_orchestration_yaml_parsed", {}),
        "gm.components.initialize.built_in": st.session_state.get(
            "gm_initializer_built_in",
            "social_media" if selected_backend in _SOCIAL_BACKENDS else "app_initialize",
        ),
        "gm.components.initialize.class_path": (
            st.session_state.get("gm_initializer_class_path") or None
        ),
        "gm.components.next_acting.built_in": st.session_state.get(
            "gm_next_acting_built_in",
            "activity_probability" if selected_backend in _SOCIAL_BACKENDS else "fixed_order",
        ),
        "gm.components.next_acting.class_path": (
            st.session_state.get("gm_next_acting_class_path") or None
        ),
        "gm.components.observe.built_in": st.session_state.get(
            "gm_observe_built_in",
            "timeline_every_turn" if selected_backend in _SOCIAL_BACKENDS else "app_observation",
        ),
        "gm.components.observe.class_path": (st.session_state.get("gm_observe_class_path") or None),
        "gm.components.resolve.built_in": st.session_state.get(
            "gm_resolve_built_in", "tool_calling"
        ),
        "gm.components.resolve.class_path": (st.session_state.get("gm_resolve_class_path") or None),
    }
    if selected_backend in _SOCIAL_BACKENDS:
        env_params.update(
            {
                "gm.components.observe.params.timeline_posts": st.session_state.get(
                    "timeline_posts", 10
                ),
                "gm.components.observe.params.timeline_mode": st.session_state.get(
                    "timeline_strategy", "follower_chronological"
                ),
                "gm.components.observe.params.timeline_config": {
                    "recsys_ratio": st.session_state.get("timeline_recsys_ratio", 0.6),
                    "follower_ratio": 1.0 - st.session_state.get("timeline_recsys_ratio", 0.6),
                },
                "gm.components.update.built_in": (
                    "social_recommendation"
                    if st.session_state.get("engine_recsys_enabled", False)
                    else "disabled"
                ),
                "gm.components.update.params.default_recsys_type": st.session_state.get(
                    "engine_recsys_type", "reddit"
                ),
                "gm.components.update.params.max_posts": st.session_state.get(
                    "engine_recsys_max_rec_posts", 10
                ),
                "gm.components.update.params.update_every_n_steps": st.session_state.get(
                    "engine_recsys_update_every_n_steps", 1
                ),
                "gm.components.update.params.lazy": st.session_state.get(
                    "engine_recsys_lazy", True
                ),
            }
        )
    else:
        env_params.update(
            {
                "gm.components.initialize.built_in": "app_initialize",
                "gm.components.next_acting.built_in": "fixed_order",
                "gm.components.observe.built_in": "app_observation",
                "gm.components.resolve.built_in": "tool_calling",
                "gm.components.update.built_in": "disabled",
            }
        )

    # Build world config for saving.
    scenario_data = _build_world_config()

    # Summary.
    st.markdown("**Configuration summary**")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.metric("Agents", sim_params["num_agents"])
        st.metric("Episodes", sim_params["num_steps"])
    with sc2:
        st.metric("Default LLM", sim_params["llm.name"])
        st.metric("Backend", selected_backend)
    with sc3:
        n_classes = len(st.session_state.get("_agent_classes", []))
        st.metric("Agent Classes", n_classes)

    # Per-class model summary.
    classes = st.session_state.get("_agent_classes", [])
    class_models = {c["name"]: c.get("model", "") for c in classes if c.get("model")}
    if class_models:
        st.markdown("**Per-class LLM models**")
        for cls_name, model_name in class_models.items():
            st.markdown(f"- `{cls_name}`: {model_name}")

    # Build Hydra CLI.
    graph_overrides = (
        {
            "gm.components.initialize.params.graph.network_type": st.session_state.get(
                "network_type", "barabasi_albert"
            ),
            "gm.components.initialize.params.graph.barabasi_albert_m": st.session_state.get(
                "ba_m", 10
            ),
            "gm.components.initialize.params.graph.base_followership_probability": st.session_state.get(
                "follow_prob", 0.3
            ),
        }
        if selected_backend in _SOCIAL_BACKENDS
        else {}
    )
    overrides = _build_hydra_overrides(
        sim_params,
        env_params,
        selected_backend,
        graph_overrides,
        eval_params,
    )

    # Determine config path for external scenarios.
    scenario_key_for_paths = st.session_state.get("_loaded_source_scenario_key", scenario_display)
    loaded_scenarios_root = _resolve_scenarios_root(
        str(st.session_state.get("_loaded_scenarios_root") or "")
    )
    config_path = _get_config_path_for_scenario(
        loaded_scenarios_root,
        str(scenario_key_for_paths),
    )

    with st.expander("Hydra CLI command", expanded=False):
        runner_path = _PACKAGE_ROOT / "runtime" / "runner.py"
        parts = [f"python {runner_path}"]
        if config_path:
            parts.append(f"--config-path {config_path}")
        parts.extend(overrides)
        st.code(" \\\n  ".join(parts), language="bash")

    st.divider()

    # Save / Launch buttons.
    st.markdown("**Actions**")
    btn1, btn2, btn3 = st.columns(3)

    with btn1:
        if st.button("Save Scenario", key="save_scenario", use_container_width=True):
            name = st.session_state.get("scenario_name_edit", scenario_display)

            # Build sim_data dict with only non-default overrides
            # Extract from sim_params but filter to only include values that differ from base defaults
            sim_data_to_save = {}
            env_data_to_save = {}
            for key in [
                # Core sim params
                "num_agents",
                "num_steps",
                "llm_provider",
                "llm_name",
                "run_name",
                "seed",
                "action_mode",
                "llm.disabled",
                # Multi-flow/orchestration flags
                "engine.step.built_in",
                # Engine turn policy
                "engine.turn_policy.built_in",
            ]:
                session_key = key.replace(".", "_")
                val = st.session_state.get(session_key, None)
                if val is not None:
                    sim_data_to_save[key] = val
            sim_data_to_save["engine.step.built_in"] = (
                "flow" if st.session_state.get("enable_engine_multi_flow", False) else "base"
            )
            sim_data_to_save["initialization.agents.built_in"] = st.session_state.get(
                "runtime_initializer_built_in", "raw_memory"
            )
            sim_data_to_save["initialization.game_masters.built_in"] = "default"
            seed_type = st.session_state.get("seed_posts_type", "none")
            sim_data_to_save["initialization.simulation.built_in"] = (
                "none" if seed_type == "none" else "seed_posts"
            )
            sim_data_to_save["initialization.simulation.params.type"] = seed_type
            if st.session_state.get("seed_posts_file"):
                sim_data_to_save["initialization.simulation.params.params.file_path"] = (
                    st.session_state.get("seed_posts_file")
                )

            env_data_to_save.update(
                {
                    "gm.components.observe.params.timeline_posts": st.session_state.get(
                        "timeline_posts", 10
                    ),
                    "gm.components.observe.params.timeline_mode": st.session_state.get(
                        "timeline_strategy", "follower_chronological"
                    ),
                    "observation_history": st.session_state.get("observation_history", 100),
                    "gm.backend.enabled_actions": (
                        st.session_state.get("enabled_actions")
                        if st.session_state.get("enabled_actions")
                        else None
                    ),
                    "gm.backend.excluded_actions": (
                        st.session_state.get("excluded_actions")
                        if st.session_state.get("excluded_actions")
                        else None
                    ),
                    "gm.components.initialize.built_in": st.session_state.get(
                        "gm_initializer_built_in", "social_media"
                    ),
                    "gm.components.initialize.class_path": (
                        st.session_state.get("gm_initializer_class_path") or None
                    ),
                    "gm.components.next_acting.built_in": st.session_state.get(
                        "gm_next_acting_built_in", "activity_markov"
                    ),
                    "gm.components.observe.built_in": st.session_state.get(
                        "gm_observe_built_in", "timeline_every_turn"
                    ),
                    "gm.components.resolve.built_in": st.session_state.get(
                        "gm_resolve_built_in", "parsed_action"
                    ),
                    "gm.components.update.built_in": (
                        "social_recommendation"
                        if st.session_state.get("engine_recsys_enabled", False)
                        else "disabled"
                    ),
                    "gm.components.update.params.default_recsys_type": st.session_state.get(
                        "engine_recsys_type", "reddit"
                    ),
                    "gm.components.update.params.max_posts": st.session_state.get(
                        "engine_recsys_max_rec_posts", 10
                    ),
                    "gm.components.update.params.update_every_n_steps": st.session_state.get(
                        "engine_recsys_update_every_n_steps", 1
                    ),
                    "gm.components.update.params.lazy": st.session_state.get(
                        "engine_recsys_lazy", True
                    ),
                }
            )
            env_data_to_save["gm.components.observe.params.timeline_config"] = {
                "recsys_ratio": st.session_state.get("timeline_recsys_ratio", 0.6),
                "follower_ratio": 1.0 - st.session_state.get("timeline_recsys_ratio", 0.6),
            }
            if st.session_state.get("gm_orchestration_yaml_parsed"):
                env_data_to_save["gm_orchestration"] = st.session_state.get(
                    "gm_orchestration_yaml_parsed"
                )

            selected_backend = st.session_state.get("backend_type", "twitter_like")
            save_path = _save_scenario(
                name,
                scenario_data,
                sim_data_to_save,
                env_data_to_save,
                selected_backend,
                loaded_scenarios_root,
                eval_params,
            )
            st.success(f"Saved: `{save_path}`")
            st.info(f"Scenario config files created in `scenarios/{name}/conf/`")
            st.rerun()

    with btn2:
        if st.button("Export as YAML", key="export_yaml", use_container_width=True):
            yaml_str = yaml.dump(scenario_data, default_flow_style=False, sort_keys=False)
            st.download_button(
                "Download", data=yaml_str, file_name="world_config.yaml", mime="text/yaml"
            )

    with btn3:
        run_clicked = st.button(
            "\U0001f680  Run Simulation", key="run_sim", type="primary", use_container_width=True
        )

    # Validation warnings.
    _validate_config(sim_params, classes)

    # Run simulation.
    if run_clicked:
        # Auto-save before running.
        name = st.session_state.get("scenario_name_edit", scenario_display)

        # Build sim_data with overrides (same comprehensive list as "Save Scenario" button)
        sim_data_to_save = {}
        env_data_to_save = {}
        for key in [
            # Core sim params
            "num_agents",
            "num_steps",
            "llm_name",
            "llm_provider",
            "run_name",
            "seed",
            "action_mode",
            "llm.disabled",
            # Multi-flow/orchestration flags
            "engine.step.built_in",
            # Engine turn policy
            "engine.turn_policy.built_in",
        ]:
            session_key = key.replace(".", "_")
            val = st.session_state.get(session_key, None)
            if val is not None:
                sim_data_to_save[key] = val
        sim_data_to_save["engine.step.built_in"] = (
            "flow" if st.session_state.get("enable_engine_multi_flow", False) else "base"
        )
        sim_data_to_save["initialization.agents.built_in"] = st.session_state.get(
            "runtime_initializer_built_in", "raw_memory"
        )
        sim_data_to_save["initialization.game_masters.built_in"] = "default"
        seed_type = st.session_state.get("seed_posts_type", "none")
        sim_data_to_save["initialization.simulation.built_in"] = (
            "none" if seed_type == "none" else "seed_posts"
        )
        sim_data_to_save["initialization.simulation.params.type"] = seed_type
        if st.session_state.get("seed_posts_file"):
            sim_data_to_save["initialization.simulation.params.params.file_path"] = (
                st.session_state.get("seed_posts_file")
            )

        env_data_to_save.update(
            {
                "gm.components.observe.params.timeline_posts": st.session_state.get(
                    "timeline_posts", 10
                ),
                "gm.components.observe.params.timeline_mode": st.session_state.get(
                    "timeline_strategy", "follower_chronological"
                ),
                "observation_history": st.session_state.get("observation_history", 100),
                "gm.backend.enabled_actions": (
                    st.session_state.get("enabled_actions")
                    if st.session_state.get("enabled_actions")
                    else None
                ),
                "gm.backend.excluded_actions": (
                    st.session_state.get("excluded_actions")
                    if st.session_state.get("excluded_actions")
                    else None
                ),
                "gm.components.initialize.built_in": st.session_state.get(
                    "gm_initializer_built_in", "social_media"
                ),
                "gm.components.initialize.class_path": (
                    st.session_state.get("gm_initializer_class_path") or None
                ),
                "gm.components.next_acting.built_in": st.session_state.get(
                    "gm_next_acting_built_in", "activity_markov"
                ),
                "gm.components.observe.built_in": st.session_state.get(
                    "gm_observe_built_in", "timeline_every_turn"
                ),
                "gm.components.resolve.built_in": st.session_state.get(
                    "gm_resolve_built_in", "parsed_action"
                ),
                "gm.components.update.built_in": (
                    "social_recommendation"
                    if st.session_state.get("engine_recsys_enabled", False)
                    else "disabled"
                ),
                "gm.components.update.params.default_recsys_type": st.session_state.get(
                    "engine_recsys_type", "reddit"
                ),
                "gm.components.update.params.max_posts": st.session_state.get(
                    "engine_recsys_max_rec_posts", 10
                ),
                "gm.components.update.params.update_every_n_steps": st.session_state.get(
                    "engine_recsys_update_every_n_steps", 1
                ),
                "gm.components.update.params.lazy": st.session_state.get(
                    "engine_recsys_lazy", True
                ),
            }
        )
        env_data_to_save["gm.components.observe.params.timeline_config"] = {
            "recsys_ratio": st.session_state.get("timeline_recsys_ratio", 0.6),
            "follower_ratio": 1.0 - st.session_state.get("timeline_recsys_ratio", 0.6),
        }
        if st.session_state.get("gm_orchestration_yaml_parsed"):
            env_data_to_save["gm_orchestration"] = st.session_state.get(
                "gm_orchestration_yaml_parsed"
            )

        selected_backend = st.session_state.get("backend_type", "twitter_like")
        _save_scenario(
            name,
            scenario_data,
            sim_data_to_save,
            env_data_to_save,
            selected_backend,
            loaded_scenarios_root,
            eval_params,
        )

        with status_placeholder.container():
            st.info("Launching simulation...")

        runner_path = _PACKAGE_ROOT / "runtime" / "runner.py"
        full_cmd = [sys.executable, str(runner_path)]
        if config_path:
            full_cmd.extend(["--config-path", config_path])
        full_cmd.extend(overrides)

        st.markdown("---")
        st.markdown("**Live output**")
        output_area = st.empty()
        log_lines: list[str] = []

        try:
            process = subprocess.Popen(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(_PROJECT_ROOT),
            )
            with status_placeholder.container():
                st.warning("Running...")

            if process.stdout:
                for line in iter(process.stdout.readline, ""):
                    log_lines.append(line)
                    output_area.code("".join(log_lines[-100:]), language="text")

            process.wait()
            if process.returncode == 0:
                with status_placeholder.container():
                    st.success("Completed successfully!")
            else:
                with status_placeholder.container():
                    st.error(f"Exited with code {process.returncode}")
        except Exception as e:
            with status_placeholder.container():
                st.error(f"Failed to launch: {e}")

        output_area.code("".join(log_lines[-200:]), language="text")
