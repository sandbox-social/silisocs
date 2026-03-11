"""Read the display name and bio of a user on Mastodon."""

import argparse

from dotenv import find_dotenv, load_dotenv

from mastodon_sim.environments.backends.mastodon.logging_config import logger
from mastodon_sim.environments.backends.mastodon.mastodon_ops.get_client import get_client
from mastodon_sim.environments.backends.mastodon.mastodon_ops.login import login


def read_bio(login_user: str, target_user: str) -> tuple[str, str]:
    """Read the display name and bio of a user on Mastodon.

    Args:
        login_user (str): The user to log in with.
        target_user (str): The user whose bio is to be read.

    Returns
    -------
        Tuple[str, str]: The display name and bio of the target user.

    Raises
    ------
        ValueError: If the target user is not found or bio information is missing.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        # Search for the user and get their ID
        logger.debug(f"{login_user} attempting to read bio of {target_user}...")
        account = mastodon.account_search(target_user, limit=1)
        if account:
            user_info = mastodon.account(account[0]["id"])
            display_name = user_info.get("display_name")
            bio = user_info.get("note")
            # If display name or bio is missing, set to "Not provided"
            if not display_name:
                display_name = "Not provided"
            if not bio:
                bio = "Not provided"
            logger.info(f"User: {target_user}\nDisplay Name: {display_name}\nBio: {bio}")
            return display_name, bio
        raise ValueError(f"User {target_user} not found.")
    except ValueError as e:
        logger.error(f"Error: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Read the display name and bio of a user on Mastodon."
    )
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("target_user", help="The user whose bio is to be read.")

    args = parser.parse_args()
    try:
        display_name, bio = read_bio(args.login_user, args.target_user)
        print(f"User: {args.target_user}\nDisplay Name: {display_name}\nBio: {bio}")
    except ValueError as e:
        print(f"Failed to read bio: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
