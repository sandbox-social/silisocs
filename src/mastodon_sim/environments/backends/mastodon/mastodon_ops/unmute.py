"""
unmute.py - Script for unmuting a Mastodon account.

This script allows you to unmute a previously muted Mastodon account.

Usage example:
    python unmute.py user001 @account_to_unmute

For more information, use the --help flag:
python unmute.py --help
"""

import argparse
import sys

from mastodon import MastodonAPIError

from mastodon_sim.environments.backends.mastodon.logging_config import logger
from mastodon_sim.environments.backends.mastodon.mastodon_ops.get_client import get_client
from mastodon_sim.environments.backends.mastodon.mastodon_ops.login import login
from mastodon_sim.environments.backends.mastodon.mastodon_utils import (
    AccountNotFoundError,
    find_account_id,
)


def unmute_account(login_user: str, unmute_user: str) -> None:
    """
    Unmute a Mastodon account.

    Args:
        login_user (str): The user to log in with.
        unmute_user (str): The username of the account to unmute.

    Raises
    ------
        AccountNotFoundError: If the account to unmute is not found.
        MastodonAPIError: If there's an API error during the unmute operation.
        Exception: For any other unexpected errors.
    """
    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        account_id = find_account_id(mastodon, unmute_user)
        result = mastodon.account_unmute(account_id)

        logger.info(f"Successfully unmuted account @{unmute_user} (ID: {account_id}).")
        logger.debug(f"Updated relationship: {result}")

    except AccountNotFoundError as e:
        logger.error(str(e))
        raise
    except MastodonAPIError as e:
        logger.error(f"API error occurred while unmuting account: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Unmute a Mastodon account.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("unmute_user", help="The username of the account to unmute.")

    args = parser.parse_args()

    try:
        unmute_account(args.login_user, args.unmute_user)
    except (AccountNotFoundError, MastodonAPIError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)
