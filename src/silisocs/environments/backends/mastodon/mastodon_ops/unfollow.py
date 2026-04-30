"""Unfollow a user on Mastodon."""

import argparse

from dotenv import find_dotenv, load_dotenv

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.login import login


def unfollow(login_user: str, unfollow_user: str) -> None:
    """Unfollow a user on Mastodon.

    Args:
        login_user (str): The user to log in with.
        unfollow_user (str): The user to unfollow.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        # Search for the user to unfollow and get their ID
        logger.debug(f"{login_user} attempting to unfollow {unfollow_user}...")
        account = mastodon.account_search(unfollow_user, limit=1)
        if account:
            mastodon.account_unfollow(account[0]["id"])
            logger.info(f"{login_user} has unfollowed {unfollow_user}.")
        else:
            logger.warning(f"User {unfollow_user} not found.")
    except ValueError as e:
        logger.error(f"Error: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unfollow a user on Mastodon.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("unfollow_user", help="The user to unfollow.")

    args = parser.parse_args()
    unfollow(args.login_user, args.unfollow_user)
