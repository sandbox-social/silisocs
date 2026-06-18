from dotenv import load_dotenv
from .get_client import get_authenticated_client
import os
import requests

load_dotenv()

FEEDGEN_DID = os.getenv("FEED_GENERATOR_PUBLISHER_DID")
FEEDGEN_URL = os.getenv("FEED_GENERATOR_URL")

def init_feed():
    feedgen_session = requests.Session()
    return feedgen_session, FEEDGEN_URL

def get_recommendation_uris(handle: str, feed_name: str = "whats-alf", limit: int = 50) -> list[str]:
    """Gets recommendation post URIs from your feed generator."""
    feedgen_session, feedgen_url = init_feed()

    feedgen_uri = f"at://{FEEDGEN_DID}/app.bsky.feed.generator/{feed_name}"

    response = feedgen_session.get(
        f"{feedgen_url}/xrpc/app.bsky.feed.getFeedSkeleton",
        params={
            "feed": feedgen_uri,
            "limit": limit,
        },
    )
    response.raise_for_status()

    data = response.json()
    return [item["post"] for item in data.get("feed", [])]

def hydrate_posts(uris: list[str]) -> list[dict]:
    """Hydrates post URIs using the connected PDS/AppView client."""
    
    client = get_authenticated_client()
    
    if not uris:
        return []

    response = client.app.bsky.feed.get_posts({
        "uris": uris,
    })

    posts = []

    for post in response.posts:
        record = post.record

        posts.append({
            "uri": post.uri,
            "cid": post.cid,
            "text": getattr(record, "text", ""),
            "author": {
                "did": post.author.did,
                "handle": post.author.handle,
                "display_name": getattr(post.author, "display_name", None),
            },
        })

    return posts


def get_recommendations(handle: str, feed_name: str = "whats-alf", limit: int = 50) -> list[dict]:
    """Gets recommendation URIs from the feed generator, then hydrates them."""
    uris = get_recommendation_uris(handle, feed_name, limit=limit)
    return hydrate_posts(uris[:limit])