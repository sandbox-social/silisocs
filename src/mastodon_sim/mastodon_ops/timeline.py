"""Retrieve posts from a user's timeline on Mastodon."""

import argparse
import base64

import mastodon
from dotenv import find_dotenv, load_dotenv

from mastodon_sim.logging_config import logger
from mastodon_sim.mastodon_ops.get_client import get_client
from mastodon_sim.mastodon_ops.login import login


def encode_media_to_base64(media_url, session):
    """Encode media from a URL into base64 format."""
    try:
        response = session.get(media_url)
        response.raise_for_status()
        return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to encode media to base64: {e}")
        return None


def get_public_timeline(limit: None | int = 10) -> mastodon.utility.AttribAccessList | list:
    """Retrieve the public timeline on Mastodon.

    Args:
        limit (int): The number of posts to retrieve. Default is 10.

    Returns
    -------
        list: A list of posts from the public timeline.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        mastodon = get_client()
        session = mastodon.session

        # Retrieve the public timeline
        timeline = mastodon.timeline_public(limit=limit)

        # Convert media URLs to base64
        for post in timeline:
            if post.get("media_attachments"):
                for media in post["media_attachments"]:
                    media["base64"] = encode_media_to_base64(media["url"], session)
                    print(media["url"])
        logger.info(f"Retrieved {len(timeline)} posts from the public timeline.")
        return timeline
    except ValueError as e:
        logger.error(f"Error: {e}")
        return []
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return []


def get_own_timeline(
    login_user: str, limit: int | None = 10, filter_type: str = "all"
) -> mastodon.utility.AttribAccessList | list:
    """Retrieve posts from the authenticated user's timeline on Mastodon.

    Args:
        login_user (str): The user to log in with.
        limit (int | None): The number of posts to retrieve. Default is 10. Use None for no limit.
        filter_type (str): Type of filtering to apply. Options are:
            'all': No filtering (default)
            'self': Only posts by the login user
            'others': Only posts not by the login user

    Returns
    -------
        mastodon.utility.AttribAccessList | list: A list of posts from the authenticated user's timeline.

    Raises
    ------
        ValueError: If an invalid filter_type is provided.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    if filter_type not in ["all", "self", "others"]:
        raise ValueError("Invalid filter_type. Must be 'all', 'self', or 'others'.")

    try:
        access_token = login(login_user)

        logger.debug(f"{login_user} attempting to retrieve their own timeline...")
        mastodon = get_client()
        mastodon.access_token = access_token
        session = mastodon.session

        # Retrieve the authenticated user's timeline
        timeline = mastodon.timeline_home(limit=limit)

        # Apply filtering based on filter_type
        if filter_type == "self":
            timeline = [post for post in timeline if post["account"]["acct"] == login_user]
        elif filter_type == "others":
            timeline = [post for post in timeline if post["account"]["acct"] != login_user]

        # Convert media URLs to base64
        for post in timeline:
            if post.get("media_attachments"):
                for media in post["media_attachments"]:
                    media["base64"] = encode_media_to_base64(media["url"], session)

        logger.info(
            f"{login_user} retrieved {len(timeline)} posts from their timeline (filter: {filter_type})."
        )
        return timeline
    except ValueError as e:
        logger.error(f"Error: {e}")
        raise e
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise e


def get_user_timeline(
    login_user: str, target_user: str, limit: None | int = None
) -> mastodon.utility.AttribAccessList | list:
    """Retrieve posts from a user's timeline on Mastodon.

    Args:
        login_user (str): The user to log in with.
        target_user (str): The target user to retrieve posts from.
        limit (int): The number of posts to retrieve. Default is 10.

    Returns
    -------
        list: A list of posts from the target user's timeline.
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)

        logger.debug(f"{login_user} attempting to retrieve posts from {target_user}...")
        mastodon = get_client()
        mastodon.access_token = access_token
        session = mastodon.session

        print(mastodon.account_verify_credentials())
        # Search for the user and get their ID
        account = mastodon.account_search(target_user, limit=1)
        if account:
            target_user_id = account[0]["id"]
            timeline = mastodon.account_statuses(target_user_id, limit=limit)

            # Convert media URLs to base64
            for post in timeline:
                if post.get("media_attachments"):
                    for media in post["media_attachments"]:
                        media["base64"] = encode_media_to_base64(media["url"], session)

            logger.info(
                f"{login_user} retrieved {len(timeline)} posts from {target_user}'s timeline."
            )
            return timeline
        logger.warning(f"User {target_user} not found.")
        return []
    except ValueError as e:
        logger.error(f"Error: {e}")
        return []
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        return []


def print_timeline(timeline) -> None:
    """
    Print the most relevant details from a Mastodon timeline.

    Args:
        timeline (list): A list of dictionaries containing post information.
    """
    if len(timeline):
        print("-" * 40)
    for post in timeline:
        post_id = post.get("id")
        created_at = post.get("created_at")
        username = post.get("account", {}).get("username")
        display_name = post.get("account", {}).get("display_name", username)
        content = post.get("content")
        url = post.get("url")
        favourites_count = post.get("favourites_count")
        reblogs_count = post.get("reblogs_count")

        print(f"Post ID: {post_id}")
        print(f"Created At: {created_at}")
        print(f"User: {display_name} (@{username})")
        print(f"Content: {content}")
        print(f"URL: {url}")
        print(f"Favourites: {favourites_count}, Reblogs: {reblogs_count}")
        print("-" * 40)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Retrieve posts from a user's timeline or the public timeline on Mastodon."
    )
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument(
        "--target_user", help="The target user to retrieve posts from (for user timeline)."
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="Retrieve the public timeline instead of a user timeline.",
    )
    parser.add_argument(
        "--limit", type=int, default=10, help="The number of posts to retrieve. Default is 10."
    )

    args = parser.parse_args()
    if args.public:
        timeline = get_public_timeline(limit=args.limit)
    else:
        if not args.target_user:
            parser.error("--target_user is required when --public is not specified.")
        timeline = get_user_timeline(args.login_user, args.target_user, limit=args.limit)
    print_timeline(timeline)
