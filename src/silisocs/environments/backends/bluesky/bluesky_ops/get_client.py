import os
import requests
from dotenv import load_dotenv
from atproto import Client

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")
ADMIN_UN = os.getenv("BLUESKY_ADMIN_UN")
ADMIN_PW = os.getenv("BLUESKY_ADMIN_PW")
ADMIN_DID = "admin"

def get_authenticated_client(handle: str, password: str) -> Client:
    """Returns a client authenticated to a non-admin user."""
    client = get_client()
    client.login(handle, password)
    
    return client

def get_admin_client() -> Client:
    """Returns a client authenticated to the admin user."""
    client = get_client()
    client.login("admin.socialsandbox3.net", ADMIN_PW)
    
    return client

def get_client() -> Client:
    """Returns an unauthenticated client."""
    client = Client(PDS_URL)
    
    return client

def get_admin_token() -> str:
    """Exchange admin credentials for a JWT access token."""
    
    response = requests.post(
        f"{PDS_URL}/xrpc/com.atproto.server.createSession",
        json={
            "identifier": ADMIN_DID,
            "password": ADMIN_PW,
        },
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Failed to authenticate:\n{response.status_code}\n{response.text}"
        )
    return response.json()["accessJwt"]