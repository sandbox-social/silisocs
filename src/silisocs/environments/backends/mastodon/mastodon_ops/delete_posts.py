"""Delete posts from a Mastodon account.

This script allows you to delete one or more posts from a Mastodon account.
It provides options for deleting specific posts by ID or deleting the most recent posts.

Usage examples:

1. Delete a specific post by ID:
   python delete_posts.py user001 --id 123456

2. Delete multiple posts by ID:
   python delete_posts.py user001 --id 123456 789012 345678

3. Delete the most recent post:
   python delete_posts.py user001 --recent 1

4. Delete the 5 most recent posts:
   python delete_posts.py user001 --recent 5

5. Delete all posts (use with caution):
   python delete_posts.py user001 --all

For more information on each option, use the --help flag:
python delete_posts.py --help
"""

import argparse

from dotenv import find_dotenv, load_dotenv

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.login import login


# TODO: Likely requries pagination handling
def delete_posts(
    login_user: str,
    post_ids: list[int] | None = None,
    recent_count: int | None = None,
    delete_all: bool = False,
    skip_confirm: bool = False,
) -> None:
    """Delete posts from a Mastodon account.

    Args:
        login_user (str): The user to log in with.
        post_ids (list[int] | None): List of post IDs to delete.
        recent_count (int | None): Number of recent posts to delete.
        delete_all (bool): Whether to delete all posts.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        if delete_all:
            if not skip_confirm:
                confirm = input(
                    "Are you sure you want to delete ALL posts? This action cannot be undone. (y/N): "
                )
                if confirm.lower() != "y":
                    logger.info("Operation cancelled.")
                    return

            statuses = mastodon.account_statuses(mastodon.me())
            post_ids = [status["id"] for status in statuses]
        elif recent_count:
            statuses = mastodon.account_statuses(mastodon.me(), limit=recent_count)
            post_ids = [status["id"] for status in statuses]

        if not post_ids:
            logger.error("No posts specified for deletion.")
            return

        for post_id in post_ids:
            try:
                mastodon.status_delete(post_id)
                logger.info(f"Successfully deleted post with ID: {post_id}")
            except Exception as e:
                logger.error(f"Failed to delete post with ID {post_id}: {e!s}")

        logger.info(f"Deletion process completed. Attempted to delete {len(post_ids)} post(s).")

    except ValueError as e:
        logger.error(f"Error: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Delete posts from a Mastodon account.")
    parser.add_argument("login_user", help="The user to log in with.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--id", type=int, nargs="+", help="ID(s) of the post(s) to delete.")
    group.add_argument("--recent", type=int, help="Number of recent posts to delete.")
    group.add_argument("--all", action="store_true", help="Delete all posts (use with caution).")
    group.add_argument("--skip-confirm", action="store_true", help="Skip confirmation prompt")

    args = parser.parse_args()

    delete_posts(
        args.login_user,
        post_ids=args.id,
        recent_count=args.recent,
        delete_all=args.all,
        skip_confirm=args.skip_confirm,
    )
