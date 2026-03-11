"""
notifications.py - Module for handling Mastodon notifications and conversations.

This module provides functionality to read, clear, dismiss Mastodon notifications,
and mark conversations as read.

Usage examples:
    1. Read notifications:
       python notifications.py user001

    2. Read and clear all notifications:
       python notifications.py user001 --clear

    3. Read notifications and dismiss specific ones:
       python notifications.py user001 --dismiss 123456 789012

    4. Mark specific conversations as read:
       python notifications.py user001 --mark-read 345678 901234

    5. Read notifications with type filtering:
       python notifications.py user001 --types mention follow

    6. Read, clear, and print detailed information for the last 5 notifications:
       python notifications.py user001 --clear --limit 5 --print

For more options, use the --help flag:
python notifications.py --help
"""

import argparse

from mastodon import MastodonAPIError, MastodonNetworkError
from mastodon.utility import AttribAccessDict

from mastodon_sim.environments.backends.mastodon.logging_config import logger
from mastodon_sim.environments.backends.mastodon.mastodon_ops.get_client import get_client
from mastodon_sim.environments.backends.mastodon.mastodon_ops.login import login


def read_notifications(  # noqa: C901, PLR0913, PLR0912
    login_user: str,
    clear: bool = False,
    limit: int | None = None,
    account_id: int | None = None,
    max_id: int | None = None,
    min_id: int | None = None,
    since_id: int | None = None,
    exclude_types: list[str] | None = None,
    types: list[str] | None = None,
    mentions_only: bool | None = None,
    dismiss_ids: list[int] | None = None,
    mark_read_conversation_ids: list[int] | None = None,
) -> list[AttribAccessDict]:
    """
    Fetch notifications for a Mastodon user and optionally clear, dismiss, or mark conversations as read.

    Args:
        login_user (str): The user to log in with.
        clear (bool): Whether to clear notifications after reading.
        limit (int): Maximum number of notifications to fetch and/or clear.
        account_id (int): Filter notifications from this account ID.
        max_id (int): Return results older than this ID.
        min_id (int): Return results newer than this ID.
        since_id (int): Return results newer than this ID.
        exclude_types (List[str]): Exclude these notification types.
        types (List[str]): Include only these notification types.
        mentions_only (bool): Only include mentions.
        dismiss_ids (List[int]): List of notification IDs to dismiss.
        mark_read_conversation_ids (List[int]): List of conversation IDs to mark as read.

    Returns
    -------
        List[mastodon.utility.AttribAccessDict]: A list of notification dictionaries.
    """
    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        notifications: list = []
        cleared_count = 0
        dismissed_count = 0
        marked_read_count = 0

        # Fetch notifications with pagination
        while True:
            batch = mastodon.notifications(
                limit=min(40, limit - len(notifications) if limit else 40),
                max_id=max_id,
                min_id=min_id,
                since_id=since_id,
                account_id=account_id,
                exclude_types=exclude_types,
                types=types,
                mentions_only=mentions_only,
            )

            notifications.extend(batch)

            if not batch or (limit and len(notifications) >= limit):
                break

            max_id = batch[-1]["id"]

        if limit:
            notifications = notifications[:limit]

        logger.info(f"Fetched {len(notifications)} notifications for user {login_user}")

        # Handle clearing notifications
        if clear:
            if limit is None:
                mastodon.notifications_clear()
                cleared_count = len(notifications)
                logger.info(f"Cleared all {cleared_count} notifications for user {login_user}")
            else:
                for notif in notifications[:limit]:
                    mastodon.notifications_dismiss(notif["id"])
                    cleared_count += 1
                logger.info(f"Cleared {cleared_count} notifications for user {login_user}")

        # Handle dismissing specific notifications
        if dismiss_ids:
            for notif_id in dismiss_ids:
                try:
                    mastodon.notifications_dismiss(notif_id)
                    dismissed_count += 1
                except Exception as e:
                    logger.error(f"Failed to dismiss notification {notif_id}: {e!s}")
            notifications = [n for n in notifications if n["id"] not in dismiss_ids]
            logger.info(f"Dismissed {dismissed_count} notifications for user {login_user}")

        # Handle marking conversations as read
        if mark_read_conversation_ids:
            for conv_id in mark_read_conversation_ids:
                try:
                    mastodon.conversations_read(conv_id)
                    marked_read_count += 1
                except Exception as e:
                    logger.error(f"Failed to mark conversation {conv_id} as read: {e!s}")
            logger.info(f"Marked {marked_read_count} conversations as read for user {login_user}")

        return notifications

    except MastodonAPIError as e:
        logger.error(f"Mastodon API error for user {login_user}: {e}")
    except MastodonNetworkError as e:
        logger.error(f"Network error while fetching notifications for user {login_user}: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred for user {login_user}: {e}")

    return []


def categorize_notifications(notifications: list[dict]) -> dict[str, list[dict]]:
    """
    Categorize notifications by type.

    Args:
        notifications (List[Dict]): List of notification dictionaries.

    Returns
    -------
        Dict[str, List[Dict]]: Dictionary of notifications categorized by type.
    """
    categories: dict = {
        "follow": [],
        "follow_request": [],
        "favourite": [],
        "reblog": [],
        "mention": [],
        "poll": [],
        "update": [],
        "status": [],
        "admin.sign_up": [],
        "admin.report": [],
    }
    for notif in notifications:
        categories[notif["type"]].append(notif)
    return categories


def print_notifications(notifications: list[AttribAccessDict]) -> None:  # noqa: C901
    """
    Print a formatted list of Mastodon notifications.

    Args:
        notifications (List[Dict]): List of notification dictionaries.
    """
    if not notifications:
        print("No notifications to display.")
        return

    for notif in notifications:
        print(f"\n{'=' * 50}")
        print(f"ID: {notif['id']}")
        print(f"Type: {notif['type']}")
        print(f"Created at: {notif['created_at']}")

        if "account" in notif:
            print(f"From: @{notif['account']['acct']} ({notif['account']['display_name']})")

        if notif["type"] == "mention":
            print(f"Status: {notif['status']['content'][:100]}...")
        elif notif["type"] == "reblog":
            print(f"Reblogged your status: {notif['status']['content'][:100]}...")
        elif notif["type"] == "favourite":
            print(f"Favourited your status: {notif['status']['content'][:100]}...")
        elif notif["type"] == "follow":
            print("Followed you")
        elif notif["type"] == "poll":
            print(f"Poll ended: {notif['status']['poll']['options']}")
        elif notif["type"] == "follow_request":
            print("Requested to follow you")
        elif notif["type"] == "update":
            print(f"Updated status: {notif['status']['content'][:100]}...")
        elif notif["type"].startswith("admin"):
            print(
                f"Admin notification: {notif.get('report', {}).get('content', 'No details available')}"
            )
        else:
            print(
                f"Content: {notif.get('status', {}).get('content', 'No content available')[:100]}..."
            )

    print(f"\nTotal notifications: {len(notifications)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Handle Mastodon notifications and conversations.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("--clear", action="store_true", help="Clear notifications after reading.")
    parser.add_argument("--limit", type=int, help="Maximum number of notifications to fetch.")
    parser.add_argument("--account", type=int, help="Filter notifications from this account ID")
    parser.add_argument("--types", nargs="+", help="Include only these notification types")
    parser.add_argument("--exclude", nargs="+", help="Exclude these notification types")
    parser.add_argument("--mentions-only", action="store_true", help="Only include mentions")
    parser.add_argument("--dismiss", nargs="+", type=int, help="IDs of notifications to dismiss")
    parser.add_argument(
        "--mark-read", nargs="+", type=int, help="IDs of conversations to mark as read"
    )
    parser.add_argument(
        "--print", action="store_true", help="Print detailed notification information"
    )

    args = parser.parse_args()

    notifications = read_notifications(
        args.login_user,
        clear=args.clear,
        limit=args.limit,
        account_id=args.account,
        types=args.types,
        exclude_types=args.exclude,
        mentions_only=args.mentions_only,
        dismiss_ids=args.dismiss,
        mark_read_conversation_ids=args.mark_read,
    )

    if args.print:
        print_notifications(notifications)
    else:
        categorized = categorize_notifications(notifications)
        print("Notification summary:")
        for category, notifs in categorized.items():
            print(f"  {category}: {len(notifs)} notifications")

        print(f"\nTotal notifications: {len(notifications)}")

    if args.clear:
        print(f"Cleared {'all' if args.limit is None else args.limit} notifications.")
    if args.dismiss:
        print(f"Dismissed {len(args.dismiss)} specific notification(s).")
    if args.mark_read:
        print(f"Marked {len(args.mark_read)} conversation(s) as read.")
