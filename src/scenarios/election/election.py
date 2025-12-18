# src/scenarios/election/election.py

from dataclasses import dataclass, field

# Import the contract from the core
from sim.config_utils.abstract_scenario import AbstractScenario, BaseScenarioConfig, SimConfig

from .config_constants import SCENARIO_NAME
from .config_dataclasses import AgentsConfig, ProbesConfig, SocSysConfig

# Import derived agent classes from THIS scenario package
from .config_functions import get_agents_config, get_probes_config, get_soc_sys_config


class Scenario(AbstractScenario):
    """
    The concrete implementation for the 'election' scenario.
    This acts as the single source of truth for the entire run.
    """

    name = SCENARIO_NAME

    def generate_scenario_configs(self, sim: SimConfig):
        """
        Generate all scenario-specific configs from sim config.

        Args:
            sim: SimConfig instance

        Returns
        -------
            Tuple of (SocSysConfig, ProbesConfig, AgentsConfig)
        """
        # Generate agents config (returns agents WITHOUT news agents in directory)
        agents, news_info = get_agents_config(sim)

        # Generate social system config
        soc_sys = get_soc_sys_config(sim, news_info, agents.directory)

        probes = get_probes_config(sim)

        return soc_sys, probes, agents

    def generate_config(self):
        """
        Delegates the config generation to the existing config_schemas.py logic.
        """
        sim_cfg = self.generate_sim_config()

        sim_cfg.scenario_name = self.name

        # Call the function from your original file
        soc_sys_cfg, probes_cfg, agents_cfg = self.generate_scenario_configs(sim_cfg)

        return ScenarioConfig(
            sim=sim_cfg, agents=agents_cfg, soc_sys=soc_sys_cfg, probes=probes_cfg
        )

    def get_agent_classes(self) -> dict[str, type]:
        """
        Defines which derived agent class corresponds to which role.
        """
        return {
            # "news_account": ExogenousAgent,
            # "voter": SimpleAgent,
        }


@dataclass
class ScenarioConfig(BaseScenarioConfig):
    """Concrete scenario config for election scenario."""

    sim: SimConfig = field(default_factory=lambda: SimConfig())
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    soc_sys: SocSysConfig = field(default_factory=SocSysConfig)
    probes: ProbesConfig = field(default_factory=ProbesConfig)
