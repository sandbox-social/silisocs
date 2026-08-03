"""Load the *pinned upstream Concordia* signaling modules with no Concordia install.

The port's strongest fidelity gate is a differential test: run the same order
book through our clearing house and through the one in
``concordia/contrib/components/game_master/marketplace.py`` at the pinned commit,
and require identical allocations, prices, cash, and inventory.

Concordia is not a dependency of this repo (and installing it would pull an LLM
stack into the test environment), but the code under comparison is pure Python
that only *touches* Concordia at class-definition time — base classes and a few
annotations. So we execute the upstream source in an isolated namespace with
those names stubbed. That compares against the verbatim upstream file rather
than a vendored copy, which is the whole point: a vendored copy could drift.

The pinned checkout location comes from ``CONCORDIA_SIGNALING_ROOT`` (see
``replications/signaling/docs/PINNED_COMMITS.md``); tests skip when it is absent,
so the suite still passes on a machine that has no checkout.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

DEFAULT_ROOT = "/scratch/sneheel/external/concordia-signaling-7779a4c"

#: Modules the upstream files import that we replace with permissive stubs.
_STUBBED_ROOTS = ("absl", "concordia")


def pinned_root() -> Path | None:
    """Return the pinned Concordia checkout, or ``None`` when unavailable."""
    root = Path(os.environ.get("CONCORDIA_SIGNALING_ROOT", DEFAULT_ROOT))
    marker = root / "concordia" / "contrib" / "components" / "game_master" / "marketplace.py"
    return root if marker.is_file() else None


class _StubModule(types.ModuleType):
    """A module whose every attribute is a fresh class.

    Enough for the upstream files: the names they resolve at import time are
    base classes (``entity_component.ContextComponent``) and annotations
    (``entity_lib.ActionSpec``), never behaviour we exercise. Anything that IS
    exercised — the clearing algorithm — is pure Python over dataclasses.
    """

    def __getattr__(self, name: str) -> Any:
        created = type(name, (object,), {})
        setattr(self, name, created)
        return created


def _install_stubs() -> dict[str, types.ModuleType]:
    """Register stub packages, returning the previous entries for restoration."""
    saved = {name: mod for name, mod in sys.modules.items() if _is_stubbed(name)}
    for name in list(saved):
        del sys.modules[name]
    for name in (
        "absl",
        "absl.logging",
        "concordia",
        "concordia.components",
        "concordia.components.agent",
        "concordia.components.agent.action_spec_ignored",
        "concordia.environment",
        "concordia.environment.engine",
        "concordia.typing",
        "concordia.typing.entity",
        "concordia.typing.entity_component",
        "concordia.associative_memory",
        "concordia.associative_memory.associative_memory",
        "concordia.typing.prefab",
    ):
        sys.modules[name] = _StubModule(name)
    # ``from concordia.typing import entity_component`` resolves by ATTRIBUTE on
    # the parent package first, and the stub's ``__getattr__`` would hand back a
    # bare class instead of the submodule. Wire the parent -> child links.
    for name, module in list(sys.modules.items()):
        if _is_stubbed(name) and "." in name:
            parent_name, _, child = name.rpartition(".")
            parent = sys.modules.get(parent_name)
            if parent is not None:
                object.__setattr__(parent, child, module)
    return saved


def _is_stubbed(name: str) -> bool:
    return any(name == root or name.startswith(f"{root}.") for root in _STUBBED_ROOTS)


def _restore(saved: dict[str, types.ModuleType]) -> None:
    for name in [n for n in list(sys.modules) if _is_stubbed(n)]:
        del sys.modules[name]
    sys.modules.update(saved)


def load_pinned_module(relative_path: str) -> dict[str, Any]:
    """Execute one pinned upstream file and return its namespace.

    ``relative_path`` is relative to the pinned checkout root, e.g.
    ``concordia/contrib/components/game_master/marketplace.py``.
    """
    root = pinned_root()
    if root is None:
        raise RuntimeError("pinned Concordia checkout not available")
    path = root / relative_path
    source = path.read_text(encoding="utf-8")
    saved = _install_stubs()
    # ``@dataclass`` resolves ``sys.modules[cls.__module__].__dict__`` while
    # processing a class, so the target must be a real registered module rather
    # than a bare dict.
    module_name = f"pinned_{path.stem}"
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        exec(compile(source, str(path), "exec"), module.__dict__)
        return dict(module.__dict__)
    finally:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        _restore(saved)


def load_pinned_marketplace() -> dict[str, Any]:
    """The upstream ``MarketPlace`` component, its ``Good``/``Order`` dataclasses."""
    return load_pinned_module("concordia/contrib/components/game_master/marketplace.py")


def load_pinned_personas() -> dict[str, Any]:
    """The upstream persona helpers (``generate_mixed_sex_dates``, ``PLAYER_SEX``)."""
    return load_pinned_module("examples/signaling/configs/personas.py")


def make_pinned_market(
    namespace: dict[str, Any],
    *,
    agents: list[Any],
    goods: list[Any],
    market_type: str = "clearing_house",
) -> Any:
    """Instantiate the upstream ``MarketPlace`` with logging neutralised.

    ``_log_self`` normally reaches for a logging channel wired by the Concordia
    entity system; the clearing path calls it, so it is replaced with a no-op.
    """
    market_cls = namespace["MarketPlace"]
    # The class is declared abstract (abc.ABC) but implements everything the
    # clearing path needs; subclass it to make it instantiable.
    concrete = type("PinnedMarketPlace", (market_cls,), {"_log_self": lambda self, *a, **k: None})
    return concrete(
        acting_player_names=[agent.name for agent in agents],
        agents=agents,
        goods=goods,
        market_type=market_type,
        show_advert=True,
    )
