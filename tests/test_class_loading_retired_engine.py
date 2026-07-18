"""FIX C17: a stale ``sim.engine.class_path`` pointing at a retired engine preset
must raise a curated migration error (not a bare ImportError/AttributeError), while
normal import failures stay untouched.
"""

from __future__ import annotations

import pytest

from silisocs.runtime.class_loading import load_attr, load_class

_RETIRED = [
    "silisocs.simulation_engines.base_engines.BaseRuntimeEngine",
    "silisocs.simulation_engines.base_engines.FlowRuntimeEngine",
    "silisocs.simulation_engines.base_engines.MultiGMRuntimeEngine",
    "silisocs.simulation_engines.multi_gm.MultiGMRuntimeEngine",
    "silisocs.simulation_engines.multi_gm",
]


@pytest.mark.parametrize("path", _RETIRED)
def test_retired_engine_path_raises_migration_hint(path: str) -> None:
    with pytest.raises(ValueError, match="sim.engine.step.built_in"):
        load_attr(path)
    with pytest.raises(ValueError, match="sim.engine.step.built_in"):
        load_class(path)


def test_missing_attr_still_raises_plain_error() -> None:
    # A genuinely absent attribute in a live module is NOT in the allowlist, so it
    # surfaces the ordinary AttributeError rather than the migration hint.
    with pytest.raises(AttributeError):
        load_attr("silisocs.simulation_engines.base_engines.NoSuchEngine")


def test_missing_module_still_raises_plain_error() -> None:
    with pytest.raises(ModuleNotFoundError):
        load_attr("silisocs.nonexistent_pkg.Thing")


def test_valid_engine_class_still_loads() -> None:
    cls = load_class("silisocs.simulation_engines.base_engines.RuntimeEngine")
    assert cls.__name__ == "RuntimeEngine"
