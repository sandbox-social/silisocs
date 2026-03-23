"""Decorators for flow-aware component field routing."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def multi_field(field_type: type) -> Callable[[F], F]:
    """Decorator marking a component field as supporting multi-entity values.

    Use this to decorate methods or properties that return configurable values.
    The decorator registers the field with the component class so it can be
    routed per-entity.

    Applied AFTER @property if decorating a property method.

    Example:
        class MyComponent(FlowComponent, MakeObservation):
            def __init__(self):
                super().__init__()

            @multi_field(str)
            def timeline_filter(self):
                '''Get timeline filter for current entity.'''
                return self.get_field_for_entity('timeline_filter', default='all')

    Or with @property (note decorator order!):

        @property
        @multi_field(str)
        def timeline_filter(self):
            return self.get_field_for_entity('timeline_filter', default='all')
    """

    def decorator(func: F) -> F:
        """Mark method as a multi-field."""
        # Store metadata on the function itself
        func._is_multi_field = True  # type: ignore
        func._multi_field_type = field_type  # type: ignore
        return func

    return decorator

