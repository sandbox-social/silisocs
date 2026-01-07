# src/scenarios/election/election.py

from typing import Any

from scenarios.election.scenario_constants import (
    ACTIVE_RATES,
    FULLY_CONNECTED_TARGETS,
    JOBNAME_FORMAT,
)
from scenarios.election.scenario_dataclasses import (
    InteractionPremiseTemplate,
)
from scenarios.election.scenario_functions import (
    get_agent_input_data,
    get_agent_numbers_by_role,
    get_agents_from_role,
    get_grouped_agent_attributes,
    get_probe_data,
    get_setting_info,
)
from sim.config_utils.simulation_constants import (
    APP_MODULE_PATH,
    LLM_NAME,
    NUM_AGENTS,
    NUM_STEPS,
    ROLEPLAYING_INSTRUCTIONS,
    RUN_NAME,
    SEED,
    SENTENCE_ENCODER,
)
from sim.config_utils.simulation_dataclasses import (
    AgentConfig,
    AgentsConfig,
    GameMasterConfig,
    InitializerConfig,
    InitializerParams,
    ProbesConfig,
    QueryData,
    SettingInfo,
    SimConfig,
    SimRole,
    SimulationConfig,
    SocSysConfig,
)
from sim.config_utils.social_media_constants import (
    CALL_TO_ACTION,
    SOCIAL_MEDIA_GAMEMASTER_FILENAME,
    SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
    USE_SERVER,
)
from sim.config_utils.social_media_dataclasses import (
    SimRoleParameters,
    SocialMediaParams,
    UserData,
)
from sim.config_utils.social_media_functions import get_followership_connection_stats


def get_agents_config(sim: SimConfig) -> AgentsConfig:
    """Generate agents configuration from sim config."""
    total_num_agents = sim.num_agents

    agent_inputs = get_agent_input_data()

    roles = get_agent_numbers_by_role(total_num_agents)

    agent_directory = []
    for role, num_agents in roles.items():
        agent_directory += get_agents_from_role(role, num_agents, agent_inputs)

    grouped_attributes = get_grouped_agent_attributes()
    agents_config = AgentsConfig(
        directory=agent_directory,
        initial_observations=grouped_attributes["initial_observations"],
        inputs=agent_inputs,
    )

    return agents_config


def get_auxillary_agent_data_from_config(
    agent_config_list: list[AgentConfig],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    roles: dict[str, Any] = {}
    player_specific_memories_map = {}
    player_specific_context_map = {}
    for agent_data in agent_config_list:
        roles[agent_data.params.name] = agent_data.params.sim_role.name
        context_parts = [agent_data.params.context]
        player_specific_memories_map[agent_data.params.name] = [""]
        player_specific_context_map[agent_data.params.name] = "\n".join(context_parts)
    return (roles, player_specific_memories_map, player_specific_context_map)


def get_soc_sys_config(sim: SimConfig, agent_data: list[AgentConfig]) -> SocSysConfig:
    """Generate social system configuration."""
    experiment_name = "independent"

    # Build role parameters
    simrole_params = SimRoleParameters(
        active_rates_per_episode=ACTIVE_RATES,
        initial_follow_prob=get_followership_connection_stats(
            list(ACTIVE_RATES.keys()), FULLY_CONNECTED_TARGETS
        ),
    )

    (simroles, player_specific_memories_map, player_specific_context_map) = (
        get_auxillary_agent_data_from_config(agent_data)
    )

    scenario_description, scenario_setting_details = get_setting_info()
    sm_user_data = UserData(sim_role_parameters=simrole_params, sim_roles=simroles)

    setting_info = SettingInfo(description=scenario_description, details=scenario_setting_details)

    # Add Configurator Game Master
    grouped_attributes = get_grouped_agent_attributes()

    shared_memories = (
        grouped_attributes["shared_memories"]
        + [setting_info.description]
        + [SOCIAL_MEDIA_USAGE_INSTRUCTIONS]
    )
    InitializerGM = InitializerConfig(
        prefab="formative_memories_initializer__GameMaster",
        params=InitializerParams(
            name="initial setup rules",
            next_game_master_name=SOCIAL_MEDIA_GAMEMASTER_FILENAME + "__GameMaster",
            shared_memories=shared_memories,
            player_specific_memories=player_specific_memories_map,
            player_specific_context=player_specific_context_map,
        ),
    )

    sim_role = SimRole(name="social media game master", module_path="sim.entities.social_media")
    SocialMediaGM = GameMasterConfig(
        prefab=SOCIAL_MEDIA_GAMEMASTER_FILENAME + "__GameMaster",
        params=SocialMediaParams(
            name="mastodon_gm",
            calls_to_action={"social_media_action": CALL_TO_ACTION},
            sim_role=sim_role,
            app_module_path=sim.app_module_path,
            sm_user_data=sm_user_data,
            app_description=SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
        ),
    )

    soc_sys = SocSysConfig(
        exp_name=experiment_name,
        game_masters=[InitializerGM, SocialMediaGM],
        setting_info=setting_info,
        shared_agent_memories_template=shared_memories,
        scenario_name=sim.scenario_name,
        social_media_usage_instructions=SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
    )

    return soc_sys


def get_probes_config(sim: SimConfig) -> ProbesConfig:
    """Generate probes config - get candidate names from CANDIDATE_INFO."""
    query_list = get_probe_data()

    probes = ProbesConfig(
        queries_data={
            num: QueryData(
                query_type=q["name"],
                interaction_premise_template=InteractionPremiseTemplate(**q["premise"]),
            )
            for num, q in enumerate(query_list)
        }
    )

    return probes


class Simulation:
    """
    The concrete implementation for the 'election' scenario.
    This acts as the single source of truth for the entire run.
    """

    def __init__(self, scenario_module, scenario_name):
        self.name = scenario_name
        self.scenario_module = scenario_module

    def generate_sim_config(self):
        sim_cfg = SimConfig(
            app_module_path=APP_MODULE_PATH,
            llm_name=LLM_NAME,
            num_agents=NUM_AGENTS,
            num_steps=NUM_STEPS,
            run_name=RUN_NAME,
            seed=SEED,
            sentence_encoder=SENTENCE_ENCODER,
            output_rootname="",  # set from hydra fields at runtime
            roleplaying_instructions=ROLEPLAYING_INSTRUCTIONS,
            scenario_name=self.name,
            use_server=USE_SERVER,
        )
        return sim_cfg

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
        agents = get_agents_config(sim)

        # Generate social system config
        soc_sys = get_soc_sys_config(sim, agents.directory)

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

        return SimulationConfig(
            sim=sim_cfg, agents=agents_cfg, soc_sys=soc_sys_cfg, probes=probes_cfg
        )

    def get_jobname_format(self, cfgname):
        return JOBNAME_FORMAT.format(cfgname=cfgname)
