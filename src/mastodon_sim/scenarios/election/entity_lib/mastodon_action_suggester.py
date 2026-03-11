"""Action suggestion component for election scenario entities."""

from __future__ import annotations

import random
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal

from concordia.components.agent import action_spec_ignored
from concordia.language_model import language_model

DEFAULT_PRE_ACT_KEY = "[Suggested Action]"

DEFAULT_ACTION_PROBABILITIES = {
    "like_toot": 0.35,
    "boost_toot": 0.15,
    "toot": 0.20,
    "reply": 0.15,
    "follow": 0.10,
    "unfollow": 0.025,
    "print_timeline": 0.0,
    "block_user": 0.0,
    "unblock_user": 0.0,
    "delete_posts": 0.0,
    "update_bio": 0.0,
    "print_notifications": 0.025,
}


class MastodonActionSuggester(action_spec_ignored.ActionSpecIgnored):
    """Suggest likely Mastodon operations for an agent."""

    def __init__(
        self,
        model: language_model.LanguageModel,
        action_probabilities: dict[str, float] | None = DEFAULT_ACTION_PROBABILITIES,
        pre_act_key: str = DEFAULT_PRE_ACT_KEY,
        logging_channel: Callable[[dict[str, object]], None] | None = None,
    ):
        super().__init__(pre_act_key)
        self._model = model
        self._logging_channel = logging_channel or (lambda _: None)
        self._action_probs = (action_probabilities or DEFAULT_ACTION_PROBABILITIES).copy()
        self._validate_probabilities(self._action_probs)
        self._last_suggestion: str | None = None

    @staticmethod
    def _validate_probabilities(probs: dict[str, float]) -> None:
        valid_actions = set(DEFAULT_ACTION_PROBABILITIES.keys())
        invalid_actions = set(probs.keys()) - valid_actions
        if invalid_actions:
            raise ValueError(
                f"Invalid actions provided: {invalid_actions}. Valid actions are: {valid_actions}"
            )

        negative_probs = {key: value for key, value in probs.items() if value < 0}
        if negative_probs:
            raise ValueError(f"Negative probabilities not allowed: {negative_probs}")

        total = Decimal(0)
        for prob in probs.values():
            total += Decimal(str(prob)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)

        if total != Decimal(1):
            raise ValueError(
                f"Action probabilities must sum to exactly 1.0 (got {float(total)}). "
                "Please adjust probabilities to ensure they sum to 100%."
            )

    def _select_action(self) -> str:
        rand_val = random.random()
        cumulative_prob = 0.0

        for action, prob in self._action_probs.items():
            cumulative_prob += prob
            if rand_val <= cumulative_prob:
                return action

        return "like_toot"

    def _make_pre_act_value(self) -> str:
        if self._last_suggestion is not None:
            return self._last_suggestion

        agent_name = self.get_entity().name
        selected_action = self._select_action()
        action_descriptions = {
            "like_toot": f"{agent_name} feels inclined to like someone's post",
            "boost_toot": f"{agent_name} considers boosting a post they appreciate",
            "toot": f"{agent_name} has something they might want to post about",
            "reply": f"{agent_name} considers replying to a post",
            "follow": f"{agent_name} thinks about following a new account",
            "unfollow": f"{agent_name} considers unfollowing an account",
            "print_timeline": f"{agent_name} feels like checking their timeline",
            "block_user": f"{agent_name} contemplates blocking a problematic user",
            "unblock_user": f"{agent_name} considers unblocking someone",
            "delete_posts": f"{agent_name} considers deleting some old posts",
            "update_bio": f"{agent_name} feels like updating their profile",
            "print_notifications": f"{agent_name} wants to check their notifications",
        }
        result = action_descriptions.get(
            selected_action, f"{agent_name} considers interacting with Mastodon"
        )

        self._last_suggestion = result
        self._logging_channel(
            {
                "Key": self.get_pre_act_key(),
                "Value": result,
                "Selected action": selected_action,
                "Action probabilities": self._action_probs,
            }
        )
        return result

    def get_state(self) -> str:
        """Return current suggestion state for inclusion in agent context."""
        return self._make_pre_act_value()

    def set_state(self, state: dict[str, dict[str, float]]) -> None:
        """Update component state from persisted action probabilities."""
        if "action_probabilities" in state:
            self._validate_probabilities(state["action_probabilities"])
            self._action_probs = state["action_probabilities"]
            self._last_suggestion = None
