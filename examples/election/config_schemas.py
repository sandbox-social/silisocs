import json
from dataclasses import dataclass, field

# ============================================================================
# Agents Configuration (agents.yaml)
# ============================================================================


@dataclass
class RoleDict:
    name: str
    module_path: str | None = None


@dataclass
class AgentConfig:
    name: str
    goal: str
    role_dict: RoleDict
    seed_toot: str = ""
    # Optional fields for candidates
    gender: str | None = None
    policy_proposals: str | None = None
    # Optional fields for exogenous agents (news)
    bio: str | None = None
    context: str | None = None
    coverage: str | None = None
    mastodon_username: str | None = None
    posts: dict[str, list[str]] | None = None
    schedule: str | None = None
    toot_posting_schedule: list[str] | None = None
    type: str | None = None


@dataclass
class AgentInputs:
    news_file: str
    persona_file: str


@dataclass
class AgentsConfig:
    directory: list[AgentConfig] = field(default_factory=list)
    initial_observations: list[str] = field(default_factory=list)
    inputs: AgentInputs = field(
        default_factory=lambda: AgentInputs(
            news_file="v1_news_bill_bias", persona_file="reddit_agents.json"
        )
    )


# ============================================================================
# Social System Configuration (soc_sys.yaml)
# ============================================================================


@dataclass
class CandidateInfo:
    name: str
    gender: str
    policy_proposals: str


@dataclass
class CandidatesInfo:
    conservative: CandidateInfo
    progressive: CandidateInfo


@dataclass
class ActiveRatesPerEpisode:
    candidate: float = 0.7
    exogenous: float = 1.0
    voter: float = 0.8


@dataclass
class InitialFollowProb:
    candidate: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "exogenous": 1.0, "voter": 0.4}
    )
    exogenous: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "exogenous": 1.0, "voter": 0.4}
    )
    voter: dict[str, float] = field(
        default_factory=lambda: {"candidate": 0.4, "exogenous": 1.0, "voter": 0.4}
    )


@dataclass
class RoleParameters:
    active_rates_per_episode: ActiveRatesPerEpisode = field(default_factory=ActiveRatesPerEpisode)
    initial_follow_prob: InitialFollowProb = field(default_factory=InitialFollowProb)


@dataclass
class SettingDetails:
    candidate_info: CandidatesInfo
    role_parameters: RoleParameters


@dataclass
class SettingInfo:
    description: str
    details: SettingDetails


@dataclass
class SocSysConfig:
    call_to_action: str
    exp_name: str
    gamemaster_memories: list[str]
    setting_info: SettingInfo
    shared_agent_memories_template: list[str]
    sim_setting: str
    social_media_usage_instructions: str


# ============================================================================
# Probes Configuration (probes.yaml)
# ============================================================================


@dataclass
class InteractionPremiseTemplate:
    candidate: str | None = None
    candidate1: str | None = None
    candidate2: str | None = None


@dataclass
class QueryData:
    query_type: str
    interaction_premise_template: InteractionPremiseTemplate | None = None


@dataclass
class ProbesConfig:
    queries_data: dict[int, QueryData]
    query_lib_module: str = "config_utils.agent_query_lib"


# ============================================================================
# Constants for Election Example
# ============================================================================

EXAMPLE_NAME = "election"
PARTISAN_TYPES = ["conservative", "progressive"]

CANDIDATE_INFO = {
    "conservative": {
        "name": "Bill Fredrickson",
        "gender": "male",
        "policy_proposals": [
            "providing tax breaks to local industry and creating jobs to help grow the economy."
        ],
    },
    "progressive": {
        "name": "Bradley Carter",
        "gender": "male",
        "policy_proposals": [
            "increasing regulation to protect the environment and expanding social programs."
        ],
    },
}

CALL_TO_ACTION = """
## AVAILABLE ACTIONS
1. POST - Create a new toot
2. REPLY - Respond to existing toot (needs ID)
3. BOOST - Share someone's toot (needs ID)
4. LIKE - Like a toot (needs ID)

## INSTRUCTIONS
Determine what ONE action {name} would take next based on:
- Their character and values via self-perception and goal descriptions
- The current context and timeline
- Not repeating recent actions
If text-based, the text must reflect their posting style descriptions.

## OUTPUT FORMAT
STEP 1: [Analyze {name}'s motivation based on their character]
STEP 2: [Consider which posts/actions align with {name}'s values]
STEP 3: [Determine the single most authentic action]

FINAL DECISION:
ACTION TYPE: [POST/REPLY/BOOST/LIKE]
TARGET ID: [Include toot ID if applicable]
CONTENT: [For posts/replies, exact text {name} would write]
REASONING: [Brief explanation of why this action fits {name}'s character]

## EXAMPLE OUTPUT
STEP 1: {name} is motivated by her interest in educational initiatives and community engagement around the election.
STEP 2: Chris's post about community priorities resonates with {name}'s values. She hasn't interacted with this post yet.
STEP 3: Responding to Chris would allow {name} to engage meaningfully about community values.

FINAL DECISION:
ACTION TYPE: REPLY
TARGET ID: 114204813429886778
CONTENT: "I appreciate your focus on community priorities, Chris! As an educator, I believe our growth depends on strong educational foundations alongside economic development."
REASONING: This reply allows Emily to acknowledge community values while highlighting her educational perspective, which is authentic to her character.
"""

SETTING_BACKGROUND = [
    "Storhampton is a small town with a population of approximately 2,500 people.",
    "Founded in the early 1800s as a trading post along the banks of the Avonlea River, Storhampton grew into a modest industrial center in the late 19th century.",
    "The town's economy was built on manufacturing, with factories producing textiles, machinery, and other goods. ",
    "Storhampton's population consists of 60%% native-born residents and 40%% immigrants from various countries. ",
    "Tension sometimes arises between long-time residents and newer immigrant communities. ",
    "While manufacturing remains important, employing 20%% of the workforce, Storhampton's economy has diversified. "
    "A significant portion of the Storhampton population has been left behind as higher-paying blue collar jobs have declined, leading to economic instability for many. ",
    "The Storhampton poverty rate stands at 15%.",
]

SHARED_MEMORIES_TEMPLATE = (
    [
        "They are a long-time active user on Storhampton.social, a Mastodon instance created for the residents of Storhampton."
    ]
    + SETTING_BACKGROUND
    + [
        "\n".join(
            [
                "Mayoral Elections: The upcoming mayoral election in Storhampton has become a heated affair.",
                "Social media has emerged as a key battleground in the race, with both candidates actively promoting themselves and engaging with voters.",
                "Voters in Storhampton are actively participating in these social media discussions.",
                "Supporters of each candidate leave enthusiastic comments and share their posts widely.",
                f"Critics also chime in, for example attacking {CANDIDATE_INFO['conservative']['name']} as out-of-touch and beholden to corporate interests,",
                f" or labeling {CANDIDATE_INFO['progressive']['name']} as a radical who will undermine law and order.",
                "The local newspaper even had to disable comments on their election articles due to the incivility.",
            ]
        )
    ]
)

SOCIAL_MEDIA_USAGE_INSTRUCTIONS = " ".join(
    [
        "MastodonSocialNetworkApp is a social media application.",
        "To share content on Mastodon, users write a 'toot' (equivalent to a tweet or post).",
        "Toots can be up to 500 characters long.",
        "A user's home timeline shows toots from people they follow and boosted (reblogged) content.",
        "Users can reply to toots, creating threaded conversations.",
        "Users can like (favorite) toots to show appreciation or save them for later.",
        "Users can boost (reblog) toots to share them with their followers.",
        "Users can mention other users in their toots using their @username.",
        "Follow other users to see their public and unlisted toots in their home timeline.",
        "Users can unfollow users if they no longer wish to see their content.",
        "A user's profile can be customized with a display name and bio.",
        "A user can block other users to prevent them from seeing the user's content or interacting with them.",
        "Unblocking a user reverses the effects of blocking.",
        "Critically important: Operations such as liking, boosting, replying, etc. require a `toot_id`. To obtain a `toot_id`, you must have memory/knowledge of a real `toot_id`. If you don't know a `toot_id`, you can't perform actions that require it. `toot_id`'s can be retrieved using the `get_timeline` action.",
    ]
)

BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY = 0.4

QUERY_LIB_MODULE = "config_utils.agent_query_lib"


# ============================================================================
# Config Generation Functions
# ============================================================================


def get_news_agent_configs(n_agents, news=None, include_images=True):
    """Generate news agent configurations."""
    news_types = ["local", "national", "international"]

    # Limit the news types to the first n_agent elements
    news_types = news_types[:n_agents]

    # Create news agent config settings
    news_info = {
        "local": {
            "name": "Storhampton Gazette",
            "type": "local",
            "coverage": "local news",
            "schedule": "hourly",
            "mastodon_username": "storhampton_gazette",
            "seed_toot": "Good morning, Storhampton! Tune in for the latest local news updates.",
        },
        "national": {
            "name": "National News Network",
            "type": "national",
            "coverage": "national news",
            "schedule": "hourly",
            "mastodon_username": "national_news_network",
            "seed_toot": "Good morning, Storhampton! Tune in for the latest national news updates.",
        },
        "international": {
            "name": "Global News Network",
            "type": "international",
            "coverage": "international news",
            "schedule": "hourly",
            "mastodon_username": "global_news_network",
            "seed_toot": "Good morning, Storhampton! Tune in for the latest international news updates.",
        },
    }

    news_agent_configs = []
    for news_type in news_types:
        news_data = news_info[news_type]

        # Create AgentConfig for news agent using dataclass
        agent_config = AgentConfig(
            name=news_data["name"],
            goal="",  # Empty string since news agents don't have goals
            role_dict=RoleDict(name="exogenous"),
            seed_toot=news_data.get("seed_toot", ""),
            bio=f"Providing {news_data['coverage']} to the users of Storhampton.social.",
            context="",
            coverage=news_data["coverage"],
            mastodon_username=news_data["mastodon_username"],
            type=news_data["type"],
            schedule=news_data["schedule"],
            posts=(
                {k: [img for img in v] if include_images else [] for k, v in news.items()}
                if news is not None
                else None
            ),
        )
        news_agent_configs.append(agent_config)

    return news_agent_configs, {k: news_info[k] for k in news_types}


def get_followership_connection_stats(roles):
    """Generate followership statistics."""
    fully_connected_targets = ["candidate", "exogenous"]
    p_from_to = {}
    for role_i in roles:
        p_from_to[role_i] = {}
        for role_j in roles:
            if role_j in fully_connected_targets:
                p_from_to[role_i][role_j] = 1.0
            else:
                p_from_to[role_i][role_j] = BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY
    return p_from_to


def get_agents_config(sim):
    """Generate agents configuration from sim config."""
    use_news_agent = sim.use_news_agent
    num_agents = sim.num_agents

    agents_dict = {
        "inputs": {"persona_file": "reddit_agents.json", "news_file": "v1_news_bill_bias"},
        "directory": [],
    }

    roles = []

    # Add candidates
    roles.append("candidate")
    candidate_configs = []
    for partisan_type in PARTISAN_TYPES:
        candidate = CANDIDATE_INFO[partisan_type].copy()
        policy_text = (
            f"{candidate['name']} campaigns on {' and '.join(candidate['policy_proposals'])}"
        )

        agent_config = AgentConfig(
            name=candidate["name"],
            gender=candidate["gender"],
            policy_proposals=policy_text,
            goal=f"{candidate['name']}'s goal is to win the election and become the mayor of Storhampton.",
            role_dict=RoleDict(name="candidate", module_path="agent_lib.simple"),
            seed_toot="",
        )
        candidate_configs.append(agent_config)

    # Add news agents if enabled
    news_agent_configs = []
    news_info = {}
    if use_news_agent:
        roles.append("exogenous")
        with open(
            f"examples/election/input/news_data/{agents_dict['inputs']['news_file']}.json"
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

        news_agent_configs, news_info = get_news_agent_configs(
            n_agents=1, news=news, include_images=include_images
        )

    # Add voters
    roles.append("voter")
    with open(f"examples/election/input/personas/{agents_dict['inputs']['persona_file']}") as f:
        persona_rows = json.load(f)

    voter_configs = []
    for row in persona_rows[: num_agents - len(candidate_configs)]:
        agent_config = AgentConfig(
            name=row["Name"],
            goal=row["Context"] + " Their goal is have a good day and vote in the election.",
            role_dict=RoleDict(name="voter", module_path="agent_lib.simple"),
            seed_toot="",
        )
        voter_configs.append(agent_config)

    # Combine all agents (voters + candidates first, then news agents added later in generate_output_configs)
    all_agents = voter_configs + candidate_configs

    agents_config = AgentsConfig(
        directory=all_agents,  # News agents added in generate_output_configs
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

    return agents_config, news_agent_configs, news_info, roles


def get_soc_sys_config(sim, gamemaster_memories, news_info, roles):
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
    active_rates = ActiveRatesPerEpisode(candidate=0.7, voter=0.8, exogenous=1.0)

    initial_follow_prob_dict = get_followership_connection_stats(roles)
    initial_follow_prob = InitialFollowProb(
        candidate=initial_follow_prob_dict.get("candidate", {}),
        exogenous=initial_follow_prob_dict.get("exogenous", {}),
        voter=initial_follow_prob_dict.get("voter", {}),
    )

    role_params = RoleParameters(
        active_rates_per_episode=active_rates, initial_follow_prob=initial_follow_prob
    )

    setting_details = SettingDetails(candidate_info=candidates_info, role_parameters=role_params)

    description = "\n".join([candidate_info_dict[p].policy_proposals for p in PARTISAN_TYPES])

    setting_info = SettingInfo(description=description, details=setting_details)

    # Add news info to shared memories if applicable
    shared_memories = SHARED_MEMORIES_TEMPLATE.copy()
    if sim.use_news_agent and news_info:
        shared_memories.append(
            f"Voters in Storhampton are actively getting the latest local news from "
            f"{news_info['local']['name']} social media account."
        )

    soc_sys = SocSysConfig(
        call_to_action=CALL_TO_ACTION,
        exp_name=experiment_name,
        gamemaster_memories=gamemaster_memories,
        setting_info=setting_info,
        shared_agent_memories_template=shared_memories,
        sim_setting=EXAMPLE_NAME,
        social_media_usage_instructions=SOCIAL_MEDIA_USAGE_INSTRUCTIONS,
    )

    return soc_sys


def generate_output_configs(sim):
    """
    Generate all example-specific configs from sim config.

    Args:
        sim: SimConfig instance

    Returns
    -------
        Tuple of (SocSysConfig, ProbesConfig, AgentsConfig)
    """
    # Generate agents config (returns agents WITHOUT news agents in directory)
    agents, news_agent_configs, news_info, roles = get_agents_config(sim)

    # Generate gamemaster memories
    gamemaster_memories = [
        f"{agent.name} is at their private home." for agent in agents.directory
    ] + [f"The workday begins for the {agent.name}" for agent in news_agent_configs]

    # Join non-news and news agents (matching old behavior)
    agents.directory = agents.directory + news_agent_configs

    # Generate social system config
    soc_sys = get_soc_sys_config(sim, gamemaster_memories, news_info, roles)

    # Generate probes config - get candidate names from CANDIDATE_INFO
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

    return soc_sys, probes, agents
