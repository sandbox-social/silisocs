"""Configuration and constants for the analysis dashboard."""

from pathlib import Path

# Package root (silisocs/)
PACKAGE_ROOT = Path(__file__).resolve().parents[3]

# Color schemes
LINE_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b", "#17becf"]

# Interaction types
INTERACTION_TYPES = ["post", "like", "repost", "reply"]
PAST_TENSE_MAP = {
    "post": "posted",
    "like": "liked",
    "repost": "reposted",
    "reply": "replied",
    "follow": "followed",
    "unfollow": "unfollowed",
}
