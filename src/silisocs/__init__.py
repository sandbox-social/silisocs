"""Silisocs: a configuration-first social simulation framework.

Core abstractions are available directly from the top-level package::

    from silisocs import Agent, LanguageModel, BackendApp, app_action
    from silisocs import ActionSpec, ActionOutput, OutputType
    from silisocs import ProbeBase, RuntimeEngine

Internal modules can still be imported for advanced use cases, but the
symbols below cover the most common extension points.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("silisocs")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"


def __getattr__(name: str):
    _LAZY_IMPORTS: dict[str, tuple[str, str]] = {
        # Agents
        "Agent": ("silisocs.agents.base_agent", "Agent"),
        # Language models
        "LanguageModel": ("silisocs.runtime.language_models.base", "LanguageModel"),
        "NoLanguageModel": ("silisocs.runtime.language_models.base", "NoLanguageModel"),
        "InvalidResponseError": ("silisocs.runtime.language_models.base", "InvalidResponseError"),
        # Runtime types
        "ActionSpec": ("silisocs.runtime.types", "ActionSpec"),
        "ActionOutput": ("silisocs.runtime.types", "ActionOutput"),
        "OutputType": ("silisocs.runtime.types", "OutputType"),
        "ToolCall": ("silisocs.runtime.types", "ToolCall"),
        # Backends
        "BackendApp": ("silisocs.environments.backends.base", "BackendApp"),
        "SocialBackendApp": ("silisocs.environments.backends.base", "SocialBackendApp"),
        "app_action": ("silisocs.environments.backends.base", "app_action"),
        # Engines
        "RuntimeEngine": ("silisocs.simulation_engines.base_engines", "RuntimeEngine"),
        "BaseRuntimeEngine": ("silisocs.simulation_engines.base_engines", "BaseRuntimeEngine"),
        # GM components
        "BaseComponent": ("silisocs.environments.gm.components.base", "BaseComponent"),
        "InitializeComponent": ("silisocs.environments.gm.components.base", "InitializeComponent"),
        "NextActingComponent": ("silisocs.environments.gm.components.base", "NextActingComponent"),
        "ObservationComponent": (
            "silisocs.environments.gm.components.base",
            "ObservationComponent",
        ),
        "ResolveComponent": ("silisocs.environments.gm.components.base", "ResolveComponent"),
        "UpdateComponent": ("silisocs.environments.gm.components.base", "UpdateComponent"),
        # Probes
        "ProbeBase": ("silisocs.evaluations.probes.types", "ProbeBase"),
        # Exceptions
        "SilisocError": ("silisocs.exceptions", "SilisocError"),
        "ConfigurationError": ("silisocs.exceptions", "ConfigurationError"),
        "ComponentError": ("silisocs.exceptions", "ComponentError"),
        "BackendError": ("silisocs.exceptions", "BackendError"),
        "ActionParseError": ("silisocs.exceptions", "ActionParseError"),
        "CheckpointError": ("silisocs.exceptions", "CheckpointError"),
    }
    if name in _LAZY_IMPORTS:
        module_path, attr = _LAZY_IMPORTS[name]
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, attr)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "__version__",
    # Agents
    "Agent",
    # Language models
    "LanguageModel",
    "NoLanguageModel",
    "InvalidResponseError",
    # Runtime types
    "ActionSpec",
    "ActionOutput",
    "OutputType",
    "ToolCall",
    # Backends
    "BackendApp",
    "SocialBackendApp",
    "app_action",
    # Engines
    "RuntimeEngine",
    "BaseRuntimeEngine",
    # GM components
    "BaseComponent",
    "InitializeComponent",
    "NextActingComponent",
    "ObservationComponent",
    "ResolveComponent",
    "UpdateComponent",
    # Probes
    "ProbeBase",
    # Exceptions
    "SilisocError",
    "ConfigurationError",
    "ComponentError",
    "BackendError",
    "ActionParseError",
    "CheckpointError",
]
