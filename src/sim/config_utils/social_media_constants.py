# ========= Social Media Constants ==================

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
1. POST - Share new content by creating a new toot
2. REPLY - Engage existing content (positively or negatively) by responding to it (needs toot ID)
3. BOOST - Extend the reach of content by sharing someone's toot (needs ID)
4. LIKE - Show the world you appreciate content by liking a toot (needs ID)

## INSTRUCTIONS
Determine what ONE action {name} would take next based on:
- Their character and values via self-perception and goal descriptions
- The current context and timeline
- Not repeating recent actions
If text-based, the text must reflect their posting style description.

## OUTPUT FORMAT
Answer: {name}
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
STEP 2: Chris's post about community priorities resonates with {name}'s values. They hasn't interacted with this post yet.
STEP 3: Responding to Chris would allow {name} to engage meaningfully about community values.

FINAL DECISION:
ACTION TYPE: REPLY
TARGET ID: 114204813429886778
CONTENT: "I appreciate your focus on community priorities, Chris! As an educator, I believe our growth depends on strong educational foundations alongside economic development."
REASONING: This reply allows Emily to acknowledge community values while highlighting her educational perspective, which is authentic to her character.
"""

SOCIAL_MEDIA_USAGE_INSTRUCTIONS = " \n".join(
    [
        "Mastodon is a social media application.",
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

USE_SERVER = True

SOCIAL_MEDIA_GAMEMASTER_FILENAME = "social_media"
