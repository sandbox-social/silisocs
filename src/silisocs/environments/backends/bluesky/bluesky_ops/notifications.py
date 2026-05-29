from .get_client import get_authenticated_client

def read_notifications(handle: str, password: str, limit: int = 50) -> list[dict]:
    """Read Bluesky notifications for a user, up to a given limit."""
    client = get_authenticated_client(handle, password)

    response = client.app.bsky.notification.list_notifications(
        {"limit": limit}
    )

    notifications = []

    for notif in response.notifications:
        record = getattr(notif, "record", None)

        notifications.append(
            {
                "reason": notif.reason,  # like, repost, follow, mention, reply
                "author_handle": notif.author.handle,
                "author_did": notif.author.did,
                "text": getattr(record, "text", ""),
                "uri": getattr(notif, "uri", None),
                "indexed_at": notif.indexed_at,
                "is_read": notif.is_read,
            }
        )

    return notifications