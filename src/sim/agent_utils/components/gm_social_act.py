from collections.abc import Sequence
from typing import Any

from concordia.components import game_master as gm_components
from concordia.components.game_master import make_observation as make_observation_component
from concordia.language_model import language_model
from concordia.typing import entity as entity_lib
from concordia.typing import entity_component
from typing_extensions import override

from mastodon_sim import mastodon_ops
from mastodon_sim.concordia.components import apps

DEFAULT_SESSION_TERMINATE_STR = "The Social-Media session has been completed."

import re
from html import unescape


def find_and_parse_action_data(data_string):
    """
    Finds and parses a block of action data from a larger string.

    This version is more flexible as it doesn't require the
    action block to be at the start of the string.

    Args:
        data_string: The input text to parse.

    Returns
    -------
        A dictionary with the parsed data, or None if parsing fails.
    """
    # The only change is removing the `^` from the start of the pattern.
    pattern = re.compile(
        r"\s*ACTION TYPE:\s*(?P<action_type>.*?)\s*"  # <-- No `^` here
        r"TARGET ID:\s*(?P<target_id>\d+)\s*"
        r"CONTENT:\s*(?P<content>.*?)\s*"
        r"REASONING:\s*(?P<reasoning>.*)$",
        re.DOTALL | re.IGNORECASE,
    )

    # Use re.search() to find the pattern anywhere in the string
    match = pattern.search(data_string)

    # --- Condition for parsing failure ---
    if not match:
        print("--- PARSING FAILED --- ")
        return None
    # --- End of failure condition ---

    # If parsing succeeds, extract data from the named groups
    parsed_data = {
        "action_type": match.group("action_type").strip(),
        "target_id": match.group("target_id").strip(),
        "content": match.group("content").strip(),
        "reasoning": match.group("reasoning").strip(),
    }

    return parsed_data


class SMAct(gm_components.switch_act.SwitchAct):
    """
    A custom game master ActComponent that inherits from SwitchAct
    but provides custom logic for observation, resolution, and termination.
    """

    @override
    def __init__(  # type: ignore[misc]
        self,
        model: language_model.LanguageModel,
        entity_names: Sequence[str],
        sm_app: apps.MastodonSocialNetworkApp,
        component_order: Sequence[str] | None = None,
        call_to_action_str: str = "",
        active_rates: dict[str, Any] = {},
    ):
        super().__init__(
            model=model,
            entity_names=entity_names,
            component_order=component_order,
        )
        self.call_to_action_str = call_to_action_str
        self.session = dict.fromkeys(entity_names, 0)
        self.sm_app = sm_app
        self.active_rates = active_rates

    @override
    def _make_observation(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        result = ""
        if make_observation_component.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY in contexts:
            result = str(
                contexts[make_observation_component.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY]
            )
        if result == "":
            active_entity_name: str | None = next(
                (s for s in self._entity_names if s in action_spec.call_to_action), None
            )
            num_timeline_posts_in_observation = 10
            # timeline = self.sm_app.get_own_timeline(
            #     active_entity_name, num_timeline_posts_in_observation
            # )

            if (active_entity_name is not None) and (self.sm_app.perform_operations):
                active_entity_username = self.sm_app.public_get_username(
                    active_entity_name.split(" ")[0]
                )
                timeline = mastodon_ops.get_own_timeline(
                    active_entity_username, limit=num_timeline_posts_in_observation
                )
            else:
                timeline = []

            def _clean_html(html_string):
                clean_text = re.sub("<[^<]+?>", "", unescape(html_string))
                return re.sub(r"\s+", " ", clean_text).strip()

            for post in timeline:
                # TODO: Rework/simplify media processing
                media_desc = ""
                # if post["media_attachments"]:
                #     media_contents = []
                #     for attachment in post["media_attachments"]:
                #         media_contents.append(attachment["url"])
                #     toot_headline = _clean_html(post["content"])
                #     call_to_speech = DEFAULT_CALL_TO_SPEECH.format(
                #         name=self._player.name,
                #     )
                #     call_to_action = (
                #         f"{media_contents!s} Context: Succinctly describe this image in the form of an impression that it made on {self._player.name.split()[0]} when they viewed it alongside the following text of the toot they just read on the Mastodon app: "
                #         + "'"
                #         + toot_headline
                #         + "'"
                #     )
                #     media_desc = self._player.act(
                #         action_spec=agent.ActionSpec(
                #             call_to_action=call_to_action,
                #             output_type=OutputType.FREE,
                #             tag="media",
                #         )
                #     )
                #     media_desc = (
                #         media_desc.strip(self._player.name.split()[0])
                #         .strip()
                #         .strip(self._player.name.split()[1])
                #         .strip()
                #         .strip("--")
                #         .strip()
                #         .strip('"')
                #     )
                #     media_desc = "Impression of attached image: \n" + media_desc
                #     print(media_desc)
                result += f"User: {post['account']['display_name']} (@{post['account']['username']})\nContent: {_clean_html(post['content'])}\n{media_desc}\nToot ID: {post['id']}\n\n"
        self._log(result, "", action_spec)
        return result

    @override
    def _resolve(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        result = ""
        active_entity, action = action_spec.call_to_action.split(":", 1)
        action_data = find_and_parse_action_data(action)
        if action_data is not None:
            current_user = active_entity  # .split()[0]
            if action_data["action_type"].lower().strip() == "post":
                result = self.sm_app.post_toot(current_user, action_data["content"])
            elif action_data["action_type"].lower().strip() == "reply":
                result = self.sm_app.reply_to_toot(
                    current_user,
                    status=action_data["content"],
                    in_reply_to_id=action_data["target_id"],
                )
            elif action_data["action_type"].lower().strip() == "boost":
                result = self.sm_app.like_toot(
                    current_user,
                    action_data["target_id"],
                )
            elif action_data["action_type"].lower().strip() == "reply":
                result = self.sm_app.boost_toot(
                    current_user,
                    action_data["target_id"],
                )

        make_observation = self.get_entity().get_component(
            make_observation_component.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY,
            type_=make_observation_component.MakeObservation,
        )
        make_observation.add_to_queue(active_entity, result)
        self._log(result, "", action_spec)
        return result

    @override
    def _terminate(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        result = "No"
        self._log(result, "", action_spec)
        return result

    @override
    def _next_game_master(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        return self.get_entity()._agent_name
        # return self.get_entity().params.get("name", "")

    @override
    def _next_entity_action_spec(  # type: ignore[misc]
        self, contexts: entity_component.ComponentContextMapping, action_spec: entity_lib.ActionSpec
    ) -> str:
        if self.call_to_action_str:
            return f"prompt: {self.call_to_action_str} ;;type: free"
        return "prompt: Conduct a social-media action. You can choose from like, reply, boost, and post. Format it correctly as: ACTION_TYPE: (action)\n TARGET_ID: (target_id)\n CONTENT: (content)\n REASONING: (reasoning)\n ;;type: free"
