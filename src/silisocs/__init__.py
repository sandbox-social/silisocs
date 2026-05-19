"""Silisocs public Python API.

Silisocs provides native agent/runtime abstractions plus a configuration-first
social simulation layer with social-media backends, game-master components,
probes, and study tooling. Concordia interoperability lives behind the optional
adapter package.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("silisocs")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"


def __getattr__(name: str):
    """__getattr__.

    :param str name:
    :type name: str
    """
    if name in ("BackendApp", "SocialMediaApp", "app_action"):
        from silisocs.environments.backends import base

        return getattr(base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BackendApp",
    "SocialMediaApp",
    "__version__",
    "app_action",
]
