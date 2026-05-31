"""Post a status on Mastodon.

This script allows you to post statuses on Mastodon with various options including
media uploads, polls, content warnings, and more.

Usage examples:

1. Post a simple status:
   python post_status.py user0001 "Hello, Mastodon!"

2. Post a status with custom visibility:
   python post_status.py user0001 "This is a private post" --visibility private

3. Post a status with a content warning:
   python post_status.py user0001 "Spoiler content" --spoiler-text "Content Warning"

4. Schedule a status:
   python post_status.py user0001 "This is a scheduled post" --schedule 60

5. Reply to another status:
   python post_status.py user0001 "This is a reply" --in-reply-to 123456

6. Post with media attachments:
   python post_status.py user0001 "Check out these pictures!" --media path/to/image1.jpg path/to/image2.png

7. Create a poll:
   python post_status.py user0001 "What's your favorite color?" --poll-option "Red" --poll-option "Blue" --poll-option "Green" --poll-expires-in 86400

8. Post with a specific language:
   python post_status.py user0001 "Bonjour, Mastodon!" --language fr

9. Use Markdown formatting (Pleroma-specific):
   python post_status.py user0001 "**Bold** and *italic* text" --content-type text/markdown

10. Quote another status (Fedibird-specific):
    python post_status.py user0001 "Interesting point!" --quote 789012

For more information on each option, use the --help flag:
python post_status.py --help
"""

import argparse
import uuid
from datetime import UTC, datetime, timedelta

from dotenv import find_dotenv, load_dotenv

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.login import login


def post_status(  # noqa: PLR0913
    login_user: str,
    status: str,
    visibility: str | None = None,
    sensitive: bool = False,
    spoiler_text: str | None = None,
    language: str | None = None,
    scheduled_at: datetime | None = None,
    in_reply_to_id: int | None = None,
    media_files: list[str] | None = None,
    idempotency_key: str | None = None,
    content_type: str | None = None,
    poll_options: list[str] | None = None,
    poll_expires_in: int | None = None,
    poll_multiple: bool = False,
    poll_hide_totals: bool = False,
    quote_id: int | None = None,
):
    """Post a status on Mastodon.

    Args:
        login_user (str): The user to log in with.
        status (str): The status to post.
        visibility (str): The visibility of the status ('direct', 'private', 'unlisted', or 'public').
        sensitive (bool): Whether the status should be marked as sensitive.
        spoiler_text (str): Text to be shown as a warning before the status.
        language (str): The language of the status (ISO 639-1 or 639-3 code).
        scheduled_at (datetime): When to schedule the status post.
        in_reply_to_id (int): ID of the status to reply to.
        media_files (list[str]): List of paths to media files to upload.
        idempotency_key (str): Unique identifier for the status post attempt.
        content_type (str): Content type for Pleroma servers.
        poll_options (list[str]): List of poll options.
        poll_expires_in (int): Number of seconds the poll should last.
        poll_multiple (bool): Whether multiple choices are allowed in the poll.
        poll_hide_totals (bool): Whether to hide poll totals until the poll ends.
        quote_id (int): ID of the status to quote (Fedibird-specific feature).
    """
    load_dotenv(find_dotenv())  # Load environment variables from .env file

    try:
        access_token = login(login_user)
        mastodon = get_client()
        mastodon.access_token = access_token

        logger.debug(f"{login_user} attempting to post a status...")

        # Handle media uploads
        media_ids = None
        if media_files:
            media_ids = []
            for media_file in media_files:
                media = mastodon.media_post(media_file)
                media_ids.append(media["id"])

        # Create poll if options are provided
        poll = None
        if poll_options:
            poll = mastodon.make_poll(
                options=poll_options,
                expires_in=poll_expires_in,
                multiple=poll_multiple,
                hide_totals=poll_hide_totals,
            )

        # Post the status with all specified parameters
        status_dict = mastodon.status_post(
            status=status,
            visibility=visibility,
            sensitive=sensitive,
            spoiler_text=spoiler_text,
            language=language,
            scheduled_at=scheduled_at,
            in_reply_to_id=in_reply_to_id,
            media_ids=media_ids,
            idempotency_key=idempotency_key,
            content_type=content_type,
            poll=poll,
            quote_id=quote_id,
        )

        if scheduled_at:
            logger.info(f"{login_user} successfully scheduled the status for {scheduled_at}.")
        else:
            logger.info(f"{login_user} successfully posted the status.")

        logger.debug(f"Status ID: {status_dict['id']}")
        return status_dict
    except ValueError as e:
        logger.error(f"Error: {e}")
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post a status on Mastodon.")
    parser.add_argument("login_user", help="The user to log in with.")
    parser.add_argument("status", help="The status to post.")
    parser.add_argument(
        "--visibility",
        choices=["direct", "private", "unlisted", "public"],
        help="The visibility of the status.",
    )
    parser.add_argument("--sensitive", action="store_true", help="Mark the status as sensitive.")
    parser.add_argument("--spoiler-text", help="Text to be shown as a warning before the status.")
    parser.add_argument("--language", help="The language of the status (ISO 639-1 or 639-3 code).")
    parser.add_argument(
        "--schedule", type=int, help="Schedule the status to be posted after specified minutes."
    )
    parser.add_argument("--in-reply-to", type=int, help="ID of the status to reply to.")
    parser.add_argument("--media", nargs="+", help="Paths to media files to upload.")
    parser.add_argument(
        "--content-type",
        choices=["text/plain", "text/markdown", "text/html", "text/bbcode"],
        help="Content type for Pleroma servers.",
    )
    parser.add_argument(
        "--poll-option", action="append", help="Add a poll option. Can be used multiple times."
    )
    parser.add_argument(
        "--poll-expires-in", type=int, help="Number of seconds the poll should last."
    )
    parser.add_argument(
        "--poll-multiple", action="store_true", help="Allow multiple choices in the poll."
    )
    parser.add_argument(
        "--poll-hide-totals", action="store_true", help="Hide poll totals until the poll ends."
    )
    parser.add_argument(
        "--quote", type=int, help="ID of the status to quote (Fedibird-specific feature)."
    )

    args = parser.parse_args()

    scheduled_at = None
    if args.schedule:
        scheduled_at = datetime.now(UTC) + timedelta(minutes=args.schedule)

    post_status(
        args.login_user,
        args.status,
        visibility=args.visibility,
        sensitive=args.sensitive,
        spoiler_text=args.spoiler_text,
        language=args.language,
        scheduled_at=scheduled_at,
        in_reply_to_id=args.in_reply_to,
        media_files=args.media,
        idempotency_key=str(uuid.uuid4()),  # Generate a random UUID for idempotency
        content_type=args.content_type,
        poll_options=args.poll_option,
        poll_expires_in=args.poll_expires_in,
        poll_multiple=args.poll_multiple,
        poll_hide_totals=args.poll_hide_totals,
        quote_id=args.quote,
    )
