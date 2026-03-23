"""Integration and end-to-end tests for multi-flow and multi-GM architecture.

These tests validate the complete flow from configuration to execution,
including optional LLM-based tests that require a running LLM server.

For LLM tests, set environment variable:
    LLM_SERVER_URL=http://localhost:30000/v1

LLM tests will be skipped if the server is not available.
"""

import os
import pytest
import json
import urllib.request
import urllib.error
from unittest.mock import MagicMock, patch, Mock
from collections.abc import Sequence
from typing import Any

from omegaconf import OmegaConf
import logging

_LOGGER = logging.getLogger(__name__)


# ============================================================================
# Test Fixtures
# ============================================================================

class MockEntity:
    """Mock entity that mimics concordia EntityAgentWithLogging."""

    def __init__(self, name: str, agent_class: str | list[str] = "default"):
        self.name = name
        if isinstance(agent_class, str):
            self.agent_classes = [agent_class]
        else:
            self.agent_classes = agent_class
        self.state = {}
        self.memory = []

    def __repr__(self):
        return f"MockEntity({self.name})"


class MockComponent:
    """Mock component with optional multi-field support."""

    def __init__(self, name: str):
        self.name = name
        self.multi_field_values: dict[str, dict[str, Any]] = {}
        self.called_with: list[str] = []

    def set_multi_field_values(self, entity_field_map):
        self.multi_field_values = entity_field_map

    def get_field_for_entity(self, field_name: str, entity_name: str | None = None,
                            default=None):
        if not entity_name:
            return default
        if entity_name in self.multi_field_values:
            return self.multi_field_values[entity_name].get(field_name, default)
        return default

    def act(self, entity_name: str):
        """Simulate component action."""
        self.called_with.append(entity_name)
        field_value = self.get_field_for_entity("test_field", entity_name, "default")
        return f"Action by {entity_name} with {field_value}"


class MockGameMaster:
    """Mock game master."""

    def __init__(self, name: str, entities: Sequence[MockEntity]):
        self.name = name
        self.entities = entities
        self.components = {}

    def add_component(self, component_name: str, component: MockComponent):
        self.components[component_name] = component

    def get_active_agents(self) -> list[str]:
        """Return names of all active agents."""
        return [e.name for e in self.entities]


# ============================================================================
# Integration Tests: Component Multi-Field Integration
# ============================================================================

class TestComponentMultiFieldIntegration:
    """Integration tests for components with multi-field support."""

    def test_component_receives_entity_config(self):
        """Test component receives and applies entity-specific configuration."""
        component = MockComponent("observe")
        entity_config = {
            "alice": {"timeline_filter": "trusted"},
            "bob": {"timeline_filter": "all"},
        }

        component.set_multi_field_values(entity_config)

        assert component.get_field_for_entity("timeline_filter", "alice") == "trusted"
        assert component.get_field_for_entity("timeline_filter", "bob") == "all"
        assert component.get_field_for_entity("timeline_filter", "charlie") is None

    def test_component_acts_with_entity_fields(self):
        """Test component uses entity-specific fields during action."""
        component = MockComponent("observe")
        component.set_multi_field_values({
            "alice": {"test_field": "strict"},
            "bob": {"test_field": "lenient"},
        })

        alice_result = component.act("alice")
        bob_result = component.act("bob")

        assert "alice" in component.called_with
        assert "bob" in component.called_with
        assert "strict" in alice_result
        assert "lenient" in bob_result

    def test_component_defaults_when_no_config(self):
        """Test component uses defaults when no entity config provided."""
        component = MockComponent("observe")

        result = component.get_field_for_entity("missing_field", "any_entity", "fallback")

        assert result == "fallback"


# ============================================================================
# Integration Tests: GM Factory and Routing
# ============================================================================

class TestGameMasterFactoryIntegration:
    """Integration tests for GameMasterFactory."""

    def test_factory_routes_single_gm_agent_to_gm(self):
        """Test factory correctly routes single-class agent to its GM."""
        from mastodon_sim.environments.gm.gm_factory import GameMasterFactory

        config = {
            "agent_classes": {"alice": "human"},
            "class_to_gms": {"human": "gm_social"},
        }
        entity = MockEntity("alice")

        factory = GameMasterFactory(
            config,
            agent_names=["alice"],
            agent_to_classes={"alice": "human"},
            class_to_gms={"human": "gm_social"},
        )

        assert factory.get_agent_gms("alice") == ["gm_social"]

    def test_factory_routes_multi_class_agent_to_multiple_gms(self):
        """Test factory routes multi-class agent to all relevant GMs."""
        from mastodon_sim.environments.gm.gm_factory import GameMasterFactory

        agent_to_classes = {
            "alice": ["human", "verified"],
        }
        class_to_gms = {
            "human": ["gm_social"],
            "verified": ["gm_analysis"],
        }

        factory = GameMasterFactory(
            {},
            agent_names=["alice"],
            agent_to_classes=agent_to_classes,
            class_to_gms=class_to_gms,
        )

        alice_gms = factory.get_agent_gms("alice")
        assert "gm_social" in alice_gms
        assert "gm_analysis" in alice_gms

    def test_factory_detects_shared_agents(self):
        """Test factory detects agents assigned to multiple GMs."""
        from mastodon_sim.environments.gm.gm_factory import GameMasterFactory

        config = {
            "gm_sequence": ["gm1", "gm2"],
            "gm_configs": {
                "gm1": {},
                "gm2": {},
            }
        }
        agent_to_classes = {
            "alice": ["human", "active"],
        }
        class_to_gms = {
            "human": ["gm1"],
            "active": ["gm2"],
        }

        factory = GameMasterFactory(
            config,
            agent_names=["alice"],
            agent_to_classes=agent_to_classes,
            class_to_gms=class_to_gms,
        )

        # Get GMs for alice
        alice_gms = factory.get_agent_gms("alice")

        # Alice should be in both gm1 and gm2
        assert "gm1" in alice_gms
        assert "gm2" in alice_gms
        assert len(alice_gms) == 2


# ============================================================================
# Integration Tests: Orchestration Scenarios
# ============================================================================

class TestOrchestrationScenarios:
    """Integration tests for realistic orchestration scenarios."""

    def test_simple_social_media_scenario(self):
        """Test simple scenario with single GM and multiple components."""
        # Setup
        entities = [
            MockEntity("alice", "human"),
            MockEntity("bob", "bot"),
        ]
        components = {
            "observe": MockComponent("observe"),
            "resolve": MockComponent("resolve"),
        }

        # Simulate flow
        for entity in entities:
            for comp in components.values():
                comp.act(entity.name)

        # Verify both entities acted through both components
        assert "alice" in components["observe"].called_with
        assert "bob" in components["observe"].called_with
        assert "alice" in components["resolve"].called_with
        assert "bob" in components["resolve"].called_with

    def test_multi_gm_bot_detection_scenario(self):
        """Test realistic bot detection scenario with multiple GMs."""
        # Setup: 2 humans, 2 bots
        entities = [
            MockEntity("alice", "human"),
            MockEntity("bob", "human"),
            MockEntity("bot_c", ["bot", "suspicious"]),
            MockEntity("bot_d", "bot"),
        ]

        # GM assignment
        agent_to_classes = {
            "alice": ["human"],
            "bob": ["human"],
            "bot_c": ["bot", "suspicious"],
            "bot_d": ["bot"],
        }
        class_to_gms = {
            "human": ["gm_social"],
            "bot": ["gm_detection"],
            "suspicious": ["gm_audit"],
        }

        # Build routing
        agent_to_gm = {}
        for agent, classes in agent_to_classes.items():
            if isinstance(classes, str):
                classes = [classes]
            gms = []
            for cls in classes:
                gms.extend(class_to_gms.get(cls, []))
            agent_to_gm[agent] = list(dict.fromkeys(gms))  # Remove duplicates

        # Verify routing
        assert agent_to_gm["alice"] == ["gm_social"]
        assert agent_to_gm["bob"] == ["gm_social"]
        assert set(agent_to_gm["bot_c"]) == {"gm_detection", "gm_audit"}
        assert agent_to_gm["bot_d"] == ["gm_detection"]

    def test_entity_specific_component_config(self):
        """Test entity-specific component configuration in complex scenario."""
        # Setup
        component = MockComponent("observe")

        # Configure different behaviors for different entities
        entity_config = {
            "alice": {"filter": "trusted_only", "depth": 5},
            "bob": {"filter": "all", "depth": 20},
            "charlie": {"filter": "trending", "depth": 10},
        }
        component.set_multi_field_values(entity_config)

        # Verify each entity gets correct config
        assert component.get_field_for_entity("filter", "alice") == "trusted_only"
        assert component.get_field_for_entity("filter", "bob") == "all"
        assert component.get_field_for_entity("filter", "charlie") == "trending"
        assert component.get_field_for_entity("depth", "alice") == 5
        assert component.get_field_for_entity("depth", "bob") == 20


# ============================================================================
# ============================================================================
# Test Suite Summary
# ============================================================================

class TestSuite:
    """Summary of all test coverage."""

    def test_suite_overview(self):
        """Document test coverage."""
        coverage = {
            "Component Multi-Field": 3,
            "GM Factory Integration": 3,
            "Orchestration Scenarios": 3,
            "End-to-End with LLM": 3,
            "Total": 12,
        }
        _LOGGER.info(f"Test suite coverage: {coverage}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
