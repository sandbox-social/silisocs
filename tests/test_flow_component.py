"""Unit tests for FlowComponent explicit flow-field contract."""

import pytest

from mastodon_sim.environments.gm.components.base import FlowComponent


class SimpleFlowComponent(FlowComponent):
    """Simple test component with flow fields."""

    FLOW_FIELDS = {
        "timeline_filter": str,
        "action_parser": str,
    }

    def __init__(self):
        super().__init__()


class NonMultiComponent(FlowComponent):
    """Component without declared flow fields."""

    def get_value(self) -> str:
        return "static"


def test_flow_component_initialization():
    """Test FlowComponent initializes with empty field values."""
    comp = SimpleFlowComponent()
    assert comp._flow_field_values == {}
    assert comp.has_flow_fields()


def test_explicit_flow_field_registration():
    """Test FLOW_FIELDS declaration registers metadata."""
    comp = SimpleFlowComponent()
    assert comp.get_flow_fields() == {
        "timeline_filter": str,
        "action_parser": str,
    }


def test_non_multi_component_has_no_fields():
    """Test component without FLOW_FIELDS has no flow fields."""
    comp = NonMultiComponent()
    assert not comp.has_flow_fields()
    assert comp.get_flow_fields() == {}


def test_set_and_get_flow_field_values():
    """Test setting and retrieving flow field values."""
    comp = SimpleFlowComponent()

    flow_mapping = {
        "alice": {"timeline_filter": "trusted", "action_parser": "strict"},
        "bob": {"timeline_filter": "all", "action_parser": "lenient"},
    }

    comp.set_flow_field_values(flow_mapping)

    # Test retrieval for alice
    assert comp.get_flow_field("timeline_filter", "alice") == "trusted"
    assert comp.get_flow_field("action_parser", "alice") == "strict"

    # Test retrieval for bob
    assert comp.get_flow_field("timeline_filter", "bob") == "all"
    assert comp.get_flow_field("action_parser", "bob") == "lenient"


def test_get_field_with_missing_flow():
    """Test get_flow_field with unknown flow returns default."""
    comp = SimpleFlowComponent()
    comp.set_flow_field_values({"alice": {"timeline_filter": "trusted"}})

    result = comp.get_flow_field("timeline_filter", "unknown_entity", default="default_value")
    assert result == "default_value"


def test_get_field_without_flow_tag():
    """Test get_flow_field without flow_tag returns default."""
    comp = SimpleFlowComponent()
    comp.set_flow_field_values({"alice": {"timeline_filter": "trusted"}})

    result = comp.get_flow_field("timeline_filter", default="fallback")
    assert result == "fallback"


def test_setting_unknown_field_raises_value_error():
    """Test setting undeclared flow field fails fast."""
    comp = SimpleFlowComponent()
    with pytest.raises(ValueError, match="Unsupported flow field"):
        comp.set_flow_field_values({"alice": {"unknown_field": "value"}})


def test_set_flow_field_values_with_none():
    """Test set_flow_field_values handles None gracefully."""
    comp = SimpleFlowComponent()
    comp.set_flow_field_values(None)
    assert comp._flow_field_values == {}


def test_get_flow_fields_returns_copy():
    """Test get_flow_fields returns a copy, not reference."""
    comp = SimpleFlowComponent()
    original = comp.get_flow_fields()
    returned = comp.get_flow_fields()

    # Should be equal in content
    assert original == returned
    # Modifying returned shouldn't affect class
    returned["new_field"] = int
    assert "new_field" not in comp.get_flow_fields()


def test_inheritance_preserves_flow_field_metadata():
    """Test subclasses inherit and extend explicit FLOW_FIELDS."""

    class ChildComponent(SimpleFlowComponent):
        FLOW_FIELDS = {
            "score": int,
        }

    child = ChildComponent()
    # Should have all parent fields plus new one
    assert "timeline_filter" in child.get_flow_fields()
    assert "action_parser" in child.get_flow_fields()
    assert "score" in child.get_flow_fields()
    assert child.get_flow_fields()["score"] == int


def test_multiple_instances_have_independent_field_values():
    """Test multiple component instances have independent field mappings."""
    comp1 = SimpleFlowComponent()
    comp2 = SimpleFlowComponent()

    comp1.set_flow_field_values({"alice": {"timeline_filter": "trusted"}})
    comp2.set_flow_field_values({"bob": {"timeline_filter": "all"}})

    assert comp1.get_flow_field("timeline_filter", "alice") == "trusted"
    assert comp2.get_flow_field("timeline_filter", "bob") == "all"

    # Instances don't affect each other
    assert comp1.get_flow_field("timeline_filter", "bob") is None
    assert comp2.get_flow_field("timeline_filter", "alice") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
