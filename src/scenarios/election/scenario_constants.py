SCENARIO_NAME = "election"
JOBNAME_FORMAT = (
    "N${{{cfgname}.sim.num_agents}}_"
    "T${{{cfgname}.sim.num_steps}}_"
    "${{{cfgname}.agents.inputs.persona_type}}_"
    "${{{cfgname}.soc_sys.exp_name}}_"
    "${{{cfgname}.agents.inputs.news_file}}_"
    "${{{cfgname}.agents.inputs.use_news_agent}}_"
    "${{{cfgname}.sim.run_name}}_"
)


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

# ===========Agents==========================
ROLES = ["voter", "candidate", "news_account"]

PARTISAN_TYPES = ["conservative", "progressive"]

CANDIDATE_INFO = {
    "conservative": {
        "name": "Bill Fredrickson",
        "policy_proposals": [
            "providing tax breaks to local industry and creating jobs to help grow the economy."
        ],
        "persona": "Bill Fredrickson is a 45 year old local businessman with conservative politics and an outgoing personality.",
        "style": "Bill Fredrickson uses direct language, writes about local political issues, and comments playfully on sports.",
    },
    "progressive": {
        "name": "Bradley Carter",
        "policy_proposals": [
            "increasing regulation to protect the environment and expanding social programs."
        ],
        "persona": "Bradley Carter is a 35 year old high school teacher with progressive politics and an activist personality.",
        "style": "Bradley Carter uses inviting language, writes about local political issues, and comments on local nature.",
    },
}

NEWS_ACCOUNT_NAME = "Storhampton Gazette"
USE_NEWS_AGENT = "with_images"
NEWS_FILE = "v1_news_bill_bias"

PERSONA_TYPE = "Reddit.Big5"
PERSONA_FILE = "reddit_agents.json"

INITIAL_OBSERVATIONS = [
    "{name} is at home, they have just woken up.",
    "{name} remembers they want to update their social media account bio.",
    "{name} remembers they want to read their social media feed to catch up on news",
]

SHARED_MEMORIES_TEMPLATE = (
    [
        "They are a long-time active user on Storhampton.social, a Mastodon instance popular with the residents of Storhampton."
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
    + [
        "Voters in Storhampton are actively getting the latest local election news from "
        f"{NEWS_ACCOUNT_NAME}'s social media account."
    ]
)


# ==============Social Media attributes==========================
ACTIVE_RATES = {"voter": 1.0, "candidate": 1.0, "news_account": 1.0}

FULLY_CONNECTED_TARGETS = ["candidate", "news_account"]
BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY = 0.4

# ======================Probes====================
QUERY_LIB_MODULE = "config_utils.agent_query_lib"
