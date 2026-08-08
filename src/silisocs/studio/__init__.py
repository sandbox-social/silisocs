"""Silisocs Studio web application."""


def create_app(*args, **kwargs):
    """Lazily import the optional FastAPI application factory."""
    from silisocs.studio.app import create_app as factory

    return factory(*args, **kwargs)


__all__ = ["create_app"]
