SCENARIO_NAME = "election"

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

PARTISAN_TYPES = ["conservative", "progressive"]

CANDIDATE_INFO = {
    "conservative": {
        "name": "Bill Fredrickson",
        "gender": "male",
        "policy_proposals": "providing tax breaks to local industry and creating jobs to help grow the economy.",
    },
    "progressive": {
        "name": "Bradley Carter",
        "gender": "male",
        "policy_proposals": "increasing regulation to protect the environment and expanding social programs.",
    },
}

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

USE_SERVER = False

QUERY_LIB_MODULE = "config_utils.agent_query_lib"

BASE_FOLLOWERSHIP_CONNECTION_PROBABILITY = 0.4

SOCIAL_MEDIA_GAMEMASTER_FILENAME = "social_media"
