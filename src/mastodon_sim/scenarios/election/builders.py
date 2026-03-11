"""Election-specific agent builder (legacy).

The election scenario now uses the class-based persona pipeline in
``BaseAgentBuilder`` (configured via ``persona_pipeline.classes`` in
``election.yaml``), so no custom builder subclass is needed.

This module is kept for reference. If you need a custom builder for a new
scenario that can't be configured via YAML alone, subclass
``BaseAgentBuilder`` and override ``build_role_agents()``.
"""

from mastodon_sim.agents.builders import BaseAgentBuilder

__all__ = ["BaseAgentBuilder"]
