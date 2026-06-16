import requests
import os

from dotenv import load_dotenv

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")
PDS_ADMIN_PW = os.getenv("BLUESKY_ADMIN_PW")
def request_crawl():
    response = requests.post(
        f"https://bsky.network/xrpc/com.atproto.sync.requestCrawl",
        json={"hostname": PDS_URL.removeprefix("https://")},
        timeout=30,
    )

    response.raise_for_status()
    print("Requested crawl")