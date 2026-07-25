"""Update the display name and bio on Mastodon."""

import argparse

from dotenv import find_dotenv, load_dotenv

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.login import login


def update_bio(login_user: str, display_name: str, bio: str) -> None:
    """Update the display name and bio on Mastodon.

    Args:
        login_user (str): The user to log in with.
        display_name (str): The new display name.
        bio (str): The new bio content.

    Raises
    ------
        ValueError: If there is a problem with the login or the credentials update.
        Exception: If an unexpected error occurs.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        # Update the display name and bio
        logger.debug(f"{login_user} attempting to update bio...")
        mastodon.account_update_credentials(display_name=display_name, note=bio)
        logger.info(f"{login_user} successfully updated the display name and bio.")
    except ValueError as e:
        logger.error(f"Error: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update the display name and bio on Mastodon.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("display_name", help="The new display name.")
    parser.add_argument("bio", help="The new bio content.")

    args = parser.parse_args()
    update_bio(args.login_user, args.display_name, args.bio)
