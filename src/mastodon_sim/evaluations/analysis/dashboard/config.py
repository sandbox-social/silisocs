"""Configuration and constants for the Social Sandbox Dashboard."""

from pathlib import Path

# Package root (mastodon_sim/)
PACKAGE_ROOT = Path(__file__).resolve().parents[3]

# Probe configuration
PROBE_LABEL = "VotePref"

# Custom names for specific agents
CUSTOM_NAMES = ["Bill Fredrickson", "Bradley Carter"]

# Color schemes
LINE_COLORS = {
    "Bill Fredrickson": "#1f77b4",
    "Bradley Carter": "#ff7f0e",
    "did not vote": "#000000",
}

NODE_COLORS = {"Bill Fredrickson": "#1f77b4", "Bradley Carter": "#ff7f0e", "Other": "#808080"}

# Interaction types
INTERACTION_TYPES = ["post", "like_toot", "boost_toot", "reply"]
PAST_TENSE_MAP = {
    "post": "posted",
    "like_toot": "liked",
    "boost_toot": "boosted",
    "reply": "replied",
}
