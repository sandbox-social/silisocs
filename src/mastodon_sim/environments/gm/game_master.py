"""Simple social-media game master prefab.

This is the default GM for the easy workflow.
"""

from __future__ import annotations

import dataclasses

from mastodon_sim.environments.gm.base_game_master import BaseSocialMediaGameMaster


@dataclasses.dataclass
class GameMaster(BaseSocialMediaGameMaster):
    """Default single-flow social-media GM preset."""

    description: str = "A social-media game master."
