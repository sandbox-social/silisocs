from pathlib import Path
import subprocess
import os
from dotenv import load_dotenv
from .create_accounts import create_bluesky_account
from .list_users import list_users

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")

def delete_user(did: str) -> None:
    """Deletes a user corresponding to the given did (decentralized identifier)."""
    script = Path(__file__).parent / "pdsadmin.sh"
    result = subprocess.run(
        [str(script), "account", "delete", did],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to delete {did}:\n{result.stderr}")
    print(f"Deleted {did}")
    
def reset_user(handle: str, password: str, email: str, did: str) -> dict:
    """Resets a user by deleting and recreating it."""
    delete_user(PDS_URL, did)
    account = create_bluesky_account(PDS_URL, handle, password, email)
    print(f"Reset {handle} -> {account['did']}")
    return account

def reset_bluesky_server() -> None:
    """Resets server by resetting all individual agents."""
    users = list_users()

    for user in users:
        delete_user(user["did"])
        create_bluesky_account(
            pds_url=PDS_URL,
            handle=user["handle"],
            password="password",
            email=user["email"],
        )

    print(f"Cleared and reset {len(users)} accounts")