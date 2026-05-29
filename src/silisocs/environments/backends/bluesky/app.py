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
from silisocs.environments.backends.bluesky.bluesky_ops.update_profile import update_profile
from silisocs.environments.backends.bluesky.bluesky_ops.follow_user import follow_user
from silisocs.environments.backends.bluesky.bluesky_ops.post import post
from silisocs.utils.network import generate_follow_network

from urllib.parse import urlparse
import os

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")

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

        password = "password"
        update_profile(handle, password, display_name, bio)
    
    @app_action
    def follow(self, display_name: str, followee: str) -> None:
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        password = "password"
        follow_user(handle, password, followee)
    
    @app_action
    def post(self, display_name: str, post_text: str) -> None:
        handle = self._user_mapping.get(display_name)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        password = "password"
        post(handle, password, post_text)