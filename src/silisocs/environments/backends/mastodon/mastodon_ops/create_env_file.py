"""
create_env_file.py.

This script creates a Mastodon app with the specified domain if one does not already exist,
and manages the .env file to store necessary credentials and configurations. It ensures that
the .env file contains API_BASE_URL and EMAIL_PREFIX if created anew, and provides options
to overwrite existing values.

Usage:
    python create_env_file.py --domain <domain> --app_name <app_name> \
        --scopes <scopes> --overwrite --email_prefix <email_prefix>
"""

import argparse
import os
from pathlib import Path

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.create_app import create_app
from silisocs.environments.backends.mastodon.mastodon_ops.env_utils import load_env

# Define the project root (6 levels up from mastodon_ops/)
PROJECT_ROOT = Path(__file__).resolve().parents[6]


def ensure_env_file_exists(env_path: str, api_base_url: str, email_prefix: str) -> None:
    """Ensure the .env file exists and initialize with API_BASE_URL and EMAIL_PREFIX if new."""
    if not os.path.exists(env_path):
        with open(env_path, "a") as env_file:
            env_file.write(f"API_BASE_URL={api_base_url}\n")
            env_file.write(f"EMAIL_PREFIX={email_prefix}\n")
        logger.info(f"Created new .env file at {env_path}")
        logger.info(f"Added API_BASE_URL={api_base_url}")
        logger.info(f"Added EMAIL_PREFIX={email_prefix}")
    else:
        logger.info(f".env file found at {env_path}")


def read_env_file(env_path: str) -> list[str]:
    """Read the current .env file content."""
    try:
        with open(env_path) as env_file:
            return env_file.readlines()
    except Exception as e:
        logger.error(f"Failed to read .env file at {env_path}: {e}")
        raise


def write_env_file(env_path: str, lines: list[str]) -> None:
    """Write updated content back to the .env file."""
    try:
        with open(env_path, "w") as env_file:
            env_file.writelines(lines)
        logger.info(f"Updated .env file at {env_path}")
    except Exception as e:
        logger.error(f"Failed to write to .env file at {env_path}: {e}")
        raise


def update_env_file(env_path: str, client_id: str, client_secret: str, overwrite: bool) -> None:
    """Update the .env file with the new client ID and client secret."""
    lines = read_env_file(env_path)
    new_lines = []
    found_client_id = False
    found_client_secret = False

    for line in lines:
        if line.startswith("MASTODON_CLIENT_ID="):
            if overwrite:
                new_lines.append(f"MASTODON_CLIENT_ID={client_id}\n")
                logger.info("Overwriting existing MASTODON_CLIENT_ID")
            else:
                new_lines.append(line)
            found_client_id = True
        elif line.startswith("MASTODON_CLIENT_SECRET="):
            if overwrite:
                new_lines.append(f"MASTODON_CLIENT_SECRET={client_secret}\n")
                logger.info("Overwriting existing MASTODON_CLIENT_SECRET")
            else:
                new_lines.append(line)
            found_client_secret = True
        else:
            new_lines.append(line)

    if not found_client_id:
        new_lines.append(f"MASTODON_CLIENT_ID={client_id}\n")
        logger.info("Adding new MASTODON_CLIENT_ID")
    if not found_client_secret:
        new_lines.append(f"MASTODON_CLIENT_SECRET={client_secret}\n")
        logger.info("Adding new MASTODON_CLIENT_SECRET")

    write_env_file(env_path, new_lines)


def create_app_and_env_if_not_exists(
    app_name: str, api_base_url: str, scopes: list[str], overwrite: bool, email_prefix: str
) -> tuple[str, str]:
    """Get or create Mastodon app credentials and update or create the .env file."""
    env_loaded = load_env()

    if env_loaded:
        try:
            client_id = os.getenv("MASTODON_CLIENT_ID")
            client_secret = os.getenv("MASTODON_CLIENT_SECRET")
            if client_id and client_secret and not overwrite:
                logger.info("Mastodon app credentials found in .env file, skipping creation")
                if client_id is None or client_secret is None:
                    raise ValueError(
                        "Environment variables for Mastodon app credentials are missing."
                    )
                return client_id, client_secret
        except ValueError:
            logger.info("Mastodon app credentials not found in .env file, creating new app")
        except Exception as e:
            logger.error(f"Error loading environment variables: {e}")
            raise

    # Create a new Mastodon app
    client_id, client_secret = create_app(app_name, api_base_url, scopes)

    if client_id is None or client_secret is None:
        raise ValueError("Failed to create Mastodon app credentials.")

    env_path = str(PROJECT_ROOT / ".env")

    try:
        ensure_env_file_exists(env_path, api_base_url, email_prefix)
        update_env_file(env_path, client_id, client_secret, overwrite)
    except Exception as e:
        logger.error(f"Failed to update .env file with new app credentials: {e}")
        raise

    return client_id, client_secret


def main() -> None:
    """Parse command line arguments and create a Mastodon app and .env file if necessary."""
    parser = argparse.ArgumentParser(
        description="Create a new Mastodon app and update the .env file with the app credentials."
    )
    parser.add_argument(
        "--domain",
        type=str,
        default="social-sandbox.com",
        help="The domain of the Mastodon instance.",
    )
    parser.add_argument(
        "--app_name", type=str, default="MyMastodonApp", help="The name of the Mastodon app."
    )
    parser.add_argument(
        "--scopes",
        type=str,
        nargs="+",
        default=["read", "write", "follow"],
        help="The scopes for the app.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing values in the .env file if they exist.",
    )
    parser.add_argument(
        "--email_prefix",
        type=str,
        default="austinmw89",
        help="The email prefix to be added to the .env file.",
    )

    args = parser.parse_args()

    api_base_url = f"https://{args.domain}"
    try:
        create_app_and_env_if_not_exists(
            args.app_name, api_base_url, args.scopes, args.overwrite, args.email_prefix
        )
        logger.info("Mastodon app credentials are ready in the environment file")
    except Exception as e:
        logger.error(f"Failed to create Mastodon app or update .env file: {e}")


if __name__ == "__main__":
    main()
