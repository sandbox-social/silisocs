import os
import requests
import base64
from .get_client import get_admin_token

from dotenv import load_dotenv

load_dotenv()
ADMIN_PW = os.getenv("BLUESKY_ADMIN_PW")
PDS_URL = os.getenv("BLUESKY_BASE_URL")

def list_users() -> list[dict]:
    """Lists existing users on the PDS."""
    credentials = base64.b64encode(f"admin:{ADMIN_PW}".encode()).decode()
    headers = {"Authorization": f"Basic {credentials}"}

    response = requests.get(
        f"{PDS_URL}/xrpc/com.atproto.sync.listRepos",
        params={"limit": 100},
    )
    if response.status_code != 200:
        raise RuntimeError(f"Failed to list repos:\n{response.status_code}\n{response.text}")
    
    dids = [repo["did"] for repo in response.json().get("repos", [])]

    users = []
    for did in dids:
        response = requests.get(
            f"{PDS_URL}/xrpc/com.atproto.admin.getAccountInfo",
            headers=headers,
            params={"did": did},
        )
        if response.status_code != 200:
            raise RuntimeError(f"Failed to get account info for {did}:\n{response.status_code}\n{response.text}")
        
        data = response.json()
        users.append({
            "did": data.get("did"),
            "handle": data.get("handle"),
            "email": data.get("email"),
        })

    return users