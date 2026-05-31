from __future__ import annotations

import importlib

from silisocs.environments.gm.components.base import BaseComponent


def test_base_component_checkpoint_hooks_are_noop() -> None:
    component = BaseComponent()

    assert component.get_state() == {}
    component.set_state({"anything": "ignored"})
    assert component.get_state() == {}


def test_removed_native_component_lifecycle_symbols_are_absent() -> None:
    base = importlib.import_module("silisocs.environments.gm.components.base")

    for name in ("LoggingComponent", "RuntimeComponent", "FlowComponent"):
        assert not hasattr(base, name)
    for name in ("pre_act", "post_act", "set_entity", "get_entity"):
        assert not hasattr(BaseComponent, name)
