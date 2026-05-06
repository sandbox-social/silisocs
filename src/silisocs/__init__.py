"""Mastodon Sim - Generative Agent simulation of social media networks."""


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
    if name in ("PhoneApp", "SocialMediaApp", "app_action"):
        from silisocs.environments.backends import base

        return getattr(base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ConfigStore",
    "PhoneApp",
    "Simulation",
    "SocialMediaApp",
    "app_action",
]
