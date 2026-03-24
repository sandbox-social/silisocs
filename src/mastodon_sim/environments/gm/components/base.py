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
    """Mixin for flow-aware components with flow-field routing support.

    Components can declare supported flow-tunable fields by defining `FLOW_FIELDS`:

        class MyComponent(FlowComponent):
            FLOW_FIELDS = {
                "timeline_mode": str,
                "recsys_type": str,
            }

    At GM initialization time, flow overrides are provided as:

        {
            "default": {"timeline_mode": "follower_chronological"},
            "active": {"timeline_mode": "pure_recsys", "recsys_type": "twitter"},
        }

    Values can then be retrieved with:

        value = self.get_field_for_entity("timeline_mode", flow_tag, default="follower_chronological")

    The legacy decorator metadata path is still recognized for backwards
    compatibility, but explicit `FLOW_FIELDS` is the canonical API.
    """

    FLOW_FIELDS: dict[str, type] = {}
    _multi_field_metadata: dict[str, type] = {}

    def __init__(self) -> None:
        """Initialize component with empty flow field mapping."""
        self._flow_field_values: dict[str, dict[str, Any]] = {}
        # Backwards-compatible alias retained for existing tests/integrations.
        self._entity_field_values = self._flow_field_values

    def __init_subclass__(cls, **kwargs):
        """Build declared field metadata for subclasses."""
        super().__init_subclass__(**kwargs)

        parent_meta: dict[str, type] = {}
        for base in cls.__bases__:
            if hasattr(base, "_multi_field_metadata"):
                parent_meta.update(dict(base._multi_field_metadata))

        declared = dict(parent_meta)

        explicit_fields = cls.__dict__.get("FLOW_FIELDS", {})
        if isinstance(explicit_fields, Mapping):
            declared.update({str(k): v for k, v in explicit_fields.items()})

        # Backward compatibility for decorator-based declarations.
        for attr_name, attr_value in cls.__dict__.items():
            if hasattr(attr_value, "_is_multi_field") and attr_value._is_multi_field:
                field_type = getattr(attr_value, "_multi_field_type", Any)
                declared[attr_name] = field_type
            elif isinstance(attr_value, property):
                func = attr_value.fget
                if func and hasattr(func, "_is_multi_field") and func._is_multi_field:
                    field_type = getattr(func, "_multi_field_type", Any)
                    declared[attr_name] = field_type

        cls._multi_field_metadata = declared
        cls.FLOW_FIELDS = dict(declared)

    def set_multi_field_values(self, entity_field_map: dict[str, dict[str, Any]]) -> None:
        """Set flow field values for routing.

        Args:
            entity_field_map: Mapping of flow_tag -> {field_name: field_value}
        """
        normalized: dict[str, dict[str, Any]] = {}
        raw_map = dict(entity_field_map or {})
        for flow_tag, field_config in raw_map.items():
            flow_key = str(flow_tag).strip()
            if not flow_key:
                continue
            normalized[flow_key] = dict(field_config or {})

        declared_fields = set(self._multi_field_metadata.keys())
        if declared_fields:
            for flow_key, fields in normalized.items():
                unknown = sorted(set(fields.keys()) - declared_fields)
                if unknown:
                    raise ValueError(
                        f"Unsupported flow field(s) for {self.__class__.__name__} on flow "
                        f"'{flow_key}': {unknown}. Supported: {sorted(declared_fields)}"
                    )

        self._flow_field_values = normalized
        self._entity_field_values = self._flow_field_values

    def get_field_for_entity(
        self, field_name: str, entity_name: str | None = None, default: Any = None
    ) -> Any:
        """Get a flow field value for a flow tag.

        Args:
            field_name: Flow field name.
            entity_name: Flow tag (legacy parameter name retained).
            default: Fallback value.
        """
        if not entity_name:
            return default

        if self._multi_field_metadata and field_name not in self._multi_field_metadata:
            return default

        return self._flow_field_values.get(entity_name, {}).get(field_name, default)

    def set_flow_field_values(self, flow_field_map: dict[str, dict[str, Any]]) -> None:
        """Explicit alias for set_multi_field_values."""
        self.set_multi_field_values(flow_field_map)

    def get_flow_field(
        self, field_name: str, flow_tag: str | None = None, default: Any = None
    ) -> Any:
        """Explicit alias for get_field_for_entity."""
        return self.get_field_for_entity(field_name, flow_tag, default)

    def has_multi_fields(self) -> bool:
        """Check if this component has any multi-field declarations."""
        return bool(self._multi_field_metadata)

    def get_multi_fields(self) -> dict[str, type]:
        """Get all declared multi-fields and their types."""
        return dict(self._multi_field_metadata)
