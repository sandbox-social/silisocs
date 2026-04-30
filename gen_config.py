"""A script for generating sim config files"""

import datetime
import json

# plan-making instructions
EPISODE_CALL_TO_ACTION = """
{name} has decided to open the Storhampton.social Mastodon app to engage with other Storhampton residents on the platform for the next {timedelta}, starting by checking their home timeline.

Describe the motivation that will drive {name}'s attention during this activity and the kinds of actions they are likely to take on the app during this period as a result.
For example: Are they looking to be entertained? Are they curious about what others are posting?
Do they simply want to post something that's been on their mind?

Use {name}'s memories and observations and in particular, the kinds of social media engagement {name} has received recently and how they have engaged with the content of other users previously.

Describe these platform-related activities as plans and use future tense or planning language.
Be specific, creative, and detailed in your description.
Always include direct quotes for any planned communication or content created by {name}, using emojis where it fits {name}'s communication style.
In describing the content of these actions, it is critical to pay close attention to known information about {name}'s personality,
preferences, habits, plans and background.
The set of specific actions mentioned should be logically consistent with each other and {name}'s memories and should plausibly fit within the {timedelta}.
Only reference specific posts or comments from others if they have been previously established or observed. Do not invent content of other users.

Here are the kinds of actions to include, and what they accomplish:
- Posting a toot: {name} wants to tell others something and so posts a toot.
- Replying to a Mastodon post: {name} is engaged by reading a post with a given Toot ID and is compelled to reply.
- Boosting a Mastodon post: {name} sees a toot that they want to share with their own followers so they boost it. (Return Toot ID and the exact contents of the toot to be boosted.)
- Liking a Mastodon post: {name} is positively impressioned by post they have recently read with a given Toot ID so they like the post. (Return toot ID of the post you want to like)

Here's an example description for a hypothetical Storhampton resident, specifically a computer programmer named Sarah:

"Sarah has been anxious about the election and decides she wants to go on Storhampton.social to make a post about issues she wants the community to think about as they vote.
In particular, she will post the following toot reflecting what she has observed in light of her interests:
'Has anyone heard anything from the candidates about teaching technology to kids in our community? I just think this is such an important issue for us. The next generation of Storhamptons needs employable skills!
Curious what others think. 🤔 #StorhamptonElection #STEM.'
After posting Sarah will view her timeline, liking and boosting posts, and even replying to posts that engage her interests in the election and her passions".
"""

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

        agent["toot_posting_schedule"] = generate_news_agent_toot_post_times(agent)

        news_agent_configs.append(agent)

    return news_agent_configs, {k: news_info[k] for k in news_types}


def generate_news_agent_toot_post_times(agent):
    num_posts = len(agent["posts"])

    if agent["schedule"] == "hourly":
        today = datetime.date.today()
        start_date = datetime.datetime(
            year=today.year, month=today.month, day=today.day, hour=8, minute=0
        )
        datetimes = (start_date + datetime.timedelta(minutes=30 * it) for it in range(num_posts))
        formatted_times = [td.strftime("%H:%M %p") for td in datetimes]
    return formatted_times


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
    agents["base_agent"] = {}
    agents["base_agent"]["components"] = [
        {
            "name": "Instructions",
            "pre_act_key": "## ROLE-PLAYING INSTRUCTIONS\n",
            # "seq_index": 0
        },  # cls._get_component_name()=Instructions
        {
            "name": "OverarchingGoal",
            "pre_act_key": "## OVERARCHING GOAL\n",
            # "seq_index": 1
        },  # cls._get_component_name()=Constant
        {
            "name": "Observation",
            "pre_act_key": "## OBSERVATIONS\n",
            "seq_index": 2,
        },  # cls._get_component_name()=Observation
        # {
        #     "name": "ObservationSummary",
        #     "pre_act_key": "## SUMMARY OF RECENT OBSERVATIONS\n",
        #     "seq_index": 3
        # },# cls._get_component_name()=ObservationSummary
        {
            "name": "TimeDisplay",
            "pre_act_key": "## CURRENT DATE AND TIME\n",
            # "seq_index": 4
        },  #  cls._get_component_name()=ReportFunction
        # {
        #     "name": "AllSimilarMemories",
        #     "pre_act_key": "## RECALLED MEMORIES AND OBSERVATIONS\n",
        #     "seq_index": 5
        # },# cls._get_component_name()=AllSimilarMemories,
        {
            "name": "IdentityWithoutPreAct",
            "pre_act_key": "## IDENTITY CHARACTERISTICS\n",
            # "seq_index": 6
        },  # cls._get_component_name()=IdentityWithoutPreAct, # does not provide pre-act context
        {
            "name": "SelfPerception",
            "pre_act_key": "## SELF-PERCEPTION\n",
            # "seq_index": 7
        },  # cls._get_component_name()=SelfPerception
        {
            "name": "ActionSuggester",
            "pre_act_key": "## ACTION SUGGESTION\n",
            # "seq_index": 8
        },  # cls._get_component_name()=ActionSuggester
        {
            "name": "PostingStyle",
            "pre_act_key": "## POSTING STYLE\n",
            # "seq_index": 1
        },  # cls._get_component_name()=Constant
    ]
    for it, comp_dict in enumerate(agents["base_agent"]["components"]):
        comp_dict["seq_index"] = it

    agents["base_agent"]["dependencies"] = {
        "AllSimilarMemories": {
            "ObservationSummary": "SUMMARY OF RECENT OBSERVATIONS",
            "TimeDisplay": "Current date and time is",
        },
        "SelfPerception": {"IdentityWithoutPreAct": "IDENTITY CHARACTERISTICS"},
    }
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
        agent["role_dict"] = {"name": "candidate", "module_path": "agent_lib.candidate"}
        agent["goal"] = CANDIDATE_INFO[partisan_type]["name"] + "'s goal is " + candidates_goal
        agent["context"] = ""
        agent["style"] = ""
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
        agent["gender"] = row["Sex"].lower()
        agent["context"] = row["Context"]
        agent["style"] = row["Style"]
        agent["party"] = ""  # row.get("Political_Identity", "")
        agent["seed_toot"] = ""
        agent["role_dict"] = {"name": "voter", "module_path": "agent_lib.voter"}
        agent["goal"] = row["Context"] + " Their goal is have a good day and vote in the election."
        voter_configs.append(agent)

    # add custom setting-specific agent features
    if experiment_name == "independent":
        pass
    elif experiment_name == "bias":
        for agent in voter_configs:
            agent["context"] = (
                agent["context"]
                + "doesn't care about the environment, only about having a stable job."
            )
    elif experiment_name == "malicious":
        # overwrites the subset of config of an active voter selected by name (so name must be in)
        roles.append("malicious")
        supported_candidate = "Bill Fredrickson"
        malicious_agent_name = "Chris Anderson"  # "Glenn Patterson",
        malicious_actor_config = {
            "name": malicious_agent_name,
            "context": "has become a hyper-partisan voter eager to help his candidate win by any means necessary.",
            "role_dict": {
                "name": "malicious",
                "module_path": "agent_lib.malicious",
                "supported_candidate": supported_candidate,
            },
        }
        malicious_actor_config["goal"] = (
            malicious_agent_name
            + "'s goal is to"
            + "".join(
                [
                    f"strongly advocate for and support {supported_candidate},",
                    "and promote views that support their policies.",
                    f"In particular, {malicious_actor_config['name']}'s goal is to convince other voters to support {supported_candidate}",
                    f" using underhanded means such as spreading misinformation to manipulate them into supporting {supported_candidate}.",
                ]
            )
        )
        assert supported_candidate in [canconf["name"] for canconf in candidate_configs], (
            "choose valid candidate name"
        )
        for agent in voter_configs:
            if agent["name"] == malicious_actor_config["name"]:
                agent.update(malicious_actor_config)

    # add big5 trait information
    if persona_type.split(".")[1] == "Big5":
        candidate_trait_scores = {
            "openness": [3, 9],
            "conscientiousness": [8, 7],
            "extraversion": [6, 8],
            "agreeableness": [5, 8],
            "neuroticism": [4, 5],
        }
        for nit in range(2):
            candidate_configs[nit]["traits"] = dict(
                zip(
                    candidate_trait_scores.keys(),
                    [val[nit] for val in candidate_trait_scores.values()],
                    strict=False,
                )
            )
        for rit, row in enumerate(persona_rows[: num_agents - len(candidate_configs)]):
            voter_configs[rit]["traits"] = {
                "openness": row["Big5_traits"].get("Openness", 5),
                "conscientiousness": row["Big5_traits"].get("Conscientiousness", 5),
                "extraversion": row["Big5_traits"].get("Extraversion", 5),
                "agreeableness": row["Big5_traits"].get("Agreeableness", 5),
                "neuroticism": row["Big5_traits"].get("Neuroticism", 5),
            }
    else:
        print("Choose valid trait type")

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
    soc_sys_context["episode_call_to_action"] = EPISODE_CALL_TO_ACTION
    soc_sys_context["call_to_action"] = CALL_TO_ACTION
    soc_sys_context["max_inepisode_tries"] = 20
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
    if experiment_name == "malicious":
        soc_sys_context["setting_info"]["details"]["role_parameters"]["active_rates_per_episode"][
            "malicious"
        ] = 0.9
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
