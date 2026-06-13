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


class ActionError(BackendError):
    """Expected, user-facing action failure (bad target, invalid argument, ...).

    Backend actions should raise this (or a subclass) for failures the acting
    agent can understand and correct. The runtime converts it into an error
    message returned to the agent. Any other exception type escaping a backend
    action is treated as an unexpected error: it is logged with a traceback and
    counted in run health metrics.
    """


class ActionParseError(SilisocError):
    """Raised when agent output cannot be parsed into a valid action."""


class CheckpointError(SilisocError):
    """Raised for checkpoint save/restore failures."""
