"""Raw memory initializer — re-exports the base ``InitializerGM``.

The base ``InitializerGM`` already performs raw memory injection, so this
module simply re-exports it as ``GameMaster`` for backward compatibility
and config resolution (``module_path: mastodon_sim.agents.initialization.raw``).
"""

from mastodon_sim.agents.initialization.base import InitializerGM, RawMemoryInjector

# The base InitializerGM *is* the raw initializer — no subclassing needed.
GameMaster = InitializerGM

__all__ = ["GameMaster", "RawMemoryInjector"]
