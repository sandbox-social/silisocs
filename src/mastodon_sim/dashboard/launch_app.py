"""Streamlit dashboard for configuring and launching mastodon-sim simulations.

Run with:
    streamlit run src/mastodon_sim/dashboard/launch_app.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import streamlit as st
import yaml

# ---------------------------------------------------------------------------
# Constants & paths
# ---------------------------------------------------------------------------
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_CONF_DIR = _PACKAGE_ROOT / "conf"
_SCENARIOS_DIR = _PACKAGE_ROOT.parents[2] / "scenarios"
_PROJECT_ROOT = _PACKAGE_ROOT.parents[2]

_PLATFORM_OPTIONS = ["twitter_like", "reddit_like", "mastodon"]
_MEMORY_BACKENDS = ["list", "associative"]
_ACTION_MODES = ["custom", "generic", "tool_calling"]
_NETWORK_TYPES = ["barabasi_albert", "random", "lfr_benchmark"]
_PROCESSING_MODES = ["raw", "formative"]
_PERSONA_SOURCES = ["hf_dataset", "local_json", "inline", "config_path"]

# ---------------------------------------------------------------------------
# Theme & page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Mastodon-Sim Launcher",
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
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _discover_scenarios() -> dict[str, Path]:
    """Discover scenario YAML files from package conf and top-level scenarios/."""
    found: dict[str, Path] = {}
    # Package-bundled scenario configs.
    pkg_dir = _CONF_DIR / "scenario"
    if pkg_dir.is_dir():
        for f in sorted(pkg_dir.glob("*.yaml")):
            found[f.stem] = f
    # External scenarios/ directory.
    if _SCENARIOS_DIR.is_dir():
        for d in sorted(_SCENARIOS_DIR.iterdir()):
            if not d.is_dir():
                continue
            # Check scenarios/<name>/conf/scenario/<name>.yaml (Hydra-compatible)
            hydra_path = d / "conf" / "scenario"
            if hydra_path.is_dir():
                for f in sorted(hydra_path.glob("*.yaml")):
                    key = f"{d.name}" if f.stem == d.name else f"{d.name}/{f.stem}"
                    found[key] = f
            # Also check scenarios/<name>/conf/<name>.yaml (flat layout)
            flat_conf = d / "conf"
            if flat_conf.is_dir():
                for f in sorted(flat_conf.glob("*.yaml")):
                    if f.stem == d.name and d.name not in found:
                        found[d.name] = f
    return found


def _discover_entity_modules() -> list[str]:
    """Discover available entity prefab modules by scanning the package."""
    modules: list[str] = []
    # Main entity module.
    entity_file = _PACKAGE_ROOT / "agents" / "entity.py"
    if entity_file.exists():
        modules.append("mastodon_sim.agents.entity")
    # Scenario-specific entity_lib/ directories.
    for scenario_dir in sorted((_PACKAGE_ROOT / "scenarios").glob("*/entity_lib")):
        for py_file in sorted(scenario_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            rel = py_file.relative_to(_PACKAGE_ROOT)
            mod_path = "mastodon_sim." + str(rel.with_suffix("")).replace("/", ".")
            modules.append(mod_path)
    return modules


def _save_scenario(name: str, data: dict) -> Path:
    """Save scenario config YAML to scenarios/<name>/conf/scenario/<name>.yaml."""
    # Ensure proper Hydra-compatible directory structure.
    target_dir = _SCENARIOS_DIR / name / "conf" / "scenario"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{name}.yaml"
    # Add @package header for Hydra.
    header = "# @package scenario\n\n"
    yaml_content = yaml.dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    target_file.write_text(header + yaml_content)
    return target_file


def _get_config_path_for_scenario(scenario_key: str) -> str | None:
    """Return the --config-path dir for an external scenario, or None for package-bundled."""
    candidate = _SCENARIOS_DIR / scenario_key / "conf"
    if candidate.is_dir():
        return str(candidate)
    # Handle compound keys like "election/variant"
    parts = scenario_key.split("/")
    if len(parts) > 1:
        candidate = _SCENARIOS_DIR / parts[0] / "conf"
        if candidate.is_dir():
            return str(candidate)
    return None


def _build_scenario_config() -> dict:
    """Build a complete scenario config dict from session state."""
    scenario_name = st.session_state.get(
        "scenario_name_edit", st.session_state.get("_loaded_scenario_name", "default")
    )
    _sc_cfg = st.session_state.get("_loaded_scenario", {})

    # Build classes config from session state.
    classes_dict: dict[str, dict] = {}
    for cls in st.session_state.get("_agent_classes", []):
        cls_cfg: dict = {}
        if cls.get("count"):
            cls_cfg["count"] = cls["count"]
        if cls.get("prefab_module"):
            cls_cfg["prefab_module"] = cls["prefab_module"]
        if cls.get("sim_role_name"):
            cls_cfg["sim_role_name"] = cls["sim_role_name"]
        if cls.get("data"):
            cls_cfg["data"] = cls["data"]
        if cls.get("field_map"):
            cls_cfg["field_map"] = cls["field_map"]
        if cls.get("model"):
            cls_cfg["model"] = cls["model"]
        classes_dict[cls.get("name", f"class_{len(classes_dict)}")] = cls_cfg

    # Parse shared memories.
    shared_text = st.session_state.get("shared_memories_edit", "")
    shared_list = [line.strip() for line in shared_text.splitlines() if line.strip()]

    # Assemble scenario data.
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
        "jobname_format": "N${sim.num_agents}_T${sim.num_steps}_${experiment_name}_${sim.run_name}",
        "setting": {
            "name": st.session_state.get("setting_name", ""),
            "background": bg_list,
        },
        "event": {
            "name": st.session_state.get("event_name", ""),
            "context": st.session_state.get("event_context", ""),
        },
        "persona_pipeline": {
            "processing_mode": st.session_state.get("processing_mode", "raw"),
            "defaults": {
                "params": {
                    "seed_post": "",
                    "bio": "",
                    "style": "",
                    "goal": None,
                    "scenario_context": "${scenario.event.context}",
                },
                "shared_memories": shared_list,
            },
            "classes": classes_dict,
        },
        "social_network": {
            "activity_transition_rates": activity_rates,
            "network_type": st.session_state.get("network_type", "barabasi_albert"),
            "barabasi_albert_m": st.session_state.get("ba_m", 10),
            "base_followership_probability": st.session_state.get("follow_prob", 0.3),
        },
        "shared_memories": shared_list,
        "initial_observations": [
            '"{name} is at home checking their social media feed."',
            '"{name} decides to browse and maybe post something."',
        ],
    }

    # Copy probes from loaded scenario if present.
    if _sc_cfg.get("probes"):
        config["probes"] = _sc_cfg["probes"]

    # Copy any extra sections from loaded config (candidates, news_account, etc.)
    for key in ("candidates", "news_account", "data", "partisan_types"):
        if key in _sc_cfg:
            config[key] = _sc_cfg[key]

    return config


def _build_hydra_overrides(sim: dict, platform: str, scenario: dict) -> list[str]:
    overrides: list[str] = []
    for key, val in sim.items():
        if val is None:
            overrides.append(f"sim.{key}=null")
        elif isinstance(val, bool):
            overrides.append(f"sim.{key}={'true' if val else 'false'}")
        elif isinstance(val, str) and " " in val:
            overrides.append(f'sim.{key}="{val}"')
        else:
            overrides.append(f"sim.{key}={val}")
    overrides.append(f"social_media={platform}")
    for key, val in scenario.items():
        if val is None:
            continue
        if isinstance(val, bool):
            overrides.append(f"scenario.{key}={'true' if val else 'false'}")
        elif isinstance(val, (int, float)):
            overrides.append(f"scenario.{key}={val}")
        elif isinstance(val, str) and val:
            overrides.append(f'scenario.{key}="{val}"' if " " in val else f"scenario.{key}={val}")
    return overrides


def _validate_config(sim_params: dict, classes: list[dict]) -> None:
    """Show validation warnings in the Launch tab."""
    warnings = []
    if not classes:
        warnings.append("No agent classes defined. Add at least one in the Agent Classes tab.")
    for cls in classes:
        if not cls.get("prefab_module"):
            warnings.append(f"Class '{cls.get('name', '?')}' has no entity module set.")
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
    st.title("\U0001f30d Mastodon-Sim")
    st.caption("Social Simulation Sandbox")
    st.divider()

    st.markdown("**Scenario**")
    available_scenarios = _discover_scenarios()
    scenario_names = list(available_scenarios.keys()) or ["default"]

    selected_scenario = st.selectbox(
        "Load scenario",
        scenario_names,
        index=0,
        key="sidebar_scenario_select",
        help="Select a scenario to load.",
    )

    if selected_scenario in available_scenarios:
        loaded_scenario = _load_yaml(available_scenarios[selected_scenario])
        st.session_state["_loaded_scenario"] = loaded_scenario
        st.session_state["_loaded_scenario_name"] = selected_scenario
    else:
        loaded_scenario = {}

    st.divider()

    # New scenario creation.
    st.markdown("**Create New Scenario**")
    new_name = st.text_input(
        "Scenario name", key="new_scenario_name_input", placeholder="my_scenario"
    )
    if st.button("Create", key="create_new_scenario", use_container_width=True):
        if new_name and new_name.strip():
            clean_name = new_name.strip().lower().replace(" ", "_")
            # Create from default.
            default_cfg = _load_yaml(_CONF_DIR / "scenario" / "default.yaml")
            default_cfg["scenario_name"] = clean_name
            save_path = _save_scenario(clean_name, default_cfg)
            st.success(f"Created: `{save_path}`")
            st.rerun()
        else:
            st.error("Enter a scenario name.")

    st.divider()
    st.markdown("**Quick Links**")
    st.markdown("- [Documentation](docs/index.md)")
    st.divider()
    status_placeholder = st.empty()

# ---------------------------------------------------------------------------
# Load defaults
# ---------------------------------------------------------------------------
_scenario_cfg = st.session_state.get("_loaded_scenario", {})
_sim_defaults = _load_yaml(_CONF_DIR / "sim" / "base.yaml")
_entity_modules = _discover_entity_modules()

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
st.title("Simulation Configuration")
scenario_display = st.session_state.get("_loaded_scenario_name", "default")
st.markdown(f"Editing scenario: **{scenario_display}**")

tab_sim, tab_scenario, tab_classes, tab_network, tab_probes, tab_launch = st.tabs(
    ["Simulation", "Scenario", "Agent Classes", "Network", "Probes", "Launch"],
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
            value=int(_sim_defaults.get("num_agents", 20)),
            step=10,
            key="num_agents",
        )
        st.number_input(
            "Number of episodes (steps)",
            min_value=1,
            max_value=500,
            value=int(_sim_defaults.get("num_steps", 50)),
            key="num_steps",
        )
        st.number_input(
            "Random seed", min_value=0, value=int(_sim_defaults.get("seed", 1)), key="seed"
        )
        st.text_input("Run name", value=str(_sim_defaults.get("run_name", "run1")), key="run_name")

    with col2:
        st.text_input(
            "Default LLM model",
            value=str(_sim_defaults.get("llm_name", "qwen3.5-4b")),
            key="llm_name",
            help="Default model. Per-class overrides in Agent Classes tab.",
        )
        st.text_input(
            "LLM API base URL",
            value=str(_sim_defaults.get("llm_api_base") or ""),
            key="llm_api_base",
            help="Leave blank for auto-detection.",
        )
        st.text_input(
            "LLM API key",
            value=str(_sim_defaults.get("llm_api_key") or ""),
            type="password",
            key="llm_api_key",
            help="Uses OPENAI_API_KEY env var if blank.",
        )
        st.number_input(
            "Max concurrent actions",
            min_value=1,
            max_value=10_000,
            value=int(_sim_defaults.get("max_concurrent_actions", 1000)),
            key="max_concurrent_actions",
        )

    with st.expander("Advanced settings", expanded=False):
        ac1, ac2 = st.columns(2)
        with ac1:
            st.selectbox(
                "Platform backend",
                _PLATFORM_OPTIONS,
                index=0,
                key="platform_type",
                help="twitter_like/reddit_like are local. mastodon requires a server.",
            )
            st.selectbox(
                "Memory backend",
                _MEMORY_BACKENDS,
                index=_MEMORY_BACKENDS.index(_sim_defaults.get("memory_backend", "list")),
                key="memory_backend",
            )
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
                value=int(_sim_defaults.get("timeline_posts", 10)),
                key="timeline_posts",
            )
            st.number_input(
                "Observation history",
                min_value=10,
                max_value=1000,
                value=int(_sim_defaults.get("observation_history", 100)),
                key="observation_history",
            )
            st.checkbox(
                "Disable language model (dry run)",
                value=bool(_sim_defaults.get("disable_language_model", False)),
                key="disable_language_model",
            )


# ---------------------------------------------------------------------------
# TAB: Scenario
# ---------------------------------------------------------------------------
with tab_scenario:
    st.subheader("Scenario Configuration")

    col1, col2 = st.columns(2)
    with col1:
        st.text_input(
            "Scenario name",
            value=_scenario_cfg.get("scenario_name", scenario_display),
            key="scenario_name_edit",
            help="Used for output directory and config resolution.",
        )
        setting = _scenario_cfg.get("setting", {})
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
        event = _scenario_cfg.get("event", {})
        st.text_input("Event name", value=event.get("name", ""), key="event_name")
        st.text_area(
            "Event context",
            value=event.get("context", ""),
            key="event_context",
            height=150,
            help="Main scenario context injected into agent memories.",
        )

    # Processing mode.
    pipeline = _scenario_cfg.get("persona_pipeline", {})
    pm = pipeline.get("processing_mode", "raw")
    pm_idx = _PROCESSING_MODES.index(pm) if pm in _PROCESSING_MODES else 0
    st.selectbox(
        "Memory processing mode",
        _PROCESSING_MODES,
        index=pm_idx,
        key="processing_mode",
        help="'raw' = direct injection. 'formative' = LLM-generated backstories.",
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

    pipeline_cfg = _scenario_cfg.get("persona_pipeline", {})
    classes_cfg = pipeline_cfg.get("classes", {})

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
                    "prefab_module": "mastodon_sim.agents.entity",
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
                "prefab_module": "mastodon_sim.agents.entity",
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
                    value=int(cls.get("count", 10)),
                    key=f"cls_count_{i}",
                )
            with c2:
                # Entity module dropdown.
                current_mod = cls.get("prefab_module", "mastodon_sim.agents.entity")
                mod_options = list(_entity_modules)
                if current_mod not in mod_options:
                    mod_options.append(current_mod)
                mod_idx = mod_options.index(current_mod) if current_mod in mod_options else 0
                cls["prefab_module"] = st.selectbox(
                    "Entity module",
                    mod_options,
                    index=mod_idx,
                    key=f"cls_mod_{i}",
                    help="Python module containing the Entity prefab class.",
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
                        scenario_name = _scenario_cfg.get("scenario_name", scenario_display)
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

    # Process removals.
    if classes_to_remove:
        for idx in reversed(classes_to_remove):
            st.session_state["_agent_classes"].pop(idx)
        st.rerun()


# ---------------------------------------------------------------------------
# TAB: Network
# ---------------------------------------------------------------------------
with tab_network:
    st.subheader("Social Network Configuration")
    net_cfg = _scenario_cfg.get("social_network", {})

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
            value=int(net_cfg.get("barabasi_albert_m", 10)),
            key="ba_m",
        )
        st.slider(
            "Base follow probability",
            0.0,
            1.0,
            float(net_cfg.get("base_followership_probability", 0.3)),
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
                    float(rates.get("inactive_to_active", 0.3)),
                    0.05,
                    key=f"act_{role}_i2a",
                )
                st.slider(
                    f"{role}: active -> inactive",
                    0.0,
                    1.0,
                    float(rates.get("active_to_inactive", 0.3)),
                    0.05,
                    key=f"act_{role}_a2i",
                )


# ---------------------------------------------------------------------------
# TAB: Probes
# ---------------------------------------------------------------------------
with tab_probes:
    st.subheader("Probe Configuration")
    probes_cfg = _scenario_cfg.get("probes", {})
    deploy_cfg = probes_cfg.get("deployment", {}) if isinstance(probes_cfg, dict) else {}

    pc1, pc2 = st.columns(2)
    with pc1:
        st.checkbox(
            "Enable probes", value=bool(deploy_cfg.get("enabled", True)), key="probes_enabled"
        )
        st.number_input(
            "Start at episode",
            min_value=0,
            value=int(deploy_cfg.get("start_step", 1)),
            key="probe_start",
        )
        st.number_input(
            "Deploy every N episodes",
            min_value=1,
            value=int(deploy_cfg.get("every_n_steps", 1)),
            key="probe_interval",
        )
    with pc2:
        st.markdown("**Active probe queries**")
        queries = probes_cfg.get("queries", {}) if isinstance(probes_cfg, dict) else {}
        if isinstance(queries, dict) and queries:
            for qid, qcfg in queries.items():
                if not isinstance(qcfg, dict):
                    continue
                qtype = qcfg.get("query_type", "Unknown")
                st.markdown(f"**Q{qid}**: `{qtype}`")
        else:
            st.info("No probes configured for this scenario.")


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
        "llm_name": st.session_state.get("llm_name", "qwen3.5-4b"),
        "llm_api_base": st.session_state.get("llm_api_base") or None,
        "llm_api_key": st.session_state.get("llm_api_key") or None,
        "max_concurrent_actions": st.session_state.get("max_concurrent_actions", 1000),
        "memory_backend": st.session_state.get("memory_backend", "list"),
        "action_mode": st.session_state.get("action_mode", "custom"),
        "timeline_posts": st.session_state.get("timeline_posts", 10),
        "observation_history": st.session_state.get("observation_history", 100),
        "disable_language_model": st.session_state.get("disable_language_model", False),
    }
    selected_platform = st.session_state.get("platform_type", "twitter_like")

    # Build scenario config for saving.
    scenario_data = _build_scenario_config()

    # Summary.
    st.markdown("**Configuration summary**")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.metric("Agents", sim_params["num_agents"])
        st.metric("Episodes", sim_params["num_steps"])
    with sc2:
        st.metric("Default LLM", sim_params["llm_name"])
        st.metric("Platform", selected_platform)
    with sc3:
        n_classes = len(st.session_state.get("_agent_classes", []))
        st.metric("Agent Classes", n_classes)
        st.metric("Memory", sim_params["memory_backend"])

    # Per-class model summary.
    classes = st.session_state.get("_agent_classes", [])
    class_models = {c["name"]: c.get("model", "") for c in classes if c.get("model")}
    if class_models:
        st.markdown("**Per-class LLM models**")
        for cls_name, model_name in class_models.items():
            st.markdown(f"- `{cls_name}`: {model_name}")

    # Build Hydra CLI.
    overrides = _build_hydra_overrides(
        sim_params,
        selected_platform,
        {
            "social_network.network_type": st.session_state.get("network_type", "barabasi_albert"),
            "social_network.barabasi_albert_m": st.session_state.get("ba_m", 10),
            "social_network.base_followership_probability": st.session_state.get(
                "follow_prob", 0.3
            ),
            "persona_pipeline.processing_mode": st.session_state.get("processing_mode", "raw"),
        },
    )

    # Determine config path for external scenarios.
    config_path = _get_config_path_for_scenario(scenario_display)

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
            save_path = _save_scenario(name, scenario_data)
            st.success(f"Saved: `{save_path}`")
            st.rerun()

    with btn2:
        if st.button("Export as YAML", key="export_yaml", use_container_width=True):
            yaml_str = yaml.dump(scenario_data, default_flow_style=False, sort_keys=False)
            st.download_button(
                "Download", data=yaml_str, file_name="scenario_config.yaml", mime="text/yaml"
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
        _save_scenario(name, scenario_data)

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
