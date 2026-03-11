"""Provides login functionality for a Mastodon user account."""

import argparse

from mastodon_sim.environments.backends.mastodon.logging_config import logger
from mastodon_sim.environments.backends.mastodon.mastodon_ops.env_utils import get_env_variable
from mastodon_sim.environments.backends.mastodon.mastodon_ops.get_client import get_client


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
