import json
from typing import Any

from sim.config_utils.simulation_dataclasses import AgentConfig, SimRole

from .scenario_constants import (
    CANDIDATE_INFO,
    INITIAL_OBSERVATIONS,
    NEWS_FILE,
    PARTISAN_TYPES,
    PERSONA_FILE,
    PERSONA_TYPE,
    SHARED_MEMORIES_TEMPLATE,
    USE_NEWS_AGENT,
)
from .scenario_dataclasses import (
    AgentInputs,
    CandidateInfo,
    CandidatesInfo,
    NewsAccountParams,
    SettingDetails,
    VoterParams,
)


def get_policy_text():
    policy_text = ""
    for partisan_type in PARTISAN_TYPES:
        candidate = CANDIDATE_INFO[partisan_type]
        policy_text += (
            f"{candidate['name']} campaigns on {' and '.join(candidate['policy_proposals'])}. \n"
        )
    return policy_text


def get_agent_input_data():
    input_data = AgentInputs(
        use_news_agent=USE_NEWS_AGENT,
        news_file=NEWS_FILE,
        persona_file=PERSONA_FILE,
        persona_type=PERSONA_TYPE,
    )

    return input_data


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
            "sm_account_username": "storhampton_gazette",
            "seed_post": "Good morning, Storhampton! Tune in for the latest local news updates.",
        },
        "national": {
            "name": "National News Network",
            "type": "national",
            "coverage": "national news",
            "schedule": "hourly",
            "sm_account_username": "national_news_network",
            "seed_post": "Good morning, Storhampton! Tune in for the latest national news updates.",
        },
        "international": {
            "name": "Global News Network",
            "type": "international",
            "coverage": "international news",
            "schedule": "hourly",
            "sm_account_username": "global_news_network",
            "seed_post": "Good morning, Storhampton! Tune in for the latest international news updates.",
        },
    }

    news_agent_configs = []

    news_data = news_info[news_type]
    posts: dict[str, str] = {}
    if news is not None:
        posts = {k: v[0] if include_images else "" for k, v in news.items()}

    # Create AgentConfig for news agent using dataclass
    sim_role = SimRole(
        name="news_account", module_path="scenarios.election.entity_lib.simple"
    )  # exogenous")
    agent_config = AgentConfig(
        prefab=sim_role.module_path.split(".")[-1] + "__Entity",
        params=NewsAccountParams(
            name=news_data["name"],
            sim_role=sim_role,
            seed_post=news_data.get("seed_post", ""),
            bio=f"Providing {news_data['coverage']} to the users of Storhampton.social.",
            posts=posts,
            context="A small-town newspaper covering local politics",
            style="",
            goal=None,
        ),
    )
    news_agent_configs.append(agent_config)
    enumerated_news_info: dict[str, dict[str, str]] = {k: news_info[k] for k in news_types}
    return news_agent_configs, enumerated_news_info


def get_agent_numbers_by_role(total_num_agents):
    num_agents = {"candidate": 2, "news_account": 1, "voter": total_num_agents - 2}
    return num_agents


def get_agents_from_role(
    role: str, num_agents: int, agent_inputs: AgentInputs
) -> list[AgentConfig]:
    configs: list[AgentConfig] = []

    if role == "candidate":
        # Add candidates (one of each partisan type)
        sim_role = SimRole(name="candidate", module_path="scenarios.election.entity_lib.simple")
        for partisan_type in PARTISAN_TYPES:
            candidate = CANDIDATE_INFO[partisan_type].copy()
            agent_config = AgentConfig(
                prefab=sim_role.module_path.split(".")[-1] + "__Entity",
                params=VoterParams(
                    name=str(candidate["name"]),
                    seed_post="",
                    sim_role=sim_role,
                    bio="",
                    election_info=get_policy_text(),
                    goal=f"{candidate['name']}'s goal is to win the election and become the mayor of Storhampton.",
                    context=str(candidate["persona"]),
                    style=str(candidate["style"]),
                ),
            )
            configs.append(agent_config)

    elif role == "news_account":
        # Add a single news agents if enabled

        # load predefined headlines
        with open(f"src/scenarios/election/input/news_data/{agent_inputs.news_file}.json") as f:
            news = json.load(f)

        print("headlines:")
        for headline in news.keys():
            print(headline)

        include_images = agent_inputs.use_news_agent == "with_images"
        print(
            "Including images with the above headlines"
            if include_images
            else "NOT including images"
        )
        configs, news_info = get_news_agent_configs(
            n_agents=num_agents, news=news, include_images=include_images
        )

    elif role == "voter":
        # Add the rest as voters, using stored persona data
        with open(f"src/scenarios/election/input/personas/{agent_inputs.persona_file}") as f:
            persona_data = json.load(f)

        sim_role = SimRole(name="voter", module_path="scenarios.election.entity_lib.simple")
        for persona in persona_data[:num_agents]:
            collapsed_persona_fields = (
                "\n".join(
                    f"{k}: {v}" for k, v in persona.items() if k not in ["Name", "User_Reference"]
                )
                + "\n"
            )
            agent_config = AgentConfig(
                prefab=sim_role.module_path.split(".")[-1] + "__Entity",
                params=VoterParams(
                    name=persona["Name"],
                    goal="Their goal is have a good day and vote in the election.",
                    sim_role=sim_role,
                    election_info=get_policy_text(),
                    seed_post="",
                    bio="",
                    context=collapsed_persona_fields,
                    style=persona["Style"],
                ),
            )
            configs.append(agent_config)
    else:
        print("role not implemented")

    return configs


def get_setting_info():
    # Build candidate info dataclasses
    candidate_info_dict = {}
    for partisan_type in PARTISAN_TYPES:
        candidate = CANDIDATE_INFO[partisan_type]
        policy_text = (
            f"{candidate['name']} campaigns on {' and '.join(candidate['policy_proposals'])}"
        )
        candidate_info_dict[partisan_type] = CandidateInfo(
            name=str(candidate["name"]), policy_proposals=policy_text
        )

    candidates_info = CandidatesInfo(
        conservative=candidate_info_dict["conservative"],
        progressive=candidate_info_dict["progressive"],
    )
    scenario_setting_details = SettingDetails(candidate_info=candidates_info)
    scenario_description = " \n".join(
        [candidate_info_dict[p].policy_proposals for p in PARTISAN_TYPES]
    )
    return scenario_description, scenario_setting_details


def get_grouped_agent_attributes():
    grouped_attributes = {}
    # Add news info to shared memories if applicable
    grouped_attributes["shared_memories"] = SHARED_MEMORIES_TEMPLATE.copy()

    grouped_attributes["initial_observations"] = INITIAL_OBSERVATIONS

    return grouped_attributes


def get_probe_data():
    candidates = [CANDIDATE_INFO[p]["name"] for p in PARTISAN_TYPES]
    query_type_list = [
        {
            "name": "VotePref",
            "premise": {"candidate1": str(candidates[0]), "candidate2": str(candidates[1])},
        },
        {"name": "Favorability", "premise": {"candidate": str(candidates[0])}},
        {"name": "Favorability", "premise": {"candidate": str(candidates[1])}},
        {"name": "VoteIntent", "premise": {}},
    ]
    return query_type_list
