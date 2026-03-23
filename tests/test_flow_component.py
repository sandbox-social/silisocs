"""Unit tests for FlowComponent and multi-field decorator system."""

import pytest

from mastodon_sim.environments.gm.components.base import FlowComponent
from mastodon_sim.environments.gm.components.decorators import multi_field


class SimpleFlowComponent(FlowComponent):
    """Simple test component with multi-fields."""

    def __init__(self):
        super().__init__()
        self._config = {}

    @property
    @multi_field(str)
    def timeline_filter(self) -> str:
        """Get timeline filter."""
        return self._config.get("timeline_filter", "all")

    @property
    @multi_field(str)
    def action_parser(self) -> str:
        """Get action parser."""
        return self._config.get("action_parser", "default")


class NonMultiComponent(FlowComponent):
    """Component without multi-fields."""

    def get_value(self) -> str:
        return "static"


def test_flow_component_initialization():
    """Test FlowComponent initializes with empty field values."""
    comp = SimpleFlowComponent()
    assert comp._entity_field_values == {}
    assert comp.has_multi_fields()


def test_multi_field_metadata_registration():
    """Test @multi_field decorator registers metadata."""
    assert "timeline_filter" in SimpleFlowComponent._multi_field_metadata
    assert "action_parser" in SimpleFlowComponent._multi_field_metadata
    assert SimpleFlowComponent._multi_field_metadata["timeline_filter"] == str
    assert SimpleFlowComponent._multi_field_metadata["action_parser"] == str


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

    result = comp.get_field_for_entity(
        "timeline_filter", "unknown_entity", default="default_value"
    )
    assert result == "default_value"


def test_get_field_without_entity_name():
    """Test get_field_for_entity without entity_name returns default."""
    comp = SimpleFlowComponent()
    comp.set_multi_field_values({"alice": {"timeline_filter": "trusted"}})

    result = comp.get_field_for_entity("timeline_filter", default="fallback")
    assert result == "fallback"


def test_get_field_for_non_multi_field():
    """Test get_field_for_entity for non-multi field returns default."""
    comp = SimpleFlowComponent()
    comp.set_multi_field_values({"alice": {"unknown_field": "value"}})

    result = comp.get_field_for_entity("unknown_field", "alice", default="fallback")
    assert result == "fallback"


def test_set_multi_field_values_with_none():
    """Test set_multi_field_values handles None gracefully."""
    comp = SimpleFlowComponent()
    comp.set_multi_field_values(None)
    assert comp._entity_field_values == {}


def test_get_multi_fields_returns_copy():
    """Test get_multi_fields returns a copy, not reference."""
    original = SimpleFlowComponent.get_multi_fields(SimpleFlowComponent)
    returned = SimpleFlowComponent.get_multi_fields(SimpleFlowComponent)

    # Should be equal in content
    assert original == returned
    # Modifying returned shouldn't affect class
    returned["new_field"] = int
    assert "new_field" not in SimpleFlowComponent._multi_field_metadata


def test_inheritance_preserves_multi_field_metadata():
    """Test that subclasses inherit parent multi-field metadata."""

    class ChildComponent(SimpleFlowComponent):
        @property
        @multi_field(int)
        def score(self) -> int:
            return 0

    # Should have all parent fields plus new one
    assert "timeline_filter" in ChildComponent._multi_field_metadata
    assert "action_parser" in ChildComponent._multi_field_metadata
    assert "score" in ChildComponent._multi_field_metadata
    assert ChildComponent._multi_field_metadata["score"] == int


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
