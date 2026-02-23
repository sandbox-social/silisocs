import os
import random
import time

from twitter_like.engine import TwitterLikePlatform

DB_PATH = "benchmark_twitter_like.db"
NUM_USERS = 1000
NUM_POSTS = 20000
NUM_FOLLOWS = 5000


def run_benchmark():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    if os.path.exists(DB_PATH + "-wal"):
        os.remove(DB_PATH + "-wal")

    print(f"Starting Benchmark: {NUM_USERS} users, {NUM_POSTS} posts, {NUM_FOLLOWS} follows")
    smp = TwitterLikePlatform(DB_PATH, use_queue=True)

    # 1. User Creation
    start = time.time()
    for i in range(NUM_USERS):
        smp.create_user(f"user_{i}", f"Bio for user {i}")
    duration = time.time() - start
    print(f"Created {NUM_USERS} users in {duration:.2f}s ({NUM_USERS / duration:.0f} ops/s)")

    # 2. Follows
    start = time.time()
    users = [f"user_{i}" for i in range(NUM_USERS)]
    for _ in range(NUM_FOLLOWS):
        u1 = random.choice(users)
        u2 = random.choice(users)
        if u1 != u2:
            smp.follow(u1, u2)
    duration = time.time() - start
    print(f"Created {NUM_FOLLOWS} follows in {duration:.2f}s ({NUM_FOLLOWS / duration:.0f} ops/s)")

    # 3. Posts
    start = time.time()
    for i in range(NUM_POSTS):
        u = random.choice(users)
        smp.create_post(u, f"This is post number {i} content data.")
    duration = time.time() - start
    print(f"Created {NUM_POSTS} posts in {duration:.2f}s ({NUM_POSTS / duration:.0f} ops/s)")

    # 4. Feed Generation
    print("\nBenchmarking Feed Generation:")
    latencies = []
    for _ in range(100):
        u = random.choice(users)
        start = time.time()
        feed = smp.get_feed("chronological_home", username=u, limit=50)
        latencies.append(time.time() - start)

    avg_latency = sum(latencies) / len(latencies) * 1000
    max_latency = max(latencies) * 1000
    print(f"Home Timeline (Limit 50): Avg {avg_latency:.2f}ms, Max {max_latency:.2f}ms")

    # 5. Global Feed
    start = time.time()
    smp.get_feed("firehose", limit=50)
    duration = (time.time() - start) * 1000
    print(f"Global Timeline (Limit 50): {duration:.2f}ms")

    smp.shutdown()

    # Cleanup
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        if os.path.exists(DB_PATH + "-wal"):
            os.remove(DB_PATH + "-wal")


if __name__ == "__main__":
    run_benchmark()
