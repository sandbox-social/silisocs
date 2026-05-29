import os
import requests
from dotenv import load_dotenv
from .get_client import get_authenticated_client

load_dotenv()

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
    
def read_profile(handle: str, password: str, target: str) -> tuple[str, str]:
    """Reads the target user's profile in Bluesky"""
    
    client = get_authenticated_client(
        handle,
        password,
    )

    profile = client.app.bsky.actor.get_profile(
        {"actor": target}
    )

    display_name = (
        profile.display_name
        if profile.display_name
        else profile.handle
    )

    bio = (
        profile.description
        if profile.description
        else ""
    )

    return display_name, bio