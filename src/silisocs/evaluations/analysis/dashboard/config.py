"""Configuration and constants for the analysis dashboard."""

from pathlib import Path

# Package root (silisocs/)
PACKAGE_ROOT = Path(__file__).resolve().parents[3]

# Color schemes
LINE_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b", "#17becf"]

# Interaction labels the dashboard understands, across microblog and forum
# backends: twitter_like emits post/like/repost/reply; reddit_like emits
# post/comment/upvote/downvote. Kept backend-agnostic so a Reddit run is not blank.
INTERACTION_TYPES = ["post", "like", "repost", "reply", "comment", "upvote", "downvote"]

# Semantic groups used to route each interaction:
#   ROOT_TEXT   — authored a top-level post (the source is the post owner)
#   REPLY       — authored a reply/comment on a parent post
#   REACTION    — reacted to an existing post (edge points at the post's owner)
ROOT_TEXT_LABELS = {"post"}
REPLY_LABELS = {"reply", "comment"}
TEXT_LABELS = ROOT_TEXT_LABELS | REPLY_LABELS
REACTION_LABELS = {"like", "repost", "upvote", "downvote"}

PAST_TENSE_MAP = {
    "post": "posted",
    "like": "liked",
    "repost": "reposted",
    "reply": "replied",
    "comment": "commented",
    "upvote": "upvoted",
    "downvote": "downvoted",
    "follow": "followed",
    "unfollow": "unfollowed",
}


def past_tense(label: str) -> str:
    """Human-readable past tense for a label, tolerating unknown labels."""
    return PAST_TENSE_MAP.get(label, str(label))
