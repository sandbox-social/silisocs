import os
from dotenv import load_dotenv
from atproto import Client

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")
ADMIN_UN = os.getenv("BLUESKY_ADMIN_UN")
ADMIN_PW = os.getenv("BLUESKY_ADMIN_PW")

def get_authenticated_client(handle: str, password: str) -> Client:
    """Returns a client authenticated to a non-admin user."""
    client = get_client()
    client.login(handle, password)
    
    return client

def get_client() -> Client:
    """Returns an unauthenticated client."""
    client = Client(PDS_URL)
    
    return client