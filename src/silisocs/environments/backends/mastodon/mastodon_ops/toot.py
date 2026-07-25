"""Toot (post) a status on Mastodon."""

import argparse

from dotenv import find_dotenv, load_dotenv

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.login import login


def toot(login_user: str, status: str) -> None:
    """Toot a status on Mastodon.

    Args:
        login_user (str): The user to log in with.
        status (str): The status to toot.

    Raises
    ------
        ValueError: If there is a problem with the login or the toot call.
        Exception: If an unexpected error occurs.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        # Post the status
        logger.debug(f"{login_user} attempting to toot a status...")
        mastodon.toot(status)
        logger.info(f"{login_user} successfully tooted the status.")
    except ValueError as e:
        logger.error(f"Error: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Toot a status on Mastodon.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("status", help="The status to toot.")

    args = parser.parse_args()
    toot(args.login_user, args.status)
