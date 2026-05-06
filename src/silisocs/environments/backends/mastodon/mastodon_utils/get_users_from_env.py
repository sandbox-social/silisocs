import os
import re

from dotenv import find_dotenv, load_dotenv


def get_users_from_env() -> list[str]:
    """
    Read the .env file and return a list of all users who have passwords defined.

    Returns
    -------
        List[str]: A list of usernames (e.g., ["user0001", "user0002", ...])
    """
    # Load the .env file
    load_dotenv(find_dotenv())

    # Regular expression pattern to match USER*_PASSWORD entries
    pattern = r"^USER(\d+)_PASSWORD"

    # List to store usernames
    users = []

    # Iterate through all environment variables
    for key in os.environ:
        match = re.match(pattern, key)
        if match:
            user_number = match.group(1)
            users.append(f"user{user_number}")

    # Sort the list to ensure consistent ordering
    users.sort()

    return users


# Example usage
if __name__ == "__main__":
    user_list = get_users_from_env()
    print(f"Users found: {user_list}")
    print(f"Total users: {len(user_list)}")
