"""Compatibility re-exports for the Studio composer.

The composer used to be one module; it is now four, split along the axis that
matters for startup cost:

- :mod:`silisocs.studio.form_schema` — the framework (fields, schemas, choice and
  preview registries). Imports no engine code, so routers import it directly.
- :mod:`silisocs.studio.form_providers` — the scenario schema and the providers
  that read runtime catalogs.
- :mod:`silisocs.studio.compose` — reading field values out of, and writing them
  back into, a scenario's YAML documents.
- :mod:`silisocs.studio.preflight` — validation and the pre-launch scale estimate
  (the piece that imports the engine).

``from silisocs.studio.forms import X`` keeps working for every name the single
module exposed, and importing this module still loads all four — which is exactly
what Studio's warm-up thread wants when it pre-pays the composer's import cost.
"""

from __future__ import annotations

from silisocs.studio.compose import compose_files, field_values
from silisocs.studio.form_providers import materialize_form_schema
from silisocs.studio.form_schema import (
    ChoiceContext,
    ChoiceProvider,
    Field,
    FormSchema,
    PreviewContext,
    PreviewProvider,
    choice_items,
    form_schema,
    list_choice_providers,
    list_form_schemas,
    list_preview_providers,
    load_widget,
    register_choice_provider,
    register_form_schema,
    register_preview_provider,
    run_choice_provider,
    run_preview_provider,
)
from silisocs.studio.preflight import preflight_payload
from silisocs.studio.scenario_repository import ScenarioRepository

__all__ = [
    "ChoiceContext",
    "ChoiceProvider",
    "Field",
    "FormSchema",
    "PreviewContext",
    "PreviewProvider",
    "ScenarioRepository",
    "choice_items",
    "compose_files",
    "field_values",
    "form_schema",
    "list_choice_providers",
    "list_form_schemas",
    "list_preview_providers",
    "load_widget",
    "materialize_form_schema",
    "preflight_payload",
    "register_choice_provider",
    "register_form_schema",
    "register_preview_provider",
    "run_choice_provider",
    "run_preview_provider",
]
