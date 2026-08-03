"""Goods tables: loading, flattening, and the wearable/edible lookups.

The ``category -> tier -> item -> {price, inventory, advert}`` shape is carried
over verbatim from ``examples/signaling/configs/goods.py`` (converted to JSON by
``tools/convert_concordia_data.py``). The helpers here are the ones the
marketplace backend and the day-boundary handler need: the flat ``Good`` list
the order books are keyed by, and the "what can this agent eat / wear" lookups
that ``dial.get_eating_statement`` / ``get_wearing_statement`` perform upstream.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Categories whose items count as displayable — the signaling channel. Upstream
#: ``dial.get_wearing_statement`` draws the worn item from exactly these.
WEARABLE_CATEGORIES: tuple[str, ...] = ("Clothing", "Accessories")
#: The category whose items are consumed once per day.
EDIBLE_CATEGORY = "Food"

GoodsTable = dict[str, dict[str, dict[str, dict[str, Any]]]]


@dataclass(frozen=True)
class Good:
    """One purchasable item (upstream ``marketplace.Good``)."""

    category: str
    quality: str
    id: str
    price: float | None = None
    inventory: int | None = None
    advert: str | None = None


def load_goods_table(path: str | Path) -> GoodsTable:
    """Load one converted goods JSON file."""
    with open(path, encoding="utf-8") as handle:
        table = json.load(handle)
    if not isinstance(table, dict):
        raise TypeError(f"{path}: expected a category -> tier -> item mapping.")
    return table


def flatten_goods(table: Mapping[str, Any]) -> list[Good]:
    """Flatten a nested goods table into ``Good`` objects (upstream ordering).

    Ported from ``simulation.get_all_goods_from_spec``: iteration follows the
    table's category/tier/item insertion order, which is what fixes the
    ``Seller_<i>`` numbering.
    """
    goods: list[Good] = []
    for category, tiers in table.items():
        for quality, items in tiers.items():
            for item_name, details in items.items():
                goods.append(
                    Good(
                        category=str(category),
                        quality=str(quality),
                        id=str(item_name),
                        price=details.get("price"),
                        inventory=details.get("inventory"),
                        advert=details.get("advert"),
                    )
                )
    return goods


def edible_ids(table: Mapping[str, Any]) -> list[str]:
    """Item ids that can be eaten (every tier of the Food category)."""
    return [item for tier in table.get(EDIBLE_CATEGORY, {}).values() for item in tier]


def wearable_ids(table: Mapping[str, Any]) -> list[str]:
    """Item ids that can be worn or displayed (Clothing + Accessories)."""
    ids: list[str] = []
    for category in WEARABLE_CATEGORIES:
        for tier in table.get(category, {}).values():
            ids.extend(tier)
    return ids


def find_tier(table: Mapping[str, Any], item_id: str) -> tuple[str, str] | None:
    """Return ``(category, tier)`` for an item id, or ``None`` if unknown.

    Upstream scans the table in the same order and stops at the first match,
    which matters only for the ``both`` table where an id could in principle
    repeat across categories.
    """
    for category, tiers in table.items():
        for tier, items in tiers.items():
            if item_id in items:
                return str(category), str(tier)
    return None
