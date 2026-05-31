"""
mute.py - Script for muting a Mastodon account.

This script allows you to mute a Mastodon account with options for notifications and duration.

Usage examples:
    1. Mute an account:
       python mute.py user001 @account_to_mute

    2. Mute an account but still receive notifications:
       python mute.py user001 @account_to_mute --notifications

    3. Mute an account for 1 hour:
       python mute.py user001 @account_to_mute --duration 3600

For more options, use the --help flag:
python mute.py --help
"""

import argparse
import sys

from mastodon import MastodonAPIError

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.login import login
from silisocs.environments.backends.mastodon.mastodon_utils import (
    AccountNotFoundError,
    find_account_id,
)


def mute_account(
    login_user: str, mute_user: str, notifications: bool = False, duration: int | None = None
) -> None:
    """
    Mute a Mastodon account.

    Args:
        login_user (str): The user to log in with.
        mute_user (str): The username of the account to mute.
        notifications (bool): Whether to receive notifications from the muted account.
        duration (int | None): Duration of the mute in seconds, or None for indefinite.

    Raises
    ------
        AccountNotFoundError: If the account to mute is not found.
        MastodonAPIError: If there's an API error during the mute operation.
        Exception: For any other unexpected errors.
    """
    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        account_id = find_account_id(mastodon, mute_user)
        result = mastodon.account_mute(account_id, notifications=notifications, duration=duration)

        mute_desc = "indefinitely" if duration is None else f"for {duration} seconds"
        notif_desc = "with" if notifications else "without"
        logger.info(
            f"Successfully muted account @{mute_user} (ID: {account_id}) {mute_desc} {notif_desc} notifications."
        )
        logger.debug(f"Updated relationship: {result}")

    except AccountNotFoundError as e:
        logger.error(str(e))
        raise
    except MastodonAPIError as e:
        logger.error(f"API error occurred while muting account: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mute a Mastodon account.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("mute_user", help="The username of the account to mute.")
    parser.add_argument(
        "--notifications", action="store_true", help="Receive notifications from the muted account."
    )
    parser.add_argument("--duration", type=int, help="Duration of the mute in seconds.")

    args = parser.parse_args()

    try:
        mute_account(
            args.login_user,
            args.mute_user,
            notifications=args.notifications,
            duration=args.duration,
        )
    except (AccountNotFoundError, MastodonAPIError) as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)
