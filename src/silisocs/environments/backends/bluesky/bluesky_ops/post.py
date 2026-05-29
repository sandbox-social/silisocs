from .get_client import get_authenticated_client

def post(handle: str, password: str, post_text: str) -> None:
    """Creates a Bluesky post for the given handle."""
    client = get_authenticated_client(handle, password)

    client.send_post(post_text)

    print(f"{handle} posted: {post_text}")