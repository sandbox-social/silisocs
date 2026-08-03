"""The journal game master's resolve component: free prose into a logged action.

A reflection is not a structured move. The journal GM runs with
``action_mode: custom`` and ``tool_calling: none`` (its per-GM overrides), so
neither shipped resolver fits: ``tool_calling`` requires typed
``OutputType.TOOL_CALLS`` output and raises on anything else, and the social
``parsed_action`` resolver looks for an ACTION TYPE / TARGET ID / CONTENT block
and would drop the whole answer as a parse failure. The agent's entire response
*is* the action here, so this resolver is the ~10 lines that say so.

It still goes through the standard invocation seam
(``backend.invoke_action_detailed``) rather than calling the backend method
directly, so a reflection is validated, logged as a committed
``action_events.jsonl`` row, and mirrored into the committed-events buffer
exactly like any other agent turn — which is what lets the reflection metrics be
derived from the standard artifacts with no bespoke logging.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from silisocs.environments.gm.components.resolve import _BaseResolveComponent
from silisocs.runtime.types import ActionOutput

_LOGGER = logging.getLogger(__name__)

#: The journal backend's single action (``JournalApp.record_reflection``).
REFLECTION_ACTION = "RECORD_REFLECTION"


@dataclass(eq=False)
class JournalResolveComponent(_BaseResolveComponent):
    """Record an agent's free-text reflection as a committed backend action."""

    def resolve(self, *, active_agent: str, action: ActionOutput | str) -> str:
        """Dispatch one reflection to the journal backend."""
        text = (action.text if isinstance(action, ActionOutput) else str(action or "")).strip()
        if not text:
            _LOGGER.warning(
                "Agent '%s' returned an empty reflection; nothing recorded.", active_agent
            )
            return "No reflection recorded: the response was empty."
        if not self._action_allowed(active_agent, REFLECTION_ACTION):
            return self._reject_filtered_action(active_agent, REFLECTION_ACTION)

        committed, message = self.backend.invoke_action_detailed(
            REFLECTION_ACTION,
            {"agent_name": active_agent, "text": text},
        )
        if not committed:
            # A rejected reflection is a real (recoverable) loss of data for the
            # analysis; surface it rather than letting the message pass as success.
            _LOGGER.warning(
                "Reflection from agent '%s' was not committed: %s", active_agent, message
            )
        return message
