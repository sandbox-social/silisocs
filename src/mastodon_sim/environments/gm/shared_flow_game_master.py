"""Shared-flow social-media game master prefab.

Use this GM when a single GM instance should own multiple flow tags while
sharing one backend/app state.
"""

from __future__ import annotations

import dataclasses

from mastodon_sim.environments.gm.base_game_master import BaseSocialMediaGameMaster


@dataclasses.dataclass
class GameMaster(BaseSocialMediaGameMaster):
    """Shared-flow GM preset for advanced orchestration workflows."""

    description: str = "A shared-flow social-media game master."

    def _is_shared_flow_mode(self) -> bool:
        return True
