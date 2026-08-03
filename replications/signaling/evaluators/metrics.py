"""Derive the signaling experiment's measures from a run's committed action log.

Everything the experiment measures is already a committed action row, so this
evaluator reads the standard artifacts through ``load_run`` and never invents a
side-channel file. The upstream outputs it stands in for are
``signaling_trades.json`` and ``signaling_prices.json``; the port additionally
recovers the date ratings, which upstream only leaves inside HTML logs.

Measures (per run):

``status_share``
    Fraction of purchased units that were High-quality — the port's operational
    reading of "conspicuous consumption". Reported overall and per day, since
    the paper's claim is about the trend across days, not a level.
``prices``
    Mean clearing price per good per day, plus the first-to-last-day change:
    the paper predicts prices move under ``social`` and stay flat-to-declining
    under ``asocial``.
``ratings``
    Distribution of the 0-10 partner ratings parsed from the final post-date
    reflection.
``mentions``
    Fraction of date utterances that name an item the speaker owns — a cheap
    direct check that consumption is actually being *displayed*, which is the
    mechanism the whole design rests on.

Usage::

    uv run python -m replications.signaling.evaluators.metrics \
        --run-dir <run output dir> --output metrics.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from replications.signaling.components import goods as goods_lib
from replications.signaling.components.calendar import DayCalendar
from silisocs.evaluations.run_artifact import load_run

HIGH_TIER = "High"
#: A first-to-last change needs at least two days to exist.
_MIN_DAYS_FOR_TREND = 2


#: The repository root, for resolving the repo-relative paths a run's effective
#: config records (``goods_path: replications/signaling/input/...``).
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _effective_config(run_dir: Path) -> dict[str, Any]:
    """Load the run's effective config, failing loudly when there is none.

    Every measure below is keyed by the run's calendar, condition, and goods
    table — all of which live only in the effective config, so a directory
    without one cannot be evaluated (and is probably not a run directory).
    """
    for candidate in (run_dir / "effective_config.yaml", run_dir / "config" / "config.yaml"):
        if candidate.is_file():
            return yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
    raise FileNotFoundError(
        f"{run_dir} has no effective_config.yaml; cannot recover the run's "
        "calendar, condition, or goods table."
    )


def _goods_table(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Load the goods table the run used, resolving its repo-relative path.

    ``status_share`` — the study's primary measure — classifies every purchase
    through this table, so a missing table must raise rather than silently
    grade everything non-High.
    """
    raw = cfg.get("goods_path")
    if not raw:
        raise ValueError("The run's effective config has no goods_path; cannot grade purchases.")
    path = Path(raw)
    if not path.is_file() and not path.is_absolute():
        path = _REPO_ROOT / path
    if not path.is_file():
        raise FileNotFoundError(
            f"goods_path {raw!r} not found (also tried {path}); run the evaluator "
            "from the repository the run was made in."
        )
    return goods_lib.load_goods_table(path)


def _tier_of(table: dict[str, Any], item: str) -> str:
    found = goods_lib.find_tier(table, item) if table else None
    return found[1] if found else ""


def compute(run_dir: Path) -> dict[str, Any]:
    """Compute every measure for one run directory."""
    run = load_run(run_dir)
    cfg = _effective_config(run_dir)
    calendar = DayCalendar.from_params(cfg.get("calendar") or {})
    table = _goods_table(cfg)

    purchases: list[dict[str, Any]] = []
    prices_by_day: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    ratings: list[float] = []
    utterances: list[dict[str, Any]] = []

    for event in run.iter_actions():
        label = str(event.get("label", ""))
        data = event.get("data") or {}
        episode = event.get("episode")
        day = calendar.day_of(int(episode)) if isinstance(episode, int) else None

        if label == "round_resolved":
            # Clearing prices live in the round summary, keyed by good id; the
            # bookkeeping fields are not goods.
            reserved = {"round", "num_trades", "num_orders", "message", "day", "round_in_day"}
            round_day = int(data.get("day", day or 0))
            for key, value in data.items():
                if key in reserved or not isinstance(value, (int, float)):
                    continue
                prices_by_day[round_day][key].append(float(value))
        elif label == "bid":
            purchases.append({"day": day, "agent": event.get("source_user"), **data})
        elif label == "record_reflection" and data.get("kind") == "rating":
            if isinstance(data.get("rating"), (int, float)):
                ratings.append(float(data["rating"]))
        elif label == "send_message":
            utterances.append({"day": day, "agent": event.get("source_user"), **data})

    return {
        "run_dir": str(run_dir),
        # The manifest's scenario name is "signaling" for every arm; the
        # experimental condition is the world config's `condition` key.
        "condition": cfg.get("condition") or run.manifest.get("scenario"),
        "seed": run.seed,
        "num_steps": run.num_steps,
        "status_share": _status_share(purchases, table),
        "prices": _price_summary(prices_by_day),
        "ratings": _rating_summary(ratings),
        "mentions": _mention_rate(utterances, table),
        "counts": {
            "orders": len(purchases),
            "utterances": len(utterances),
            "ratings": len(ratings),
        },
    }


def _status_share(purchases: list[dict[str, Any]], table: dict[str, Any]) -> dict[str, Any]:
    """Share of ordered units that were High-quality, overall and per day.

    Computed over ORDERS rather than fills: an order is the agent's revealed
    preference, which is what the signaling claim is about, and it is available
    even for a round that found no counterparty.
    """
    by_day: dict[int, list[int]] = defaultdict(list)
    for row in purchases:
        good = str(row.get("good", ""))
        qty = int(row.get("qty", 0) or 0)
        if not good or qty <= 0:
            continue
        high = _tier_of(table, good) == HIGH_TIER
        by_day[int(row.get("day") or 0)].extend([1 if high else 0] * qty)
    overall = [flag for flags in by_day.values() for flag in flags]
    return {
        "overall": (sum(overall) / len(overall)) if overall else None,
        "per_day": {
            str(day): (sum(flags) / len(flags)) if flags else None
            for day, flags in sorted(by_day.items())
        },
    }


def _price_summary(prices_by_day: dict[int, dict[str, list[float]]]) -> dict[str, Any]:
    """Mean clearing price per good per day, and the first-to-last-day change."""
    per_day = {
        str(day): {good: statistics.fmean(values) for good, values in sorted(goods.items())}
        for day, goods in sorted(prices_by_day.items())
    }
    days = sorted(prices_by_day)
    change: dict[str, float] = {}
    if len(days) >= _MIN_DAYS_FOR_TREND:
        first, last = per_day[str(days[0])], per_day[str(days[-1])]
        for good in set(first) & set(last):
            change[good] = last[good] - first[good]
    mean_change = statistics.fmean(change.values()) if change else None
    return {"per_day": per_day, "first_to_last_change": change, "mean_change": mean_change}


def _rating_summary(ratings: list[float]) -> dict[str, Any]:
    if not ratings:
        return {"n": 0, "mean": None, "stdev": None, "min": None, "max": None}
    return {
        "n": len(ratings),
        "mean": statistics.fmean(ratings),
        "stdev": statistics.stdev(ratings) if len(ratings) > 1 else 0.0,
        "min": min(ratings),
        "max": max(ratings),
    }


def _mention_rate(utterances: list[dict[str, Any]], table: dict[str, Any]) -> dict[str, Any]:
    """Fraction of date utterances naming any catalogue item.

    A direct check that the display channel is live: if agents never mention
    what they own, no signal is being sent no matter what the purchase data says.
    """
    if not utterances or not table:
        return {"n": len(utterances), "rate": None}
    item_ids = [item for tiers in table.values() for items in tiers.values() for item in items]
    hits = 0
    for row in utterances:
        text = str(row.get("text", "")).lower()
        if any(item.lower() in text for item in item_ids):
            hits += 1
    return {"n": len(utterances), "rate": hits / len(utterances)}


def main() -> None:
    """Run the evaluator from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, help="Run output directory.")
    parser.add_argument("--output", default=None, help="Where to write the JSON report.")
    args = parser.parse_args()

    report = compute(Path(args.run_dir))
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(payload + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
