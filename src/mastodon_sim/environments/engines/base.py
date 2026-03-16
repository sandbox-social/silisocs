"""Base classes for environment engines."""

from __future__ import annotations


class BaseEnvironmentEngine:
    """Marker base class for environment engines.

    SocialMediaEngine continues to own the concrete runtime loop in the legacy
    module, while this package provides a stable extension location for new
    engine policies and presets.
    """
