import os
import requests
from dotenv import load_dotenv
from .get_client import get_authenticated_client

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")

def update_profile(handle: str, password: str, display_name: str, bio: str) -> None:
    """Updates the profile with the given handle with a new display_name and bio."""
    
    client = get_authenticated_client(handle, password)
    
    profile = client.app.bsky.actor.get_profile(
        {"actor": handle}
    )

    client.com.atproto.repo.put_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.actor.profile",
            "rkey": "self",
            "record": {
                "$type": "app.bsky.actor.profile",
                "displayName": display_name,
                "description": bio,

                # preserve existing fields if present
                "avatar": getattr(profile, "avatar", None),
                "banner": getattr(profile, "banner", None),
            },
        }
    )