import os

from mastodon_sim.environments.backends.twitter_like.engine import TwitterLikePlatform

DB_PATH = "test_twitter_like.db"


def test_basic_flow():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    smp = TwitterLikePlatform(DB_PATH, use_queue=True)

    # 1. Create Users
    print("Creating users...")
    alice_id = smp.create_user("alice", "Alice in Wonderland")
    bob_id = smp.create_user("bob", "Bob the Builder")
    charlie_id = smp.create_user("charlie", "Charlie Chaplin")
    print(f"Users created: Alice({alice_id}), Bob({bob_id}), Charlie({charlie_id})")

    # 2. Create Posts
    print("\nCreating posts...")
    p1 = smp.create_post("alice", "Hello world!")
    p2 = smp.create_post("bob", "Building something cool.")
    p3 = smp.create_post("alice", "Another post from Alice.")
    print(f"Posts created: {p1}, {p2}, {p3}")

    # 3. Follows
    print("\nFollowing...")
    smp.follow("charlie", "alice")
    smp.follow("charlie", "bob")

    # 4. Likes & Reposts
    print("\nInteractions...")
    smp.like("charlie", p1)
    smp.repost("bob", p1)

    # 5. Check Timelines
    print("\nChecking Charlie's home timeline (should see Alice and Bob)...")
    feed = smp.get_feed("chronological_home", "charlie")
    for post in feed["posts"]:
        print(
            f"- [{post['username']}] {post['content']} (Likes: {post['likes_count']}, Reposts: {post['reposts_count']})"
        )

    assert len(feed["posts"]) >= 2
    # The first post should be the repost by Bob, since it was created last
    latest_post = feed["posts"][0]
    # It should be a repost of p1
    print(f"Latest post: {latest_post}")
    assert latest_post["quote_of_id"] == p1 or latest_post["id"] > p3

    print("\nChecking Alice's profile timeline...")
    alice_feed = smp.get_feed("profile", "alice")
    assert len(alice_feed["posts"]) == 2

    print("\nVerification Successful!")

    smp.shutdown()

    # Cleanup
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        if os.path.exists(DB_PATH + "-wal"):
            os.remove(DB_PATH + "-wal")
        if os.path.exists(DB_PATH + "-shm"):
            os.remove(DB_PATH + "-shm")


if __name__ == "__main__":
    test_basic_flow()
