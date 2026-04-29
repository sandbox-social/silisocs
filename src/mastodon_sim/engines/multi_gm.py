"""Advanced multi-GM runtime engine with external GM sequencing.

This engine orchestrates multiple game masters that execute in a defined sequence,
with clear separation between:
- GM sequencing (which GMs execute, in what order)
- Agent flow sequencing (within each GM, how agents are grouped)

Each GM independently manages its assigned agents and flow groups.
GMs execute sequentially to avoid state conflicts.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from concordia.typing import entity as entity_lib
from typing_extensions import override

from mastodon_sim.engines.base_engines import FlowRuntimeEngine
from mastodon_sim.runtime.config import ConfigStore

_LOGGER = logging.getLogger(__name__)


class MultiGMRuntimeEngine(FlowRuntimeEngine):
    """Advanced engine supporting multiple game masters with explicit sequencing.

    This engine extends FlowRuntimeEngine to support advanced scenarios where:

    1. **External GM Sequencing**: GMs execute in a configured order (gm_sequence)
       separate from agent flow sequencing (flow_order)

    2. **Many-to-Many Routing**: Agents can belong to multiple classes, classes
       can route to multiple GMs, enabling flexible composition

    3. **Clear Responsibility Separation**:
       - gm_sequence: Controls which GMs execute, and in what order
       - flow_order (within each GM): Controls agent groups within that GM
       - entity_to_flow (within each GM): Assigns agents to flows

    4. **Sequential Execution**: GMs execute one at a time to avoid conflicts
       if agents are shared across multiple GMs

    Usage in configuration:

        engine:
          class: MultiGMRuntimeEngine

        gm:
          agent_classes:
            alice: ["human"]
            bob: ["bot", "active"]

          class_to_gms:
            human: ["gm_social"]
            bot: ["gm_detection"]
            active: ["gm_analysis"]

          gm_sequence: ["gm_social", "gm_detection", "gm_analysis"]

          gm_configs:
            gm_social:
              name: "social_gm"
              components: {...}
            gm_detection:
              name: "detection_gm"
              components: {...}
            gm_analysis:
              name: "analysis_gm"
              components: {...}

        engine:
          flow_routing:
            flow_order: [pre_act, respond, post_act]
            entity_to_flow:
              alice: "pre_act"
    """

    def __init__(self, *args, **kwargs):
        """Initialize multi-GM engine."""
        super().__init__(*args, **kwargs)
        self._gm_sequence_names: list[str] | str | None = None
        self._gm_instances: dict[str, entity_lib.Entity] = {}
        self._agent_gm_map: dict[str, list[str]] = {}

    @override
    def _setup_agents_and_environment(
        self,
        environment: Any,
        initial_state: str = "",
    ) -> None:
        """Extended setup that configures GM sequencing from config."""
        # Call parent setup
        super()._setup_agents_and_environment(environment, initial_state)

        # Extract GM configuration
        cfg = ConfigStore.get_config()
        gm_config = getattr(getattr(cfg, "env", object()), "gm", {})

        # Load GM sequencing and instance mapping
        self._gm_sequence_names = getattr(gm_config, "gm_sequence", [])
        if isinstance(self._gm_sequence_names, str):
            self._gm_sequence_names = [self._gm_sequence_names]

        # Build agent-to-GM mapping from config if available
        self._build_agent_to_gm_mapping(cfg, gm_config)

        if self._gm_sequence_names:
            _LOGGER.info(
                "Multi-GM engine initialized with sequence: %s",
                " → ".join(self._gm_sequence_names),
            )

    def _build_agent_to_gm_mapping(self, cfg: Any, gm_config: Any) -> None:
        """Build mapping of agent names to their assigned GM(s).

        This is used to:
        1. Detect if agents are shared across multiple GMs
        2. Log which agents belong to which GM(s)
        3. Optionally enforce serialization for shared agents
        """
        agent_classes = getattr(gm_config, "agent_classes", {})
        class_to_gms = getattr(gm_config, "class_to_gms", {})

        # Build agent → GMs mapping
        for agent_name, classes in agent_classes.items():
            if isinstance(classes, str):
                classes = [classes]

            gms = []
            for cls in classes:
                assigned_gms = class_to_gms.get(cls, [])
                if isinstance(assigned_gms, str):
                    assigned_gms = [assigned_gms]
                gms.extend(assigned_gms)

            # Remove duplicates while preserving order
            self._agent_gm_map[agent_name] = list(dict.fromkeys(gms))

    def get_agent_gms(self, agent_name: str) -> list[str]:
        """Get list of GM names this agent is assigned to.

        Returns empty list if agent not found in mapping.
        """
        return self._agent_gm_map.get(agent_name, [])

    def detect_gm_conflicts(self) -> dict[str, list[str]]:
        """Detect agents assigned to multiple GMs.

        Returns
        -------
            Dict of agent_name → [gm_names] for agents in multiple GMs.
        """
        conflicts = {}
        for agent_name, gms in self._agent_gm_map.items():
            if len(gms) > 1:
                conflicts[agent_name] = gms
        return conflicts

    def log_orchestration_info(self) -> None:
        """Log detailed information about GM orchestration setup."""
        if not self._gm_sequence_names:
            _LOGGER.info("Engine running in single-GM mode")
            return

        _LOGGER.info("=== Multi-GM Orchestration Setup ===")
        _LOGGER.info("GM Execution Sequence: %s", " → ".join(self._gm_sequence_names))

        conflicts = self.detect_gm_conflicts()
        if conflicts:
            _LOGGER.warning(
                "Agents assigned to multiple GMs (will serialize): %s",
                {k: " + ".join(v) for k, v in conflicts.items()},
            )

        # Group agents by GM
        gm_to_agents: dict[str, list[str]] = {}
        for agent, gms in self._agent_gm_map.items():
            for gm in gms:
                if gm not in gm_to_agents:
                    gm_to_agents[gm] = []
                gm_to_agents[gm].append(agent)

        for gm_name in self._gm_sequence_names:
            agents = gm_to_agents.get(gm_name, [])
            _LOGGER.info("  %s: %s", gm_name, ", ".join(sorted(agents)))

    def validate_gm_sequence(self) -> bool:
        """Validate that gm_sequence is properly configured.

        Returns
        -------
            True if valid, False otherwise.
        """
        if self._gm_sequence_names is None:
            return True  # Single-GM mode is valid

        if isinstance(self._gm_sequence_names, str):
            _LOGGER.error("gm_sequence must be a list of GM names")
            return False

        if len(self._gm_sequence_names) == 0:
            _LOGGER.error(
                "gm_sequence is empty (must be None for single-GM mode or non-empty list)"
            )
            return False

        return True

    @override
    def run_episode(
        self,
        environment: Any,
        agents: Sequence[entity_lib.Entity],
        game_masters: Sequence[entity_lib.Entity],
        initial_state: str = "",
        max_steps: int | None = None,
    ) -> str:
        """Run simulation episode with multi-GM orchestration.

        If gm_sequence is configured, GMs execute sequentially in that order.
        Otherwise, falls back to standard FlowRuntimeEngine behavior.
        """
        # Validate configuration
        if not self.validate_gm_sequence():
            _LOGGER.error("Invalid GM sequence configuration, aborting episode")
            return ""

        # Log orchestration details
        if self._gm_sequence_names:
            self.log_orchestration_info()

        # Run episode using parent's logic
        # The parent FlowRuntimeEngine already supports multiple GMs via
        # _phase_game_masters(), _gm_sequence(), etc.
        # This class just adds explicit configuration-based orchestration.
        return super().run_episode(
            environment=environment,
            agents=agents,
            game_masters=game_masters,
            initial_state=initial_state,
            max_steps=max_steps,
        )
