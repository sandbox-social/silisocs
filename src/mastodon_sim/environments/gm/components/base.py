"""Base interfaces for configurable game-master components."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any


class BackendInitializer(ABC):
    """Initialize backend app state for a simulation run."""

    @abstractmethod
    def initialize(
        self,
        *,
        sm_app: Any,
        agent_names: Sequence[str],
        init_kwargs: Mapping[str, Any],
    ) -> None:
        """Initialize backend runtime state for the provided agent set."""
        raise NotImplementedError


class FlowComponent:
    """Mixin for flow-aware components with multi-entity field routing support.

    This class provides optional multi-field routing capabilities, allowing components
    to receive different field values for different entities. Components that support
    multi-fields are initialized with entity-specific field mappings via
    set_multi_field_values().

    Typically used with components that receive the active entity name and need
    to dispatch to different field values per entity (e.g., observe, resolve).
    Components that don't use entity context (e.g., next_acting) don't use this.

    Multi-fields are declared via the @multi_field decorator:
        class MyComponent(FlowComponent, SomeContextComponent):
            @multi_field(str)
            def my_field(self): ...

    At GM initialization time, multi-field components are provided with a mapping:
        component.set_multi_field_values({
            'entity1': {'my_field': 'value1'},
            'entity2': {'my_field': 'value2'},
        })

    When the component is called with entity context, it can retrieve values via:
        value = self.get_field_for_entity('my_field', entity_name)
    """

    _multi_field_metadata: dict[str, type] = {}  # field_name -> field_type

    def __init__(self) -> None:
        """Initialize component with empty field value mapping."""
        self._entity_field_values: dict[str, dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs):
        """Auto-register multi-field metadata from decorator."""
        super().__init_subclass__(**kwargs)

        # Inherit parent's metadata
        parent_meta = {}
        for base in cls.__bases__:
            if hasattr(base, '_multi_field_metadata'):
                parent_meta.update(base._multi_field_metadata)

        cls._multi_field_metadata = dict(parent_meta)

        # Scan class __dict__ for marked fields (not inherited)
        for attr_name, attr_value in cls.__dict__.items():
            # Check if it's directly marked (method)
            if hasattr(attr_value, '_is_multi_field') and attr_value._is_multi_field:
                field_type = getattr(attr_value, '_multi_field_type', Any)
                cls._multi_field_metadata[attr_name] = field_type
            # Check if it's a property wrapping a marked function
            elif isinstance(attr_value, property):
                func = attr_value.fget
                if func and hasattr(func, '_is_multi_field') and func._is_multi_field:
                    field_type = getattr(func, '_multi_field_type', Any)
                    cls._multi_field_metadata[attr_name] = field_type

    def set_multi_field_values(
        self, entity_field_map: dict[str, dict[str, Any]]
    ) -> None:
        """Set multi-entity field values for routing.

        Args:
            entity_field_map: Mapping of entity_name -> {field_name: field_value}
                Example: {'alice': {'timeline_filter': 'trusted'},
                         'bob': {'timeline_filter': 'all'}}
        """
        self._entity_field_values = entity_field_map or {}

    def get_field_for_entity(
        self, field_name: str, entity_name: str | None = None, default: Any = None
    ) -> Any:
        """Get field value for entity, with multi-field routing support.

        If field has multi-entity values and entity_name is provided, returns
        the entity-specific value. Otherwise returns the default.

        Args:
            field_name: Name of the field to retrieve
            entity_name: Name of the entity (optional)
            default: Default value if field not found

        Returns:
            Entity-specific field value if available, else default
        """
        if not entity_name or field_name not in self._multi_field_metadata:
            return default

        return (
            self._entity_field_values.get(entity_name, {}).get(field_name, default)
        )

    def has_multi_fields(self) -> bool:
        """Check if this component has any multi-field declarations."""
        return bool(self._multi_field_metadata)

    def get_multi_fields(self) -> dict[str, type]:
        """Get all declared multi-fields and their types."""
        return dict(self._multi_field_metadata)
