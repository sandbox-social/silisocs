"""Utility module for handling and checking environment variables."""

import os

from dotenv import find_dotenv, load_dotenv

from silisocs.environments.backends.mastodon.logging_config import logger


def load_env() -> bool:
    """Load environment variables from .env file."""
    return load_dotenv(find_dotenv())


def get_env_variable(var_name: str) -> str:
    """
    Get the value of an environment variable.

    Args:
        var_name (str): The name of the environment variable to retrieve.

    Returns
    -------
        str: The value of the environment variable.

    Raises
    ------
        ValueError: If the environment variable is not set.
    """
    value = os.getenv(var_name)
    if value is None:
        logger.error(f"Environment variable {var_name} is not set.")
        raise ValueError(f"Environment variable {var_name} is not set.")
    return value


def mask_password(value: str) -> str:
    """Mask the password with asterisks, showing only the first and last character."""
    if len(value) <= 2:
        return "*" * len(value)
    return value[0] + "*" * (len(value) - 2) + value[-1]


def check_env() -> None:
    """Check the environment variables and print their values."""
    # Attempt to load the .env file
    if load_env():
        logger.info("Successfully loaded .env file from:" + find_dotenv())
    else:
        logger.warning("Warning: .env file not found or empty.")

    # List of expected environment variables
    expected_vars = [
        "API_BASE_URL",
        "EMAIL_PREFIX",
        "MASTODON_CLIENT_ID",
        "MASTODON_CLIENT_SECRET",
    ]

    # Check and print each environment variable
    for var in expected_vars:
        value = os.getenv(var)
        if value:
            if var.endswith(("_SECRET", "_ID")):
                logger.info(f"{var}: {mask_password(value)}")
            else:
                logger.info(f"{var}: {value}")
        else:
            logger.warning(f"{var}: Not set")

    # Check for user passwords
    user_passwords = [var for var in os.environ if var.endswith("_PASSWORD")]
    for var in user_passwords:
        value = os.getenv(var)
        if value:
            masked_value = mask_password(value)
            logger.info(f"{var}: {masked_value}")
        else:
            logger.warning(f"{var}: Not set")


if __name__ == "__main__":
    check_env()
else:
    # Load environment variables when this module is imported
    load_env()
