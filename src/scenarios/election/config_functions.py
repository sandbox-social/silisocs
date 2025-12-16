# scenarios/election/config_functions.py

import json
from typing import Any

from sim.config_utils.abstract_scenario import (
    GameMasterConfig,
    InitializerConfig,
    InitializerParams,
    QueryData,
    SettingInfo,
    SimConfig,
)

from .config_constants import (
    BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY,
    CALL_TO_ACTION,
    CANDIDATE_INFO,
    PARTISAN_TYPES,
    SCENARIO_NAME,
    SHARED_MEMORIES_TEMPLATE,
    SOCIAL_MEDIA_GAMEMASTER_FILENAME,
    SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
    USE_SERVER,
)
from .config_dataclasses import (
    ActiveRatesPerStep,
    AgentConfig,
    AgentInputs,
    AgentsConfig,
    Candidate,
    CandidateInfo,
    CandidatesInfo,
    InitialFollowProb,
    InteractionPremiseTemplate,
    NewsAccount,
    ProbesConfig,
    SettingDetails,
    SimRole,
    SimRoleParameters,
    SocialMediaParams,
    SocSysConfig,
    UserData,
    Voter,
)

# ============================================================================
# Config Generation Functions
# ============================================================================


def get_news_agent_configs(
    n_agents: int, news: dict[str, str] | None = None, include_images: bool = True
) -> tuple[list[AgentConfig], dict[Any, Any]]:
    """Generate news agent configurations."""
    news_types = ["local", "national", "international"]

    # Limit the news types to the first n_agent elements
    news_type = news_types[0]

    # Create news agent config settings
    news_info: dict[str, dict[str, str]] = {
        "local": {
            "name": "Storhampton Gazette",
            "type": "local",
            "coverage": "local news",
            "schedule": "hourly",
            "mastodon_username": "storhampton_gazette",
            "seed_post": "Good morning, Storhampton! Tune in for the latest local news updates.",
        },
        "national": {
            "name": "National News Network",
            "type": "national",
            "coverage": "national news",
            "schedule": "hourly",
            "mastodon_username": "national_news_network",
            "seed_post": "Good morning, Storhampton! Tune in for the latest national news updates.",
        },
        "international": {
            "name": "Global News Network",
            "type": "international",
            "coverage": "international news",
            "schedule": "hourly",
            "mastodon_username": "global_news_network",
            "seed_post": "Good morning, Storhampton! Tune in for the latest international news updates.",
        },
    }

    news_agent_configs = []

    news_data = news_info[news_type]
    posts: dict[str, str] = {}
    if news is not None:
        posts = {k: v[0] if include_images else "" for k, v in news.items()}

    # Create AgentConfig for news agent using dataclass
    agent_config = AgentConfig(
        prefab="exogeneous__Entity",
        params=NewsAccount(
            name=news_data["name"],
            simrole_dict=SimRole(name="news", model_module_path="agent_lib.exogenous"),
            seed_post=news_data.get("seed_post", ""),
            bio=f"Providing {news_data['coverage']} to the users of Storhampton.social.",
            posts=posts,
            context="",
            style="",
        ),
    )
    news_agent_configs.append(agent_config)

    return news_agent_configs, {k: news_info[k] for k in news_types}


def get_followership_connection_stats(roles: list) -> dict[Any, dict[Any, Any]]:
    """Generate followership statistics."""
    fully_connected_targets = ["candidate", "news"]
    p_from_to: dict[str, dict[str, float]] = {}
    for role_i in roles:
        p_from_to[role_i] = {}
        for role_j in roles:
            if role_j in fully_connected_targets:
                p_from_to[role_i][role_j] = 1.0
            else:
                p_from_to[role_i][role_j] = BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY
    return p_from_to


def get_agents_config(sim: SimConfig) -> tuple[AgentsConfig, dict[Any, Any]]:
    """Generate agents configuration from sim config."""
    use_news_agent = sim.use_news_agent
    num_agents = sim.num_agents

    input_data: dict[str, str] = {
        "persona_file": "reddit_agents.json",
        "news_file": "v1_news_bill_bias",
    }
    agents_dict: dict[Any, Any] = {
        "inputs": input_data,
        "directory": [],
    }

    # Add candidates
    candidate_configs: list[AgentConfig] = []
    # One of each partisan type
    for partisan_type in PARTISAN_TYPES:
        candidate = CANDIDATE_INFO[partisan_type].copy()
        policy_text = f"{candidate['name']} campaigns on {candidate['policy_proposals']}"
        agent_config = AgentConfig(
            prefab="candidate_Entity",
            params=Candidate(
                name=candidate["name"],
                seed_post="",
                simrole_dict=SimRole(name="candidate", model_module_path="agent_lib.simple"),
                bio="",
                gender=candidate["gender"],
                policy_proposals=policy_text,
                goal=f"{candidate['name']}'s goal is to win the election and become the mayor of Storhampton.",
                context="",
                style="",
            ),
        )
        candidate_configs.append(agent_config)

    # Add a single news agents if enabled
    news_agent_configs: list[AgentConfig] = []
    news_info: dict[str, str] = {}
    if use_news_agent:
        # load predefined headlines
        with open(
            f"src/scenarios/election/input/news_data/{agents_dict['inputs']['news_file']}.json"
        ) as f:
            news = json.load(f)

        print("headlines:")
        for headline in news.keys():
            print(headline)

        include_images = use_news_agent == "with_images"
        print(
            "Including images with the above headlines"
            if include_images
            else "NOT including images"
        )
        n_agents = 1
        news_agent_configs, news_info = get_news_agent_configs(
            n_agents=n_agents, news=news, include_images=include_images
        )

    # Add the rest as voters, using stored persona data
    with open(
        f"src/scenarios/election/input/personas/{agents_dict['inputs']['persona_file']}"
    ) as f:
        persona_rows = json.load(f)

    voter_configs: list[AgentConfig] = []
    for row in persona_rows[: num_agents - len(candidate_configs)]:
        agent_config = AgentConfig(
            prefab="voter__Entity",
            params=Voter(
                name=row["Name"],
                goal=" Their goal is have a good day and vote in the election.",
                gender=row["Sex"],
                simrole_dict=SimRole(name="voter", model_module_path="agent_lib.simple"),
                policy_proposals=policy_text,
                seed_post="",
                bio="",
                context=row["Context"],
                style=row["Style"],
            ),
        )
        voter_configs.append(agent_config)

    # Combine all agents (voters + candidates first, then news agents added later)
    all_agents = voter_configs + candidate_configs + news_agent_configs

    agents_config = AgentsConfig(
        directory=list(all_agents),
        initial_observations=[
            "{name} is at home, they have just woken up.",
            "{name} remembers they want to update their Mastodon bio.",
            "{name} remembers they want to read their Mastodon feed to catch up on news",
        ],
        inputs=AgentInputs(
            news_file=agents_dict["inputs"]["news_file"],
            persona_file=agents_dict["inputs"]["persona_file"],
        ),
    )

    return agents_config, news_info


def get_auxillary_agent_data_from_config(
    agent_config_list: list[AgentConfig],
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    roles: dict[str, Any] = {}
    player_specific_memories_map = {}
    player_specific_context_map = {}
    for agent_data in agent_config_list:
        roles[agent_data.params.name] = agent_data.params.simrole_dict.name
        context_parts = [agent_data.params.context]
        player_specific_memories_map[agent_data.params.name] = ""
        player_specific_context_map[agent_data.params.name] = "\n".join(context_parts)
    return (roles, player_specific_memories_map, player_specific_context_map)


def get_soc_sys_config(
    sim: SimConfig, news_info: dict[Any, Any], agent_data: list[AgentConfig]
) -> SocSysConfig:
    """Generate social system configuration."""
    experiment_name = "independent"

    # Build candidate info dataclasses
    candidate_info_dict = {}
    for partisan_type in PARTISAN_TYPES:
        candidate = CANDIDATE_INFO[partisan_type]
        policy_text = (
            f"{candidate['name']} campaigns on {' and '.join(candidate['policy_proposals'])}"
        )
        candidate_info_dict[partisan_type] = CandidateInfo(
            name=candidate["name"], gender=candidate["gender"], policy_proposals=policy_text
        )

    candidates_info = CandidatesInfo(
        conservative=candidate_info_dict["conservative"],
        progressive=candidate_info_dict["progressive"],
    )

    # Build role parameters
    active_rates = ActiveRatesPerStep(candidate=0.7, voter=0.8, exogenous=1.0)
    roles_tmp = ["candidate", "news", "voter"]
    initial_follow_prob_dict = get_followership_connection_stats(roles_tmp)
    initial_follow_prob = InitialFollowProb(
        candidate=initial_follow_prob_dict.get("candidate", {}),
        news=initial_follow_prob_dict.get("news", {}),
        voter=initial_follow_prob_dict.get("voter", {}),
    )

    simrole_params = SimRoleParameters(
        active_rates_per_episode=active_rates, initial_follow_prob=initial_follow_prob
    )

    (simroles, player_specific_memories_map, player_specific_context_map) = (
        get_auxillary_agent_data_from_config(agent_data)
    )

    sm_user_data = UserData(simrole_parameters=simrole_params, simroles=simroles)
    setting_details = SettingDetails(
        candidate_info=candidates_info, simrole_parameters=simrole_params
    )

    description = "\n".join([candidate_info_dict[p].policy_proposals for p in PARTISAN_TYPES])

    setting_info = SettingInfo(description=description, details=setting_details)

    # Add news info to shared memories if applicable
    shared_memories = SHARED_MEMORIES_TEMPLATE.copy()
    if sim.use_news_agent and news_info:
        shared_memories.append(
            f"Voters in Storhampton are actively getting the latest local news from "
            f"{news_info['local']['name']} social media account."
        )

    # Add Configurator Game Master
    shared_memories = (
        shared_memories + [setting_info.description] + [SOCIAL_MEDIA_USAGE_INSTRUCTIONS]
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

    SocialMediaGM = GameMasterConfig(
        prefab=SOCIAL_MEDIA_GAMEMASTER_FILENAME + "__GameMaster",
        params=SocialMediaParams(
            name="mastodon",
            app_module=sim.app_module,
            call_to_action=CALL_TO_ACTION,
            sm_user_data=sm_user_data,
            use_server=USE_SERVER,
            app_description=SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
        ),
    )

    soc_sys = SocSysConfig(
        exp_name=experiment_name,
        game_masters=[InitializerGM, SocialMediaGM],
        setting_info=setting_info,
        shared_agent_memories_template=shared_memories,
        scenario_name=SCENARIO_NAME,
        social_media_usage_instructions=SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
    )

    return soc_sys


def get_probes_config(sim: SimConfig) -> ProbesConfig:
    """Generate probes config - get candidate names from CANDIDATE_INFO."""
    candidates = [CANDIDATE_INFO[p]["name"] for p in PARTISAN_TYPES]

    probes = ProbesConfig(
        queries_data={
            0: QueryData(
                query_type="VotePref",
                interaction_premise_template=InteractionPremiseTemplate(
                    candidate1=candidates[0], candidate2=candidates[1]
                ),
            ),
            1: QueryData(
                query_type="Favorability",
                interaction_premise_template=InteractionPremiseTemplate(candidate=candidates[0]),
            ),
            2: QueryData(
                query_type="Favorability",
                interaction_premise_template=InteractionPremiseTemplate(candidate=candidates[1]),
            ),
            3: QueryData(query_type="VoteIntent"),
        }
    )

    return probes
