import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone
from .get_client import get_authenticated_client

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")

def follow_user(follower_handle: str, follower_password: str, target_did: str) -> None:
    """Follows a user on the PDS."""
    client = get_authenticated_client(follower_handle, follower_password)
    client.com.atproto.repo.create_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.graph.follow",
            "record": {
                "$type": "app.bsky.graph.follow",
                "subject": target_did,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            },
        }
    )

def follow_users(follower_handle: str, follower_password: str, target_dids: list[str]) -> None:
    """Follows multiple users on the PDS."""
    for did in target_dids:
        try:
            follow_user(follower_handle, follower_password, did)
            print(f"{follower_handle} followed {did}")
        except Exception as e:
            print(f"Failed to follow {did}: {e}")