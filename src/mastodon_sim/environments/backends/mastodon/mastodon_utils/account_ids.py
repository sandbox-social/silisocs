"""
mastodon_utils.py - Utility functions for Mastodon operations.

This module contains utility functions that can be used across different Mastodon-related scripts.
"""

from mastodon import Mastodon, MastodonAPIError

from mastodon_sim.environments.backends.mastodon.logging_config import logger


class AccountNotFoundError(Exception):
    """Raised when the specified account is not found."""


def find_account_id(mastodon: Mastodon, username: str) -> int:
    """
    Find the account ID for a given username.

    Args:
        mastodon (Mastodon): The Mastodon client instance.
        username (str): The username to search for.

    Returns
    -------
        int: The account ID if found.

    Raises
    ------
        AccountNotFoundError: If the account is not found.
        MastodonAPIError: If there's an API error during the search.
    """
    try:
        results = mastodon.account_search(username, limit=1)
        if results and results[0]["acct"].lower() == username.lower().lstrip("@"):
            return results[0]["id"]
        raise AccountNotFoundError(f"Account with username {username} not found.")
    except MastodonAPIError as e:
        logger.error(f"API error while searching for account: {e}")
        raise
