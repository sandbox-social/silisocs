"""Election scenario simple entity — re-exports the base agent entity.

All generic agent logic now lives in ``mastodon_sim.agents.entity``.
This module re-exports ``Entity`` so that existing prefab references
(``mastodon_sim.scenarios.election.entity_lib.simple__Entity``) continue
to resolve correctly.
"""

from mastodon_sim.agents.entity import Entity

__all__ = ["Entity"]
