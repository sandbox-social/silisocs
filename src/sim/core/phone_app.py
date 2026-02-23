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


# --------------------------------------------------------------------------- #
# Argument Text Parser
# --------------------------------------------------------------------------- #


def _parse_argument_text(args_text: str) -> dict[str, str]:
    """Parse multiline argument text to a dict.

    'param1: value1\\n param2: value2' -> {'param1': 'value1', 'param2': 'value2'}
    """
    matches = _ARGUMENT_REGEX.finditer(args_text)
    return {m.group("param"): m.group("value").strip() for m in matches if m.group("value").strip()}
