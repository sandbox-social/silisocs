"""Raw memory initializer — re-exports the base ``InitializerGM``.

The base ``InitializerGM.generate_memories()`` returns an empty list,
which means only config-defined shared and specific memories are injected.
That IS raw initialization — no subclassing needed.
"""

from mastodon_sim.agents.initialization.base import InitializerGM

GameMaster = InitializerGM

__all__ = ["GameMaster"]
