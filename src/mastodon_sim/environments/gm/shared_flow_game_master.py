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
from collections.abc import Mapping, Sequence
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
    initialize_component_flow_fields,
)
from mastodon_sim.runtime.config import ConfigStore

logger = logging.getLogger(__name__)


def _env_cfg(cfg: Any) -> Any:
    return getattr(cfg, "env", getattr(cfg, "environment", object()))


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

    Component routing is built from entity_flow_tags and the multi-component
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
        env_gm_cfg = getattr(_env_cfg(cfg), "gm", None)
        if env_gm_cfg is not None and getattr(env_gm_cfg, "components", None) is not None:
            gm_components_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(env_gm_cfg.components, resolve=True),
            )
        elif hasattr(cfg.env, "gm") and getattr(cfg.env.gm, "components", None) is not None:
            gm_components_cfg = cast(
                dict[str, Any],
                OmegaConf.to_container(cfg.env.gm.components, resolve=True),
            )

        user_data = self.params["sm_user_data"]
        activity_rates = dict(user_data.get("activity_transition_rates", {}))
        entity_flow_tags = dict(user_data.get("entity_flow_tags", {}))
        gm_orchestration = dict(user_data.get("gm_orchestration", {}) or {})
        gm_prompt_cfg = dict(gm_orchestration.get("prompt", {}) or {})

        next_actor = build_next_acting_component(
            gm_components_cfg.get("next_acting"),
            player_names=player_names,
            activity_transition_rates=activity_rates,
        )

        resolve_slot = dict(gm_components_cfg.get("resolve", {}))
        if not resolve_slot:
            resolve_slot = {
                "built_in": action_mode_to_resolve_map.get(
                    getattr(cfg.simulator, "action_mode", "custom"), "parsed_action"
                ),
            }
        tool_calling_mode = (
            str(OmegaConf.select(cfg, "simulator.tool_calling.mode", default="none") or "none")
            .strip()
            .lower()
        )
        enable_tool_calling = tool_calling_mode in {"single", "multi"}

        platform_type = getattr(_env_cfg(cfg), "platform_type", "twitter_like")

        timeline_mode = str(
            getattr(_env_cfg(cfg), "timeline_mode", None) or "follower_chronological"
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
        if hasattr(_env_cfg(cfg), "timeline_config"):
            timeline_config = (
                cast(
                    dict[str, Any],
                    OmegaConf.to_container(_env_cfg(cfg).timeline_config, resolve=True),
                )
                if isinstance(_env_cfg(cfg).timeline_config, dict)
                else {}
            )
        elif hasattr(cfg.env, "timeline_config"):
            timeline_config = (
                cast(
                    dict[str, Any],
                    OmegaConf.to_container(cfg.env.timeline_config, resolve=True),
                )
                if isinstance(cfg.env.timeline_config, dict)
                else {}
            )

        # Build the social media app (same as base)
        from mastodon_sim.environments.backends.factory import create_social_media_app

        action_logger = None  # Would need to build this from config
        db_path = os.path.join(cfg.output_rootname, "multiflow.db")

        sm_app = create_social_media_app(
            platform_type=platform_type,
            action_logger=action_logger,
            perform_operations=getattr(_env_cfg(cfg), "use_server", False),
            app_description=self.params.get("app_description", ""),
            db_path=db_path,
        )

        enabled_actions_cfg = getattr(_env_cfg(cfg), "enabled_actions", None)
        if enabled_actions_cfg is not None:
            if isinstance(enabled_actions_cfg, Sequence) and not isinstance(
                enabled_actions_cfg, (str, bytes)
            ):
                enabled_actions = [str(action).strip() for action in enabled_actions_cfg]
            else:
                enabled_actions = [str(enabled_actions_cfg).strip()]

            action_loop_built_in = ""
            if hasattr(cfg.simulator, "engine") and getattr(
                cfg.simulator.engine, "action_loop", None
            ):
                action_loop_built_in = str(
                    getattr(cfg.simulator.engine.action_loop, "built_in", "")
                ).strip()
            enabled_actions_upper = {name.upper() for name in enabled_actions if name}
            if action_loop_built_in == "open_ended" and "FINISHED" not in enabled_actions_upper:
                enabled_actions.append("FINISHED")

            sm_app.set_enabled_actions(enabled_actions)

        action_mode = (
            str(getattr(cfg.simulator, "action_mode", "custom") or "custom").strip().lower()
        )
        if action_mode == "generic":
            call_to_sm_action = self.build_generic_prompt(
                cfg=cfg,
                sm_app=sm_app,
                tool_calling_mode=tool_calling_mode,
                gm_prompt_cfg=gm_prompt_cfg,
            )

        catalog = sm_app.action_catalog()
        allowed_action_types = sorted(
            {
                str(item.get("selectable_name", "")).strip().upper()
                for item in catalog
                if str(item.get("selectable_name", "")).strip()
            }
        )
        for entity in self.entities:
            setter = getattr(entity, "set_allowed_action_types", None)
            existing = getattr(entity, "_allowed_action_types", None)
            if callable(setter) and not existing:
                setter(allowed_action_types)

        action_output_mode = str(resolve_slot.get("built_in", "parsed_action") or "parsed_action")
        for entity in self.entities:
            mode_setter = getattr(entity, "set_action_output_mode", None)
            if callable(mode_setter):
                mode_setter(action_output_mode)

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
                entity_flow_tags=entity_flow_tags,
                episode_observation_flow=episode_observation_flow,
                timeline_mode=timeline_mode,
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
                entity_flow_tags=entity_flow_tags,
                episode_observation_flow=episode_observation_flow,
                timeline_mode=timeline_mode,
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
                    initialize_component_flow_fields(component, slot_cfg)

        if hasattr(recommend_component, "validate_recsys_types") and callable(
            recommend_component.validate_recsys_types
        ):
            recommend_component.validate_recsys_types()

        # KEY CHANGE: Build flow-to-component mapping
        flow_to_component_map = _build_flow_to_component_map(
            entity_flow_tags,
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
            entity_flow_tags=entity_flow_tags,
            component_order=list(components.keys()),
            call_to_action_str=call_to_sm_action,
            activity_transition_rates=activity_rates,
            action_mode=getattr(cfg.simulator, "action_mode", "custom"),
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
    entity_flow_tags: dict[str, str],
    observe_components: dict[str, Any],
    resolve_component_key: str,
    observe_slot_cfg: Mapping[str, Any] | None = None,
    resolve_slot_cfg: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """Build flow-to-component mapping from config + available components.

    Args:
        entity_flow_tags: Maps entity name to flow tag
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
    unique_flows = set(entity_flow_tags.values())
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
