"""Factory functions for creating environment app instances.

Provides a single entry point for the game master to instantiate the
correct backend ``BackendApp`` based on configuration.
"""

import importlib
import inspect
from collections.abc import Mapping
from typing import Any

from silisocs.environments.backends.base import BackendApp


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


def create_environment_app(platform_type: str, **kwargs: Any) -> BackendApp:
    """Create and return a BackendApp for the given platform type.

    Args:
        platform_type: Built-in environment selector such as ``"mastodon"``,
            ``"twitter_like"``, ``"reddit_like"``, ``"resource_market"``, or
            ``"virtual_space"``.
        **kwargs: Common keys:
            - ``action_logger``: Logger for recording actions.
            - ``app_description`` (str): Description of the app.
            - ``perform_operations`` (bool): Whether to perform real API calls
              (Mastodon-specific).

    Returns
    -------
        A configured ``BackendApp`` instance.

    Raises
    ------
        ValueError: If ``platform_type`` is not recognized.
    """
    action_logger = kwargs.get("action_logger")
    app_description = kwargs.get("app_description", "")
    app_class_path = str(kwargs.get("app_class_path") or "").strip()
    app_params = dict(kwargs.get("app_params") or {})

    if app_class_path:
        cls = _load_app_class(app_class_path)
        init_kwargs = {
            "action_logger": action_logger,
            "app_description": app_description,
            "db_path": kwargs.get("db_path", "twitter_like.db"),
        }
        init_kwargs.update(app_params)
        return _instantiate_app_with_supported_kwargs(
            cls,
            init_kwargs,
            config_param_keys=app_params.keys(),
        )

    if platform_type == "mastodon":
        from silisocs.environments.backends.mastodon.apps import SocialNetworkApp

        return SocialNetworkApp(
            action_logger=action_logger,
            perform_operations=kwargs.get("perform_operations", False),
            app_description=app_description,
        )
    if platform_type == "twitter_like":
        from silisocs.environments.backends.twitter_like.app import TwitterLikeApp

        # Derive DB path from output directory if available
        db_path = kwargs.get("db_path", "twitter_like.db")
        return TwitterLikeApp(
            action_logger=action_logger,
            app_description=app_description,
            db_path=db_path,
        )
    if platform_type == "reddit_like":
        from silisocs.environments.backends.reddit_like.app import RedditLikeApp

        db_path = kwargs.get("db_path", "reddit_like.db")
        return RedditLikeApp(
            action_logger=action_logger,
            app_description=app_description,
            db_path=db_path,
        )
    if platform_type == "resource_market":
        from silisocs.environments.backends.resource_market.app import ResourceMarketApp

        init_kwargs = {"action_logger": action_logger, "app_description": app_description}
        init_kwargs.update(app_params)
        return _instantiate_app_with_supported_kwargs(
            ResourceMarketApp,
            init_kwargs,
            config_param_keys=app_params.keys(),
        )
    if platform_type == "virtual_space":
        from silisocs.environments.backends.virtual_space.app import VirtualSpaceApp

        init_kwargs = {"action_logger": action_logger, "app_description": app_description}
        init_kwargs.update(app_params)
        return _instantiate_app_with_supported_kwargs(
            VirtualSpaceApp,
            init_kwargs,
            config_param_keys=app_params.keys(),
        )
    raise ValueError(
        f"Unknown environment platform type: '{platform_type}'. "
        "Supported types: 'mastodon', 'twitter_like', 'reddit_like', "
        "'resource_market', 'virtual_space'."
    )


def create_social_media_app(platform_type: str, **kwargs: Any) -> BackendApp:
    """Compatibility wrapper for existing social-media call sites."""
    return create_environment_app(platform_type=platform_type, **kwargs)
