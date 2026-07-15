"""Factory functions for creating backend app instances.

Provides a single entry point for the game master to instantiate the correct
``BackendApp`` based on configuration.

Built-in backends are registered in ``_BUILTIN_BACKENDS`` by backend type name
to fully-qualified class path. Custom backends can set
``env.gm.backend.class_path`` in config.
"""

import importlib
import inspect
import os
from collections.abc import Mapping
from typing import Any

from silisocs.environments.backends.base import BackendApp

_BUILTIN_BACKENDS: dict[str, str] = {
    "twitter_like": "silisocs.environments.backends.twitter_like.app.TwitterLikeApp",
    "reddit_like": "silisocs.environments.backends.reddit_like.app.RedditLikeApp",
    "mastodon": "silisocs.environments.backends.mastodon.apps.SocialNetworkApp",
    "resource_market": "silisocs.environments.backends.resource_market.app.ResourceMarketApp",
    "virtual_space": "silisocs.environments.backends.virtual_space.app.VirtualSpaceApp",
}


def registered_backend_types() -> tuple[str, ...]:
    """Return built-in selector names for discovery surfaces."""
    return tuple(sorted(_BUILTIN_BACKENDS))


def resolve_backend_class(
    backend_type: str,
    *,
    class_path: str | None = None,
) -> type[BackendApp]:
    """Resolve a configured backend class without instantiating it."""
    resolved = str(class_path or _BUILTIN_BACKENDS.get(backend_type, "")).strip()
    if not resolved:
        available = ", ".join(registered_backend_types())
        raise ValueError(
            f"Unknown environment backend type: {backend_type!r}. "
            f"Available built-in types: {available}. Or provide class_path."
        )
    return _load_app_class(resolved)


def default_backend_db_filename(backend_type: str) -> str:
    """Return the default db filename convention for a backend type."""
    return f"{backend_type}.db"


def resolve_backend_db_path(
    output_rootname: str,
    backend_type: str,
    db_path: str | None = None,
) -> str:
    """Compute the resolved backend database path for a game master.

    Single source of truth for the db path so the multi-GM collision guard and
    the actual app instantiation never diverge. An explicit ``db_path`` (e.g. a
    configured ``params.db_path``) is honored: an absolute path is used as-is,
    while a relative one is nested under ``output_rootname``. Otherwise the
    default ``<output_rootname>/<backend_type>.db`` convention is used.
    """
    rootname = str(output_rootname or "")
    explicit = str(db_path or "").strip()
    if explicit:
        if os.path.isabs(explicit):
            return explicit
        return os.path.join(rootname, explicit)
    return os.path.join(rootname, default_backend_db_filename(backend_type))


def _load_app_class(class_path: str) -> type[BackendApp]:
    module_path, class_name = class_path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    if not issubclass(cls, BackendApp):
        raise TypeError(f"Configured app class is not a BackendApp: {class_path}")
    return cls


def _instantiate_app_with_supported_kwargs(
    cls: type[BackendApp],
    kwargs: Mapping[str, Any],
    *,
    config_param_keys: Any = (),
) -> BackendApp:
    """Instantiate an app while validating user-supplied config params."""
    params = inspect.signature(cls.__init__).parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return cls(**dict(kwargs))

    supported = {
        name
        for name, param in params.items()
        if name != "self"
        and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    unsupported_config = sorted(set(config_param_keys) - supported)
    if unsupported_config:
        raise ValueError(
            f"Unsupported config param(s) for {cls.__module__}.{cls.__name__}: "
            f"{unsupported_config}. Supported params: {sorted(supported)}"
        )
    filtered = {k: v for k, v in kwargs.items() if k in supported}
    return cls(**filtered)


def create_backend_app(backend_type: str, **kwargs: Any) -> BackendApp:
    """Create and return a BackendApp for the given backend type.

    Parameters
    ----------
    backend_type:
        Built-in environment selector (see ``_BUILTIN_BACKENDS``) or any
        string when ``class_path`` is also provided in kwargs.
    **kwargs:
        Common keys: ``action_logger``, ``app_description``, ``db_path``,
        ``perform_operations``, ``class_path``, ``params``.

    Returns
    -------
    BackendApp
        A configured backend instance.
    """
    action_logger = kwargs.get("action_logger")
    app_description = kwargs.get("app_description", "")
    class_path = str(kwargs.get("class_path") or "").strip()
    backend_params = dict(kwargs.get("params") or {})

    cls = resolve_backend_class(backend_type, class_path=class_path)

    # Resolve the db path from a single source so an explicit ``params.db_path``
    # override and the default ``<backend_type>.db`` convention match exactly what
    # the multi-GM collision guard checks (see ``resolve_backend_db_path``). The
    # caller passes ``db_path`` already nested under the run output dir; a relative
    # ``params.db_path`` override is nested under that same root rather than
    # silently clobbering it with an un-rooted path.
    base_db_path = str(kwargs.get("db_path") or default_backend_db_filename(backend_type))
    output_rootname = os.path.dirname(base_db_path)
    params_db_path = backend_params.pop("db_path", None)
    if params_db_path is not None:
        db_path = resolve_backend_db_path(output_rootname, backend_type, db_path=params_db_path)
    else:
        db_path = base_db_path

    init_kwargs: dict[str, Any] = {
        "action_logger": action_logger,
        "app_description": app_description,
        "db_path": db_path,
        "perform_operations": kwargs.get("perform_operations", False),
    }
    init_kwargs.update(backend_params)

    return _instantiate_app_with_supported_kwargs(
        cls,
        init_kwargs,
        config_param_keys=backend_params.keys(),
    )
