"""Boost a post from a user's timeline on Mastodon."""

import argparse

from dotenv import find_dotenv, load_dotenv

from mastodon_sim.logging_config import logger
from mastodon_sim.mastodon_ops.get_client import get_client
from mastodon_sim.mastodon_ops.login import login


def boost_check(login_user: str, toot_id: str) -> bool:
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        # Check
        logger.debug(f"Attempting to check if {login_user} has boosted toot {toot_id}...")
        reblogged_by = mastodon.status_reblogged_by(toot_id)
        # Check if the user is in the lists
        boosted = any(user["acct"] == login_user for user in reblogged_by)
        logger.info(f"Successfully checked if {login_user} boosted post {toot_id} - {boosted}.")
        return boosted
    except ValueError as e:
        logger.error(f"Error: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise


def boost_toot(login_user: str, toot_id: str) -> None:
    """Boost (reblog) a post from a user's timeline on Mastodon.

    Args:
        login_user (str): The user to log in with.
        toot_id (str): The ID of the post to boost.

    Raises
    ------
        ValueError: If there is a problem with the login or boosting the post.
        Exception: If an unexpected error occurs.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        # Boost the post
        logger.debug(f"{login_user} attempting to boost toot {toot_id}...")
        mastodon.status_reblog(toot_id)
        logger.info(f"{login_user} boosted post {toot_id}.")
    except ValueError as e:
        logger.error(f"Error: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Boost a post from a user's timeline on Mastodon.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("toot_id", help="The ID of the post to boost.")

    args = parser.parse_args()
    try:
        boost_toot(args.login_user, args.toot_id)
        print(f"Successfully boosted post {args.toot_id}.")
    except Exception as e:
        print(f"Failed to boost post {args.toot_id}. Error: {e}")
