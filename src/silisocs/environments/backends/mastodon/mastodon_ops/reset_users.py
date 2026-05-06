"""Reset Mastodon user accounts by deleting all posts, removing likes and boosts, and resetting profile information.

This script provides a comprehensive reset of Mastodon user accounts.

Usage examples:
    1. Reset a single user:
       python reset_users.py user001

    2. Reset multiple users:
       python reset_users.py user001 user002 user003

    3. Reset multiple users without confirmation prompts:
       python reset_users.py user001 user002 user003 --skip-confirm

    4. Reset multiple users in parallel:
       python reset_users.py user001 user002 user003 --parallel

For more information, use the --help flag:
python reset_users.py --help
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import find_dotenv, load_dotenv

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.delete_posts import delete_posts
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.login import login
from silisocs.environments.backends.mastodon.mastodon_ops.timeline import get_public_timeline
from silisocs.environments.backends.mastodon.mastodon_ops.unfollow import unfollow
from silisocs.environments.backends.mastodon.mastodon_utils import get_users_from_env


def clear_mastodon_server(max_num_agents):
    users = get_users_from_env()[: max_num_agents + 1]
    reset_users(users, skip_confirm=True, parallel=True)
    while len(get_public_timeline(limit=None)):
        print("All posts not cleared. Running reset operation again...")
        reset_users(users, skip_confirm=True, parallel=True)
    print("All posts cleared")


def reset_profile(mastodon) -> None:
    """Reset a user's profile information."""
    try:
        mastodon.account_update_credentials(display_name="", note="")
        logger.info("Profile information reset successfully.")
    except Exception as e:
        logger.error(f"Failed to reset profile information: {e!s}")


def unfollow_all_users(mastodon, login_user: str) -> None:
    """Unfollow all users that the current user is following."""
    try:
        following = mastodon.account_following(mastodon.me()["id"])
        total_following = len(following)
        unfollowed_count = 0

        logger.info(f"Attempting to unfollow {total_following} users for {login_user}...")

        for user in following:
            try:
                unfollow(login_user=login_user, unfollow_user=user["acct"])
                unfollowed_count += 1
            except Exception as e:
                logger.warning(f"Failed to unfollow user {user['acct']}: {e}")

        logger.info(
            f"Successfully unfollowed {unfollowed_count} out of {total_following} users for {login_user}."
        )
    except Exception as e:
        logger.error(f"Error in unfollow_all_users for {login_user}: {e}")


def remove_favourites_and_boosts(mastodon) -> None:
    """Remove all favourites (likes) and boosts (reblogs) for a user."""
    try:
        favourites = mastodon.favourites()
        removed_favourites = 0
        for favourite in favourites:
            try:
                mastodon.status_unfavourite(favourite["id"])
                removed_favourites += 1
            except Exception as e:
                logger.warning(f"Failed to remove favourite {favourite['id']}: {e}")
        logger.info(f"Removed {removed_favourites} out of {len(favourites)} favourites.")

        account_id = mastodon.me()["id"]
        statuses = mastodon.account_statuses(account_id)
        reblogs = [status for status in statuses if status.get("reblog")]
        removed_boosts = 0
        for status in reblogs:
            try:
                mastodon.status_unreblog(status["reblog"]["id"])
                removed_boosts += 1
            except Exception as e:
                logger.warning(f"Failed to remove boost {status['id']}: {e}")
        logger.info(f"Removed {removed_boosts} out of {len(reblogs)} boosts.")
    except Exception as e:
        logger.error(f"Error removing favourites and boosts: {e}")


def reset_user(login_user: str, skip_confirm: bool = False) -> None:
    """Reset a Mastodon user's account comprehensively."""
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        if not skip_confirm:
            confirm = input(
                f"Are you sure you want to reset the account for {login_user}? "
                f"This will delete all posts, remove all likes and boosts, unfollow all users, "
                f"and reset the profile. This action cannot be undone. (y/N): "
            )
            if confirm.lower() != "y":
                logger.info(f"Operation cancelled for user {login_user}.")
                return

        logger.info(f"Starting comprehensive reset process for user: {login_user}")

        delete_posts(login_user=login_user, delete_all=True, skip_confirm=True)
        remove_favourites_and_boosts(mastodon)
        unfollow_all_users(mastodon, login_user)
        reset_profile(mastodon)

        logger.info(f"Comprehensive reset process completed for user: {login_user}")

    except ValueError as e:
        logger.error(f"Error for user {login_user}: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred for user {login_user}: {e}")


def reset_users(login_users, skip_confirm=False, parallel=False):
    """Reset multiple Mastodon user accounts comprehensively."""
    if parallel:
        with ThreadPoolExecutor() as executor:
            futures = [executor.submit(reset_user, user, skip_confirm) for user in login_users]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"An error occurred: {e}")
    else:
        for user in login_users:
            reset_user(user, skip_confirm)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reset Mastodon user accounts comprehensively.")
    parser.add_argument("login_users", nargs="+", help="The user(s) to log in with and reset.")
    parser.add_argument("--skip-confirm", action="store_true", help="Skip confirmation prompts.")
    parser.add_argument(
        "--parallel",
        action="store_true",
        help="Run the reset process in parallel for multiple users.",
    )

    args = parser.parse_args()

    reset_users(args.login_users, args.skip_confirm, args.parallel)
