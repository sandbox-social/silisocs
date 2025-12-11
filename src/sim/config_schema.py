from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from omegaconf import MISSING

# Use TYPE_CHECKING to avoid runtime circular imports
if TYPE_CHECKING:
    from typing import Protocol

    class AgentsConfigProtocol(Protocol):
        directory: list[Any]
        initial_observations: list[str]
        inputs: Any

    class SocSysConfigProtocol(Protocol):
        call_to_action: str
        exp_name: str
        gamemaster_memories: list[str]
        setting_info: Any
        shared_agent_memories_template: list[str]
        sim_setting: str
        social_media_usage_instructions: str

    class ProbesConfigProtocol(Protocol):
        queries_data: dict[int, Any]
        query_lib_module: str


# ============================================================================
# Sim Configuration
# ============================================================================


@dataclass
class SimConfig:
    app_module: str = "mastodon_sim"
    load_path: str = ""
    model: str = "gpt-4o-mini"
    num_agents: int = 20
    num_episodes: int = 1
    run_name: str = "run1"
    seed: int = 1
    sentence_encoder: str = "sentence-transformers/all-mpnet-base-v2"
    social_media_gamemaster_filename: str = "social_media_game_master"

    output_rootname: str | None = None

    example_name: str = MISSING  # required

    roleplaying_instructions: str = (
        "<system>"
        "You are simulating {name}, a character in a social science experiment. "
        "Always use third-person limited perspective when describing {name}'s thoughts and actions. "
        "Your goal is to determine the single most appropriate action {name} would take next on a social media platform."
        "</system>"
    )
    persona_type: str = "Reddit.Big5"
    use_news_agent: str = "with_images"
    use_server: bool = False


# ============================================================================
# Main Config (no hydra block defined here)
# ============================================================================


@dataclass
class Config:
    sim: SimConfig
    agents: Any = MISSING
    soc_sys: Any = MISSING
    probes: Any = MISSING

    # No hydra: ... field — avoids conflicts
    defaults: list[Any] = field(default_factory=list)


# ============================================================================
# Dynamic Registration Function
# ============================================================================


def register_configs(example_name: str):
    """
    Register structured configs with Hydra's ConfigStore.
    Does NOT register anything under the reserved 'hydra' group.
    """
    import importlib
    import sys
    from pathlib import Path

    from hydra.core.config_store import ConfigStore

    PROJECT_ROOT = Path(__file__).resolve().parents[2]

    # Ensure example path importable
    example_path = PROJECT_ROOT / "examples" / example_name
    if str(example_path) not in sys.path:
        sys.path.insert(0, str(example_path))

    try:
        config_schemas = importlib.import_module("config_schemas")
    except ImportError as e:
        raise ImportError(
            f"Could not import config_schemas from examples/{example_name}/. "
            f"Make sure config_schemas.py exists. Error: {e}"
        )

    AgentsConfig = config_schemas.AgentsConfig
    SocSysConfig = config_schemas.SocSysConfig
    ProbesConfig = config_schemas.ProbesConfig
    generate_output_configs = config_schemas.generate_output_configs

    cs = ConfigStore.instance()

    @dataclass
    class ExampleConfig:
        sim: SimConfig
        agents: AgentsConfig  # type: ignore[valid-type]
        soc_sys: SocSysConfig  # type: ignore[valid-type]
        probes: ProbesConfig  # type: ignore[valid-type]

        defaults: list[Any] = field(
            default_factory=lambda: [
                {"soc_sys": f"{example_name}_soc_sys"},
                {"probes": f"{example_name}_probes"},
                {"agents": f"{example_name}_agents"},
                {"sim": f"{example_name}_sim"},
                "_self_",
            ]
        )

    # register top-level config
    cs.store(name="config_schema", node=ExampleConfig)

    # register sim config
    sim_config = SimConfig(example_name=example_name)
    cs.store(group="sim", name=f"{example_name}_sim_schema", node=sim_config)

    # example-specific data
    soc_sys_data, probes_data, agents_data = generate_output_configs(sim_config)

    cs.store(group="agents", name=f"{example_name}_agents_schema", node=agents_data)
    cs.store(group="soc_sys", name=f"{example_name}_soc_sys_schema", node=soc_sys_data)
    cs.store(group="probes", name=f"{example_name}_probes_schema", node=probes_data)

    return ExampleConfig


# ============================================================================
# YAML generation including hydra override block
# ============================================================================

if __name__ == "__main__":
    """
    Generates:
      - sim/example_sim.yaml
      - probes/example_probes.yaml
      - soc_sys/example_soc_sys.yaml
      - agents/example_agents.yaml
      - config.yaml  ← including hydra overrides (Option A)
    """

    import importlib
    import sys
    from pathlib import Path

    from omegaconf import OmegaConf

    EXAMPLE_NAME = "election"
    PROJECT_ROOT = Path(__file__).resolve().parents[2]

    example_path = PROJECT_ROOT / "examples" / EXAMPLE_NAME
    if str(example_path) not in sys.path:
        sys.path.insert(0, str(example_path))

    try:
        config_schemas = importlib.import_module("config_schemas")
    except ImportError as e:
        raise ImportError(
            f"Could not import config_schemas from examples/{EXAMPLE_NAME}. Error: {e}"
        )

    generate_output_configs = config_schemas.generate_output_configs

    output_dir = Path("conf")
    output_dir.mkdir(exist_ok=True)

    sim_config = SimConfig(example_name=EXAMPLE_NAME)
    soc_sys_config, probes_config, agents_config = generate_output_configs(sim_config)

    # write structured configs
    configs = {
        f"sim/{EXAMPLE_NAME}_sim.yaml": sim_config,
        f"probes/{EXAMPLE_NAME}_probes.yaml": probes_config,
        f"soc_sys/{EXAMPLE_NAME}_soc_sys.yaml": soc_sys_config,
        f"agents/{EXAMPLE_NAME}_agents.yaml": agents_config,
    }

    for path, cfg in configs.items():
        full = output_dir / path
        full.parent.mkdir(parents=True, exist_ok=True)
        with open(full, "w") as f:
            f.write(OmegaConf.to_yaml(OmegaConf.structured(cfg)))
        print(f"Generated: {full}")

    # ----------------------------------------------------------------------
    # Write top-level config.yaml INCLUDING hydra overrides (Option A)
    # ----------------------------------------------------------------------

    config_yaml = f"""
defaults:
  - soc_sys: {EXAMPLE_NAME}_soc_sys
  - probes: {EXAMPLE_NAME}_probes
  - agents: {EXAMPLE_NAME}_agents
  - sim: {EXAMPLE_NAME}_sim
  - _self_

hydra:
  job:
    name: N${{sim.num_agents}}_T${{sim.num_episodes}}_${{sim.persona_type}}_${{soc_sys.exp_name}}_${{agents.inputs.news_file}}_${{sim.use_news_agent}}_${{sim.run_name}}_${{now:%Y-%m-%d_%H-%M-%S}}
  output_subdir: configs/N${{sim.num_agents}}_T${{sim.num_episodes}}_${{sim.persona_type}}_${{soc_sys.exp_name}}_${{agents.inputs.news_file}}_${{sim.use_news_agent}}_${{sim.run_name}}
  run:
    dir: examples/${{sim.example_name}}/outputs/N${{sim.num_agents}}_T${{sim.num_episodes}}_${{sim.persona_type}}_${{soc_sys.exp_name}}_${{agents.inputs.news_file}}_${{sim.use_news_agent}}_${{sim.run_name}}
""".strip()

    with open(output_dir / "config.yaml", "w") as f:
        f.write(config_yaml + "\n")

    print(f"Generated: {output_dir / 'config.yaml'}")
    print("\nDone.")
