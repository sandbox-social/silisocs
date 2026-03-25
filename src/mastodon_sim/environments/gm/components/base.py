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

        value = self.get_flow_field("timeline_mode", flow_tag, default="follower_chronological")
    """

    FLOW_FIELDS: dict[str, type] = {}

    def __init__(self) -> None:
        """Initialize component with empty flow field mapping."""
        self._flow_field_values: dict[str, dict[str, Any]] = {}

    def __init_subclass__(cls, **kwargs):
        """Build merged FLOW_FIELDS metadata for subclasses."""
        super().__init_subclass__(**kwargs)

        merged_fields: dict[str, type] = {}
        for base in cls.__bases__:
            base_fields = getattr(base, "FLOW_FIELDS", None)
            if isinstance(base_fields, Mapping):
                merged_fields.update({str(k): v for k, v in base_fields.items()})

        explicit_fields = cls.__dict__.get("FLOW_FIELDS", None)
        if isinstance(explicit_fields, Mapping):
            merged_fields.update({str(k): v for k, v in explicit_fields.items()})
        cls.FLOW_FIELDS = merged_fields

    def set_flow_field_values(self, flow_field_map: dict[str, dict[str, Any]] | None) -> None:
        """Set flow field values for routing.

        Args:
            flow_field_map: Mapping of flow_tag -> {field_name: field_value}
        """
        normalized: dict[str, dict[str, Any]] = {}
        raw_map = dict(flow_field_map or {})
        for flow_tag, field_config in raw_map.items():
            flow_key = str(flow_tag).strip()
            if not flow_key:
                continue
            normalized[flow_key] = dict(field_config or {})

        declared_fields = set(self.FLOW_FIELDS.keys())
        if declared_fields:
            for flow_key, fields in normalized.items():
                unknown = sorted(set(fields.keys()) - declared_fields)
                if unknown:
                    raise ValueError(
                        f"Unsupported flow field(s) for {self.__class__.__name__} on flow "
                        f"'{flow_key}': {unknown}. Supported: {sorted(declared_fields)}"
                    )

        self._flow_field_values = normalized

    def get_flow_field(
        self, field_name: str, flow_tag: str | None = None, default: Any = None
    ) -> Any:
        """Get a flow field value for the provided flow tag."""
        if not flow_tag:
            return default

        if self.FLOW_FIELDS and field_name not in self.FLOW_FIELDS:
            return default

        return self._flow_field_values.get(flow_tag, {}).get(field_name, default)

    def has_flow_fields(self) -> bool:
        """Check whether this component declares flow-tunable fields."""
        return bool(self.FLOW_FIELDS)

    def get_flow_fields(self) -> dict[str, type]:
        """Get all declared flow fields and their types."""
        return dict(self.FLOW_FIELDS)
