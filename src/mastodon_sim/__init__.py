"""Mastodon Sim - Generative Agent simulation of social media networks."""


def __getattr__(name: str):
    if name == "ConfigStore":
        from mastodon_sim.runtime.config import ConfigStore

        return ConfigStore
    if name == "Simulation":
        from mastodon_sim.runtime.simulation import Simulation

        return Simulation
    if name in ("PhoneApp", "SocialMediaApp", "app_action"):
        from mastodon_sim.environments.backends import base

        return getattr(base, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ConfigStore",
    "PhoneApp",
    "Simulation",
    "SocialMediaApp",
    "app_action",
]
