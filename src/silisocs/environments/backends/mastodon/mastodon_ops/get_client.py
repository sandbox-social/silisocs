"""Provides utility functions for Mastodon client."""

from mastodon import Mastodon
from mastodon.errors import MastodonError

from silisocs.environments.backends.mastodon.logging_config import logger
from silisocs.environments.backends.mastodon.mastodon_ops.env_utils import get_env_variable


def get_client() -> Mastodon:
    """
    Get the Mastodon client using environment variables for configuration.

    Returns
    -------
        Mastodon: An instance of the Mastodon client.
    """
    try:
        api_base_url = get_env_variable("API_BASE_URL")
        client_id = get_env_variable("MASTODON_CLIENT_ID")
        client_secret = get_env_variable("MASTODON_CLIENT_SECRET")

        mastodon = Mastodon(
            client_id=client_id, client_secret=client_secret, api_base_url=api_base_url
        )

        logger.debug("Successfully created Mastodon client.")
        return mastodon
    except MastodonError as e:
        logger.exception(f"An error occurred while creating the Mastodon client: {e}")
        raise
    except ValueError as e:
        logger.error(f"Environment variable error: {e}")
        raise
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
        raise
