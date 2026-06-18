from .get_client import get_authenticated_client
from .recommendations import get_recommendations

def get_timeline(handle: str, password: str, limit: int = 50, feed_name: str = None) -> list[dict]:
    """Gets the home timeline for a user."""
    client = get_authenticated_client(handle, password)

    timeline = client.app.bsky.feed.get_timeline(
        {"limit": limit}
    )

    posts = []

    for item in timeline.feed:
        post = item.post

        posts.append({
            "uri": post.uri,
            "cid": post.cid,
            "text": post.text,
            "author": {
                "did": post.author.did,
                "handle": post.author.handle,
                "display_name": getattr(post.author, "display_name", None),
            },
        })

    if feed_name:
        recommended_posts = get_recommendations(handle, feed_name)
        
        for post in recommended_posts:
            posts.append(post)
            
    return posts

def print_timeline(handle: str, password: str, limit: int = 50) -> None:
    """Prints a user's home timeline in a readable format."""
    timeline = get_timeline(
        handle=handle,
        password=password,
        limit=limit,
    )

    if not timeline:
        print("Timeline is empty.")
        return

    print(f"\nTimeline for {handle}")
    print("-" * 80)

    for i, post in enumerate(timeline, start=1):
        print(f"[{i}] {post['author_handle']}")
        print(f"    {post['text']}")
        print(f"    {post['created_at']}")
        print(f"    uri={post['uri']}")
        print()