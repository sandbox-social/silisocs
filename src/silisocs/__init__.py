"""Silisocs public Python API.

Silisocs builds on Concordia's agent/runtime abstractions and adds a
configuration-first social simulation layer with social-media backends,
game-master components, probes, and study tooling.
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
    if name == "ConfigStore":
        from silisocs.runtime.config import ConfigStore

        return ConfigStore
    if name == "Simulation":
        from silisocs.runtime.simulation import Simulation

        return Simulation
    if name in ("EnvironmentApp", "PhoneApp", "SocialMediaApp", "app_action"):
        from silisocs.environments.backends import base

        return getattr(base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ConfigStore",
    "EnvironmentApp",
    "PhoneApp",
    "Simulation",
    "SocialMediaApp",
    "__version__",
    "app_action",
]
