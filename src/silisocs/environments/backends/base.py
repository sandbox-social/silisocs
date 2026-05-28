"""Backend app base class and action infrastructure for simulations.

Provides the shared backend action infrastructure for social and non-social
environment backends.
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


def app_action(
    method: Callable[..., Any] | None = None,
    *,
    selectable_name: str | None = None,
    description: str | None = None,
):
    """Mark BackendApp methods as callable actions.

    Decorated methods become discoverable via BackendApp.actions() and can be
    invoked through BackendApp.invoke_action().
    """

    def _decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(fn)
        required_params = [
            name
            for name, param in signature.parameters.items()
            if param.default == inspect.Parameter.empty and name != "self"
        ]
        fn_any = typing.cast(Any, fn)
        fn_any.__app_action__ = True
        fn_any.__required_params__ = required_params
        fn_any.__action_selectable_name__ = selectable_name
        fn_any.__action_description_override__ = description
        return fn

    if method is not None:
        return _decorate(method)
    return _decorate


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
    """Represents an action that can be invoked on a backend app."""

    name: str
    selectable_name: str
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
        description_override = getattr(method, "__action_description_override__", None)
        if description_override:
            description = str(description_override)
        signature = inspect.signature(method)
        type_hints = get_type_hints(method)

        method_parameters = []
        for name, param in signature.parameters.items():
            if name == "self":
                continue
            param_type = type_hints.get(name, Any)
            required = param.default == inspect.Parameter.empty
            param_description = next(
                (p.description for p in doc.params if p.arg_name == name), None
            )
            method_parameters.append(Parameter(name, param_type, param_description, required))

        return cls(
            name=method.__name__,
            selectable_name=str(
                getattr(method, "__action_selectable_name__", None) or method.__name__
            ),
            description=description,
            parameters=method_parameters,
            docstring=doc,
        )


# --------------------------------------------------------------------------- #
# BackendApp Base Class
# --------------------------------------------------------------------------- #


class BackendApp(metaclass=abc.ABCMeta):
    """Base class for environment backends that agents can interact with.

    Extend this class and decorate any method that should be callable from the
    simulation with ``@app_action``. The base contract is intentionally
    domain-neutral: apps expose callable actions, can provide observations, and
    may opt into a generic initialization hook. Social backends layer timeline
    behavior on top through :class:`SocialBackendApp`.
    """

    action_logger: Any = None
    _log_color: COLOR_TYPE = "blue"

    def __init__(self) -> None:
        self._enabled_actions: set[str] | None = None

    @abc.abstractmethod
    def name(self) -> str:
        """Return the name of the app."""
        raise NotImplementedError

    @abc.abstractmethod
    def description(self) -> str:
        """Return a description of the app."""
        raise NotImplementedError

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        """Optional generic app setup hook.

        Game-master-owned backend initializers call this only for backends that
        opt into the generic ``app_initialize`` path. Social backends should
        prefer explicit setup helpers consumed by the social backend
        initializer.
        """
        del agent_names, kwargs

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        """Return a domain-specific observation string for an actor."""
        del actor_name, kwargs
        return ""

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
        actions = [
            ActionDescriptor.from_method(m) for _, m in methods if hasattr(m, _ACTION_PROPERTY)
        ]
        enabled_actions = getattr(self, "_enabled_actions", None)
        if enabled_actions is None:
            return actions
        return [
            action
            for action in actions
            if action.name in enabled_actions or action.selectable_name in enabled_actions
        ]

    def action_catalog(self) -> list[dict[str, Any]]:
        """Return a normalized action catalog for prompting/UI/config validation."""
        catalog: list[dict[str, Any]] = []
        for action in self.actions():
            catalog.append(
                {
                    "name": action.name,
                    "selectable_name": action.selectable_name,
                    "description": action.description.strip(),
                    "parameters": [
                        {
                            "name": p.name,
                            "required": p.required,
                            "kind": str(p.kind),
                            "description": p.description,
                        }
                        for p in action.parameters
                    ],
                }
            )
        return catalog

    def set_enabled_actions(self, enabled_actions: Sequence[str] | None) -> None:
        """Restrict actions exposed to prompts/parsers/tool-calling.

        If ``enabled_actions`` is None, all actions are exposed.
        """
        if enabled_actions is None:
            self._enabled_actions = None
            return

        normalized = {str(name).strip() for name in enabled_actions if str(name).strip()}
        all_action_names = {action.name for action in self.actions()} | {
            action.selectable_name for action in self.actions()
        }
        unknown = sorted(name for name in normalized if name not in all_action_names)
        if unknown:
            available = ", ".join(sorted(all_action_names))
            raise ValueError(
                "Unknown enabled action(s): "
                + ", ".join(unknown)
                + f". Available actions: {available}"
            )
        self._enabled_actions = normalized

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

    def _action_lookup(self) -> dict[str, ActionDescriptor]:
        lookup: dict[str, ActionDescriptor] = {}
        for action in self.actions():
            lookup[action.name] = action
            lookup[action.selectable_name] = action
        return lookup

    def invoke_action_by_name(self, action_name: str, args_text: str) -> str:
        """Find an action by name and invoke it with the given argument text.

        Used by the 'generic' action_mode to dispatch LLM output directly.
        """
        action_map = self._action_lookup()
        if action_name not in action_map:
            available = ", ".join(sorted(action_map.keys()))
            return f"Unknown action '{action_name}'. Available actions: {available}"
        return self.invoke_action(action_map[action_name], args_text) or ""

    def invoke_action_with_kwargs(self, action_name: str, args: dict[str, Any]) -> str:
        """Invoke action by name using structured kwargs payload."""
        action_map = self._action_lookup()
        if action_name not in action_map:
            available = ", ".join(sorted(action_map.keys()))
            return f"Unknown action '{action_name}'. Available actions: {available}"

        action = action_map[action_name]
        expected = {param.name: param for param in action.parameters}
        payload = dict(args or {})

        missing_required = [
            name for name, param in expected.items() if param.required and name not in payload
        ]
        if missing_required:
            return f"Missing required argument(s): {', '.join(missing_required)}"

        unexpected = sorted(set(payload.keys()) - set(expected.keys()))
        if unexpected:
            return f"Unexpected argument(s): {', '.join(unexpected)}"

        processed: dict[str, Any] = {}
        for name, param in expected.items():
            if name not in payload:
                if not param.required:
                    processed[name] = None
                continue

            value = payload[name]
            if value is None and not param.required:
                processed[name] = None
                continue

            if isinstance(value, str):
                try:
                    processed[name] = param.value_from_text(value)
                except Exception:
                    processed[name] = value
            else:
                processed[name] = value

        try:
            return getattr(self, action.name)(**processed) or ""
        except Exception as exc:
            self._print(f"Error invoking action {action.name}: {exc}", color="red")
            return f"Error invoking action {action.name}: {exc}"

    def generate_generic_action_prompt(self) -> str:
        """Build a call-to-action prompt auto-generated from @app_action methods.

        Used when action_mode='generic'.  The LLM must respond in the format::

            ACTION: <action_name>
            <param>: <value>
            <param>: <value>
        """
        description = str(self.description() or "").strip()
        lines = [description] if description else []
        lines += ["", "Available actions:"]
        actions = list(self.actions())
        for action in actions:
            params_desc = ", ".join(
                f"{p.name} ({'required' if p.required else 'optional'}, {p.kind})"
                for p in action.parameters
            )
            lines.append(f"  {action.selectable_name}({params_desc})")
            lines.append(f"    {action.description.strip()}")

        lines += [
            "",
            "[OUTPUT STYLE]",
            "Respond with EXACTLY ONE action using this format:",
            "ACTION: <action_name>",
            "<param_name>: <value>",
            "<param_name>: <value>",
        ]
        return "\n".join(lines)

    def generate_tool_schemas(self) -> list[dict]:
        """Return OpenAI-compatible tool schemas for all @app_action methods.

        Used when resolve mode is 'tool_calling'.
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
                        "name": action.selectable_name,
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

    @app_action(
        selectable_name="FINISHED",
        description="To be used when desirable actions for current timestep have been conducted.",
    )
    def finish_action_episode(self) -> str:
        """No-op terminal action for open-ended loops and constrained action sets."""
        return "Finished action episode"

    def generate_action_prompt(self) -> str:
        """Generate the call-to-action prompt listing all available actions."""
        return self.full_description()


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
# SocialBackendApp - social-specific backend base
# --------------------------------------------------------------------------- #


class SocialBackendApp(BackendApp):
    """Base class for social-media backends.

    Social backends add explicit timeline, feed, action parsing, and optional
    recommendation hooks on top of the domain-neutral :class:`BackendApp`.
    Non-social backends should subclass :class:`BackendApp` directly.
    """

    def setup_social_state(
        self,
        *,
        agent_names: list[str],
        sim_roles: dict[str, str] | None = None,
        graph_config: dict[str, Any] | None = None,
        following_graph: dict[str, list[str]] | None = None,
        agent_bios: dict[str, str] | None = None,
    ) -> None:
        """Set up social users and relationships for this backend."""
        del agent_names, sim_roles, graph_config, following_graph, agent_bios
        raise NotImplementedError(f"{type(self).__name__} does not implement social state setup.")

    def get_timeline(self, user_name: str, limit: int = 10) -> list[dict]:
        """Return raw timeline data for a user."""
        del user_name, limit
        raise NotImplementedError(f"{type(self).__name__} does not implement timelines.")

    def get_timeline_mode(
        self,
        timeline_mode: str,
        user_name: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config: dict,
    ) -> list[dict]:
        """Return timeline data for a specific mode."""
        del timeline_mode, user_name, limit, recsys_type, timeline_config
        raise NotImplementedError(f"{type(self).__name__} does not implement timeline modes.")

    def format_timeline_for_observation(self, timeline: list[dict]) -> str:
        """Convert raw timeline data into text for an agent observation."""
        del timeline
        raise NotImplementedError(f"{type(self).__name__} does not format social timelines.")

    def parse_and_resolve_action(self, user_name: str, action_data: dict) -> str:
        """Dispatch a parsed social action to the correct backend method."""
        del user_name, action_data
        raise NotImplementedError(f"{type(self).__name__} does not resolve social actions.")
