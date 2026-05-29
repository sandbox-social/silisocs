from .get_client import get_authenticated_client
from datetime import datetime, timezone

def post(handle: str, password: str, post_text: str) -> None:
    """Creates a Bluesky post for the given handle with post_text."""
    client = get_authenticated_client(handle, password)

    response = client.com.atproto.repo.create_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": post_text,
                "createdAt": datetime.now(
                    timezone.utc
                ).isoformat(),
            },
        }
    )

    print(f"{handle} posted: {post_text}")

    return {
        "uri": response.uri,
        "cid": response.cid,
    }

def reply_to_post(handle: str, password: str, post_text: str, parent_uri: str, parent_cid: str, root_uri: str | None = None, root_cid: str | None = None) -> dict:
    """
    Reply to a Bluesky post.

    Args:
        handle: Account posting the reply
        password: Account password
        post_text: Reply text
        parent_uri: URI of post being replied to
        parent_cid: CID of post being replied to
        root_uri: Thread root URI (optional)
        root_cid: Thread root CID (optional)

    Returns:
        Created reply metadata
    """
    client = get_authenticated_client(
        handle,
        password,
    )
    if root_uri is None:
        root_uri = parent_uri
    if root_cid is None:
        root_cid = parent_cid

    response = client.com.atproto.repo.create_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.feed.post",
            "record": {
                "$type": "app.bsky.feed.post",
                "text": post_text,
                "createdAt": datetime.now(
                    timezone.utc
                ).isoformat(),
                "reply": {
                    "root": {
                        "uri": root_uri,
                        "cid": root_cid,
                    },
                    "parent": {
                        "uri": parent_uri,
                        "cid": parent_cid,
                    },
                },
            },
        }
    )

    print(f"{handle} replied: {post_text}")

    return {
        "uri": response.uri,
        "cid": response.cid,
    }
    
def like_post(handle: str, password: str, post_uri: str, post_cid: str) -> dict:
    """
    Like a Bluesky post.

    Args:
        handle: Account liking the post
        password: Account password
        post_uri: URI of the post to like
        post_cid: CID of the post to like

    Returns:
        Metadata of created like record
    """
    client = get_authenticated_client(handle, password)

    response = client.com.atproto.repo.create_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.feed.like",
            "record": {
                "$type": "app.bsky.feed.like",
                "subject": {
                    "uri": post_uri,
                    "cid": post_cid,
                },
                "createdAt": datetime.now(
                    timezone.utc
                ).isoformat(),
            },
        }
    )

    print(f"{handle} liked {post_uri}")

    return {
        "uri": response.uri,
        "cid": response.cid,
    }
    
def repost_post(handle: str, password: str, post_uri: str, post_cid: str) -> dict:
    """Repost (boost) a Bluesky post."""
    client = get_authenticated_client(handle, password)

    response = client.com.atproto.repo.create_record(
        {
            "repo": client.me.did,
            "collection": "app.bsky.feed.repost",
            "record": {
                "$type": "app.bsky.feed.repost",
                "subject": {
                    "uri": post_uri,
                    "cid": post_cid,
                },
                "createdAt": datetime.now(
                    timezone.utc
                ).isoformat(),
            },
        }
    )

    print(f"{handle} reposted {post_uri}")

    return {
        "uri": response.uri,
        "cid": response.cid,
    }