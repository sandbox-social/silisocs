"""Default social-media game master prefab.

This module provides a lightweight single-flow game master preset used by
the simple/easy workflow. It exposes the :class:`GameMaster` dataclass which
extends :class:`silisocs.environments.gm.base_game_master.BaseSocialMediaGameMaster`.
"""

from __future__ import annotations

import dataclasses

from silisocs.environments.gm.base_game_master import BaseSocialMediaGameMaster


@dataclasses.dataclass
class GameMaster(BaseSocialMediaGameMaster):
    """Single-flow social-media game master preset.

    The class is intentionally minimal and primarily exists to expose a
    named prefab that can be referenced in scenario configs.
    """

    description: str = "A social-media game master."
