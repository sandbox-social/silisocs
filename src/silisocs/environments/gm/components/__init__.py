"""Configurable game-master component primitives and factories."""

__all__ = [
    "build_action_prompt_component",
    "build_initialize_component",
    "build_initialize_components",
    "build_next_acting_component",
    "build_observe_component",
    "build_resolve_component",
    "build_update_component",
]


def __getattr__(name: str):
    if name in __all__:
        from silisocs.environments.gm.components import factory

        return getattr(factory, name)
    raise AttributeError(name)
