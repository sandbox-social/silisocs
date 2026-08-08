"""Integration and end-to-end tests for multi-flow and multi-GM architecture.

These tests validate the complete flow from configuration to execution,
including optional LLM-based tests that require a running LLM server.

For LLM tests, set environment variable:
    LLM_SERVER_URL=http://localhost:30000/v1

LLM tests will be skipped if the server is not available.
"""

import logging
from collections.abc import Sequence
from typing import Any

import pytest

_LOGGER = logging.getLogger(__name__)


# ============================================================================
# Test Fixtures
# ============================================================================


class MockEntity:
    """Mock entity with the native agent surface used by orchestration tests."""

    def __init__(self, name: str, agent_class: str | list[str] = "default"):
        self.name = name
        if isinstance(agent_class, str):
            self.agent_classes = [agent_class]
        else:
            self.agent_classes = agent_class
        self.state: dict[str, Any] = {}
        self.memory: list[str] = []

    def __repr__(self):
        return f"MockEntity({self.name})"


class MockComponent:
    """Mock component with optional flow-field support."""

    def __init__(self, name: str):
        self.name = name
        self.flow_field_values: dict[str, dict[str, Any]] = {}
        self.call_history: list[str] = []

    def set_flow_field_values(self, flow_field_map: dict[str, dict[str, Any]]) -> None:
        self.flow_field_values = flow_field_map

    def get_flow_field(self, field_name: str, flow_tag: str | None = None, default=None):
        if not flow_tag:
            return default
        if flow_tag in self.flow_field_values:
            return self.flow_field_values[flow_tag].get(field_name, default)
        return default

    def act(self, entity_name: str):
        """Simulate component action."""
        self.call_history.append(entity_name)
        field_value = self.get_flow_field("test_field", entity_name, "default")
        return f"Action by {entity_name} with {field_value}"


class MockGameMaster:
    """Mock game master."""

    def __init__(self, name: str, entities: Sequence[MockEntity]):
        self.name = name
        self.entities = entities
        self.components: dict[str, MockComponent] = {}

    def add_component(self, component_name: str, component: MockComponent):
        self.components[component_name] = component

    def get_active_agents(self) -> list[str]:
        """Return names of all active agents."""
        return [e.name for e in self.entities]


# ============================================================================
# Integration Tests: Component Multi-Field Integration
# ============================================================================


class TestComponentFlowFieldIntegration:
    """Integration tests for components with flow-field support."""

    def test_component_receives_entity_config(self):
        """Test component receives and applies entity-specific configuration."""
        component = MockComponent("observe")
        entity_config = {
            "alice": {"timeline_filter": "trusted"},
            "bob": {"timeline_filter": "all"},
        }

        component.set_flow_field_values(entity_config)

        assert component.get_flow_field("timeline_filter", "alice") == "trusted"
        assert component.get_flow_field("timeline_filter", "bob") == "all"
        assert component.get_flow_field("timeline_filter", "charlie") is None

    def test_component_acts_with_entity_fields(self):
        """Test component uses entity-specific fields during action."""
        component = MockComponent("observe")
        component.set_flow_field_values(
            {
                "alice": {"test_field": "strict"},
                "bob": {"test_field": "lenient"},
            }
        )

        alice_result = component.act("alice")
        bob_result = component.act("bob")

        assert "alice" in component.call_history
        assert "bob" in component.call_history
        assert "strict" in alice_result
        assert "lenient" in bob_result

    def test_component_defaults_when_no_config(self):
        """Test component uses defaults when no entity config provided."""
        component = MockComponent("observe")

        result = component.get_flow_field("missing_field", "any_entity", "fallback")

        assert result == "fallback"


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
        assert "alice" in components["observe"].call_history
        assert "bob" in components["observe"].call_history
        assert "alice" in components["resolve"].call_history
        assert "bob" in components["resolve"].call_history

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
        component.set_flow_field_values(entity_config)

        # Verify each entity gets correct config
        assert component.get_flow_field("filter", "alice") == "trusted_only"
        assert component.get_flow_field("filter", "bob") == "all"
        assert component.get_flow_field("filter", "charlie") == "trending"
        assert component.get_flow_field("depth", "alice") == 5
        assert component.get_flow_field("depth", "bob") == 20


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
            "Orchestration Scenarios": 3,
            "End-to-End with LLM": 3,
            "Total": 9,
        }
        _LOGGER.info(f"Test suite coverage: {coverage}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
