from dataclasses import dataclass, field
from typing import Any

from omegaconf import MISSING, OmegaConf

# Reference for traceability (remove for deployment)
from scenarios.election.config_constants import SCENARIO_NAME

# ============================================================================
# Structured Configuration Loading
# ============================================================================
from sim.config_utils.abstract_scenario import BaseScenarioConfig


@dataclass
class ExperimentConfig:
    """
    Root experiment config.
    The 'sc' field will be populated with scenario-specific config at runtime.
    Type casting happens at point of use for IDE support.
    """

    hydra_overrides: dict[str, Any] = field(default_factory=dict)
    sc: BaseScenarioConfig = MISSING


# ============================================================================
# Dynamic Registration Function
# ============================================================================


def register_configs(scenario_name: str | None = SCENARIO_NAME):
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
        module_path = f"scenarios.{scenario_name}.{scenario_name}"
        scenario_module = importlib.import_module(module_path)
        ScenarioClass = scenario_module.Scenario
        ScenarioConfigClass = scenario_module.ScenarioConfig
    except (ImportError, AttributeError) as e:
        raise ImportError(f"Could not import scenario from {module_path}. Error: {e}")

    # ----------------------------------------------------------------------
    # 2. Generate Config Instance
    # ----------------------------------------------------------------------
    scenario_instance = ScenarioClass()
    sc_cfg = scenario_instance.generate_config(scenario_instance.name)

    print(f"Generated config type: {type(sc_cfg)}")
    print(f"  sim type: {type(sc_cfg.sim)}")
    print(f"  agents type: {type(sc_cfg.agents)}")
    print(f"  soc_sys type: {type(sc_cfg.soc_sys)}")
    print(f"  probes type: {type(sc_cfg.probes)}")

    # ----------------------------------------------------------------------
    # 3. Build Hydra Overrides
    # ----------------------------------------------------------------------
    job_label = "_".join(
        [
            "N${sc.sim.num_agents}",
            "T${sc.sim.num_steps}",
            "${sc.sim.persona_type}",
            "${sc.soc_sys.exp_name}",
            "${sc.agents.inputs.news_file}",
            "${sc.sim.use_news_agent}",
            "${sc.sim.run_name}",
        ]
    )

    job_label_with_time = job_label + "_${now:%Y-%m-%d_%H-%M-%S}"

    hydra_overrides = {
        "job": {"name": job_label_with_time},
        "run": {"dir": f"scenarios/{scenario_name}/outputs/{job_label}"},
        "output_subdir": f"configs/{job_label}",
    }

    # ----------------------------------------------------------------------
    # 4. Register with ConfigStore
    # ----------------------------------------------------------------------
    cs = ConfigStore.instance()
    cs.store(group="hydra", name=f"{scenario_name}_hydra", node=hydra_overrides)

    # Register using OmegaConf.structured to create proper DictConfig
    # This maintains type information while being OmegaConf-compatible
    cs.store(group="sc", name=f"{scenario_name}_scenario", node=OmegaConf.structured(sc_cfg))

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
        sc: BaseScenarioConfig = MISSING

    # Attach the concrete type as metadata for runtime introspection
    ConcreteExperimentConfig.__annotations__["sc"] = ScenarioConfigClass

    cs.store(name="config_schema", node=ConcreteExperimentConfig)

    return ConcreteExperimentConfig
