from .get_client import get_authenticated_client

def get_timeline(handle: str, password: str, limit: int = 50) -> list[dict]:
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
            "author_handle": post.author.handle,
            "author_did": post.author.did,
            "text": post.record.text,
            "created_at": post.record.created_at,
        })

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