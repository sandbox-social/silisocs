"""Follow a user on Mastodon."""

import argparse

from dotenv import find_dotenv, load_dotenv

from mastodon_sim.environments.backends.mastodon.logging_config import logger
from mastodon_sim.environments.backends.mastodon.mastodon_ops.get_client import get_client
from mastodon_sim.environments.backends.mastodon.mastodon_ops.login import login


def follow(login_user: str, follow_user: str) -> None:
    """Follow a user on Mastodon.

    Args:
        login_user (str): The user to log in with.
        follow_user (str): The user to follow.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        # Search for the user to follow and get their ID
        logger.debug(f"{login_user} attempting to follow {follow_user}...")
        account = mastodon.account_search(follow_user, limit=1)
        if account:
            mastodon.account_follow(account[0]["id"])
            logger.info(f"{login_user} is now following {follow_user}.")
        else:
            logger.warning(f"User {follow_user} not found.")
    except ValueError as e:
        logger.error(f"Error: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Follow a user on Mastodon.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("follow_user", help="The user to follow.")

    args = parser.parse_args()
    follow(args.login_user, args.follow_user)
