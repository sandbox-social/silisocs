"""Base classes for environment engines."""

from __future__ import annotations


class BaseEnvironmentEngine:
    """Marker base class for environment engines.

    Concrete social-media runtime engines live in `social_media.py`:

    - `BaseSocialMediaEngine` for simple one-GM execution.
    - `FlowSocialMediaEngine` for flow/multi-GM orchestration.

    This marker remains a stable extension location for future engine families.
    """
