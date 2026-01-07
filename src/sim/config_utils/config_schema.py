from dataclasses import dataclass, field
from typing import Any

from omegaconf import MISSING, OmegaConf

# ============================================================================
# Structured Configuration Loading
# ============================================================================
from sim.config_utils.simulation_dataclasses import SimulationConfig
from sim.config_utils.simulation_functions import Simulation


@dataclass
class ExperimentConfig:
    """
    Root experiment config.
    The 'sc' field will be populated with scenario-specific config at runtime.
    Type casting happens at point of use for IDE support.
    """

    hydra_overrides: dict[str, Any] = field(default_factory=dict)
    sc: SimulationConfig = MISSING


# ============================================================================
# Dynamic Registration Function
# ============================================================================


def register_configs(scenario_name: str | None):
    """
    Register structured experiment configs with Hydra's ConfigStore.

    Returns
    -------
        The experiment config class (with scenario-specific typing if available).
    """
    import importlib

    from hydra.core.config_store import ConfigStore

    # ----------------------------------------------------------------------
    # 1. Import Scenario Classes
    # ----------------------------------------------------------------------
    try:
        module_path = f"scenarios.{scenario_name}.scenario_functions"
        scenario_module = importlib.import_module(module_path)
    except (ImportError, AttributeError) as e:
        raise ImportError(f"Could not import scenario from {module_path}. Error: {e}")

    # ----------------------------------------------------------------------
    # 2. Generate Config Instance
    # ----------------------------------------------------------------------
    simulation_instance = Simulation(scenario_module, scenario_name)
    sc_cfg = simulation_instance.generate_config()

    print(f"Generated config type: {type(sc_cfg)}")
    print(f"  sim type: {type(sc_cfg.sim)}")
    print(f"  agents type: {type(sc_cfg.agents)}")
    print(f"  soc_sys type: {type(sc_cfg.soc_sys)}")
    print(f"  probes type: {type(sc_cfg.probes)}")

    # ----------------------------------------------------------------------
    # 3. Build Hydra Overrides
    # ----------------------------------------------------------------------
    root_cfgname = "sc"
    job_label = simulation_instance.get_jobname_format(root_cfgname)

    job_label_with_time = job_label + "_${now:%Y-%m-%d_%H-%M-%S}"

    hydra_overrides = {
        "job": {"name": job_label_with_time},
        "run": {"dir": f"src/scenarios/{scenario_name}/outputs/{job_label}"},
        "output_subdir": f"configs/{job_label}",
    }

    # ----------------------------------------------------------------------
    # 4. Register with ConfigStore
    # ----------------------------------------------------------------------
    cs = ConfigStore.instance()
    cs.store(group="hydra", name=f"{scenario_name}_hydra", node=hydra_overrides)

    # Register using OmegaConf.structured to create proper DictConfig
    # This maintains type information while being OmegaConf-compatible
    cs.store(
        group=root_cfgname, name=f"{scenario_name}_scenario", node=OmegaConf.structured(sc_cfg)
    )

    # ----------------------------------------------------------------------
    # 5. Create Root Config
    # ----------------------------------------------------------------------
    # Dynamically create the config class to avoid hardcoded scenario references
    @dataclass
    class ConcreteExperimentConfig:
        defaults: list[Any] = field(
            default_factory=lambda: [
                {"hydra": f"{scenario_name}_hydra"},
                {"sc": f"{scenario_name}_scenario"},
                "_self_",
            ]
        )
        hydra_overrides: dict[str, Any] = field(default_factory=dict)
        sc: SimulationConfig = MISSING

    # Attach the concrete type as metadata for runtime introspection
    ConcreteExperimentConfig.__annotations__["sc"] = SimulationConfig

    cs.store(name="config_schema", node=ConcreteExperimentConfig)

    return ConcreteExperimentConfig, sc_cfg, hydra_overrides


# ============================================================================
# Main Block for Writing Configs
# ============================================================================

if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Generate Hydra config YAML files")
    parser.add_argument(
        "--scenario", type=str, required=True, help="Name of the scenario to generate configs for"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="conf",
        help="Base output directory for config files (default: conf)",
    )
    args = parser.parse_args()

    # Register and generate configs
    print(f"Generating configs for scenario: {args.scenario}")
    _, sc_cfg, hydra_overrides = register_configs(args.scenario)

    # Create output directory structure
    base_dir = Path(args.output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    # Create subdirectories
    hydra_dir = base_dir / "hydra"
    sim_dir = base_dir / "sim"
    agents_dir = base_dir / "agents"
    soc_sys_dir = base_dir / "soc_sys"
    probes_dir = base_dir / "probes"

    for dir_path in [hydra_dir, sim_dir, agents_dir, soc_sys_dir, probes_dir]:
        dir_path.mkdir(parents=True, exist_ok=True)

    # Write hydra config
    hydra_file = hydra_dir / f"{args.scenario}_hydra.yaml"
    with open(hydra_file, "w") as f:
        OmegaConf.save(config=hydra_overrides, f=f)
    print(f"✓ Written: {hydra_file}")

    # Write sim config
    sim_file = sim_dir / f"{args.scenario}_sim.yaml"
    with open(sim_file, "w") as f:
        OmegaConf.save(config=sc_cfg.sim, f=f)
    print(f"✓ Written: {sim_file}")

    # Write agents config
    agents_file = agents_dir / f"{args.scenario}_agents.yaml"
    with open(agents_file, "w") as f:
        OmegaConf.save(config=sc_cfg.agents, f=f)
    print(f"✓ Written: {agents_file}")

    # Write soc_sys config
    soc_sys_file = soc_sys_dir / f"{args.scenario}_soc_sys.yaml"
    with open(soc_sys_file, "w") as f:
        OmegaConf.save(config=sc_cfg.soc_sys, f=f)
    print(f"✓ Written: {soc_sys_file}")

    # Write probes config
    probes_file = probes_dir / f"{args.scenario}_probes.yaml"
    with open(probes_file, "w") as f:
        OmegaConf.save(config=sc_cfg.probes, f=f)
    print(f"✓ Written: {probes_file}")

    # Write root config with references to all components
    root_config = {
        "defaults": [
            {"hydra": f"{args.scenario}_hydra"},
            {"sim": f"{args.scenario}_sim"},
            {"agents": f"{args.scenario}_agents"},
            {"soc_sys": f"{args.scenario}_soc_sys"},
            {"probes": f"{args.scenario}_probes"},
            "_self_",
        ]
    }
    root_file = base_dir / "config.yaml"
    with open(root_file, "w") as f:
        OmegaConf.save(config=root_config, f=f)
    print(f"✓ Written: {root_file}")

    print(f"\n✅ Config generation complete! Files written to: {base_dir.absolute()}")
    print("\nDirectory structure:")
    print(f"  {base_dir}/")
    print("  ├── config.yaml")
    print("  ├── hydra/")
    print(f"  │   └── {args.scenario}_hydra.yaml")
    print("  ├── sim/")
    print(f"  │   └── {args.scenario}_sim.yaml")
    print("  ├── agents/")
    print(f"  │   └── {args.scenario}_agents.yaml")
    print("  ├── soc_sys/")
    print(f"  │   └── {args.scenario}_soc_sys.yaml")
    print("  └── probes/")
    print(f"      └── {args.scenario}_probes.yaml")
