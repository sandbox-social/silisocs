import os
import time

from mastodon_sim.environments.backends.reddit_like.engine import RedditLikePlatform

DB_PATH = "test_reddit_like.db"


def test_reddit_flow():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    rp = RedditLikePlatform(DB_PATH, use_queue=True)

    # 1. Create Users
    print("Creating users...")
    u1 = rp.create_user("alice", "I love AI")
    u2 = rp.create_user("bob", "Robotics fan")
    u3 = rp.create_user("charlie", "Data scientist")

    assert u1 and u2 and u3
    print(f"Users created: Alice({u1}), Bob({u2}), Charlie({u3})")

    # 2. Subreddits
    print("\nCreating subreddits...")
    s1 = rp.create_subreddit("r/machinelearning", "All about ML")
    s2 = rp.create_subreddit("robotics", "Robots!")
    assert s1 and s2

    rp.join_subreddit("alice", "machinelearning")
    rp.join_subreddit("bob", "machinelearning")
    rp.join_subreddit("bob", "robotics")
    rp.join_subreddit("charlie", "machinelearning")

    # 3. Posts
    print("\nCreating posts...")
    p1 = rp.create_post(
        "alice", "machinelearning", "Transformer models explained", "Here is how attention works."
    )
    p2 = rp.create_post("bob", "robotics", "My new robot dog", "It can backflip.")
    p3 = rp.create_post(
        "charlie", "machinelearning", "Dataset analysis request", "Need help with CSVs."
    )

    assert p1 and p2 and p3

    # 4. Comments
    print("\nCreating comments...")
    c1 = rp.create_comment("bob", p1, "Great explanation Alice!")
    c2 = rp.create_comment("alice", p1, "Thanks Bob!", parent_id=c1)

    # 5. Votes
    print("\nVoting...")
    rp.vote("bob", p1, "post", 1)  # Bob upvotes Alice's post
    rp.vote("charlie", p1, "post", 1)  # Charlie upvotes Alice's post
    rp.vote("alice", p2, "post", 1)  # Alice upvotes Bob's robot
    rp.vote("charlie", p3, "post", -1)  # Charlie downvotes his own post? Sure.

    rp.vote("alice", c1, "comment", 1)  # Alice upvotes Bob's comment

    time.sleep(0.5)  # Wait for queue

    # 6. Verify Feeds
    print("\nChecking Feeds...")

    # ML Subreddit
    ml_feed = rp.get_subreddit_feed("machinelearning")
    assert len(ml_feed["posts"]) == 2
    print(f"r/machinelearning posts: {len(ml_feed['posts'])}")

    # Bob's Home
    bob_home = rp.get_feed("home", username="bob")
    assert len(bob_home["posts"]) == 3  # Joined both
    print(f"Bob's home posts: {len(bob_home['posts'])}")

    # Popular
    popular = rp.get_feed("popular")
    assert len(popular["posts"]) == 2  # Only p1 and p2 have positive score
    print(f"Popular posts (positive score): {len(popular['posts'])}")
    assert popular["posts"][0]["title"] == "Transformer models explained"  # p1 has 2 upvotes

    # Comments
    comments = rp.get_post_comments(p1)
    # The parent comment (c1) is the only top-level comment. c2 is nested.
    assert len(comments) == 1
    assert len(comments[0]["replies"]) == 1
    print(f"Post 1 top-level comments: {len(comments)}")

    # User Karma Check
    alice_profile = rp.view_profile("alice")
    print(f"Alice Karma: {alice_profile['karma']}")
    assert alice_profile["karma"] > 0  # Two upvotes on her post

    print("\nReddit-like Verification Successful!")

    rp.shutdown()

    # Cleanup
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    if os.path.exists(DB_PATH + "-wal"):
        os.remove(DB_PATH + "-wal")


if __name__ == "__main__":
    test_reddit_flow()
