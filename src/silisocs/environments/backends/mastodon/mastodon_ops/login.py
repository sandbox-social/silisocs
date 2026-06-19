"""Provides login functionality for a Mastodon user account."""

import argparse

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.env_utils import get_env_variable
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client

# OAuth access tokens are reusable for the life of a run, but every backend action
# previously triggered a fresh password log_in() round-trip. Cache the token per
# username so repeated actions skip re-authentication.
_TOKEN_CACHE: dict[str, str] = {}


def clear_token_cache() -> None:
    """Drop all cached access tokens (e.g. between runs or after a credential change)."""
    _TOKEN_CACHE.clear()


def login(username: str, save_to_file: bool = False) -> str:
    """
    Log in to Mastodon with the given username.

    Args:
        username (str): The username to log in with.
        save_to_file (bool): Whether to save the credentials to a file. Default is False.

    Returns
    -------
        str: The user credentials in string format.
    """
    if not save_to_file:
        cached = _TOKEN_CACHE.get(username)
        if cached:
            return cached

    email_prefix = get_env_variable("EMAIL_PREFIX")
    password = get_env_variable(f"{username.upper()}_PASSWORD")
    email = f"{email_prefix}+{username}@gmail.com"

    mastodon = get_client()

    try:
        access_token = mastodon.log_in(
            email,
            password,
            to_file=f"{username}_usercred.secret" if save_to_file else None,
            scopes=["read", "write", "follow"],
        )
        if save_to_file:
            logger.info(
                f"Successfully logged in as {username}. Credentials saved to {username}_usercred.secret"
            )
    except Exception as e:
        logger.error(f"Login failed for {username}: {e}")
        return ""
    else:
        logger.debug(f"Access token for {username}: {access_token}")
        if access_token:
            _TOKEN_CACHE[username] = access_token
        return access_token


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Log in to Mastodon with the given username.")
    parser.add_argument(
        "--username", type=str, default="user001", help="The username to log in with."
    )
    parser.add_argument(
        "--save_to_file", action="store_true", help="Whether to save the credentials to a file."
    )

    args = parser.parse_args()

    access_token = login(args.username, save_to_file=args.save_to_file)
    logger.info(f"User access token: {access_token}")
