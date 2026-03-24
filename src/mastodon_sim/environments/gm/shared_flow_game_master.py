"""Multi-flow social-media game master with component routing.

Use this GM when enable_gm_multi_flow=true in config. Supports:
- Multiple component instances per role (e.g., TimelineObservation + EpisodeObservation)
- Flow-based component routing (agents use components based on their assigned flow)
- Per-flow multi-field configuration (different algorithms/strategies per flow)

All agents share one backend/app state but may see different observations/resolutions.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
from collections.abc import Mapping
from typing import Any, cast

from concordia.agents import entity_agent_with_logging
from concordia.associative_memory import basic_associative_memory
from concordia.components import game_master as gm_components  # type: ignore[attr-defined]
from concordia.language_model import language_model
from omegaconf import OmegaConf

from mastodon_sim.environments.gm import act as gm_social_act
from mastodon_sim.environments.gm.base_game_master import BaseSocialMediaGameMaster
from mastodon_sim.environments.gm.components.factory import (
    build_next_acting_component,
    build_observe_component,
    build_observe_components,
    build_recommendation_component,
    build_resolve_component,
    initialize_component_multi_fields,
)
from mastodon_sim.runtime.config import ConfigStore

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class MultiFlowSocialMediaGameMaster(BaseSocialMediaGameMaster):
    """Multi-flow game master with explicit component routing per flow.

    This GM enables advanced scenarios where different agents (grouped by flow)
    use different component implementations and configurations.

    Configuration:
        sim:
          enable_gm_multi_flow: true
          gm:
            preset: shared_flow

    Component routing is built from entity_action_flows and the multi-component
    configuration in gm.components.observe, etc.
    """

    description: str = "A multi-flow social-media game master with component routing."

    def _is_shared_flow_mode(self) -> bool:
        """Indicates this is a multi-flow aware mode."""
        return True

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        """Build multi-flow GM with component routing.

        Overrides base build to handle multiple component instances per role.
        """
        cfg = ConfigStore.get_config()
        name = str(self.params.get("name"))
        calls_to_action = self.params.get("calls_to_action", {})
        user_data = self.params["sm_user_data"]
        call_to_sm_action = calls_to_action.get("social_media_action", "")

        player_names = [e.name for e in self.entities]

        # Build single components first (next_acting, resolve, recommend) - same as base
        action_mode_to_resolve_map = {
            "custom": "parsed_action",
            "generic": "generic_action",
        }
        gm_components_cfg: dict[str, Any] = {}
        if hasattr(cfg.sim, "gm") and getattr(cfg.sim.gm, "components", None) is not None:
            gm_components_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(cfg.sim.gm.components, resolve=True),
            )
        gm_components_cfg = _expand_shared_flow_map_alias(gm_components_cfg)

        user_data = self.params["sm_user_data"]
        activity_rates = dict(user_data.get("activity_transition_rates", {}))
        entity_action_flows = dict(user_data.get("entity_action_flows", {}))

        next_actor = build_next_acting_component(
            gm_components_cfg.get("next_acting"),
            player_names=player_names,
            activity_transition_rates=activity_rates,
        )

        resolve_slot = dict(gm_components_cfg.get("resolve", {}))
        if not resolve_slot:
            resolve_slot = {
                "built_in": action_mode_to_resolve_map.get(
                    getattr(cfg.sim, "action_mode", "custom"), "parsed_action"
                ),
            }
        enable_tool_calling = resolve_slot.get("built_in") == "tool_calling"

        platform_type = getattr(cfg.social_media, "platform_type", "twitter_like")

        timeline_mode = str(
            getattr(
                cfg.sim,
                "timeline_mode",
                getattr(cfg.sim, "timeline_strategy", "follower_chronological"),
            )
        )
        supported_timeline_modes = {
            "twitter_like": {
                "follower_chronological",
                "pure_recsys",
                "hybrid_recsys_follower",
                "curated_global",
            },
            "reddit_like": {
                "follower_chronological",
                "pure_recsys",
                "hybrid_recsys_follower",
            },
            "mastodon": {"follower_chronological"},
        }
        allowed_modes = supported_timeline_modes.get(platform_type, {"follower_chronological"})
        if timeline_mode not in allowed_modes:
            raise ValueError(
                f"Unsupported timeline_mode='{timeline_mode}' for platform '{platform_type}'. "
                f"Supported: {sorted(allowed_modes)}"
            )
        timeline_config = {}
        if hasattr(cfg.sim, "timeline_config"):
            timeline_config = (
                cast(
                    dict[str, Any],
                    OmegaConf.to_container(cfg.sim.timeline_config, resolve=True),
                )
                if isinstance(cfg.sim.timeline_config, dict)
                else {}
            )

        # Build the social media app (same as base)
        from mastodon_sim.environments.backends.factory import create_social_media_app

        action_logger = None  # Would need to build this from config
        db_path = os.path.join(cfg.sim.output_rootname, "multiflow.db")

        sm_app = create_social_media_app(
            platform_type=platform_type,
            action_logger=action_logger,
            perform_operations=getattr(cfg.social_media, "use_server", False),
            app_description=self.params.get("app_description", ""),
            db_path=db_path,
        )

        # KEY CHANGE: Build MULTIPLE observe components
        observe_slots = dict(gm_components_cfg.get("observe", {}))
        episode_observation_flow = (
            dict(observe_slots.get("params") or {}).get("episode_observation_flow", "fixed_pre")
            if isinstance(observe_slots, dict) and "params" in observe_slots
            else "fixed_pre"
        )

        # Check if observe has nested structure (multi-instance) or flat structure (single)
        has_multi_instances = isinstance(observe_slots.get("instances"), dict) or any(
            isinstance(v, dict) and ("built_in" in v or "class_path" in v)
            for v in observe_slots.values()
            if v  # Skip None/empty values
        )

        if has_multi_instances:
            # Multi-instance mode: build multiple components
            logger.info("Building multiple observe components for multi-flow mode")
            observe_components = build_observe_components(
                observe_slots,
                model=model,
                player_names=player_names,
                sm_app=sm_app,
                entity_action_flows=entity_action_flows,
                episode_observation_flow=episode_observation_flow,
                timeline_mode=timeline_mode,
                timeline_strategy=timeline_mode,
                timeline_config=timeline_config,
            )
        else:
            # Single-instance mode: use single component (fallback)
            logger.info("Using single observe component (no multi-instance config)")
            single_observe = build_observe_component(
                observe_slots,
                model=model,
                player_names=player_names,
                sm_app=sm_app,
                entity_action_flows=entity_action_flows,
                episode_observation_flow=episode_observation_flow,
                timeline_mode=timeline_mode,
                timeline_strategy=timeline_mode,
                timeline_config=timeline_config,
            )
            class_name = single_observe.__class__.__name__
            kebab_key = _class_to_kebab_case(class_name)
            full_key = f"observe__{kebab_key}"
            observe_components = {full_key: single_observe}

        # Build resolve component (no multi-instance for now, but could add)
        resolve_component = build_resolve_component(
            resolve_slot,
            sm_app=sm_app,
            model=model,
            call_to_action_str=call_to_sm_action,
        )

        # Build recommendation component
        recommend_slot = dict(gm_components_cfg.get("recommend", {}))
        recommend_component = build_recommendation_component(
            recommend_slot,
            sm_app=sm_app,
            platform_type=platform_type,
            timeline_mode=timeline_mode,
        )

        # Combine all components
        components = {
            gm_components.next_acting.DEFAULT_NEXT_ACTING_COMPONENT_KEY: next_actor,
            gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY: resolve_component,
            "recommendation": recommend_component,
        }
        components.update(observe_components)

        # Initialize multi-field values for all components
        for slot_key, slot_cfg in gm_components_cfg.items():
            for component_key, component in components.items():
                if component_key.startswith(f"{slot_key}__"):
                    initialize_component_multi_fields(component, slot_cfg)

        if hasattr(recommend_component, "validate_recsys_types") and callable(
            recommend_component.validate_recsys_types
        ):
            recommend_component.validate_recsys_types()

        # KEY CHANGE: Build flow-to-component mapping
        flow_to_component_map = _build_flow_to_component_map(
            entity_action_flows,
            observe_components,
            gm_components.event_resolution.DEFAULT_RESOLUTION_COMPONENT_KEY,
            observe_slots,
            resolve_slot,
        )

        logger.info(f"Built flow-to-component mapping: {flow_to_component_map}")

        # KEY CHANGE: Create MultiFlowSMAct instead of simple SMAct
        act_component = gm_social_act.MultiFlowSMAct(
            model=model,
            entity_names=player_names,
            sm_app=sm_app,
            flow_to_component_map=flow_to_component_map,
            entity_action_flows=entity_action_flows,
            component_order=list(components.keys()),
            call_to_action_str=call_to_sm_action,
            activity_transition_rates=activity_rates,
            action_mode=getattr(cfg.sim, "action_mode", "custom"),
            enable_tool_calling=enable_tool_calling,
        )

        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components,
        )


def _class_to_kebab_case(class_name: str) -> str:
    """Convert ClassName to kebab-case (duplicate of factory utility)."""
    import re

    kebab = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name)
    return kebab.lower()


def _build_flow_to_component_map(
    entity_action_flows: dict[str, str],
    observe_components: dict[str, Any],
    resolve_component_key: str,
    observe_slot_cfg: Mapping[str, Any] | None = None,
    resolve_slot_cfg: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """Build flow-to-component mapping from config + available components.

    Args:
        entity_action_flows: Maps entity name to flow name
        observe_components: {component_key: component_instance} for observe role
        resolve_component_key: Context key used by resolve component
        observe_slot_cfg: Observe slot configuration (may include flow_map)
        resolve_slot_cfg: Resolve slot configuration (may include flow_map)

    Returns
    -------
        {
            flow_name: {
                "observe": "observe__component_key",
                "resolve": "<resolve_context_key>"
            }
        }
    """
    mapping: dict[str, dict[str, str]] = {}
    observe_slot_cfg = dict(observe_slot_cfg or {})
    resolve_slot_cfg = dict(resolve_slot_cfg or {})
    observe_flow_map = _normalize_flow_map(observe_slot_cfg.get("flow_map"))
    resolve_flow_map = _normalize_flow_map(resolve_slot_cfg.get("flow_map"))

    # Get unique flows
    unique_flows = set(entity_action_flows.values())
    unique_flows.update(observe_flow_map.keys())
    unique_flows.update(resolve_flow_map.keys())
    unique_flows.add("default")  # Always include default

    # Get the first observe component (or only one if single-instance)
    observe_components_list = list(observe_components.keys())
    default_observe_key = observe_components_list[0] if observe_components_list else ""
    available_observe_keys = set(observe_components_list)

    # Map each flow to components
    for flow in unique_flows:
        requested_observe_key = (
            observe_flow_map.get(flow) or observe_flow_map.get("default") or default_observe_key
        )
        observe_key = _resolve_observe_component_key(
            requested_key=requested_observe_key,
            available_keys=available_observe_keys,
            default_key=default_observe_key,
            flow=flow,
        )
        resolve_key = (
            resolve_flow_map.get(flow) or resolve_flow_map.get("default") or resolve_component_key
        )
        if resolve_key != resolve_component_key:
            raise ValueError(
                "Invalid resolve flow_map configuration for flow "
                f"'{flow}': requested '{resolve_key}', but this GM supports only "
                f"the single resolve context key '{resolve_component_key}'."
            )

        mapping[flow] = {
            "observe": observe_key,
            "resolve": resolve_key,
        }

    logger.debug(f"Flow-to-component mapping: {mapping}")
    return mapping


def _normalize_flow_map(raw_flow_map: Any) -> dict[str, str]:
    """Normalize flow_map config into {flow_tag: component_key}."""
    if not isinstance(raw_flow_map, Mapping):
        return {}
    normalized: dict[str, str] = {}
    for flow_name, component_key in raw_flow_map.items():
        flow = str(flow_name).strip()
        key = str(component_key).strip()
        if flow and key:
            normalized[flow] = key
    return normalized


def _expand_shared_flow_map_alias(components_cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Expand optional GM-level flow_map alias into per-slot configs.

    Supported alias shape:
        gm.components.flow_map.<flow>.<role>: <component_key_or_name>
        gm.components.flow_map.<flow>.<role>:
          instance: <component_key_or_name>   # optional routing target
          <field_name>: <field_value>         # optional FlowComponent fields
    """
    expanded = dict(components_cfg or {})
    alias_raw = expanded.pop("flow_map", None)
    if not isinstance(alias_raw, Mapping):
        return expanded

    for flow_name, role_map in alias_raw.items():
        flow = str(flow_name).strip()
        if not flow or not isinstance(role_map, Mapping):
            continue

        for role_name, role_cfg in role_map.items():
            role = str(role_name).strip()
            if not role:
                continue

            slot_cfg = dict(expanded.get(role) or {})

            if isinstance(role_cfg, str):
                flow_map = dict(slot_cfg.get("flow_map") or {})
                flow_map[flow] = role_cfg
                slot_cfg["flow_map"] = flow_map
                expanded[role] = slot_cfg
                continue

            if not isinstance(role_cfg, Mapping):
                continue

            role_cfg_dict = dict(role_cfg)
            instance_name = role_cfg_dict.pop("instance", None)
            if instance_name is None:
                instance_name = role_cfg_dict.pop("component", None)

            if instance_name is not None:
                flow_map = dict(slot_cfg.get("flow_map") or {})
                flow_map[flow] = str(instance_name).strip()
                slot_cfg["flow_map"] = flow_map

            if role_cfg_dict:
                flows_cfg = dict(slot_cfg.get("flows") or {})
                existing_fields = dict(flows_cfg.get(flow) or {})
                existing_fields.update(role_cfg_dict)
                flows_cfg[flow] = existing_fields
                slot_cfg["flows"] = flows_cfg

            expanded[role] = slot_cfg

    return expanded


def _resolve_observe_component_key(
    *,
    requested_key: str,
    available_keys: set[str],
    default_key: str,
    flow: str,
) -> str:
    """Resolve requested observe key against available observe context keys."""
    candidate = str(requested_key or "").strip()
    if not candidate:
        return default_key

    candidates = [candidate]
    if not candidate.startswith("observe__"):
        candidates.append(f"observe__{candidate}")
        snake = re.sub(r"(?<!^)(?=[A-Z])", "_", candidate).lower()
        candidates.append(f"observe__{snake}")

    for item in candidates:
        if item in available_keys:
            return item

    raise ValueError(
        "Invalid observe flow_map configuration for flow "
        f"'{flow}': requested '{candidate}', available keys are {sorted(available_keys)}."
    )
