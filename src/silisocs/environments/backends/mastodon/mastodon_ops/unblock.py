"""Unblock a user on Mastodon."""

import argparse

from dotenv import find_dotenv, load_dotenv

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.login import login
from silisocs.environments.backends.mastodon.mastodon_utils import AccountNotFoundError


def unblock_user(login_user: str, target_user: str) -> None:
    """Unblock a user on Mastodon.

    Args:
        login_user (str): The user to log in with.
        target_user (str): The user to unblock.

    Raises
    ------
        AccountNotFoundError: If the account to unblock is not found.
        ValueError: If there is a problem with the login or the unblock call.
        Exception: If an unexpected error occurs.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        # Search for the user to unblock and get their ID
        logger.debug(f"{login_user} attempting to unblock {target_user}...")
        account = mastodon.account_search(target_user, limit=1)
        if not account:
            raise AccountNotFoundError(f"User {target_user} not found.")
        target_user_id = account[0]["id"]
        mastodon.account_unblock(target_user_id)
        logger.info(f"{login_user} has unblocked {target_user}.")
    except AccountNotFoundError as e:
        logger.error(str(e))
        raise
    except ValueError as e:
        logger.error(f"Error: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unblock a user on Mastodon.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("target_user", help="The user to unblock.")

    args = parser.parse_args()
    unblock_user(args.login_user, args.target_user)
