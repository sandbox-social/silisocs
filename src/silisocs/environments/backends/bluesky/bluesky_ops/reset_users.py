from pathlib import Path
import os
import base64
import requests
from dotenv import load_dotenv
from .create_accounts import create_bluesky_account
from .list_users import list_users
from .get_client import get_authenticated_client, get_admin_client
load_dotenv()

ADMIN_PW = os.getenv("BLUESKY_ADMIN_PW")
PDS_URL = os.getenv("BLUESKY_BASE_URL")

def delete_user(did: str) -> None:
    """Deletes a user corresponding to the given did (decentralized identifier)."""

    credentials = base64.b64encode(
        f"admin:{ADMIN_PW}".encode()
    ).decode()

    headers = {
        "Authorization": f"Basic {credentials}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        f"{PDS_URL}/xrpc/com.atproto.admin.deleteAccount",
        headers=headers,
        json={"did": did},
    )

    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to delete {did}:\n"
            f"{response.status_code}\n"
            f"{response.text}"
        )

    print(f"{did} deleted")
        
def reset_user(handle: str, password: str, email: str, did: str) -> dict:
    """Resets a user by deleting and recreating it."""
    delete_user(did)
    account = create_bluesky_account(handle, password, email)
    print(f"Reset {handle} -> {account['did']}")
    return account

def reset_bluesky_server(num_users: int) -> None:
    """Resets server by resetting all individual agents."""
    users = list_users()
    
    #Sorts by agent0 -> agent1 -> agent2 ...
    users.sort(key=lambda u: int(u["handle"].split(".")[0].replace("agent", "")))
    
    for user in users[:num_users]:
        reset_user(handle=user["handle"], password="password", email=user["email"], did=user["did"])

    print(f"Cleared and reset {len(users)} accounts")