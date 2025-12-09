import random
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal

from concordia.components.agent import action_spec_ignored
from concordia.language_model import language_model

DEFAULT_PRE_ACT_KEY = "[Suggested Action]"

# Default probabilities for different Mastodon operations
DEFAULT_ACTION_PROBABILITIES = {
    # High frequency actions
    "like_toot": 0.35,  # Most common action
    "boost_toot": 0.15,  # Common but less than likes
    "toot": 0.20,  # Regular posting
    "reply": 0.15,
    # Medium frequency actions
    "follow": 0.10,  # Following new accounts
    "unfollow": 0.025,  # Unfollowing accounts
    "print_timeline": 0.0,  # Reading timeline
    # Low frequency actions
    "block_user": 0.0,  # Blocking problematic users
    "unblock_user": 0.0,  # Unblocking users
    "delete_posts": 0.0,  # Deleting own posts
    "update_bio": 0.0,  # Updating profile
    "print_notifications": 0.025,  # Checking notifications
}


class MastodonActionSuggester(action_spec_ignored.ActionSpecIgnored):
    """Suggests likely Mastodon operations for an agent to perform."""

    def __init__(
        self,
        model: language_model.LanguageModel,
        action_probabilities: dict[str, float] | None = DEFAULT_ACTION_PROBABILITIES,
        pre_act_key: str = DEFAULT_PRE_ACT_KEY,
        logging_channel: Callable[[dict[str, object]], None] | None = None,
    ):
        """Initialize the action suggester component.

        Args:
            model: The language model to use.
            action_probabilities: Optional mapping from action names to
                probabilities.
            pre_act_key: Key to identify component output in pre_act.
            logging_channel: Channel for logging component behavior.
        """
        super().__init__(pre_act_key)
        self._model = model
        self._logging_channel = logging_channel or (lambda _: None)

        # Use provided probabilities or defaults
        self._action_probs = (action_probabilities or DEFAULT_ACTION_PROBABILITIES).copy()

        # Validate probabilities and actions
        self._validate_probabilities(self._action_probs)

        # Store last suggestion for consistency within same context
        self._last_suggestion: str | None = None

    @staticmethod
    def _validate_probabilities(probs: dict[str, float]) -> None:
        """Validate the probability configuration."""
        # Check for valid actions
        valid_actions = set(DEFAULT_ACTION_PROBABILITIES.keys())
        invalid_actions = set(probs.keys()) - valid_actions
        if invalid_actions:
            raise ValueError(
                f"Invalid actions provided: {invalid_actions}. Valid actions are: {valid_actions}"
            )

        # Check for negative probabilities
        negative_probs = {k: v for k, v in probs.items() if v < 0}
        if negative_probs:
            raise ValueError(f"Negative probabilities not allowed: {negative_probs}")

        # Sum probabilities using Decimal for precise comparison
        total = Decimal("0")
        for prob in probs.values():
            total += Decimal(str(prob)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP)

        if total != Decimal("1"):
            raise ValueError(
                f"Action probabilities must sum to exactly 1.0 (got {float(total)}). "
                "Please adjust probabilities to ensure they sum to 100%."
            )

    def _select_action(self) -> str:
        """Randomly select an action based on configured probabilities."""
        rand_val = random.random()
        cumulative_prob = 0.0

        for action, prob in self._action_probs.items():
            cumulative_prob += prob
            if rand_val <= cumulative_prob:
                return action

        # Fallback to most common action if we somehow don't select one
        return "like_toot"

    def _make_pre_act_value(self) -> str:
        """Generate a suggestion for the next Mastodon action."""
        # If we already have a suggestion for this context, return it
        if self._last_suggestion is not None:
            return self._last_suggestion

        agent_name = self.get_entity().name
        selected_action = self._select_action()

        # Create natural language suggestions for different action types
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

        # Store suggestion for consistency
        self._last_suggestion = result

        # Log the suggestion
        self._logging_channel(
            {
                "Key": self.get_pre_act_key(),
                "Value": result,
                "Selected action": selected_action,
                "Action probabilities": self._action_probs,
            }
        )

        print(f"\n[Action Suggester] Returning: {result}")
        return result

    def get_state(self) -> str:
        """Get the component's state as a string for the agent's context."""
        result = self._make_pre_act_value()
        print(f"\n[Action Suggester] Adding to {self.get_entity().name}'s context: {result}")
        return result

    def set_state(self, state: dict[str, dict[str, float]]) -> None:
        """Set the component's state."""
        if "action_probabilities" in state:
            self._validate_probabilities(state["action_probabilities"])
            self._action_probs = state["action_probabilities"]
            self._last_suggestion = None  # Reset suggestion when probabilities change
