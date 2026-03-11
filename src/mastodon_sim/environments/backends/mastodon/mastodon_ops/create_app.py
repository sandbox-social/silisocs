"""
create_app.py.

This script creates a new Mastodon app with the specified name, domain, and scopes.

This only needs to be done once (per server, or when distributing rather than hosting
an application, most likely per device and server).

Usage:
    python create_app.py --app_name <app_name> --domain <domain> --scopes <scopes>
"""

import argparse

from mastodon import Mastodon
from mastodon.Mastodon import MastodonNetworkError

from mastodon_sim.environments.backends.mastodon.logging_config import logger


def create_app(app_name: str, api_base_url: str, scopes: list[str]) -> tuple[str, str]:
    """Create a Mastodon app with the specified parameters."""
    try:
        logger.info("Creating new Mastodon app...")
        logger.info(f"App name: {app_name}")
        logger.info(f"API base URL: {api_base_url}")
        logger.info(f"Scopes: {scopes}")
        client_id, client_secret = Mastodon.create_app(
            app_name, api_base_url=api_base_url, scopes=scopes
        )
        logger.info("Created new Mastodon app")
        return client_id, client_secret
    except MastodonNetworkError as e:
        logger.error(f"Failed to create Mastodon app: {e}")
        raise
    except Exception as e:
        logger.error(f"Failed to create Mastodon app: {e}")
        raise


def main() -> None:
    """Parse command line arguments and create a Mastodon app."""
    parser = argparse.ArgumentParser(
        description="Create a new Mastodon app with the specified name, domain, and scopes."
    )
    parser.add_argument("--app_name", type=str, required=True, help="The name of the Mastodon app.")
    parser.add_argument(
        "--domain", type=str, required=True, help="The domain of the Mastodon instance."
    )
    parser.add_argument(
        "--scopes",
        type=str,
        nargs="+",
        default=["read", "write", "follow"],
        help="The scopes for the app.",
    )

    args = parser.parse_args()

    api_base_url = f"https://{args.domain}"
    create_app(args.app_name, api_base_url, args.scopes)


if __name__ == "__main__":
    main()
