"""mastodon_ops package for performing Mastodon API operations."""

from silisocs.environments.backends.mastodon.mastodon_ops.block import block_user
from silisocs.environments.backends.mastodon.mastodon_ops.boost import boost_check, boost_toot
from silisocs.environments.backends.mastodon.mastodon_ops.create_app import create_app
from silisocs.environments.backends.mastodon.mastodon_ops.create_env_file import (
    create_app_and_env_if_not_exists,
)
from silisocs.environments.backends.mastodon.mastodon_ops.delete_posts import delete_posts
from silisocs.environments.backends.mastodon.mastodon_ops.env_utils import (
    check_env,
    get_env_variable,
)
from silisocs.environments.backends.mastodon.mastodon_ops.follow import follow
from silisocs.environments.backends.mastodon.mastodon_ops.get_client import get_client
from silisocs.environments.backends.mastodon.mastodon_ops.like import like_check, like_toot
from silisocs.environments.backends.mastodon.mastodon_ops.login import login
from silisocs.environments.backends.mastodon.mastodon_ops.mute import mute_account
from silisocs.environments.backends.mastodon.mastodon_ops.notifications import (
    print_notifications,
    read_notifications,
)
from silisocs.environments.backends.mastodon.mastodon_ops.post_status import post_status
from silisocs.environments.backends.mastodon.mastodon_ops.read_bio import read_bio
from silisocs.environments.backends.mastodon.mastodon_ops.reset_users import (
    clear_mastodon_server,
    reset_users,
)
from silisocs.environments.backends.mastodon.mastodon_ops.timeline import (
    get_own_timeline,
    get_public_timeline,
    get_user_timeline,
    print_timeline,
)
from silisocs.environments.backends.mastodon.mastodon_ops.toot import toot
from silisocs.environments.backends.mastodon.mastodon_ops.unblock import unblock_user
from silisocs.environments.backends.mastodon.mastodon_ops.unfollow import unfollow
from silisocs.environments.backends.mastodon.mastodon_ops.unmute import unmute_account
from silisocs.environments.backends.mastodon.mastodon_ops.update_bio import update_bio

__all__ = [
    "block_user",
    "boost_check",
    "boost_toot",
    "check_env",
    "clear_mastodon_server",
    "create_app",
    "create_app_and_env_if_not_exists",
    "delete_posts",
    "follow",
    "get_client",
    "get_env_variable",
    "get_own_timeline",
    "get_public_timeline",
    "get_user_timeline",
    "like_check",
    "like_toot",
    "login",
    "mute_account",
    "post_status",
    "print_notifications",
    "print_timeline",
    "read_bio",
    "read_notifications",
    "reset_users",
    "toot",
    "unblock_user",
    "unfollow",
    "unmute_account",
    "update_bio",
]
