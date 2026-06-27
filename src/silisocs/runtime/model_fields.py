"""Single source of truth for the per-instance LLM config fields."""

from __future__ import annotations

# The fields that make up a fully-resolved (effective) LLM config. The global
# ``sim.llm`` block and every per-class/per-instance ``model`` override accept
# exactly these keys, and two instances share a built model iff these fields are
# byte-identical. Defined once so the validators (persona-pipeline ``model`` blocks
# and ``sim.llm``), the per-instance resolver, and the dedup signature cannot drift
# apart — adding a field here extends all three at once (and, critically, keeps the
# dedup signature aware of it so two configs differing only in the new field are not
# silently collapsed onto one shared model).
MODEL_FIELDS: tuple[str, ...] = (
    "name",
    "temperature",
    "provider",
    "api_base",
    "api_key",
    "disabled",
    "extra_kwargs",
)
