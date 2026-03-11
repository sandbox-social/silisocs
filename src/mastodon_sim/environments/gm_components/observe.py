import copy

from concordia.components import game_master as gm_components
from concordia.typing import entity as entity_lib
from typing_extensions import override

DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY = "__make_observation__"
DEFAULT_MAKE_OBSERVATION_PRE_ACT_LABEL = "\nPrompt"
DEFAULT_CALL_TO_MAKE_OBSERVATION = "{name}"

GET_ACTIVE_ENTITY_QUERY = (
    "Who is being asked about? Respond using only their name and no other "
    "words. Use their full name if known."
)


class SimpleMakeObservation(gm_components.make_observation.MakeObservation):
    """A component that generates observations to send to players."""

    @override
    def pre_act(  # type: ignore[misc]
        self,
        action_spec: entity_lib.ActionSpec,
    ) -> str:
        result = ""
        prompt_to_log = ""
        log_entry = {}
        if action_spec.output_type == entity_lib.OutputType.MAKE_OBSERVATION:
            active_entity_name = self._get_active_entity_name_from_call_to_action(
                action_spec.call_to_action
            )

            log_entry["Active Entity"] = active_entity_name
            with self._lock:
                log_entry["queue"] = copy.deepcopy(self._queue)

                if self._queue.get(active_entity_name):
                    log_entry["queue_active_entity"] = copy.deepcopy(
                        self._queue[active_entity_name]
                    )
                    result = ""
                    for event in self._queue[active_entity_name]:
                        result += event + "\n\n\n"

                    self._queue[active_entity_name] = []

        log_entry["Key"] = self._pre_act_label
        log_entry["Summary"] = result
        log_entry["Value"] = result
        self._logging_channel(log_entry)
        return result
