from datetime import datetime, timezone
from dotenv import load_dotenv
from typing import Any    

from silisocs.environments.backends.base import (  # noqa: F401 – re-exported for backward compat
    COLOR_TYPE,
    ActionArgumentError,
    ActionDescriptor,
    Parameter,
    PhoneApp,
    SocialMediaApp,
    app_action,
)
from silisocs.environments.backends.bluesky.bluesky_ops.reset_users import reset_bluesky_server
from silisocs.environments.backends.bluesky.bluesky_ops.profile import update_profile, read_profile
from silisocs.environments.backends.bluesky.bluesky_ops.follow_user import follow_user, unfollow_user
from silisocs.environments.backends.bluesky.bluesky_ops.post import post, reply_to_post, like_post, repost_post
from silisocs.environments.backends.bluesky.bluesky_ops.timeline import get_timeline
from silisocs.environments.backends.bluesky.bluesky_ops.notifications import read_notifications
from silisocs.utils.network import generate_follow_network

from urllib.parse import urlparse
import os

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")
DEFAULT_AGENT_PW = os.getenv("BLUESKY_AGENT_PASSWORD")

class BlueskyApp(SocialMediaApp):
    
    app_description = "Self hosted Bluesky application."
    
    def initialize(self, agent_names: list[str], **kwargs: Any):
        sim_roles = kwargs.get("sim_roles", {})
        seed_posts = kwargs.get("seed_posts", {})
        social_network = kwargs.get("social_network", {})
        agent_bios = kwargs.get("agent_bios", {})

        # Build user mapping.
        domain = urlparse(PDS_URL).netloc
        user_mapping = {}
        for i, display_name in enumerate(agent_names):
            parts = display_name.strip().split()
            short_name = parts[0] if parts else display_name
            concat_name = f"{parts[0]}{parts[1]}" if len(parts) >= 2 else parts[0]
            username = f"agent{i}.{domain}"
            user_mapping[short_name] = username
            user_mapping[concat_name] = username
        self.set_user_mapping(user_mapping)
        
        for display_name, bio in agent_bios.items():
            if bio:
                try:
                    self.update_profile(display_name, bio)
                except Exception as e:
                    self._print(f"Error setting bio for {display_name}: {e}", color="red")
        
        following = generate_follow_network(agent_names, sim_roles, social_network)
        for display_name, followees in following.items():
            for followee in followees:
                try:
                    self.follow(display_name, followee)
                except Exception as e:
                    self._print(f"Follow error ({display_name}->{followee}): {e}", color="red")
        
        for display_name, post_text in seed_posts.items():
            if post_text:
                try:
                    self.post(display_name, post_text)
                except Exception as e:
                    self._print(f"Seed post error for {display_name}: {e}", color="red")
    
        follow_edges = sum(len(v) for v in following.values())
        self._print(
            f"Initialized {len(agent_names)} users on Mastodon ({follow_edges} follow edges)",
        )
        
    def name(self) -> str:
        """Define the name of the app."""
        return "BlueskyApp"

    def description(self) -> str:
        """Define the description of the app."""
        return self.app_description
    
    def set_user_mapping(self, mapping: dict[str, str]) -> None:
        self._user_mapping = mapping
        num_agents = len(mapping)
        self._print(f"Updated user mapping with {num_agents} entries", emoji="🔄")
        self._print("Resetting server")
        reset_bluesky_server()
        
    @app_action
    def update_profile(self, display_name: str, bio: str) -> None:
        """Updates the bio of the user with the given display name."""
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        update_profile(handle, DEFAULT_AGENT_PW, display_name, bio)
    
    @app_action
    def read_profile(self, display_name: str, target: str) -> tuple:
        """Reads the profile of the target user. Returns target name and bio."""
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        read_profile(handle, DEFAULT_AGENT_PW, target)
        
    @app_action
    def follow(self, display_name: str, target: str) -> None:
        """Makes the account with the given display name follow the target."""
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        follow_user(handle, DEFAULT_AGENT_PW, target)
    
    @app_action
    def unfollow(self, display_name: str, target: str) -> None:
        """Unfollows the target on the account with the given display name."""
        
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        unfollow_user(handle, DEFAULT_AGENT_PW, target)
        
    @app_action
    def post(self, display_name: str, post_text: str) -> dict:
        """Creates a post on the account with the given display name. Returns post uri and content ID hash."""
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        post_metadata = post(handle, DEFAULT_AGENT_PW, post_text)
        return post_metadata
    
    @app_action
    def repost(self, display_name: str, post_uri: str, post_cid: str) -> dict: 
        """Reposts a post on bluesky on the account with the given display name. Returns post uri and content ID hash."""
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        repost_metadata = repost_post(handle, DEFAULT_AGENT_PW, post_uri, post_cid)
        return repost_metadata
        
    @app_action
    def reply(self, display_name: str, post_text: str, parent_uri: str, parent_cid: str, root_uri: str | None = None, root_cid: str | None = None) -> dict:
        """
        Reply to a Bluesky post.

        Args:
            display_name: Local agent replying
            post_text: Text of the reply
            parent_uri: URI of the post being replied to
            parent_cid: CID of the post being replied to
            root_uri: Thread root URI (optional)
            root_cid: Thread root CID (optional)

        Returns:
            Metadata of created reply
        """
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        response = reply_to_post(
            handle=handle,
            password=DEFAULT_AGENT_PW,
            post_text=post_text,
            parent_uri=parent_uri,
            parent_cid=parent_cid,
            root_uri=root_uri,
            root_cid=root_cid,
        )

        print(
            f"{display_name} replied "
            f"to {parent_uri}"
        )

        return response
    
    @app_action
    def get_timeline(self, display_name: str, limit: int = 50) -> list[dict]:
        """Returns the home timeline of the user. Returns a list of dictionary objects representing the timeline."""
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")
        
        timeline = get_timeline(handle, DEFAULT_AGENT_PW, limit)
        return timeline
    
    @app_action
    def like_post(self, display_name: str, post_uri: str, post_cid: str) -> dict:
        """Likes a given post on the account with the given display name. Return post uri content ID hash."""
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")
        
        response = like_post(
            handle=handle,
            password=DEFAULT_AGENT_PW,
            post_uri=post_uri,
            post_cid=post_cid,
        )
        
        return response

    @app_action
    def read_notifications(self, display_name: str, limit: int = 50) -> list[dict]:
        """Gets a list of notifications for the account with the given display_name. Returns a list of dictionary objects represnting the notifications."""
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")
        
        notifications = read_notifications(handle, DEFAULT_AGENT_PW, limit)
        return notifications