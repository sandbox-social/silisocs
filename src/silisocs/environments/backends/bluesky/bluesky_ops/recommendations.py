from dotenv import load_dotenv
import os
import requests

load_dotenv()

FEEDGEN_DID = os.getenv("FEED_GENERATOR_PUBLISHER_DID")
FEEDGEN_URL = os.getenv("FEED_GENERATOR_URL")

def init_feed():
    """Initializes a session to the feed generator service."""
    
    feedgen_session = requests.Session()
    feedgen_url = FEEDGEN_URL
    
    return feedgen_session, feedgen_url
        
def get_recommendations(handle: str, feed_name: str = "whats-alf") -> list[str]:
    """Gets recommendations for the given handle from the specified feed."""
    feedgen_session, feedgen_url = init_feed()
    feedgen_uri = f"at://{FEEDGEN_DID}/app.bsky.feed.generator/{feed_name}"
    response = feedgen_session.get(
        f"{feedgen_url}/xrpc/app.bsky.feed.getFeedSkeleton",
        params={"feed": feedgen_uri}
    )
    response.raise_for_status()
    data = response.json()
    return [item["post"] for item in data.get("feed", [])]