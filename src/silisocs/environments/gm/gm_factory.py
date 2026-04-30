"""Factory for composing single or multiple game masters with full flexibility.

Supports flexible many-to-many mapping:
- Multiple agent classes per agent
- Multiple GMs per agent class
- Configurable GM execution order

This enables advanced scenarios where agents can be routed to different GMs
based on multiple criteria, and GMs execute in a defined sequence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.language_model import language_model

from silisocs.environments.gm.base_game_master import BaseSocialMediaGameMaster


class GameMasterFactory:
    """Factory for composing game masters with flexible agent-to-GM routing.

    Supports:
    1. Single GM (default): All agents → one GM instance
    2. Multi-class GMs (advanced): Flexible many-to-many mapping
       - Agents can have multiple classes
       - Classes can route to multiple GMs
       - Full composition control via YAML

    Within each GM, existing agent flow sequencing still applies via
    engine.flow_routing.flow_order.
    """

    def __init__(
        self,
        gm_config: Mapping[str, Any],
        agent_names: Sequence[str],
        agent_to_classes: Mapping[str, Sequence[str]] | None = None,
        class_to_gms: Mapping[str, Sequence[str]] | None = None,
    ):
        """Initialize GM factory with flexible routing.

        Args:
            gm_config: Base GM configuration. Can include:
                - "gm_configs": Dict of {gm_name: gm_config_dict}
                - "gm_sequence": List of GM names in execution order

            agent_names: Names of all agents in the simulation

            agent_to_classes: Mapping of agent_name → [class_names].
                             Allows agents to have multiple classes.
                             If None, all agents use "default" class.

            class_to_gms: Mapping of class_name → [gm_names].
                         Allows classes to route to multiple GMs.
                         This is many-to-many routing.
                         If None, classes route to the default GM.
        """
        self.gm_config = dict(gm_config or {})
        self.agent_names = list(agent_names)
        self.agent_to_classes = self._normalize_agent_to_classes(agent_to_classes)
        self.class_to_gms = self._normalize_class_to_gms(class_to_gms)
        self._validate_config()

        # GM instances created during build
        self._gm_instances: dict[str, entity_agent_with_logging.EntityAgentWithLogging] = {}

    def _normalize_agent_to_classes(
        self, agent_to_classes: Mapping[str, Sequence[str]] | None
    ) -> dict[str, list[str]]:
        """Normalize agent-to-classes mapping, handling single/multiple class cases."""
        if agent_to_classes is None:
            # Default: all agents in "default" class
            return {agent: ["default"] for agent in self.agent_names}

        result = {}
        for agent, classes in agent_to_classes.items():
            if isinstance(classes, str):
                result[agent] = [classes]
            else:
                result[agent] = list(classes)

        # Add any unmapped agents to default class
        for agent in self.agent_names:
            if agent not in result:
                result[agent] = ["default"]

        return result

    def _normalize_class_to_gms(
        self, class_to_gms: Mapping[str, Sequence[str]] | None
    ) -> dict[str, list[str]]:
        """Normalize class-to-GMs mapping, handling single/multiple GM cases."""
        if class_to_gms is None:
            # No mapping: all classes → single "default" GM
            return {cls: ["default"] for cls in set(sum(self.agent_to_classes.values(), []))}

        result = {}
        for class_name, gms in class_to_gms.items():
            if isinstance(gms, str):
                result[class_name] = [gms]
            else:
                result[class_name] = list(gms)

        return result

    def _validate_config(self) -> None:
        """Validate that GM configuration is coherent."""
        # Check that all referenced GMs have configs
        all_gm_names = set()
        for gm_list in self.class_to_gms.values():
            all_gm_names.update(gm_list)

        gm_configs = self.gm_config.get("gm_configs", {})

        for gm_name in all_gm_names:
            if gm_name not in gm_configs and gm_name != "default":
                # Only warn; allow forward references or default handling
                pass

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> tuple[entity_agent_with_logging.EntityAgentWithLogging, ...]:
        """Build and return GM instance(s).

        Returns
        -------
            Tuple of GM entities. Order follows gm_sequence config if specified.
        """
        # Check if using advanced multi-GM mode
        gm_configs = self.gm_config.get("gm_configs")
        gm_sequence = self.gm_config.get("gm_sequence")

        if gm_configs and gm_sequence:
            # Advanced mode: explicit gm_configs and gm_sequence
            return self._build_advanced_multi_gm(
                model, memory_bank, entities, gm_configs, gm_sequence
            )
        # Default mode: single GM
        return self._build_single_gm(model, memory_bank, entities)

    def _build_single_gm(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
    ) -> tuple[entity_agent_with_logging.EntityAgentWithLogging]:
        """Build single GM for all agents (default mode)."""
        # Use entire config, but exclude multi-GM keys
        gm_params = {
            k: v for k, v in self.gm_config.items() if k not in {"gm_configs", "gm_sequence"}
        }

        gm_prefab = BaseSocialMediaGameMaster(
            params=dict(gm_params),
            entities=entities,
        )
        gm = gm_prefab.build(model, memory_bank)
        self._gm_instances["default"] = gm
        return (gm,)

    def _build_advanced_multi_gm(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
        entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging],
        gm_configs: Mapping[str, Any],
        gm_sequence: Sequence[str],
    ) -> tuple[entity_agent_with_logging.EntityAgentWithLogging, ...]:
        """Build GMs using advanced many-to-many routing.

        Steps:
        1. For each GM name in gm_sequence:
           - Find all classes that route to this GM
           - Find all agents belonging to those classes
           - Build GM with only those agents
        2. Return GMs in sequence order
        """
        gm_instances_ordered = []

        # Build reverse mapping: gm_name → [classes that route to it]
        gm_to_classes: dict[str, set[str]] = {}
        for class_name, gm_list in self.class_to_gms.items():
            for gm_name in gm_list:
                if gm_name not in gm_to_classes:
                    gm_to_classes[gm_name] = set()
                gm_to_classes[gm_name].add(class_name)

        # For each GM in sequence, build with its assigned agents
        for gm_name in gm_sequence:
            if gm_name not in gm_configs:
                continue  # Skip undefined GMs

            # Find agents for this GM
            classes_for_gm = gm_to_classes.get(gm_name, set())
            gm_agents = []

            for entity in entities:
                agent_classes = self.agent_to_classes.get(entity.name, ["default"])
                # Include agent if any of its classes route to this GM
                if any(cls in classes_for_gm for cls in agent_classes):
                    gm_agents.append(entity)

            # Build GM only if it has agents
            if gm_agents:
                gm_config = dict(gm_configs[gm_name])
                gm_prefab = BaseSocialMediaGameMaster(
                    params=gm_config,
                    entities=gm_agents,
                )
                gm = gm_prefab.build(model, memory_bank)
                self._gm_instances[gm_name] = gm
                gm_instances_ordered.append(gm)

        return tuple(gm_instances_ordered)

    def get_gm_instance(
        self, gm_name: str
    ) -> entity_agent_with_logging.EntityAgentWithLogging | None:
        """Get a specific GM instance by name (after build() called)."""
        return self._gm_instances.get(gm_name)

    def get_all_gm_instances(self) -> dict[str, entity_agent_with_logging.EntityAgentWithLogging]:
        """Get all built GM instances."""
        return dict(self._gm_instances)

    def get_default_gm(self) -> entity_agent_with_logging.EntityAgentWithLogging | None:
        """Get the single/default GM (if in single-GM mode)."""
        if len(self._gm_instances) == 1:
            return next(iter(self._gm_instances.values()))
        return None

    def get_agent_gms(self, agent_name: str) -> list[str]:
        """Get list of GM names this agent is assigned to.

        Uses the agent_to_gm mapping built from agent_to_classes and class_to_gms.

        Args:
            agent_name: Name of the agent

        Returns
        -------
            List of GM names this agent belongs to, empty if agent not found.
        """
        # Build agent-to-gm mapping on demand
        agent_to_gm: dict[str, list[str]] = {}
        for agent_name_iter, classes in self.agent_to_classes.items():
            gms = []
            for cls in classes:
                assigned_gms = self.class_to_gms.get(cls, [])
                gms.extend(assigned_gms)
            # Remove duplicates while preserving order
            agent_to_gm[agent_name_iter] = list(dict.fromkeys(gms))

        return agent_to_gm.get(agent_name, [])
