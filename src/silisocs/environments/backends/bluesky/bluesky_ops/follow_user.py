import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone
from .get_client import get_authenticated_client

load_dotenv()

def follow_user(follower_handle: str, follower_password: str, target_handle: str) -> None:
    """Follows a user on the PDS."""
    client = get_authenticated_client(follower_handle, follower_password)
    
    profile = client.app.bsky.actor.get_profile(
        {"actor": target_handle}
    )

    target_did = profile.did
    
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

def follow_users(follower_handle: str, follower_password: str, target_handles: list[str]) -> None:
    """Follows multiple users on the PDS."""
    for handle in target_handles:
        try:
            follow_user(follower_handle, follower_password, handle)
            print(f"{follower_handle} followed {handle}")
        except Exception as e:
            print(f"Failed to follow {handle}: {e}")
            
def unfollow_user(follower_handle: str, follower_password: str, target_handle: str) -> None:
    """
    Unfollow a user on Bluesky.

    Args:
        follower_handle: Account doing the unfollowing
        follower_password: Account password
        target_actor: Handle or DID to unfollow
    """
    client = get_authenticated_client(follower_handle, follower_password)

    profile = client.app.bsky.actor.get_profile(
        {"actor": target_handle}
    )
    target_did = profile.did

    cursor = None
    found_follow = None

    while True:
        follows = client.app.bsky.graph.get_follows(
            {
                "actor": client.me.did,
                "limit": 100,
                "cursor": cursor,
            }
        )

        for follow in follows.follows:
            if follow.did == target_did:
                found_follow = follow
                break

        if found_follow or not follows.cursor:
            break

        cursor = follows.cursor

    if found_follow is None:
        print(
            f"{follower_handle} is not following "
            f"{target_handle}"
        )
        return

    rkey = found_follow.viewer.follow.split("/")[-1]

    client.com.atproto.repo.delete_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.graph.follow",
            "rkey": rkey,
        }
    )

    print(
        f"{follower_handle} unfollowed "
        f"{profile.handle}"
    )