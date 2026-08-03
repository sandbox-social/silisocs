#!/usr/bin/env python
"""One-shot conversion of the pinned Concordia signaling data into ``input/``.

The upstream example carries its data as Python literals in
``examples/signaling/configs/{goods,personas}.py``. This script lifts those
literals out with :mod:`ast` (no Concordia import, so it runs anywhere) and
writes the JSON the SiliSocS port loads:

* ``input/goods/{original,synthetic,subculture}.json`` — the nested
  ``category -> tier -> item -> {price, inventory, advert}`` tables, verbatim.
* ``input/personas_la.json`` — 50 records ``{name, sex, context, memories}``
  where ``context`` is the ``[Persona]`` blob's description and ``memories``
  are the verbatim formative-memory strings.
* ``input/sellers_<item_list>.json`` — one producer per good, with the verbatim
  seller goal text from ``simulation.get_marketplace_config``.

This script is kept in the repository because it *is* the documentation of the
mapping between the two arms. Re-run it only to re-pin against a new upstream
commit (see ``docs/PINNED_COMMITS.md``).

Usage::

    uv run python replications/signaling/tools/convert_concordia_data.py \
        --concordia-root /scratch/sneheel/external/concordia-signaling-7779a4c
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONCORDIA_ROOT = "/scratch/sneheel/external/concordia-signaling-7779a4c"

# Verbatim from examples/signaling/simulation.py (the seller goal template).
SELLER_GOAL_TEMPLATE = (
    "You are a seller of {good_id}. Your cost to produce each unit is "
    "${price:.2f}. Your goal is to sell your stock for a profit. You must sell "
    "for more than your cost to be profitable."
)


def _module_literals(path: Path, names: set[str]) -> dict[str, Any]:
    """Return ``{name: literal}`` for top-level assignments in a Python file.

    Uses ``ast.literal_eval`` so the upstream module's imports are never
    executed — the data files are pure literals.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(node.value)
    missing = sorted(names - set(found))
    if missing:
        raise SystemExit(f"{path}: could not find literal(s) {missing}")
    return found


def _module_namespace(path: Path, names: set[str]) -> dict[str, Any]:
    """Return ``{name: value}`` by executing an import-free data module.

    ``goods.py`` is pure data but not pure *literals*: ``SUBCULTURE_GOODS``
    composes the base tables with ``**`` unpacking and subscripts, which
    ``ast.literal_eval`` cannot evaluate. The module has no imports (asserted
    below), so executing it in an isolated namespace reproduces the upstream
    tables exactly, with no Concordia dependency.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    if imports:
        raise SystemExit(
            f"{path}: expected an import-free data module, found {len(imports)} imports"
        )
    namespace: dict[str, Any] = {}
    exec(compile(tree, str(path), "exec"), namespace)
    missing = sorted(names - set(namespace))
    if missing:
        raise SystemExit(f"{path}: could not find name(s) {missing}")
    return {name: namespace[name] for name in names}


def _merge_goods(base: dict, extra: dict) -> dict:
    """Deep-merge two goods tables (the upstream ``item_list='both'`` rule)."""
    merged = json.loads(json.dumps(base))
    for category, tiers in extra.items():
        merged.setdefault(category, {})
        for tier, items in tiers.items():
            merged[category].setdefault(tier, {})
            merged[category][tier].update(items)
    return merged


def _persona_context(persona_blob: str) -> str:
    """Extract the human-readable persona description from a ``[Persona]`` line.

    The upstream blob is ``[Persona] {json}``; the JSON carries ``description``
    plus an ``axis_position`` trait map and ``initial_context``. We render them
    into the free-text ``context`` the persona pipeline feeds to the agent,
    preserving every fact and inventing none.
    """
    payload = persona_blob.split("[Persona]", 1)[-1].strip()
    data = json.loads(payload)
    parts = [str(data.get("description", "")).strip()]
    axes = data.get("axis_position") or {}
    if axes:
        traits = ", ".join(f"{key}: {value}" for key, value in axes.items())
        parts.append(f"Traits — {traits}.")
    initial = str(data.get("initial_context", "")).strip()
    if initial:
        parts.append(initial)
    return "\n".join(part for part in parts if part)


def convert(concordia_root: Path, out_root: Path) -> None:
    """Write every ``input/`` artifact derived from the pinned Concordia source."""
    configs = concordia_root / "examples" / "signaling" / "configs"
    goods_literals = _module_namespace(
        configs / "goods.py", {"ORIGINAL_GOODS", "SYNTHETIC_GOODS", "SUBCULTURE_GOODS"}
    )
    persona_literals = _module_literals(configs / "personas.py", {"PLAYER_SEX", "PERSONA_MEMORIES"})

    goods_dir = out_root / "goods"
    goods_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        "original": goods_literals["ORIGINAL_GOODS"],
        "synthetic": goods_literals["SYNTHETIC_GOODS"],
        "subculture": goods_literals["SUBCULTURE_GOODS"],
    }
    # ``both`` is upstream's ORIGINAL deep-merged with SYNTHETIC (simulation.py).
    tables["both"] = _merge_goods(tables["original"], tables["synthetic"])
    for name, table in tables.items():
        path = goods_dir / f"{name}.json"
        path.write_text(json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        total = sum(len(items) for tiers in table.values() for items in tiers.values())
        print(f"wrote {path} ({total} goods)")

    sex_map = persona_literals["PLAYER_SEX"]
    memories_map = persona_literals["PERSONA_MEMORIES"]
    records = []
    for name, memories in memories_map.items():
        blob = next((m for m in memories if "[Persona]" in m), "")
        if not blob:
            raise SystemExit(f"persona {name!r} has no [Persona] blob")
        formative = [m for m in memories if "[Persona]" not in m]
        records.append(
            {
                "name": name,
                "sex": sex_map.get(name, ""),
                "context": _persona_context(blob),
                "memories": list(formative),
            }
        )
    unsexed = [r["name"] for r in records if not r["sex"]]
    if unsexed:
        raise SystemExit(f"personas missing a PLAYER_SEX entry: {unsexed}")
    personas_path = out_root / "personas_la.json"
    personas_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"wrote {personas_path} ({len(records)} personas)")

    for name, table in tables.items():
        sellers = []
        index = 0
        for category, tiers in table.items():
            for tier, items in tiers.items():
                for good_id, details in items.items():
                    index += 1
                    price = float(details["price"])
                    sellers.append(
                        {
                            "name": f"Seller_{index}",
                            "good": good_id,
                            "category": category,
                            "quality": tier,
                            "cost": price,
                            "stock": int(details["inventory"]),
                            "goal": SELLER_GOAL_TEMPLATE.format(good_id=good_id, price=price),
                        }
                    )
        path = out_root / f"sellers_{name}.json"
        path.write_text(json.dumps(sellers, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {path} ({len(sellers)} sellers)")


def main() -> None:
    """Run the conversion from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--concordia-root",
        default=os.environ.get("CONCORDIA_SIGNALING_ROOT", DEFAULT_CONCORDIA_ROOT),
        help="Checkout of the pinned Concordia commit (see docs/PINNED_COMMITS.md).",
    )
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parent.parent / "input"),
        help="Destination input/ directory.",
    )
    args = parser.parse_args()
    root = Path(args.concordia_root)
    if not (root / "examples" / "signaling" / "configs" / "goods.py").is_file():
        raise SystemExit(f"no signaling example under {root}")
    convert(root, Path(args.out))


if __name__ == "__main__":
    main()
