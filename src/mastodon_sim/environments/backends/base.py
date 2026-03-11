"""Base PhoneApp class and action infrastructure for simulation apps.

Extracted from mastodon_sim/apps.py to provide a shared foundation
for all social media platform apps (Mastodon, Twitter-like, Reddit-like, etc.).
"""

import abc
import dataclasses
import datetime
import inspect
import re
import textwrap
import types
import typing
from collections.abc import Callable, Sequence
from typing import Any, Literal, get_type_hints

import docstring_parser
import termcolor

# --------------------------------------------------------------------------- #
# Constants & Types
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Literal / Argument Parsing Helpers
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# @app_action Decorator
# --------------------------------------------------------------------------- #


def app_action(method):
    """Mark PhoneApp methods as callable actions.

    Decorated methods become discoverable via PhoneApp.actions() and can be
    invoked through PhoneApp.invoke_action().
    """
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


# --------------------------------------------------------------------------- #
# Parameter Dataclass
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# ActionDescriptor Dataclass
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# PhoneApp Base Class
# --------------------------------------------------------------------------- #


class PhoneApp(metaclass=abc.ABCMeta):
    """Base class for apps that concordia can interact with using plain English.

    Extend this class and decorate any method that should be callable from the
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

    def invoke_action_by_name(self, action_name: str, args_text: str) -> str:
        """Find an action by name and invoke it with the given argument text.

        Used by the 'generic' action_mode to dispatch LLM output directly.
        """
        action_map = {a.name: a for a in self.actions()}
        if action_name not in action_map:
            available = ", ".join(action_map.keys())
            return f"Unknown action '{action_name}'. Available actions: {available}"
        return self.invoke_action(action_map[action_name], args_text) or ""

    def generate_generic_action_prompt(self) -> str:
        """Build a call-to-action prompt auto-generated from @app_action methods.

        Used when action_mode='generic'.  The LLM must respond in the format::

            ACTION: <action_name>
            <param>: <value>
            <param>: <value>
        """
        lines = [
            f"{self.name()}: {self.description()}",
            "",
            "Available actions:",
        ]
        for action in self.actions():
            params_desc = ", ".join(
                f"{p.name} ({'required' if p.required else 'optional'}, {p.kind})"
                for p in action.parameters
            )
            lines.append(f"  {action.name}({params_desc})")
            lines.append(f"    {action.description.strip()}")

        lines += [
            "",
            "Respond with EXACTLY ONE action using this format:",
            "ACTION: <action_name>",
            "<param_name>: <value>",
            "<param_name>: <value>",
            "",
            "Rules:",
            '  - current_user is always the full display name, e.g. "Alice Smith".',
            "  - Only use real post/toot IDs from the timeline shown above.",
            "  - For status/content fields, write in first person as the character.",
            "  - Omit optional parameters you do not want to use.",
        ]
        return "\n".join(lines)

    def generate_tool_schemas(self) -> list[dict]:
        """Return OpenAI-compatible tool schemas for all @app_action methods.

        Used when action_mode='tool_calling'.
        """
        schemas = []
        for action in self.actions():
            properties: dict[str, Any] = {}
            required: list[str] = []
            for param in action.parameters:
                prop = _param_to_json_schema(param)
                if param.description:
                    prop = dict(prop, description=param.description)
                properties[param.name] = prop
                if param.required:
                    required.append(param.name)
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": action.name,
                        "description": action.description.strip(),
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        },
                    },
                }
            )
        return schemas


# --------------------------------------------------------------------------- #
# Argument Text Parser
# --------------------------------------------------------------------------- #


def _parse_argument_text(args_text: str) -> dict[str, str]:
    """Parse multiline argument text to a dict.

    'param1: value1\\n param2: value2' -> {'param1': 'value1', 'param2': 'value2'}
    """
    matches = _ARGUMENT_REGEX.finditer(args_text)
    return {m.group("param"): m.group("value").strip() for m in matches if m.group("value").strip()}


def _param_to_json_schema(param: "Parameter") -> dict:
    """Convert a Parameter's type annotation to a JSON Schema property dict."""
    kind = param.kind
    origin = typing.get_origin(kind)

    if kind is str:
        return {"type": "string"}
    if kind is int:
        return {"type": "integer"}
    if kind is bool:
        return {"type": "boolean"}
    if kind is float:
        return {"type": "number"}

    if origin in (typing.Union, types.UnionType):
        non_none = [t for t in typing.get_args(kind) if t is not type(None)]
        if len(non_none) == 1:
            return _param_to_json_schema(
                Parameter(param.name, non_none[0], param.description, param.required)
            )

    if origin is list:
        inner = typing.get_args(kind)
        inner_type = "string"
        if inner and inner[0] is int:
            inner_type = "integer"
        return {"type": "array", "items": {"type": inner_type}}

    if typing.get_origin(kind) is Literal:
        return {"type": "string", "enum": list(typing.get_args(kind))}

    return {"type": "string"}  # fallback


# --------------------------------------------------------------------------- #
# SocialMediaApp - abstract base for all social media platform apps
# --------------------------------------------------------------------------- #


class SocialMediaApp(PhoneApp, abc.ABC):
    """Base class for all social media platform apps used in the simulation.

    Subclasses wrap a platform engine (e.g. Mastodon API, TwitterLikePlatform,
    RedditLikePlatform) and expose ``@app_action`` decorated methods that agents
    can invoke.

    The only **required** method is ``initialize()``, which sets up platform
    state (users, follow networks, seed posts, etc.) at the start of a
    simulation run.  Everything else (``get_timeline``,
    ``format_timeline_for_observation``, ``parse_and_resolve_action``) has a
    default no-op implementation so simple subclasses can start minimal and
    grow.
    """

    # ---------------------------------------------------------------------- #
    # Required interface
    # ---------------------------------------------------------------------- #

    @abc.abstractmethod
    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Set up the platform state for a simulation run.

        The game master calls this once with all agents and config.  Each
        platform is responsible for its own network generation strategy
        (e.g. Barabasi-Albert for Twitter/Mastodon, subreddit-based for Reddit).

        Args:
            agent_names: List of agent display names.
            **kwargs: Common keys:
                - ``sim_roles`` (dict[str, str]): Agent name -> role.
                - ``seed_posts`` (dict[str, str]): Agent name -> initial post.
                - ``social_network`` (dict): Network config from scenario YAML.
                  Keys include ``network_type``, ``barabasi_albert_m``,
                  ``fully_connected_targets``, ``base_followership_probability``,
                  ``activity_transition_rates``, and platform-specific keys
                  like ``subreddits`` for Reddit.
        """
        ...

    # ---------------------------------------------------------------------- #
    # Optional interface (override per platform)
    # ---------------------------------------------------------------------- #

    def get_timeline(self, user_name: str, limit: int = 10) -> list[dict]:
        """Return raw timeline data for a user.

        Args:
            user_name: The display name of the user whose timeline to fetch.
            limit: Maximum number of posts to return.

        Returns
        -------
            A list of dicts, each representing a post in platform-native format.
        """
        return []

    def format_timeline_for_observation(self, timeline: list[dict]) -> str:
        """Convert raw timeline data into a human-readable string for the LLM prompt.

        Args:
            timeline: List of post dicts as returned by ``get_timeline()``.

        Returns
        -------
            A formatted string suitable for inclusion in an agent observation.
        """
        return ""

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        """Dispatch a parsed action to the correct ``@app_action`` method.

        Args:
            user_name: The display name of the acting agent.
            action_data: Dict with keys ``action_type``, ``target_id``,
                ``content``, ``reasoning`` as parsed from the LLM output.

        Returns
        -------
            A result string describing the outcome of the action.
        """
        return ""

    def generate_action_prompt(self) -> str:
        """Generate the call-to-action prompt listing all available actions.

        Uses ``self.full_description()`` by default; subclasses can override
        for custom prompt formatting.
        """
        return self.full_description()
