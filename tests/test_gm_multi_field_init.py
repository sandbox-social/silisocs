"""Integration tests for multi-field GM initialization."""

import pytest

from mastodon_sim.environments.gm.components.base import FlowComponent
from mastodon_sim.environments.gm.components.factory import initialize_component_flow_fields


class MockObserveComponent(FlowComponent):
    """Mock observe component with multi-fields."""

    FLOW_FIELDS = {
        "timeline_filter": str,
    }

    def __init__(self):
        super().__init__()


class MockResolveComponent(FlowComponent):
    """Mock resolve component with multi-fields."""

    FLOW_FIELDS = {
        "action_parser": str,
    }

    def __init__(self):
        super().__init__()


class NormalComponent:
    """Non-FlowComponent for comparison."""

    def get_value(self):
        return "static"


def test_initialize_component_flow_fields_with_flows():
    """Test initializing flow fields from component config."""
    component = MockObserveComponent()
    config = {
        "built_in": "timeline_every_turn",
        "flows": {
            "alice": {"timeline_filter": "trusted"},
            "bob": {"timeline_filter": "all"},
            "charlie": {"timeline_filter": "verified"},
        },
    }

    initialize_component_flow_fields(component, config)

    assert component.get_flow_field("timeline_filter", "alice") == "trusted"
    assert component.get_flow_field("timeline_filter", "bob") == "all"
    assert component.get_flow_field("timeline_filter", "charlie") == "verified"


def test_initialize_component_flow_fields_without_flows():
    """Test component with config but no flows section."""
    component = MockObserveComponent()
    config = {
        "built_in": "timeline_every_turn",
        "params": {"some_param": "value"},
    }

    # Should not fail; component should remain unchanged
    initialize_component_flow_fields(component, config)
    assert component.get_flow_field("timeline_filter", "alice") is None


def test_initialize_component_flow_fields_with_none_config():
    """Test component with None config."""
    component = MockObserveComponent()

    # Should not fail
    initialize_component_flow_fields(component, None)
    assert component.get_flow_field("timeline_filter", "alice") is None


def test_initialize_non_flow_component():
    """Test initializing non-FlowComponent ignores multi-field config."""
    component = NormalComponent()
    config = {
        "built_in": "something",
        "flows": {"alice": {"field": "value"}},
    }

    # Should not fail or raise any errors
    initialize_component_flow_fields(component, config)


def test_multiple_fields_on_component():
    """Test component with multiple multi-fields."""

    class MultiFieldComponent(FlowComponent):
        FLOW_FIELDS = {
            "timeline_filter": str,
            "action_parser": str,
        }

        def __init__(self):
            super().__init__()

    component = MultiFieldComponent()
    config = {
        "flows": {
            "alice": {"timeline_filter": "trusted", "action_parser": "strict"},
            "bob": {"timeline_filter": "all", "action_parser": "lenient"},
        },
    }

    initialize_component_flow_fields(component, config)

    # Both fields should be initialized correctly
    assert component.get_flow_field("timeline_filter", "alice") == "trusted"
    assert component.get_flow_field("action_parser", "alice") == "strict"
    assert component.get_flow_field("timeline_filter", "bob") == "all"
    assert component.get_flow_field("action_parser", "bob") == "lenient"


def test_flow_config_partial_fields():
    """Test flow config with partial field values."""

    class MultiFieldComponent(FlowComponent):
        FLOW_FIELDS = {
            "field_a": str,
            "field_b": str,
        }

        def __init__(self):
            super().__init__()

    component = MultiFieldComponent()
    config = {
        "flows": {
            "alice": {"field_a": "modified_a"},  # Only field_a
            "bob": {"field_b": "modified_b"},  # Only field_b
        },
    }

    initialize_component_flow_fields(component, config)

    # Alice should have field_a set, but field_b as default
    assert component.get_flow_field("field_a", "alice") == "modified_a"
    assert component.get_flow_field("field_b", "alice") is None

    # Bob should have field_b set, but field_a as default
    assert component.get_flow_field("field_a", "bob") is None
    assert component.get_flow_field("field_b", "bob") == "modified_b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
