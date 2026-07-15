"""Shipped panels built exclusively on the artifact interface.

Importing this package registers every built-in panel (the registry's
``_load_builtins`` imports it by name). Each module groups one family of
panels; new built-ins get their own module here.
"""

from silisocs.analysis.panels import behavior, content, core, events, metrics, network, study

__all__ = ["behavior", "content", "core", "events", "metrics", "network", "study"]
