from datetime import datetime, timezone
from dotenv import load_dotenv
from typing import Any

from silisocs.environments.backends.base import (  # noqa: F401 – re-exported for backward compat
    COLOR_TYPE,
    ActionArgumentError,
    ActionDescriptor,
    Parameter,
    BackendApp,
    SocialBackendApp,
    app_action,
)
from silisocs.environments.backends.bluesky.bluesky_ops.reset_users import reset_bluesky_server
from silisocs.environments.backends.bluesky.bluesky_ops.profile import update_profile, read_profile
from silisocs.environments.backends.bluesky.bluesky_ops.follow_user import follow_user, unfollow_user
from silisocs.environments.backends.bluesky.bluesky_ops.post import post, reply_to_post, like_post, repost_post
from silisocs.environments.backends.bluesky.bluesky_ops.timeline import get_timeline
from silisocs.environments.backends.bluesky.bluesky_ops.notifications import read_notifications

from urllib.parse import urlparse
import os
import dataclasses

load_dotenv()

PDS_URL = os.getenv("BLUESKY_BASE_URL")
DEFAULT_AGENT_PW = os.getenv("BLUESKY_AGENT_PASSWORD")

@dataclasses.dataclass
class BlueskyApp(SocialBackendApp):
    
    action_logger: Any = None
    app_description: str = "Self hosted Bluesky application."
    
    # ------------------------------------------------------------------ #
    # SocialBackendApp required interface
    # ------------------------------------------------------------------ #

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Compatibility entrypoint for legacy callers.

        The native runtime uses `setup_social_state` + simulation initializers,
        but some older workflows call `initialize` directly.
        """
        self.setup_social_state(
            agent_names=agent_names,
            sim_roles=dict(kwargs.get("sim_roles") or {}),
            graph_config=dict(kwargs.get("social_network") or {}),
            following_graph=None,
            agent_bios=dict(kwargs.get("agent_bios") or {}),
        )

        seed_posts = dict(kwargs.get("seed_posts") or {})
        for display_name, post_text in seed_posts.items():
            post_text = str(post_text or "").strip()
            if not post_text:
                continue
            try:
                self.post(display_name, post_text)
            except Exception as e:
                self._print(f"Seed post error for {display_name}: {e}", color="red")

    def setup_social_state(
        self,
        *,
        agent_names: list[str],
        sim_roles: dict[str, str] | None = None,
        graph_config: dict[str, Any] | None = None,
        following_graph: dict[str, list[str]] | None = None,
        agent_bios: dict[str, str] | None = None,
    ) -> None:
        """Create users, bios, and follows for Bluesky simulations."""
        sim_roles = dict(sim_roles or {})
        graph_config = dict(graph_config or {})
        agent_bios = dict(agent_bios or {})

        # Build user mapping.
        domain = urlparse(PDS_URL).netloc
        user_mapping = {}
        for i, display_name in enumerate(agent_names):
            parts = display_name.strip().split()
            short_name = parts[0] if parts else display_name
            concat_name = f"{parts[0]} {parts[1]}" if len(parts) >= 2 else parts[0]
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

        print(following_graph)
        for display_name, followees in following_graph.items():
            for followee in followees:
                try:
                    self.follow(display_name, followee)
                except Exception as e:
                    self._print(f"Follow error ({display_name}->{followee}): {e}", color="red")

        follow_edges = sum(len(v) for v in following_graph.values())
        self._last_initialization_stats = {
            "platform": "bluesky",
            "num_users": len(agent_names),
            "num_follow_edges": follow_edges,
        }
        self._print(
            f"Initialized {len(agent_names)} users on Bluesky ({follow_edges} follow edges)",
        )

    TIMELINE_MODES = {
        "follower_chronological": {
            "description": "Home timeline returned by Bluesky (chronological)",
        }
    }

    def get_timeline_mode(
        self,
        timeline_mode: str,
        user_name: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config: dict,
    ) -> list[dict]:
        del timeline_mode, recsys_type, timeline_config
        try:
            return list(self.get_timeline(user_name, limit=limit) or [])
        except Exception as e:
            self._print(f"Error fetching timeline for {user_name}: {e}", color="red")
            return []

    def format_timeline_for_observation(self, timeline: list[dict]) -> str:
        lines: list[str] = []
        for idx, item in enumerate(timeline or [], start=1):
            if not isinstance(item, dict):
                continue
            author = (
                (item.get("author") or {}).get("displayName")
                if isinstance(item.get("author"), dict)
                else item.get("author")
            )
            record = item.get("record") if isinstance(item.get("record"), dict) else {}
            text = record.get("text") if isinstance(record, dict) else item.get("text")
            uri = item.get("uri") or item.get("post_uri") or ""
            cid = item.get("cid") or item.get("post_cid") or ""
            header = f"{idx}. {author or 'Unknown'}"
            meta = " ".join(part for part in [f"uri={uri}" if uri else "", f"cid={cid}" if cid else ""] if part)
            if meta:
                header = f"{header} ({meta})"
            lines.append(header)
            if text:
                lines.append(str(text))
            lines.append("")
        return "\n".join(lines).strip()
        
    def name(self) -> str:
        """Define the name of the app."""
        return "BlueskyApp"

    def description(self) -> str:
        """Define the description of the app."""
        return self.app_description
    
    def set_user_mapping(self, mapping: dict[str, str]) -> None:
        self._user_mapping = mapping
        num_agents = len(mapping)
        self._print(mapping)
        self._print(f"Updated user mapping with {num_agents} entries", emoji="🔄")
        self._print("Resetting server")
        reset_bluesky_server()
      
    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        """Dispatch a parsed action to the correct Bluesky app_action method."""
        action_type = action_data.get("action_type", "").lower().strip()

        content = action_data.get("content", "") or action_data.get("post_text", "")
        target = action_data.get("target", "") or action_data.get("target_user", "")

        post_uri = action_data.get("post_uri", "") or action_data.get("parent_uri", "")
        post_cid = action_data.get("post_cid", "") or action_data.get("parent_cid", "")

        parent_uri = action_data.get("parent_uri", "") or post_uri
        parent_cid = action_data.get("parent_cid", "") or post_cid
        root_uri = action_data.get("root_uri")
        root_cid = action_data.get("root_cid")

        try:
            if action_type in {"finished", "finish", "finish_action_episode"}:
                return self.finish_action_episode()

            if action_type in {"post", "create_post"}:
                result = self.post(user_name, content)
                return f"{user_name} posted: {content}\nResult: {result}"

            if action_type == "reply":
                result = self.reply(
                    display_name=user_name,
                    post_text=content,
                    parent_uri=parent_uri,
                    parent_cid=parent_cid,
                    root_uri=root_uri,
                    root_cid=root_cid,
                )
                return f"{user_name} replied to {parent_uri}: {content}\nResult: {result}"

            if action_type in {"like", "like_post"}:
                result = self.like_post(
                    display_name=user_name,
                    post_uri=post_uri,
                    post_cid=post_cid,
                )
                return f"{user_name} liked post {post_uri}\nResult: {result}"

            if action_type in {"repost", "boost"}:
                result = self.repost(
                    display_name=user_name,
                    post_uri=post_uri,
                    post_cid=post_cid,
                )
                return f"{user_name} reposted {post_uri}\nResult: {result}"

            if action_type == "follow":
                self.follow(user_name, target)
                return f"{user_name} followed {target}"

            if action_type == "unfollow":
                self.unfollow(user_name, target)
                return f"{user_name} unfollowed {target}"

            if action_type in {"read_profile", "profile"}:
                result = self.read_profile(user_name, target)
                return f"{user_name} read profile of {target}\nResult: {result}"

            if action_type in {"read_notifications", "notifications"}:
                result = self.read_notifications(user_name)
                return f"{user_name} read notifications\nResult: {result}"

            if action_type in {"timeline", "get_timeline", "read_timeline"}:
                result = self.get_timeline(user_name)
                return f"{user_name} read timeline\nResult: {result}"

            return f"Unknown action type: {action_type}"

        except Exception as e:
            self._print(f"Error resolving action {action_type}: {e}", color="red")
            return f"Error performing {action_type}: {e}"  
        
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
        target_handle = self._user_mapping.get(target)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        read_profile(handle, DEFAULT_AGENT_PW, target_handle)
        
    @app_action
    def follow(self, display_name: str, target: str) -> None:
        """Makes the account with the given display name follow the target."""
        handle = self._user_mapping.get(display_name)
        target_handle = self._user_mapping.get(target)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        follow_user(handle, DEFAULT_AGENT_PW, target_handle)
    
    @app_action
    def unfollow(self, display_name: str, target: str) -> None:
        """Unfollows the target on the account with the given display name."""
        handle = self._user_mapping.get(display_name)
        target_handle = self._user_mapping.get(target)
        if not handle:
            raise ActionArgumentError(f"No handle found for display name: {display_name}")

        unfollow_user(handle, DEFAULT_AGENT_PW, target_handle)
        
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