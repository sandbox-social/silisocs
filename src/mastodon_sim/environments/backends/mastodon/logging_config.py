"""Logging configuration for the application."""

import sys

from loguru import logger

# Remove any pre-configured handlers
logger.remove()

# Add a new handler with the desired configuration
logger.add(
    sys.stdout, level="INFO", format="{time} {level} {message}", backtrace=True, diagnose=True
)

# Optionally, you can add more handlers for file logging, etc.
logger.add(
    "app.log",
    rotation="1 week",
    retention="1 month",
    level="INFO",
    format="{time} {level} {message}",
)


# Function to configure logging (if needed to do additional setup)
def configure_logging():
    """Configure logging for the application."""
