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

_PLATFORM_OPTIONS = ["twitter_like", "reddit_like", "mastodon"]
_MEMORY_BACKENDS = ["list", "associative"]
_ACTION_MODES = ["custom", "generic", "tool_calling"]
_NETWORK_TYPES = ["barabasi_albert", "random", "lfr_benchmark"]
_PROCESSING_MODES = ["raw", "llm_formative"]
_PERSONA_SOURCES = ["hf_dataset", "local_json", "config_path"]

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
    """Discover scenario YAML files from both package conf and top-level scenarios dir."""
    found: dict[str, Path] = {}
    # Package-bundled scenarios under conf/scenario/
    pkg_scenario_dir = _CONF_DIR / "scenario"
    if pkg_scenario_dir.is_dir():
        for f in sorted(pkg_scenario_dir.glob("*.yaml")):
            found[f.stem] = f
    # Top-level scenarios/ directory
    if _SCENARIOS_DIR.is_dir():
        for d in sorted(_SCENARIOS_DIR.iterdir()):
            conf_dir = d / "conf"
            if conf_dir.is_dir():
                for f in sorted(conf_dir.glob("*.yaml")):
                    key = f"{d.name}/{f.stem}" if f.stem != d.name else d.name
                    found[key] = f
            # Also check direct yaml files
            for f in sorted(d.glob("*.yaml")):
                key = f"{d.name}/{f.stem}" if f.stem != d.name else d.name
                found.setdefault(key, f)
    return found


def _save_scenario(name: str, data: dict, base_scenario: str | None = None) -> Path:
    """Save scenario config YAML to the scenarios directory."""
    target_dir = _SCENARIOS_DIR / name / "conf"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{name}.yaml"
    if base_scenario:
        data["_based_on"] = base_scenario
    with open(target_file, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return target_file


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


# ===================================================================
# SIDEBAR — scenario management
# ===================================================================
with st.sidebar:
    st.title("\U0001f30d Mastodon-Sim")
    st.caption("Social Simulation Sandbox")
    st.divider()

    # Scenario selector
    st.markdown("**Scenario**")
    available_scenarios = _discover_scenarios()
    scenario_names = list(available_scenarios.keys())
    if not scenario_names:
        scenario_names = ["election"]

    selected_scenario = st.selectbox(
        "Load scenario",
        scenario_names,
        index=0,
        key="sidebar_scenario_select",
        help="Select an existing scenario to load its config.",
    )

    if selected_scenario in available_scenarios:
        loaded_scenario = _load_yaml(available_scenarios[selected_scenario])
        st.session_state["_loaded_scenario"] = loaded_scenario
        st.session_state["_loaded_scenario_name"] = selected_scenario
    else:
        loaded_scenario = {}

    st.divider()
    st.markdown("**Quick Links**")
    st.markdown("- [Documentation](https://github.com/social-sandbox/mastodon-sim)")
    st.markdown("- [Concordia](https://github.com/google-deepmind/concordia)")
    st.divider()
    status_placeholder = st.empty()


# ===================================================================
# Get defaults from loaded scenario or bundled configs
# ===================================================================
_scenario_cfg = st.session_state.get("_loaded_scenario", {})
_sim_defaults = _load_yaml(_CONF_DIR / "sim" / "base.yaml")


# ===================================================================
# MAIN
# ===================================================================
st.title("Simulation Configuration")
st.markdown(
    f"Editing scenario: **{st.session_state.get('_loaded_scenario_name', 'election')}** "
    "&mdash; configure and launch from this dashboard."
)

tab_sim, tab_platform, tab_scenario, tab_network, tab_probes, tab_launch = st.tabs(
    ["Simulation", "Platform", "Scenario", "Network", "Probes", "Launch"],
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
            help="Total number of agents (voters + candidates + special roles).",
        )
        st.number_input(
            "Number of episodes (steps)",
            min_value=1,
            max_value=500,
            value=int(_sim_defaults.get("num_steps", 50)),
            key="num_steps",
            help="Each episode gives every active agent one turn to act.",
        )
        st.number_input(
            "Random seed", min_value=0, value=int(_sim_defaults.get("seed", 1)), key="seed"
        )
        st.text_input("Run name", value=str(_sim_defaults.get("run_name", "run1")), key="run_name")

    with col2:
        st.text_input(
            "LLM model name",
            value=str(_sim_defaults.get("llm_name", "qwen3-4b")),
            key="llm_name",
            help="OpenAI-compatible model name.",
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
            help="Hard cap for parallel LLM calls. Adaptive controller starts at min(this, 256).",
        )

    with st.expander("Advanced settings", expanded=False):
        ac1, ac2 = st.columns(2)
        with ac1:
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
            st.text_input(
                "Sentence encoder",
                value=str(
                    _sim_defaults.get("sentence_encoder", "sentence-transformers/all-MiniLM-L6-v2")
                ),
                key="sentence_encoder",
            )
        with ac2:
            st.number_input(
                "Timeline posts shown",
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
            st.checkbox(
                "Write HTML log",
                value=bool(_sim_defaults.get("write_html_log", True)),
                key="write_html_log",
            )


# ---------------------------------------------------------------------------
# TAB: Platform
# ---------------------------------------------------------------------------
with tab_platform:
    st.subheader("Social Media Platform")
    platform_type = st.selectbox(
        "Platform backend",
        _PLATFORM_OPTIONS,
        index=0,
        key="platform_type",
        help="twitter_like/reddit_like are local. mastodon needs a server.",
    )
    if platform_type == "mastodon":
        st.info("Mastodon requires a running server and .env credentials.")
    else:
        st.success(f"**{platform_type}** uses a local SQLite database.")

    with st.expander("Platform instructions", expanded=False):
        plat_defaults = _load_yaml(_CONF_DIR / "social_media" / f"{platform_type}.yaml")
        st.code(plat_defaults.get("usage_instructions", "N/A"), language=None)


# ---------------------------------------------------------------------------
# TAB: Scenario
# ---------------------------------------------------------------------------
with tab_scenario:
    st.subheader("Scenario Configuration")
    st.caption(f"Currently loaded: **{st.session_state.get('_loaded_scenario_name', 'election')}**")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Setting**")
        setting = _scenario_cfg.get("setting", {})
        st.text_input("Town name", value=setting.get("name", "Storhampton"), key="setting_name")
        st.text_area(
            "Event context",
            value=_scenario_cfg.get("event", {}).get("context", ""),
            key="event_context",
            height=100,
        )

    with col2:
        st.markdown("**Candidates**")
        candidates = _scenario_cfg.get("candidates", {})
        for cand_key, cand_data in candidates.items():
            if not isinstance(cand_data, dict):
                continue
            with st.expander(cand_data.get("name", cand_key), expanded=False):
                st.text_input(
                    "Name", cand_data.get("name", ""), key=f"cand_name_{cand_key}", disabled=True
                )
                st.text_area(
                    "Persona",
                    cand_data.get("persona", ""),
                    key=f"cand_persona_{cand_key}",
                    height=100,
                )
                st.text_input("Goal", cand_data.get("goal", ""), key=f"cand_goal_{cand_key}")

    st.divider()
    st.markdown("**Persona Pipeline**")
    pipeline_cfg = _scenario_cfg.get("persona_pipeline", {})
    pc1, pc2 = st.columns(2)
    with pc1:
        pm_default = pipeline_cfg.get("processing_mode", "raw")
        pm_idx = _PROCESSING_MODES.index(pm_default) if pm_default in _PROCESSING_MODES else 0
        st.selectbox(
            "Processing mode",
            _PROCESSING_MODES,
            index=pm_idx,
            key="processing_mode",
            help="'raw' = direct persona text. 'llm_formative' = LLM-generated memories.",
        )
    with pc2:
        voter_cls = pipeline_cfg.get("classes", {}).get("voter", {})
        voter_data = voter_cls.get("data", {}) if isinstance(voter_cls, dict) else {}
        ps_default = voter_data.get("source", "hf_dataset")
        ps_idx = _PERSONA_SOURCES.index(ps_default) if ps_default in _PERSONA_SOURCES else 0
        st.selectbox("Persona data source", _PERSONA_SOURCES, index=ps_idx, key="persona_source")

    persona_source = st.session_state.get("persona_source", "hf_dataset")
    if persona_source == "hf_dataset":
        dc1, dc2 = st.columns(2)
        with dc1:
            st.text_input(
                "HuggingFace dataset",
                value=voter_data.get("dataset", "nvidia/Nemotron-Personas-USA"),
                key="hf_dataset",
            )
        with dc2:
            st.text_input("Dataset split", value=voter_data.get("split", "train"), key="hf_split")
    elif persona_source == "local_json":
        st.text_input(
            "Local JSON path",
            key="persona_json_path",
            value="input/personas/reddit_agents.json",
            help="Relative to the scenario directory.",
        )

    with st.expander("News Account", expanded=False):
        news_cfg = _scenario_cfg.get("news_account", {})
        if isinstance(news_cfg, dict):
            st.text_input("Account name", news_cfg.get("name", ""), key="news_name")
            st.text_input("Username", news_cfg.get("username", ""), key="news_username")
            st.text_input("Bio", news_cfg.get("bio", ""), key="news_bio")

    with st.expander("Data Files", expanded=False):
        data_cfg = _scenario_cfg.get("data", {})
        if isinstance(data_cfg, dict):
            st.text_input(
                "News file",
                value=data_cfg.get("news_file", "v1_news_bill_bias"),
                key="news_file",
                help="Name (without .json) from input/news_data/.",
            )
            st.selectbox(
                "News agent mode",
                ["with_images", "without_images", "none"],
                index=0,
                key="use_news_agent",
            )


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
            value=int(net_cfg.get("barabasi_albert_m", 30)),
            key="ba_m",
        )
        st.slider(
            "Base follow probability",
            0.0,
            1.0,
            float(net_cfg.get("base_followership_probability", 0.4)),
            0.05,
            key="follow_prob",
        )

    with nc2:
        st.markdown("**Activity transition rates**")
        st.caption("Two-state Markov process per episode.")
        activity = net_cfg.get("activity_transition_rates", {})
        for role, rates in activity.items():
            if not isinstance(rates, dict):
                continue
            with st.expander(role, expanded=(role == "voter")):
                st.slider(
                    f"{role}: inactive -> active",
                    0.0,
                    1.0,
                    float(rates.get("inactive_to_active", 0.1)),
                    0.05,
                    key=f"act_{role}_i2a",
                )
                st.slider(
                    f"{role}: active -> inactive",
                    0.0,
                    1.0,
                    float(rates.get("active_to_inactive", 0.1)),
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
        if isinstance(queries, dict):
            for qid, qcfg in queries.items():
                if not isinstance(qcfg, dict):
                    continue
                qtype = qcfg.get("query_type", "Unknown")
                qdata = qcfg.get("query_data", {})
                premise = (
                    qdata.get("interaction_premise_template", {}) if isinstance(qdata, dict) else {}
                )
                labels = (
                    [str(v) for v in premise.values() if v is not None]
                    if isinstance(premise, dict)
                    else []
                )
                st.markdown(f"**Q{qid}**: `{qtype}` — {', '.join(labels) or 'default'}")


# ---------------------------------------------------------------------------
# TAB: Launch
# ---------------------------------------------------------------------------
with tab_launch:
    st.subheader("Review & Launch")

    sim_params = {
        "num_agents": st.session_state.get("num_agents", 20),
        "num_steps": st.session_state.get("num_steps", 50),
        "seed": st.session_state.get("seed", 1),
        "run_name": st.session_state.get("run_name", "run1"),
        "llm_name": st.session_state.get("llm_name", "qwen3-4b"),
        "llm_api_base": st.session_state.get("llm_api_base") or None,
        "llm_api_key": st.session_state.get("llm_api_key") or None,
        "max_concurrent_actions": st.session_state.get("max_concurrent_actions", 1000),
        "memory_backend": st.session_state.get("memory_backend", "list"),
        "action_mode": st.session_state.get("action_mode", "custom"),
        "sentence_encoder": st.session_state.get(
            "sentence_encoder", "sentence-transformers/all-MiniLM-L6-v2"
        ),
        "timeline_posts": st.session_state.get("timeline_posts", 10),
        "observation_history": st.session_state.get("observation_history", 100),
        "disable_language_model": st.session_state.get("disable_language_model", False),
        "write_html_log": st.session_state.get("write_html_log", True),
    }
    scenario_overrides = {
        "data.news_file": st.session_state.get("news_file", "v1_news_bill_bias"),
        "data.use_news_agent": st.session_state.get("use_news_agent", "with_images"),
        "data.persona_type": st.session_state.get("persona_source", "hf_dataset"),
        "social_network.network_type": st.session_state.get("network_type", "barabasi_albert"),
        "social_network.barabasi_albert_m": st.session_state.get("ba_m", 30),
        "social_network.base_followership_probability": st.session_state.get("follow_prob", 0.4),
        "persona_pipeline.processing_mode": st.session_state.get("processing_mode", "raw"),
    }

    selected_platform = st.session_state.get("platform_type", "twitter_like")
    overrides = _build_hydra_overrides(sim_params, selected_platform, scenario_overrides)

    # Summary
    st.markdown("**Configuration summary**")
    sc1, sc2, sc3 = st.columns(3)
    with sc1:
        st.metric("Agents", sim_params["num_agents"])
        st.metric("Episodes", sim_params["num_steps"])
    with sc2:
        st.metric("LLM", sim_params["llm_name"])
        st.metric("Platform", selected_platform)
    with sc3:
        st.metric("Max Workers", sim_params["max_concurrent_actions"])
        st.metric("Memory", sim_params["memory_backend"])

    # Hydra command preview
    with st.expander("Hydra CLI command", expanded=False):
        runner_path = _PACKAGE_ROOT / "runtime" / "runner.py"
        cmd_str = f"python {runner_path} \\\n  " + " \\\n  ".join(overrides)
        st.code(cmd_str, language="bash")

    st.divider()

    # -----------------------------------------------------------------------
    # Scenario save/export
    # -----------------------------------------------------------------------
    st.markdown("**Save Configuration**")
    save_col1, save_col2, save_col3 = st.columns(3)

    with save_col1:
        if st.button("Export as YAML", key="export_yaml", use_container_width=True):
            combined = {
                "sim": sim_params,
                "social_media": selected_platform,
                "scenario": scenario_overrides,
            }
            yaml_str = yaml.dump(combined, default_flow_style=False, sort_keys=False)
            st.download_button(
                "Download YAML", data=yaml_str, file_name="sim_config.yaml", mime="text/yaml"
            )

    with save_col2:
        save_clicked = st.button("Save Scenario", key="save_scenario", use_container_width=True)

    with save_col3:
        runner_path = _PACKAGE_ROOT / "runtime" / "runner.py"
        run_clicked = st.button(
            "\U0001f680  Run Simulation", key="run_sim", type="primary", use_container_width=True
        )

    # Save dialog
    if save_clicked:
        st.session_state["_show_save_dialog"] = True

    if st.session_state.get("_show_save_dialog", False):
        st.markdown("---")
        st.markdown("#### Save Scenario")

        current_name = st.session_state.get("_loaded_scenario_name", "election")
        save_mode = st.radio(
            "How would you like to save?",
            [
                f"Overwrite current ({current_name})",
                f"Save as sub-scenario of {current_name}",
                "Save as new scenario",
            ],
            key="save_mode",
        )

        if "sub-scenario" in save_mode:
            sub_name = st.text_input(
                "Sub-scenario name (e.g. 'high_turnout')", key="sub_scenario_name"
            )
            final_name = f"{current_name}_{sub_name}" if sub_name else ""
            base_scenario = current_name
        elif "new scenario" in save_mode:
            final_name = st.text_input("New scenario name", key="new_scenario_name")
            base_scenario = None
        else:
            final_name = current_name
            base_scenario = None

        if st.button("Confirm Save", key="confirm_save"):
            if not final_name or not final_name.strip():
                st.error("Please provide a scenario name.")
            else:
                save_data = dict(_scenario_cfg)
                save_data.update(scenario_overrides)
                save_path = _save_scenario(final_name.strip(), save_data, base_scenario)
                st.success(f"Saved to: `{save_path}`")
                st.session_state["_show_save_dialog"] = False
                st.rerun()

    # Run simulation
    if run_clicked:
        with status_placeholder.container():
            st.markdown("### Status")
            st.info("Preparing to launch...")

        full_cmd = [sys.executable, str(runner_path)] + overrides
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
                cwd=str(_PACKAGE_ROOT.parents[2]),
            )
            with status_placeholder.container():
                st.markdown("### Status")
                st.warning("Running...")

            if process.stdout is None:
                raise RuntimeError("Subprocess started without a readable stdout pipe")

            for line in iter(process.stdout.readline, ""):
                log_lines.append(line)
                output_area.code("".join(log_lines[-100:]), language="text")

            process.wait()
            if process.returncode == 0:
                with status_placeholder.container():
                    st.markdown("### Status")
                    st.success("Completed successfully!")
            else:
                with status_placeholder.container():
                    st.markdown("### Status")
                    st.error(f"Exited with code {process.returncode}")
        except Exception as e:
            with status_placeholder.container():
                st.markdown("### Status")
                st.error(f"Failed to launch: {e}")

        output_area.code("".join(log_lines[-200:]), language="text")
