"""Classes for implementing virtual apps simulation."""

import abc
import dataclasses
import datetime
import inspect
import re
import textwrap
import types
import typing
from collections.abc import Callable, Sequence
from html import unescape
from typing import Any, Literal, get_type_hints

import docstring_parser  # pytype: disable=import-error  # Fails on GitHub.
import termcolor

_DATE_FORMAT = "%Y-%m-%d %H:%M"

_ARGUMENT_REGEX = re.compile(r"(?P<param>\w+):\s*(?P<value>[^\n]+)")

ParserFunc = Callable[[str], Any]

_ACTION_PROPERTY = "__app_action__"

COLOR_TYPE = (
    Literal[
        "black",
        "grey",
        "red",
        "green",
        "yellow",
        "blue",
        "magenta",
        "cyan",
        "light_grey",
        "dark_grey",
        "light_red",
        "light_green",
        "light_yellow",
        "light_blue",
        "light_magenta",
        "light_cyan",
        "white",
    ]
    | None
)


def parse_literal(literal_type: type) -> ParserFunc:
    """Parse a literal type."""

    def _parse(value: str) -> Any:
        literal_values = typing.get_args(literal_type)
        if value in literal_values:
            return value
        raise ValueError(f"'{value}' is not a valid literal value for {literal_type}")

    return _parse


_ARGUMENT_PARSERS: dict[str, ParserFunc | type] = {
    "datetime.datetime": lambda date: datetime.datetime.strptime(date, _DATE_FORMAT),  # noqa: DTZ007
    "str": str,
    "int": int,
}


def app_action(method):
    """Mark PhoneApp methods as callable actions."""
    signature = inspect.signature(method)
    required_params = [
        name
        for name, param in signature.parameters.items()
        if param.default == inspect.Parameter.empty and name != "self"
    ]
    method.__app_action__ = True
    method.__required_params__ = required_params
    return method


class ActionArgumentError(Exception):
    """An error that is raised when argument parsing fails."""


# endregion


# region[PhoneApp]
@dataclasses.dataclass(frozen=True)
class Parameter:
    """A parameter for an action."""

    name: str
    kind: Any
    description: str | None
    required: bool

    def value_from_text(self, text: str):
        """Parse a value from a string."""
        if text == "" and not self.required:
            return None
        origin = typing.get_origin(self.kind)
        if origin is None:
            return self._parse_single_argument(text)
        if origin in (typing.Union, types.UnionType):
            args = typing.get_args(self.kind)
            if set(args) == {str, type(None)}:
                return text if text != "" else None
            return self.parse_union_type(text, args)
        if origin is list:
            return self._parse_list_argument(text)
        raise ValueError(f"Unsupported type {self.kind}")

    def parse_union_type(self, value: str, types: tuple[type, ...]) -> Any:
        """Parse a value from a string, trying each type in the union."""
        for t in types:
            if t is type(None) and value == "":
                return None
            try:
                if typing.get_origin(t) is Literal:
                    return parse_literal(t)(value)
                parser = _ARGUMENT_PARSERS.get(t.__name__, t)
                return parser(value)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse '{value}' as any of {types}")

    def full_description(self):
        """Return a full description of the parameter."""
        return f"{self.name}: {self.description or ''}, type: {self.kind}"

    def _parse_single_argument(self, text: str, kind: Any = None):
        kind = kind or self.kind
        if kind is type(None):
            return None
        parser = _ARGUMENT_PARSERS.get(kind, kind)  # type: ignore
        return parser(text)

    def _parse_list_argument(self, text: str):
        arg = typing.get_args(self.kind)
        parser = _ARGUMENT_PARSERS.get(arg, arg)  # type: ignore
        return [parser(e) for e in text.split(",")]

    @classmethod
    def create(cls, parameter: inspect.Parameter, docstring: docstring_parser.Docstring):
        """Create a Parameter from a method docstring and inspect.Parameter."""
        description = next(
            (p.description for p in docstring.params if p.arg_name == parameter.name),
            None,
        )
        return cls(parameter.name, parameter.annotation, description)  # type: ignore


# endregion


# region[ActionDescriptor]
@dataclasses.dataclass(frozen=True)
class ActionDescriptor:
    """Represents an action that can be invoked on a PhoneApp."""

    name: str
    description: str
    parameters: Sequence[Parameter]
    docstring: dataclasses.InitVar[docstring_parser.Docstring]

    def __post_init__(self, docstring: docstring_parser.Docstring):  # noqa: D105
        pass

    def instructions(self):
        """Return a string containing instructions for using the action."""
        required_params = [p for p in self.parameters if p.required]
        optional_params = [p for p in self.parameters if not p.required]

        instructions = f"The {self.name} action expects the following parameters:\n"

        if required_params:
            instructions += "\nRequired parameters:\n"
            instructions += "\n".join(p.full_description() for p in required_params)
            instructions += "\n"

        if optional_params:
            instructions += "\nOptional parameters:\n"
            instructions += "\n".join(p.full_description() for p in optional_params)
            instructions += "\n"

        instructions += textwrap.dedent("""
        Provide values for the required parameters and any optional parameters you want to use.
        Each parameter should be on its own line, for example:
        param1: value1
        param2: value2

        For optional parameters you don't want to use, you should omit them rather than provide an empty value.

        Critically important: If an argument is message or a post (e.g. `status`), make sure it is
        from first person perspective and makes sense as a realistic user post based on their information.
        Do not post any statuses from 3rd person perspective.

        Note: current_user, target_user or the username field is ALWAYS the full name of the agents in the format: "Firstname Lastname".

        Bad examples:
            `bio`: Updated my bio and checking notifications!
            `status`: I'm updating my status and posting a message
            `status`: Wrote about goals for today

        Good examples:
            `bio`: I'm a software engineer with a passion for building great apps. Let's connect!
            `status`: Just finished writing a chapter of my book. Feeling productive!
            `status`: My goals for today are to get to the gym and submit my grant proposal.

        Also, several string/int args require real knowledge, such as a real `target_user` or `toot_id`, so don't
        fabricate these values and only fill them in with values you've been provided.
        You can read posts by using the `get_public_timeline` action. These are operations like:
        liking, boosting, replying, reading profile, following user, etc.
        """)

        return instructions

    @classmethod
    def from_method(cls, method):
        """Create an ActionDescriptor from a method."""
        doc = docstring_parser.parse(method.__doc__)
        description = f"{doc.short_description}\n{doc.long_description or ''}"
        signature = inspect.signature(method)
        type_hints = get_type_hints(method)

        method_parameters = []
        for name, param in signature.parameters.items():
            if name == "self":
                continue
            param_type = type_hints.get(name, Any)
            required = param.default == inspect.Parameter.empty
            description = next((p.description for p in doc.params if p.arg_name == name), None)
            method_parameters.append(Parameter(name, param_type, description, required))

        return cls(
            name=method.__name__,
            description=description,
            parameters=method_parameters,
            docstring=doc,
        )


# endregion

# region[PhoneApp]


class PhoneApp(metaclass=abc.ABCMeta):
    """Base class for apps that concordia can interact with using plain English.

    Extend this class and decorated any method that should be callable from the
    simulation with @app_action.
    """

    action_logger: Any = None
    _log_color: COLOR_TYPE = "blue"

    @abc.abstractmethod
    def name(self) -> str:
        """Return the name of the app."""
        raise NotImplementedError

    @abc.abstractmethod
    def description(self) -> str:
        """Return a description of the app."""
        raise NotImplementedError

    def _print(
        self,
        entry: str,
        emoji: str = "",
        color: COLOR_TYPE = None,
    ) -> None:
        formatted_entry = f"{emoji} {entry}" if emoji else entry
        print(termcolor.colored(formatted_entry, color or self._log_color))

    def actions(self) -> Sequence[ActionDescriptor]:
        """Return this app's callable actions."""
        methods = inspect.getmembers(self, predicate=inspect.ismethod)
        return [ActionDescriptor.from_method(m) for _, m in methods if hasattr(m, _ACTION_PROPERTY)]

    def full_description(self):
        """Return a description of the app and all the actions it supports."""
        return textwrap.dedent(f"""\
    {self.name()}: {self.description()}
    The app supports the following actions:
    """) + "\n".join(f"{a.name}: {a.description}" for a in self.actions())

    def invoke_action(self, action: ActionDescriptor, args_text: str) -> str | None:
        """Invoke the given action with the given arguments."""
        args = _parse_argument_text(args_text)
        self._print(f"Invoking action {action.name} with arguments {args}", color="yellow")
        expected_params = {p.name: p for p in action.parameters}

        # Check for missing required arguments
        missing_args = [
            name for name, param in expected_params.items() if param.required and name not in args
        ]
        if missing_args:
            raise ActionArgumentError(f"Missing required argument(s): {', '.join(missing_args)}")

        # Check for unexpected arguments
        unexpected_args = set(args) - set(expected_params)
        if unexpected_args:
            raise ActionArgumentError(f"Unexpected argument(s): {', '.join(unexpected_args)}")

        # Process values
        processed_args: dict[str, str | None] = {}
        for name, param in expected_params.items():
            if name in args:
                value = args[name]
                if value == "" and not param.required:
                    processed_args[name] = None
                else:
                    processed_args[name] = param.value_from_text(value)
            elif not param.required:
                processed_args[name] = None

        try:
            return getattr(self, action.name)(**processed_args)
        except Exception as e:
            self._print(f"Error invoking action {action.name}: {e}", color="red")
            return f"Error invoking action {action.name}: {e}"


# endregion

# region[Phone]


@dataclasses.dataclass(frozen=True)
class Phone:
    """Represent a player's phone."""

    player_name: str
    apps: Sequence[PhoneApp]

    def description(self):
        """Return a description of the phone and its apps."""
        return textwrap.dedent(f"""\
    {self.player_name} has a smartphone.
    {self.player_name} uses their phone frequently to achieve their daily goals.
    {self.player_name}'s phone has only the following apps available:
    {", ".join(self.app_names())}."
    """)

    def app_names(self):
        """Return the names of the apps installed on the phone."""
        return [a.name() for a in self.apps]


# Parse multiline argument text to a text dictionary:
# 'param1: value1\n param2: value2' is parsed to:
# {'param1': 'value1', 'param2': 'value2'}
def _parse_argument_text(args_text: str) -> dict[str, str]:
    matches = _ARGUMENT_REGEX.finditer(args_text)
    return {m.group("param"): m.group("value").strip() for m in matches if m.group("value").strip()}


()
# region[Mastodon Social Network App]


@dataclasses.dataclass
class MastodonSocialNetworkApp(PhoneApp):
    """Mastodon social network app.
        description = (
            "MastodonSocialNetworkApp is a social media application similar to"
            " Twitter that allows users to interact on social media.\n\n    This"
            " app provides functionality for users to post status updates (toots), follow"
            " other users, like, boost, and respond to posts, and manage their"
            " notifications.\n\n    Critically important: Operations such as"
            " liking, boosting, replying, etc. require a `toot_id`. To obtain a"
            " `toot_id`, you must have memory/knowledge of a real `toot_id`. If you"
            " don't know a `toot_id`, you can't perform actions that require it."
            " `toot_id`'s can be retrieved using the `get_timeline` action."
        )
    A social media application similar to Twitter that allows users to interact on social media.
    """

    action_logger: Any = None
    perform_operations: bool = True
    app_description: str = "MastodonSocialNetworkApp"
    _log_color: COLOR_TYPE = dataclasses.field(default="blue", init=False)
    _mastodon_ops: Any = dataclasses.field(default=None, init=False)
    _user_mapping: dict[str, str] = dataclasses.field(default_factory=dict, init=False)

    def __post_init__(self) -> None:  # noqa: D105
        super().__init__()
        if self.perform_operations:
            from mastodon_sim import mastodon_ops

            self._mastodon_ops = mastodon_ops

    def name(self) -> str:
        """Define the name of the app."""
        return "MastodonSocialNetworkApp"

    def description(self) -> str:
        """Define the description of the app."""
        return self.app_description

    def set_user_mapping(self, mapping: dict[str, str]) -> None:
        """Set the mapping of display names to usernames."""
        self._user_mapping = mapping
        self._print(f"Updated user mapping with {len(mapping)} entries", emoji="🔄")

    def get_user_mapping(self) -> dict[str, str]:
        """Get the mapping of display names to usernames."""
        return self._user_mapping

    def _get_username(self, display_name: str) -> str:
        """Get the username for a given display name."""
        username = self._user_mapping.get(display_name)
        # self._print(f"Mapped {display_name} to @{username}", emoji="🔗")
        if not username:
            raise ValueError(f"No username found for display name: {display_name}")
        return username

    def public_get_username(self, display_name: str) -> str:
        """Public interface to get the username."""
        return self._get_username(display_name)

    @app_action
    def update_profile(self, current_user: str, bio: str) -> str:
        """Update the user's bio."""
        current_user_full = str(current_user)
        current_user = current_user.split()[0]

        username = self._get_username(current_user)
        self._print(f"Updating profile for @{username}: {current_user}", emoji="✏️")
        if self.perform_operations:
            self._mastodon_ops.update_bio(username, current_user, bio)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        bio_message = f'Profile updated successfully: "{bio}"'
        self._print(bio_message, emoji="✅")
        self.action_logger.log(
            {"source_user": current_user_full, "label": "update_profile", "data": {"new_bio": bio}}
        )

        return bio_message

    @app_action
    def read_profile(self, current_user: str, target_user: str) -> tuple[str, str]:
        """Read a user's profile on Mastodon social network."""
        current_user_full = str(current_user)
        current_user = current_user.split()[0]
        target_user_full = str(target_user)
        target_user = target_user.split()[0]

        current_username = self._get_username(current_user)
        target_username = self._get_username(target_user)
        self._print(f"@{current_username} reading profile of @{target_username}", emoji="👀")
        if self.perform_operations:
            display_name, bio = self._mastodon_ops.read_bio(current_username, target_username)
        else:
            display_name, bio = "Mock Name", "Mock Bio"
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        self._print(f"Profile: {display_name} - {bio}", emoji="📄")

        self.action_logger.log(
            {
                "source_user": current_user_full,
                "label": "read_profile",
                "data": {"target_user": target_user_full, "bio": bio},
            }
        )
        return display_name, bio

    @app_action
    def follow_user(self, current_user: str, target_user: str) -> str:
        """Follow a user on Mastodon social network."""
        current_user_full = str(current_user)
        current_user = current_user.split()[0]
        target_user_full = str(target_user)
        target_user = target_user.split()[0]
        current_username = self._get_username(current_user)
        target_username = self._get_username(target_user)
        if self.perform_operations:
            self._mastodon_ops.follow(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        follow_message = (
            f"current_user (@{current_username}) followed target_user (@{target_username})"
        )
        self._print(follow_message, emoji="➕")  # noqa: RUF001
        self.action_logger.log(
            {
                "source_user": current_user_full,
                "label": "follow",
                "data": {"target_user": target_user_full},
            }
        )
        return follow_message

    @app_action
    def unfollow_user(self, current_user: str, target_user: str) -> str:
        """Unfollow a user."""
        current_user_full = str(current_user)
        current_user = current_user.split()[0]
        target_user_full = str(target_user)
        target_user = target_user.split()[0]
        current_username = self._get_username(current_user)
        target_username = self._get_username(target_user)
        self._print(
            f"@{current_username} unfollowing user: @{target_username}",
            emoji="➖",  # noqa: RUF001
        )
        if self.perform_operations:
            self._mastodon_ops.unfollow(current_username, target_username)
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        unfollow_message = (
            f"current_user (@{current_username}) unfollowed target_user (@{target_username})"
        )
        self._print(unfollow_message, emoji="✅")
        self.action_logger.log(
            {
                "source_user": current_user_full,
                "label": "unfollow",
                "data": {"target_user": target_user_full},
            }
        )
        return unfollow_message

    # @app_action
    # def post_status(
    #     self,
    #     current_user: str,
    #     status: str,
    #     visibility: (Literal["private", "public", "unlisted", "direct"] | None) = None,
    #     sensitive: bool = False,
    #     spoiler_text: str | None = None,
    #     language: str | None = None,
    #     scheduled_at: datetime.datetime | None = None,
    #     in_reply_to_id: int | None = None,
    #     media_files: list[str] | None = None,
    #     idempotency_key: str | None = None,
    #     content_type: str | None = None,
    #     poll_options: list[str] | None = None,
    #     poll_expires_in: int | None = None,
    #     poll_multiple: bool = False,
    #     poll_hide_totals: bool = False,
    #     quote_id: int | None = None,
    # ) -> str:
    #     """Post a new status update to the Mastodon-like social network.

    #     Args:
    #         current_user (str): The username of the user posting the status.
    #         status (str): The text content of the status update.
    #         visibility (str | None): The visibility level of the post ('direct', 'private', 'unlisted', or 'public').
    #         sensitive (bool): Whether the post should be marked as sensitive content.
    #         spoiler_text (str | None): Text to be shown as a warning before the status.
    #         language (str | None): The language of the status (ISO 639-1 or 639-3 code).
    #         scheduled_at (datetime.datetime | None): When to schedule the post for future publishing.
    #         in_reply_to_id (int | None): The `toot_id` of the status this post is replying to.
    #         media_files (List[str] | None): List of paths to media files to attach to the post.
    #         idempotency_key (str | None): A unique key to prevent duplicate posts.
    #         content_type (str | None): The MIME type of the status content (for Pleroma servers).
    #         poll_options (List[str] | None): List of options for a poll attached to the post.
    #         poll_expires_in (int | None): Number of seconds until the poll expires.
    #         poll_multiple (bool): Whether multiple choices are allowed in the poll.
    #         poll_hide_totals (bool): Whether to hide poll results until it expires.
    #         quote_id (int | None): The ID of a status being quoted (Fedibird-specific feature).

    #     Raises
    #     ------
    #         ValueError: If the input parameters are invalid.
    #         Exception: For any other unexpected errors during posting.
    #     """
    #     try:
    #         username = self._get_username(current_user)
    #         if self.perform_operations:
    #             self._mastodon_ops.post_status(
    #                 login_user=username,
    #                 status=status,
    #                 visibility=visibility,
    #                 sensitive=sensitive,
    #                 spoiler_text=spoiler_text,
    #                 language=language,
    #                 scheduled_at=scheduled_at,
    #                 in_reply_to_id=in_reply_to_id,
    #                 media_files=media_files,
    #                 idempotency_key=idempotency_key,
    #                 content_type=content_type,
    #                 poll_options=poll_options,
    #                 poll_expires_in=poll_expires_in,
    #                 poll_multiple=poll_multiple,
    #                 poll_hide_totals=poll_hide_totals,
    #                 quote_id=quote_id,
    #             )
    #         else:
    #             self._print(
    #                 "Skipping real Mastodon API call since perform_operations is set to False",
    #                 color="light_grey",
    #             )

    #         # Log success
    #         if scheduled_at:
    #             self._print(
    #                 "Status scheduled successfully for user:"
    #                 f' {current_user} ({username}) at {scheduled_at}: "{status}"',
    #                 emoji="🕒",
    #             )
    #         else:
    #             self._print(
    #                 f'Status posted for user: {current_user} ({username}): "{status}"',
    #                 emoji="📝",
    #             )

    #         if poll_options:
    #             self._print("Poll attached to the status.", emoji="📊")

    #         if media_files:
    #             self._print(f"Attached {len(media_files)} media file(s).", emoji="📎")

    #     except ValueError as e:
    #         self._print(f"Invalid input: {e!s}", emoji="❌")
    #         raise

    #     except Exception as e:
    #         self._print(f"An unexpected error occurred: {e!s}", emoji="❌")
    #         raise
    #     return_msg = f'Status posted for user: {current_user} ({username}): "{status}"'
    #     return return_msg

    @app_action
    def post_toot(
        self,
        current_user: str,
        status: str,
        media_links: list[str] | None = None,
    ) -> str:
        """Post a new toot to the Mastodon-like social network.

        Args:
            current_user (str): The username of the user posting the status.
            status (str): The text content of the status update.

        Raises
        ------
            ValueError: If the input parameters are invalid.
            Exception: For any other unexpected errors during posting.
        """
        return_val = None
        current_user_full = str(current_user)
        try:
            current_user = current_user.split()[0]
            username = self._get_username(current_user)
            if self.perform_operations:
                return_val = self._mastodon_ops.post_status(
                    login_user=username,
                    status=status,
                    media_files=media_links,
                )
            else:
                self._print(
                    "Skipping real Mastodon API call since perform_operations is set to False",
                    color="light_grey",
                )

            self._print(
                f'Status posted for user: {current_user} ({username}): "{status}"',
                emoji="📝",
            )
            # self._print(return_val)

        except ValueError as e:
            self._print(f"Invalid input: {e!s}", emoji="❌")
            raise

        except Exception as e:
            self._print(f"An unexpected error occurred: {e!s}", emoji="❌")
            raise
        toot_id = None
        if return_val:
            return_msg = (
                f"{current_user} posted a toot with Toot ID: {return_val['id']} --- {status}\n"
            )
            toot_id = return_val["id"]
        else:
            return_msg = f'{current_user} posted a toot!: "{status}"\n'
        self.action_logger.log(
            {
                "source_user": current_user_full,
                "label": "post",
                "data": {"toot_id": str(toot_id), "post_text": status},
            }
        )
        return return_msg

    # @app_action
    # def post_media_toot(
    #     self,
    #     current_user: str,
    #     status: str,
    #     media_link: str,
    # ) -> str:
    #     """Post a new toot to the Mastodon-like social network.

    #     Args:
    #         current_user (str): The username of the user posting the status.
    #         status (str): The text content of the status update.

    #     Raises
    #     ------
    #         ValueError: If the input parameters are invalid.
    #         Exception: For any other unexpected errors during posting.
    #     """
    #     return_val = None
    #     current_user_full = str(current_user)
    #     try:
    #         current_user = current_user.split()[0]
    #         username = self._get_username(current_user)
    #         if self.perform_operations:
    #             return_val = self._mastodon_ops.post_status(
    #                 login_user=username,
    #                 status=status,
    #             )
    #         else:
    #             self._print(
    #                 "Skipping real Mastodon API call since perform_operations is set to False",
    #                 color="light_grey",
    #             )

    #         self._print(
    #             f'Status posted for user: {current_user} ({username}): "{status}"',
    #             emoji="📝",
    #         )
    #         # self._print(return_val)

    #     except ValueError as e:
    #         self._print(f"Invalid input: {e!s}", emoji="❌")
    #         raise

    #     except Exception as e:
    #         self._print(f"An unexpected error occurred: {e!s}", emoji="❌")
    #         raise
    #     toot_id = None
    #     if return_val:
    #         return_msg = (
    #             f"{current_user} posted a toot with Toot ID: {return_val['id']} --- {status}\n"
    #         )
    #         toot_id = return_val["id"]
    #     else:
    #         return_msg = f'{current_user} posted a toot!: "{status}"\n'
    #     self.action_logger.log(
    #         {
    #             "source_user": current_user_full,
    #             "label": "post",
    #             "data": {"toot_id": toot_id, "post_text": status},
    #         }
    #     )
    #     return return_msg

    @app_action
    def reply_to_toot(
        self,
        current_user: str,
        status: str,
        in_reply_to_id: int,
    ) -> str:
        """Post a new status update to the Mastodon-like social network.

        Args:
            current_user (str): The username of the user posting the status.
            status (str): The text content of the status update.
            in_reply_to_id (int): The `toot_id` of the status this post is replying to.

        Raises
        ------
            ValueError: If the input parameters are invalid.
            Exception: For any other unexpected errors during posting.
        """
        return_val = None
        try:
            current_user_full = str(current_user)
            current_user = current_user.split()[0]
            username = self._get_username(current_user)
            if self.perform_operations:
                return_val = self._mastodon_ops.post_status(
                    login_user=username,
                    status=status,
                    in_reply_to_id=in_reply_to_id,
                )
                toot_id = return_val["id"]
            else:
                self._print(
                    "Skipping real Mastodon API call since perform_operations is set to False",
                    color="light_grey",
                )
                toot_id = ""

            self._print(
                f"You replied to the toot with toot id {in_reply_to_id} : {status}",
                emoji="📝",
            )
            return_msg = (
                f"{current_user} replied to a toot with toot id {in_reply_to_id} : {status}"
            )
            self.action_logger.log(
                {
                    "source_user": current_user_full,
                    "label": "reply",
                    "data": {
                        "reply_to": {"toot_id": in_reply_to_id},
                        "toot_id": toot_id,
                        "post_text": status,
                    },
                }
            )
        except ValueError as e:
            self._print(f"Invalid input, regular toot posted: {e!s}", emoji="❌")
            return_msg = f'''There was an error in posting {current_user}'s reply, response was posted as a new toot!: "{status}"'''

        except Exception as e:
            self._print(f"An unexpected error occurred, regular toot posted: {e!s}", emoji="❌")
            return_msg = f'''There was an error in posting {current_user}'s reply, response was posted as a new toot!: "{status}"'''
        # issue: where is new toot
        return return_msg

    # @app_action
    # def get_public_timeline(self, limit: int) -> str:
    #     """Read the public Mastodon social network feed."""
    #     self._print(f"Fetching public timeline (limit: {limit})", emoji="🌐")
    #     if self.perform_operations:
    #         timeline = self._mastodon_ops.get_public_timeline(limit=limit)
    #     else:
    #         self._print(
    #             "Skipping real Mastodon API call since perform_operations is set to False",
    #             color="light_grey",
    #         )
    #         timeline = []
    #     self._print(f"Retrieved {len(timeline)} posts from the public timeline", emoji="📊")
    #     str_timeline = self.print_and_return_timeline(timeline)
    #     return f"{self._get_username} viewed the Public Mastodon timeline:\n" + str_timeline

    def print_timeline(self, timeline: list[dict[str, Any]]) -> None:
        """Print the timeline in a readable format."""

        def _clean_html(html_string):
            clean_text = re.sub("<[^<]+?>", "", unescape(html_string))
            return re.sub(r"\s+", " ", clean_text).strip()

        for post in timeline:
            self._print("----------------------------------------")
            self._print(f"User: {post['account']['display_name']} (@{post['account']['username']})")
            self._print(f"Content: {_clean_html(post['content'])}")
            self._print(f"Toot ID: {post['id']}")
            self._print(f"Favourites: {post['favourites_count']}, Reblogs: {post['reblogs_count']}")
            # self._print(f"URL: {post['url']}")
        self._print("----------------------------------------")

    def print_and_return_timeline(self, timeline: list[dict[str, Any]]) -> str:
        """Print the timeline in a readable format and return it as a string."""

        def _clean_html(html_string):
            clean_text = re.sub("<[^<]+?>", "", unescape(html_string))
            return re.sub(r"\s+", " ", clean_text).strip()

        output = []
        for post in timeline:
            output.extend(
                [
                    "----------------------------------------",
                    f"User: {post['account']['display_name']} (@{post['account']['username']})",
                    f"Content: {_clean_html(post['content'])}",
                    f"Toot ID: {post['id']}",
                    f"Favourites: {post['favourites_count']}, Reblogs: {post['reblogs_count']}",
                    # f"URL: {post['url']}",
                    "",  # Add an empty string to create a blank line between posts
                ]
            )
        output.append("----------------------------------------")

        str_timeline = "\n".join(output)
        self._print(str_timeline)
        return str_timeline

    @app_action
    def get_own_timeline(self, current_user: str, limit: int) -> str:
        """Read the Mastodon social network feed for the current user."""
        current_user_full = str(current_user)
        current_user = current_user.split()[0]
        username = self._get_username(current_user)
        self._print(
            f"Fetching @{username}'s timeline (limit: {limit})",
            emoji="🏠",
        )
        if self.perform_operations:
            timeline = self._mastodon_ops.get_own_timeline(username, limit=limit)
        else:
            timeline = []
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        self._print(
            f"Retrieved {len(timeline)} posts from @{username}'s timeline",
            emoji="📊",
        )
        str_timeline = self.print_and_return_timeline(timeline)

        self.action_logger.log(
            {
                "source_user": current_user_full,
                "label": "get_own_timeline",
                "data": {"num_posts_retreived": len(timeline)},  # TODO: add timeline here
            }
        )

        return "Own Mastodon Timeline:\n" + str_timeline

    # @app_action
    # def get_user_timeline(self, current_user: str, target_user: str, limit: int) -> str:
    #     """Read a specific user's timeline on Mastodon social network."""
    #     current_username = self._get_username(current_user.split()[0])
    #     target_username = self._get_username(target_user.split()[0])
    #     self._print(
    #         f"@{current_username} fetching @{target_username}'s timeline (limit: {limit})",
    #         emoji="👥",
    #     )
    #     if self.perform_operations:
    #         timeline = self._mastodon_ops.get_user_timeline(
    #             current_username, target_username, limit=limit
    #         )
    #     else:
    #         timeline = []
    #         self._print(
    #             "Skipping real Mastodon API call since perform_operations is set to False",
    #             color="light_grey",
    #         )
    #     self._print(
    #         f"Retrieved {len(timeline)} posts from @{target_username}'s timeline",
    #         emoji="📊",
    #     )
    #     str_timeline = self.print_and_return_timeline(timeline)
    #     return f"@{current_username}'s Mastodon Timeline:\n" + str_timeline

    def print_notifications(self, notifications: list[dict[str, Any]]) -> str:
        """Generate a string of important details of notifications, one per line."""
        if not notifications:
            return "No notifications to display."

        notification_lines = []
        for notification in notifications:
            notif_type = notification["type"]
            created_at = notification["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            account = notification["account"]
            display_name = account["display_name"]
            username = account["username"]

            notification_info = (
                f"[{created_at}] {notif_type.capitalize()} from {display_name} (@{username})"
            )

            if notif_type == "mention":
                status = notification.get("status", {})
                content = status.get("content", "No content available")
                # Truncate content if it's too long
                content = content[:50] + "..." if len(content) > 50 else content  # noqa: PLR2004
                notification_info += f" - Content: {content}"

            notification_lines.append(notification_info)

        return "\n".join(notification_lines)

    @app_action
    def read_notifications(self, current_user: str, clear: bool, limit: int) -> str:
        """Read Mastodon social network notifications."""
        current_user_full = str(current_user)
        current_user = current_user.split()[0]

        username = self._get_username(current_user)
        self._print(
            f"Reading notifications for @{username} (clear: {clear}, limit: {limit})",
            emoji="🔔",
        )
        if self.perform_operations:
            notifications = self._mastodon_ops.read_notifications(
                username, clear=clear, limit=limit
            )
        else:
            notifications = []
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )

        retrieval_message = f"Retrieved {len(notifications)} notifications for {current_user}:"
        self._print(retrieval_message, emoji="📬")

        notifications_string = self.print_notifications(notifications)
        full_output = f"{retrieval_message}\n{notifications_string}"
        self._print(full_output)
        self.action_logger.log(
            {
                "source_user": current_user_full,
                "label": "read_notification",
                "data": {
                    "num_notifications_retreived": len(notifications)
                },  # TODO: add notifications timeline here
            }
        )

        return full_output

    @app_action
    def like_toot(self, current_user: str, toot_id: str) -> str:
        """Like (favorite) a toot."""
        current_user_full = str(current_user)
        current_user = current_user.split()[0]
        current_username = self._get_username(current_user)
        # self._print(
        #     f"@{current_username} liking post {toot_id}",
        #     emoji="❤️",
        # )
        like_message = f"{current_user} (@{current_username}) liked post {toot_id}"
        if self.perform_operations:
            check = self._mastodon_ops.like_check(current_username, toot_id)
            if not check:
                self._mastodon_ops.like_toot(current_username, toot_id)
            else:
                like_message = f"{current_user} (@{current_username}) has previously liked post {toot_id}. Please conduct a different action!!"
        else:
            self._print(
                "Skipping real Mastodon API call since perform_operations is set to False",
                color="light_grey",
            )
        self._print(like_message, emoji="✅")
        self.action_logger.log(
            {
                "source_user": current_user_full,
                "label": "like_toot",
                "data": {"toot_id": str(toot_id)},
            }
        )
        return like_message

    # region[additional methods]

    @app_action
    def boost_toot(self, current_user: str, toot_id: str) -> str:
        """Boost (reblog) a toot."""
        current_user_full = str(current_user)
        current_user = current_user.split()[0]
        current_username = self._get_username(current_user)
        self._print(
            f"@{current_username} boosting post {toot_id}",
            emoji="🔁",
        )
        boost_message = f"{current_user} (@{current_username}) boosted post {toot_id}"
        if self.perform_operations:
            check = self._mastodon_ops.boost_check(current_username, toot_id)
            if not check:
                self._mastodon_ops.boost_toot(current_username, toot_id)
            else:
                boost_message = f"{current_user} (@{current_username}) has previously boosted post {toot_id}. Please conduct a different action!!"
        self._print(
            f"@{current_username} boosted post {toot_id}",
            emoji="✅",
        )
        self.action_logger.log(
            {
                "source_user": current_user_full,
                "label": "boost_toot",
                "data": {"toot_id": str(toot_id)},
            }
        )
        return boost_message

    # @app_action
    # def block_user(self, current_user: str, target_user: str) -> None:
    #   """Block a user."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(
    #       f"@{current_username} blocking user: @{target_username}", emoji="🚫"
    #   )
    #   if self.perform_operations:
    #     self._mastodon_ops.block_user(current_username, target_username)
    #   self._print(
    #       f"@{current_username} blocked user @{target_username}", emoji="✅"
    #   )

    # @app_action
    # def unblock_user(self, current_user: str, target_user: str) -> None:
    #   """Unblock a user."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(
    #       f"@{current_username} unblocking user: @{target_username}", emoji="✅"
    #   )
    #   if self.perform_operations:
    #     self._mastodon_ops.unblock_user(current_username, target_username)
    #   self._print(
    #       f"@{current_username} unblocked user @{target_username}", emoji="✅"
    #   )

    # @app_action
    # def mute_account(
    #     self,
    #     current_user: str,
    #     target_user: str,
    #     notifications: bool,
    #     duration: int,
    # ) -> None:
    #   """Mute an account."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(
    #       f"@{current_username} muting @{target_username} (notifications:"
    #       f" {notifications}, duration: {duration})",
    #       emoji="🔇",
    #   )
    #   if self.perform_operations:
    #     self._mastodon_ops.mute_account(
    #         current_username,
    #         target_username,
    #         notifications=notifications,
    #         duration=duration,
    #     )
    #   self._print(f"@{current_username} muted @{target_username}", emoji="✅")

    # @app_action
    # def unmute_account(self, current_user: str, target_user: str) -> None:
    #   """Unmute an account."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(f"@{current_username} unmuting @{target_username}", emoji="🔊")
    #   if self.perform_operations:
    #     self._mastodon_ops.unmute_account(current_username, target_username)
    #   self._print(f"@{current_username} unmuted @{target_username}", emoji="✅")

    # @app_action
    # def delete_posts(
    #     self,
    #     current_user: str,
    #     post_ids: list[str],
    #     recent_count: int,
    #     delete_all: bool,
    # ) -> None:
    #   """Delete posts for a user."""
    #   username = self._get_username(current_user)
    #   if delete_all:
    #     self._print(f"Deleting all posts for @{username}", emoji="🗑️")
    #   elif recent_count:
    #     self._print(
    #         f"Deleting {recent_count} recent posts for @{username}", emoji="🗑️"
    #     )
    #   elif post_ids:
    #     self._print(f"Deleting specific posts for @{username}", emoji="🗑️")
    #   else:
    #     self._print("No posts specified for deletion", emoji="❌")
    #     return

    #   if self.perform_operations:
    #     self._mastodon_ops.delete_posts(
    #         username,
    #         post_ids=post_ids,
    #         recent_count=recent_count,
    #         delete_all=delete_all,
    #     )
    #   self._print("Deletion process completed", emoji="✅")

    # @app_action
    # def send_direct_message(
    #     self, current_user: str, target_user: str, message: str
    # ) -> None:
    #   """Send a direct message to another user."""
    #   current_username = self._get_username(current_user)
    #   target_username = self._get_username(target_user)
    #   self._print(
    #       f"@{current_username} sending DM to @{target_username}", emoji="✉️"
    #   )
    #   if self.perform_operations:
    #     self._mastodon_ops.post_status(
    #         current_username, f"@{target_username} {message}", visibility="direct"
    #     )
    #   self._print(
    #       f"DM sent from @{current_username} to @{target_username}", emoji="✅"
    #   )

    # endregion


# endregion
