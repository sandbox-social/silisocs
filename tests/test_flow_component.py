"""Unit tests for FlowComponent explicit flow-field contract."""

import pytest

from mastodon_sim.environments.gm.components.base import FlowComponent
from mastodon_sim.environments.gm.components.decorators import multi_field


class SimpleFlowComponent(FlowComponent):
    """Simple test component with multi-fields."""

    FLOW_FIELDS = {
        "timeline_filter": str,
        "action_parser": str,
    }

    def __init__(self):
        super().__init__()


class NonMultiComponent(FlowComponent):
    """Component without multi-fields."""

    def get_value(self) -> str:
        return "static"


def test_flow_component_initialization():
    """Test FlowComponent initializes with empty field values."""
    comp = SimpleFlowComponent()
    assert comp._flow_field_values == {}
    assert comp._entity_field_values == {}
    assert comp.has_multi_fields()


def test_explicit_flow_field_registration():
    """Test FLOW_FIELDS declaration registers metadata."""
    comp = SimpleFlowComponent()
    assert comp.get_multi_fields() == {
        "timeline_filter": str,
        "action_parser": str,
    }


def test_non_multi_component_has_no_fields():
    """Test component without decorators has no multi-fields."""
    comp = NonMultiComponent()
    assert not comp.has_multi_fields()
    assert comp.get_multi_fields() == {}


def test_set_and_get_multi_field_values():
    """Test setting and retrieving multi-field values."""
    comp = SimpleFlowComponent()

    entity_mapping = {
        "alice": {"timeline_filter": "trusted", "action_parser": "strict"},
        "bob": {"timeline_filter": "all", "action_parser": "lenient"},
    }

    comp.set_multi_field_values(entity_mapping)

    # Test retrieval for alice
    assert comp.get_field_for_entity("timeline_filter", "alice") == "trusted"
    assert comp.get_field_for_entity("action_parser", "alice") == "strict"

    # Test retrieval for bob
    assert comp.get_field_for_entity("timeline_filter", "bob") == "all"
    assert comp.get_field_for_entity("action_parser", "bob") == "lenient"


def test_get_field_with_missing_entity():
    """Test get_field_for_entity with unknown entity returns default."""
    comp = SimpleFlowComponent()
    comp.set_multi_field_values({"alice": {"timeline_filter": "trusted"}})

    result = comp.get_field_for_entity("timeline_filter", "unknown_entity", default="default_value")
    assert result == "default_value"


def test_get_field_without_entity_name():
    """Test get_field_for_entity without entity_name returns default."""
    comp = SimpleFlowComponent()
    comp.set_multi_field_values({"alice": {"timeline_filter": "trusted"}})

    result = comp.get_field_for_entity("timeline_filter", default="fallback")
    assert result == "fallback"


def test_setting_unknown_field_raises_value_error():
    """Test setting undeclared flow field fails fast."""
    comp = SimpleFlowComponent()
    with pytest.raises(ValueError, match="Unsupported flow field"):
        comp.set_multi_field_values({"alice": {"unknown_field": "value"}})


def test_set_multi_field_values_with_none():
    """Test set_multi_field_values handles None gracefully."""
    comp = SimpleFlowComponent()
    comp.set_multi_field_values(None)
    assert comp._entity_field_values == {}


def test_get_multi_fields_returns_copy():
    """Test get_multi_fields returns a copy, not reference."""
    comp = SimpleFlowComponent()
    original = comp.get_multi_fields()
    returned = comp.get_multi_fields()

    # Should be equal in content
    assert original == returned
    # Modifying returned shouldn't affect class
    returned["new_field"] = int
    assert "new_field" not in comp.get_multi_fields()


def test_inheritance_preserves_multi_field_metadata():
    """Test subclasses inherit and extend explicit FLOW_FIELDS."""

    class ChildComponent(SimpleFlowComponent):
        FLOW_FIELDS = {
            "score": int,
        }

    child = ChildComponent()
    # Should have all parent fields plus new one
    assert "timeline_filter" in child.get_multi_fields()
    assert "action_parser" in child.get_multi_fields()
    assert "score" in child.get_multi_fields()
    assert child.get_multi_fields()["score"] == int


def test_decorator_registration_still_supported_for_compatibility():
    """Test legacy @multi_field declarations still register."""

    class DecoratedComponent(FlowComponent):
        @property
        @multi_field(str)
        def legacy_field(self) -> str:
            return "value"

    comp = DecoratedComponent()
    assert "legacy_field" in comp.get_multi_fields()
    comp.set_multi_field_values({"default": {"legacy_field": "x"}})
    assert comp.get_field_for_entity("legacy_field", "default") == "x"


def test_multiple_instances_have_independent_field_values():
    """Test multiple component instances have independent field mappings."""
    comp1 = SimpleFlowComponent()
    comp2 = SimpleFlowComponent()

    comp1.set_multi_field_values({"alice": {"timeline_filter": "trusted"}})
    comp2.set_multi_field_values({"bob": {"timeline_filter": "all"}})

    assert comp1.get_field_for_entity("timeline_filter", "alice") == "trusted"
    assert comp2.get_field_for_entity("timeline_filter", "bob") == "all"

    # Instances don't affect each other
    assert comp1.get_field_for_entity("timeline_filter", "bob") is None
    assert comp2.get_field_for_entity("timeline_filter", "alice") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
