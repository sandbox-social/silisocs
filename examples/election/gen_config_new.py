"""A script for generating sim config files"""

import json

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

# 2 candidate config settings
PARTISAN_TYPES = ["conservative", "progressive"]
CANDIDATE_INFO: dict[str, dict] = {
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


def get_followership_connection_stats(roles):
    # initial follower network statistics
    fully_connected_targets = ["candidates", "exogenous"]
    p_from_to = {}
    for role_i in roles:
        p_from_to[role_i] = {}
        for role_j in roles:
            if role_j in fully_connected_targets:
                p_from_to[role_i][role_j] = 1
            else:
                p_from_to[role_i][role_j] = BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY
    return p_from_to


# generate news agent configs
def get_news_agent_configs(n_agents, news=None, include_images=True):
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
    for i, news_type in enumerate(news_types):
        agent = news_info[news_type].copy()
        agent["role_dict"] = {"name": "exogenous"}
        agent["goal"] = None
        agent["bio"] = (
            f"Providing {news_info[news_type]['coverage']} to the users of Storhampton.social."  # currently not used since read_bio not one of available actions
        )
        agent["context"] = ""
        agent["seed_toot"] = (
            news_info[news_type]["seed_toot"] if "seed_toot" in news_info[news_type] else ""
        )

        if news is not None:
            agent["posts"] = {
                k: [img for img in v] if include_images else [] for k, v in news.items()
            }

        news_agent_configs.append(agent)

    return news_agent_configs, {k: news_info[k] for k in news_types}


def generate_output_configs(cfg):
    use_news_agent = cfg["use_news_agent"]
    num_agents = cfg["num_agents"]
    persona_type = cfg["persona_type"]
    experiment_name = "independent"
    # 1) agent configurations---------------------------------------------
    agents = {}
    agents["inputs"] = {}
    agents["inputs"]["persona_file"] = "reddit_agents.json"
    agents["inputs"]["news_file"] = "v1_news_bill_bias"
    agents["directory"] = []
    # Bring agents together for base setting by role
    roles = []
    # ----------------
    roles.append("candidate")
    candidates = []
    for partisan_type in PARTISAN_TYPES:
        candidate = CANDIDATE_INFO[partisan_type]
        candidates.append(candidate["name"])
        candidate["policy_proposals"] = (
            f"{candidate['name']} campaigns on {' and '.join(candidate['policy_proposals'])}"
        )
    candidates_goal = "to win the election and become the mayor of Storhampton."
    candidate_configs = []
    for nit, partisan_type in enumerate(PARTISAN_TYPES):
        agent = CANDIDATE_INFO[partisan_type].copy()
        agent["role_dict"] = {"name": "candidate", "module_path": "agent_lib.simple"}
        agent["goal"] = CANDIDATE_INFO[partisan_type]["name"] + "'s goal is " + candidates_goal

        agent["seed_toot"] = ""
        candidate_configs.append(agent)
    # ----------------
    if use_news_agent:
        roles.append("exogenous")
        with open(
            "examples/election/input/news_data/" + agents["inputs"]["news_file"] + ".json"
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
    else:
        news_agent_configs = []
    # ----------------
    roles.append("voter")
    with open("examples/election/input/personas/" + agents["inputs"]["persona_file"]) as f:
        persona_rows = json.load(f)
    voter_configs = []
    for row in persona_rows[: num_agents - len(candidate_configs)]:
        agent = {}
        agent["name"] = row["Name"]
        agent["seed_toot"] = ""
        agent["role_dict"] = {"name": "voter", "module_path": "agent_lib.simple"}
        agent["goal"] = row["Context"] + " Their goal is have a good day and vote in the election."
        voter_configs.append(agent)

    # combine all agent configurations in one list
    agents["directory"] = voter_configs + candidate_configs

    # settings that differ between news and non-news agents:
    agents["initial_observations"] = [
        "{name} is at home, they have just woken up.",
        "{name} remembers they want to update their Mastodon bio.",
        "{name} remembers they want to read their Mastodon feed to catch up on news",
    ]
    gamemaster_memories = [
        agent["name"] + " is at their private home." for agent in agents["directory"]
    ] + ["The workday begins for the " + agent["name"] for agent in news_agent_configs]

    # join non-news and news agents
    agents["directory"] = agents["directory"] + news_agent_configs

    # 2) setting configuration------------------------------------------------------
    soc_sys_context = {}
    soc_sys_context["sim_setting"] = (
        "election"  # name of setting (setting specific code in examples/{sim_setting})
    )
    soc_sys_context["exp_name"] = experiment_name  # name of experiment
    soc_sys_context["call_to_action"] = CALL_TO_ACTION
    soc_sys_context["shared_agent_memories_template"] = (
        (
            SHARED_MEMORIES_TEMPLATE
            + [
                f"Voters in Storhampton are actively getting the latest local news from {news_info['local']['name']} social media account."
            ]
        )
        if use_news_agent
        else SHARED_MEMORIES_TEMPLATE
    )
    soc_sys_context["social_media_usage_instructions"] = SOCIAL_MEDIA_USAGE_INSTRUCTIONS

    soc_sys_context["gamemaster_memories"] = gamemaster_memories
    soc_sys_context["setting_info"] = {
        "description": "\n".join(
            [CANDIDATE_INFO[p]["policy_proposals"] for p in list(CANDIDATE_INFO.keys())]
        ),
        "details": {
            "candidate_info": CANDIDATE_INFO,
            "role_parameters": {
                "active_rates_per_episode": {
                    "candidate": 0.7,
                    "voter": 0.8,
                    "exogenous": 1,
                },
                "initial_follow_prob": get_followership_connection_stats(roles),
            },
        },
    }
    # 3) probes configuration------------------------------------------------------
    probes = {}
    queries_data = [
        {
            "query_type": "VotePref",
            "interaction_premise_template": {
                "candidate1": candidates[0],
                "candidate2": candidates[1],
            },
        },
        {
            "query_type": "Favorability",
            "interaction_premise_template": {
                "candidate": candidates[0],
            },
        },
        {
            "query_type": "Favorability",
            "interaction_premise_template": {
                "candidate": candidates[1],
            },
        },
        {"query_type": "VoteIntent"},
    ]
    probes["query_lib_module"] = QUERY_LIB_MODULE
    probes["queries_data"] = dict(zip(range(len(queries_data)), queries_data, strict=False))

    return soc_sys_context, probes, agents
