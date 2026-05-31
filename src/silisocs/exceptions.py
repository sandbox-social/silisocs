"""Silisocs exception hierarchy.

All framework-specific exceptions inherit from :class:`SilisocError` so
callers can catch a single base class when needed.
"""


class SilisocError(Exception):
    """Base exception for all Silisocs errors."""


class ConfigurationError(SilisocError):
    """Raised when configuration is invalid or incomplete."""


class ComponentError(SilisocError):
    """Raised when a GM component fails during execution."""


class BackendError(SilisocError):
    """Raised when a backend action fails."""


class ActionParseError(SilisocError):
    """Raised when agent output cannot be parsed into a valid action."""


class CheckpointError(SilisocError):
    """Raised for checkpoint save/restore failures."""
