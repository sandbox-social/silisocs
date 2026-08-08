"""Projecting composer fields out of, and writing them back into, YAML documents.

Reading a field value and writing one both have to agree on which document in a
scenario's Hydra group layout owns a dotted key, which is what
:func:`_group_file` decides for both directions.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import yaml

from silisocs.studio.form_schema import FormSchema, form_schema
from silisocs.studio.scenario_repository import ensure_package_directive, leading_comment_block

_GROUP_FILES = {
    "world": "world/default.yaml",
    "agents": "agents/default.yaml",
    "sim": "sim.yaml",
    "env": "env.yaml",
    "eval": "eval.yaml",
}


def _group_file(group: str, available: Sequence[str]) -> str | None:
    """The document a composer field's group reads from and writes to.

    A scenario may name its group files after itself (``world/resource_market.yaml``,
    selected with ``world=resource_market``) instead of shipping the default
    variant. Writing to the fixed default name would add a SECOND option to the
    same Hydra group and silently change which one a run composes, so a lone
    variant is the target when the default is absent.
    """
    default = _GROUP_FILES.get(group)
    if default is None or "/" not in default or default in set(available):
        return default
    variants = sorted(
        name
        for name in available
        if name.startswith(f"{group}/") and name.endswith(".yaml") and name.count("/") == 1
    )
    return variants[0] if len(variants) == 1 else default


def field_values(files: dict[str, str], schema: FormSchema | None = None) -> dict[str, Any]:
    """Project known schema fields from YAML documents without altering unknown keys."""
    documents = {name: yaml.safe_load(text) or {} for name, text in files.items()}
    values: dict[str, Any] = {}
    names = list(files)
    for item in (schema or form_schema()).fields:
        group, *parts = item.key.split(".")
        value: Any = documents.get(_group_file(group, names) or "", {})
        for part in parts:
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        values[item.key] = value
    return values


# The generic component pipeline a non-social backend needs, written into a
# scenario's env.yaml whenever the composer points it at one. ``params: null``
# rather than ``{}`` is load-bearing: a scenario env.yaml is MERGED over the
# default (social) env group, and OmegaConf.merge cannot clear sibling keys — an
# empty mapping leaves the social params (graph, recsys, timeline) in place, and
# the generic component then rejects them as unsupported. Null replaces the node.
_GENERIC_COMPONENTS: dict[str, str] = {
    "initialize": "app_initialize",
    "observe": "app_observation",
    "update": "none",
    "action_prompt": "default",
}


def _generic_components_block(resolve_built_in: str) -> dict[str, Any]:
    slots = {**_GENERIC_COMPONENTS, "resolve": resolve_built_in}
    return {
        role: {"built_in": built_in, "class_path": None, "params": None}
        for role, built_in in slots.items()
    }


def _is_generated_component_slot(slot: Any) -> bool:
    """True only for a slot this composer wrote itself (the bare generic block).

    ``_generic_components_block`` emits exactly ``{built_in, class_path: None,
    params: None}``. Matching that precise shape distinguishes the composer's own
    output from a user-authored slot carrying params or a class_path, so
    re-deriving components on a backend-type change never clobbers hand config.
    """
    return (
        isinstance(slot, dict)
        and set(slot) == {"built_in", "class_path", "params"}
        and slot.get("class_path") is None
        and slot.get("params") is None
    )


def _apply_backend_component_defaults(documents: dict[str, Any], touched: set[str]) -> None:
    """Keep a draft's GM components compatible with the backend it selects.

    The composer's scenario runs against the default (social) env group, whose
    components call SocialBackendApp-only methods. Selecting a non-social backend
    without replacing them produces a scenario that composes cleanly and then
    fails at run time, so the components a backend needs follow the backend.

    Only ever run when the backend *type/class* changes (the caller gates this),
    and even then only touch slots this composer itself wrote — a user's authored
    observe/action_prompt/init config must survive a backend edit untouched.
    """
    # Both imports are per call, not module scope: reading the draft's backend
    # class is the one thing here that needs the backend layer, and only a
    # backend-type edit reaches it.
    from silisocs.environments.backends.base import SocialBackendApp  # noqa: PLC0415
    from silisocs.studio.form_providers import configured_backend  # noqa: PLC0415

    names = list(documents)
    env_file = _group_file("env", names) or "env.yaml"
    env = documents.get(env_file)
    if not isinstance(env, dict):
        return
    files = {name: yaml.safe_dump(document) for name, document in documents.items()}
    _, cls = configured_backend(files)
    if cls is None:  # Unimportable: say nothing rather than guess (preflight reports it).
        return
    # Coerce ``gm``/``components`` that a draft may carry as null or a non-mapping
    # (e.g. ``gm: {components: null}``) before mutating, so a hand-written scalar
    # never surfaces as an unhandled AttributeError.
    gm = env.get("gm")
    if not isinstance(gm, dict):
        gm = {}
        env["gm"] = gm
    components = gm.get("components")
    if not isinstance(components, dict):
        components = {}
        gm["components"] = components
    if issubclass(cls, SocialBackendApp):
        # The social env group already supplies these; reclaim only the generic
        # block this composer previously wrote, never a user-authored slot.
        for role in (*_GENERIC_COMPONENTS, "resolve"):
            if _is_generated_component_slot(components.get(role)):
                components.pop(role, None)
        if not components:
            gm.pop("components", None)
    else:
        sim_file = _group_file("sim", names) or "sim.yaml"
        sim = documents.get(sim_file)
        tool_calling = ((sim or {}).get("tool_calling") or {}).get("mode") if sim else None
        # action_mode is set to ``generic`` below; the canonical resolve for generic
        # output is ``generic_action`` (parsed_action needs an ACTION TYPE block and
        # a SocialBackendApp-only method, so it fails on generic output).
        resolve = (
            "tool_calling" if str(tool_calling or "") in {"single", "multi"} else "generic_action"
        )
        # Fill only slots that are absent or hold our own prior generic block;
        # never overwrite a slot the user customized with params or a class_path.
        for role, slot in _generic_components_block(resolve).items():
            if role not in components or _is_generated_component_slot(components.get(role)):
                components[role] = slot
        # The social group's prompt text is written for a timeline; a generic
        # backend describes itself through its own action catalog instead.
        if isinstance(sim, dict):
            sim["action_mode"] = "generic"
            touched.add(sim_file)
    touched.add(env_file)


def compose_files(files: dict[str, str], updates: dict[str, Any]) -> dict[str, str]:
    """Apply dotted schema updates to YAML documents while preserving every other key.

    Documents an update does not touch pass through byte-for-byte; touched
    documents are re-serialized but keep their leading comment block (which is
    where the ``# @package`` directives live).
    """
    documents = {name: yaml.safe_load(text) or {} for name, text in files.items()}
    touched: set[str] = set()
    names = list(files)
    for key, value in updates.items():
        group, *parts = str(key).split(".")
        relative = _group_file(group, names)
        if relative is None or not parts:
            raise ValueError(f"Unknown composer field path {key!r}")
        document = documents.setdefault(relative, {})
        touched.add(relative)
        cursor = document
        for part in parts[:-1]:
            child = cursor.setdefault(part, {})
            if not isinstance(child, dict):
                raise ValueError(f"Cannot write {key!r}; {part!r} is not a mapping")
            cursor = child
        cursor[parts[-1]] = value
    # Re-derive GM components only when the backend TYPE/class actually changes,
    # never on other backend.* edits (enabled_actions, params) — those must not
    # disturb authored component config.
    if any(str(key) in ("env.gm.backend.type", "env.gm.backend.class_path") for key in updates):
        _apply_backend_component_defaults(documents, touched)
    composed = {}
    for relative, document in documents.items():
        if relative in touched:
            text = leading_comment_block(files.get(relative, "")) + yaml.safe_dump(
                document, sort_keys=False, allow_unicode=True
            )
        else:
            text = files[relative]
        composed[relative] = ensure_package_directive(relative, text)
    return composed
