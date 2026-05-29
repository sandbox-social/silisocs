#!/usr/bin/env python3

import os
from dotenv import load_dotenv
from .get_client import get_client
from atproto.exceptions import BadRequestError

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")

def create_bluesky_account(handle: str, password: str, email: str) -> dict:
    """
    Creates a Bluesky account on the PDS.
    If the account already exists, skips.
    """
    client = get_client()

    try:
        session = client.com.atproto.server.create_account(
            {
                "handle": handle,
                "password": password,
                "email": email,
            }
        )

        return {
            "handle": session.handle,
            "did": session.did,
            "access_jwt": session.access_jwt,
            "refresh_jwt": session.refresh_jwt,
        }

    except BadRequestError as e:
        error_message = str(e).lower()

        if "taken" in error_message:
            print(f"{handle} already exists, skipping.")
            return None

        print(f"Failed creating {handle}")
        print(e)
        raise