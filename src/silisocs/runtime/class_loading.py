"""Shared ``class_path`` loading + signature-filtered instantiation.

Loading a class from a dotted ``class_path`` and instantiating it with only the
kwargs its constructor accepts is the identical operation behind every ``class_path``
extension seam — engine policies, GM components, backends, and the agent/GM/simulation
initializers. This leaf module is the one home for it so the seam behaves the same
everywhere (same loud "unsupported config param" error, same non-class rejection).

It is a leaf: it imports only the standard library, so any layer can depend on it
without an import cycle. A caller whose instantiation must also accept plain factory
functions (not just classes) keeps its own variant — see
``runtime/construction/assembly.py`` — rather than widening this contract.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Mapping
from typing import Any, cast


def load_attr(attr_path: str) -> Any:
    """Import and return the attribute named by a fully-qualified dotted path.

    Unlike :func:`load_class` this does not require the target to be a class, so a
    ``class_path`` that points at a plain function (e.g. a branch router written as a
    function rather than a class) resolves too. The caller decides how to treat a
    class vs a function.
    """
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


def instantiate_with_supported_kwargs(cls: type[Any], kwargs: Mapping[str, Any]) -> Any:
    """Instantiate ``cls`` passing only the kwargs its ``__init__`` accepts.

    A ``**kwargs`` constructor receives everything; otherwise any key the constructor
    does not declare raises ``ValueError`` naming the class and its supported params,
    so a mistyped config key fails loudly instead of being silently dropped.
    """
    params = inspect.signature(cls.__init__).parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return cls(**dict(kwargs))
    supported = {
        name
        for name, param in params.items()
        if name != "self"
        and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    unsupported = sorted(set(kwargs) - supported)
    if unsupported:
        raise ValueError(
            f"Unsupported config param(s) for {cls.__module__}.{cls.__name__}: "
            f"{unsupported}. Supported params: {sorted(supported)}"
        )
    return cls(**{k: v for k, v in kwargs.items() if k in supported})
