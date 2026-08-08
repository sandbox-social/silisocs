"""Shared ``class_path`` loading + signature-filtered instantiation.

Loading a class (or plain factory callable) from a dotted ``class_path`` and calling
it with only the kwargs it accepts is the identical operation behind every
``class_path`` extension seam — engine policies, GM components, backends, LLM
providers, checkpoint strategies, and the agent/GM/simulation initializers. This leaf
module is the one home for it so the seam behaves the same everywhere: the same loud
"unsupported config param" error naming the offending keys, the target, and the config
block they came from.

It is a leaf: it imports only the standard library, so any layer can depend on it
without an import cycle.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Collection, Mapping
from typing import Any, cast

# Engine presets retired in the strategy-based refactor. The three RuntimeEngine
# subclasses (once in ``simulation_engines.base_engines``) and the whole
# ``simulation_engines.multi_gm`` module were reachable via ``sim.engine.class_path``
# on older configs; a stale path now dies with a bare ImportError/AttributeError.
# This narrow, named allowlist turns exactly those paths into a migration hint and
# leaves every other ImportError untouched.
_RETIRED_ENGINE_PATHS = frozenset(
    {
        "silisocs.simulation_engines.base_engines.BaseRuntimeEngine",
        "silisocs.simulation_engines.base_engines.FlowRuntimeEngine",
        "silisocs.simulation_engines.base_engines.MultiGMRuntimeEngine",
    }
)
_RETIRED_ENGINE_MODULE = "silisocs.simulation_engines.multi_gm"
_RETIRED_ENGINE_HINT = (
    "Retired engine class_path {path!r}. The BaseRuntimeEngine/FlowRuntimeEngine/"
    "MultiGMRuntimeEngine presets and the silisocs.simulation_engines.multi_gm module "
    "were removed: traversal is now selected by sim.engine.step.built_in "
    "(base | flow | multi_gm | multi_gm_serial | multi_gm_staged) with no engine "
    "class_path. Drop sim.engine.class_path and set sim.engine.step.built_in instead."
)


def _check_retired_engine_path(attr_path: str) -> None:
    """Raise a curated migration error for a known retired engine ``class_path``."""
    if (
        attr_path in _RETIRED_ENGINE_PATHS
        or attr_path == _RETIRED_ENGINE_MODULE
        or attr_path.startswith(_RETIRED_ENGINE_MODULE + ".")
    ):
        raise ValueError(_RETIRED_ENGINE_HINT.format(path=attr_path))


def load_attr(attr_path: str) -> Any:
    """Import and return the attribute named by a fully-qualified dotted path.

    Unlike :func:`load_class` this does not require the target to be a class, so a
    ``class_path`` that points at a plain function (e.g. a branch router written as a
    function rather than a class) resolves too. The caller decides how to treat a
    class vs a function.

    A stale ``sim.engine.class_path`` pointing at a retired engine preset raises a
    curated migration error (see :data:`_RETIRED_ENGINE_PATHS`) instead of a bare
    ImportError.
    """
    _check_retired_engine_path(attr_path)
    module_path, attr_name = attr_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, attr_name)


def load_class(class_path: str, *, what: str = "class") -> type[Any]:
    """Import and return the class named by a fully-qualified ``class_path``.

    Raises ``TypeError`` naming ``what`` (the kind of thing being loaded, e.g.
    ``"step strategy"``) if the path resolves to a non-class, so a ``class_path``
    pointing at a function or constant fails loudly at load time.
    """
    loaded = load_attr(class_path)
    if not inspect.isclass(loaded):
        raise TypeError(f"{class_path} is not a {what}.")
    return cast(type[Any], loaded)


def supported_kwargs(target: Any) -> set[str] | None:
    """Return the keyword names ``target`` accepts, or ``None`` for "accepts anything".

    ``None`` means the target declares ``**kwargs`` (or its signature cannot be
    introspected, e.g. a C-implemented callable), so nothing may be filtered out and
    the target itself owns validation. Otherwise the returned set is exactly the
    keyword-passable parameters — the contract every ``class_path`` seam checks a
    config's ``params`` against.
    """
    try:
        params = inspect.signature(target).parameters
    except (TypeError, ValueError):
        return None
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return None
    return {
        name
        for name, param in params.items()
        if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }


def describe_target(target: Any) -> str:
    """Human-readable ``module.Qualname`` for a class or callable, for error text."""
    module = getattr(target, "__module__", "") or ""
    name = getattr(target, "__qualname__", None) or getattr(target, "__name__", None)
    if name is None:
        name = type(target).__name__
    return f"{module}.{name}" if module else str(name)


def instantiate_with_supported_kwargs(
    target: Any,
    kwargs: Mapping[str, Any],
    *,
    strict_keys: Collection[str] | None = None,
    config_path: str | None = None,
) -> Any:
    """Call ``target`` (a class or factory callable) with only the kwargs it accepts.

    Any key the target does not declare raises ``ValueError`` naming the offending
    keys, the target, its supported params, and — when the caller supplies
    ``config_path`` — the config block the keys came from, so a mistyped config key
    fails loudly instead of being silently dropped. A ``**kwargs`` target receives
    everything.

    ``strict_keys`` narrows *which* keys must be accepted (default: all of ``kwargs``).
    Callers that also pass framework-injected kwargs a custom class need not declare
    (GM components, backends, LLM providers) list only the user-authored keys there;
    the rest are filtered out silently.

    A ``TypeError`` raised while *binding* the arguments (missing required param,
    keyword rejected by a ``**kwargs``-free C callable) is re-raised as the same kind
    of config error; a ``TypeError`` raised *inside* a Python target propagates
    untouched. Caveat: a C-implemented target that raises ``TypeError`` in its body
    pushes no Python frame, so it is indistinguishable from a binding failure and is
    reported as the config error (the original message is preserved inside it).
    """
    supported = supported_kwargs(target)
    if supported is not None:
        checked = set(kwargs) if strict_keys is None else set(strict_keys)
        unsupported = sorted(checked - supported)
        if unsupported:
            raise ValueError(
                f"Unsupported config param(s) for {describe_target(target)}"
                f"{_at(config_path)}: {unsupported}. Supported params: {sorted(supported)}"
            )
        kwargs = {key: value for key, value in kwargs.items() if key in supported}
    try:
        return target(**dict(kwargs))
    except TypeError as exc:
        traceback = exc.__traceback__
        if traceback is not None and traceback.tb_next is not None:
            raise  # raised inside the target's body, not by argument binding
        raise ValueError(
            f"Could not construct {describe_target(target)}{_at(config_path)} with params "
            f"{sorted(kwargs)}: {exc}"
        ) from exc


def _at(config_path: str | None) -> str:
    return f" (configured at {config_path})" if config_path else ""
