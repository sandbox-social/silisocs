"""Utility helpers for creating Mastodon app credentials."""

from mastodon import Mastodon


def create_new_app_credentials(
    app_name: str = "MyMastodonApp",
    *,
    api_base_url: str = "https://social-sandbox.com",
    scopes: list[str] | None = None,
    to_file: str = "clientcred.secret",
):
    """Create Mastodon application credentials.

    This wrapper keeps credential creation explicit and avoids executing any
    network calls at import time.
    """
    effective_scopes = scopes or ["read", "write", "follow"]
    return Mastodon.create_app(
        app_name,
        api_base_url=api_base_url,
        scopes=effective_scopes,
        to_file=to_file,
    )
