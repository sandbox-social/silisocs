#!/usr/bin/env python3
"""Compare social-action metrics across three experiment arms.

This script is world-local so experiment reruns can be evaluated quickly.
It computes social action totals and per-agent rates while excluding fixed
agents (for example, deterministic news accounts).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import yaml

SOCIAL_LABELS = ("post", "reply", "like", "repost")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _find_run_dir(path_like: str) -> Path:
    """Resolve a run directory that contains action_events.jsonl.

    Accepts either:
    - the direct run directory (contains action_events.jsonl), or
    - a parent directory where run directories are nested.
    """
    root = Path(path_like).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path does not exist: {root}")

    if (root / "action_events.jsonl").exists():
        return root

    candidates = [p.parent for p in root.rglob("action_events.jsonl")]
    if not candidates:
        raise FileNotFoundError(f"No run directory with action_events.jsonl found under: {root}")

    # Use latest modified run directory to simplify repeated experiment runs.
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _extract_fixed_usernames_from_config(effective_config: dict[str, Any]) -> set[str]:
    fixed_usernames: set[str] = set()
    classes = effective_config.get("agents", {}).get("persona_pipeline", {}).get("classes", {})
    if not isinstance(classes, dict):
        return fixed_usernames

    for class_cfg in classes.values():
        if not isinstance(class_cfg, dict):
            continue
        class_path = str(class_cfg.get("class_path", ""))
        if "FixedAgent" not in class_path:
            continue

        data_cfg = class_cfg.get("data", {})
        if not isinstance(data_cfg, dict):
            continue

        if data_cfg.get("source") == "inline":
            records = data_cfg.get("records", [])
            if isinstance(records, list):
                for record in records:
                    if isinstance(record, dict) and isinstance(record.get("name"), str):
                        fixed_usernames.add(record["name"])

    return fixed_usernames


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def compute_arm_metrics(
    run_dir: Path,
    arm_name: str,
    extra_excluded_users: set[str],
    exclude_final_episode: bool = True,
) -> dict[str, Any]:
    events = _read_jsonl(run_dir / "action_events.jsonl")
    effective_config = _load_yaml(run_dir / "effective_config.yaml")

    init_users = {
        str(e.get("source_user", ""))
        for e in events
        if e.get("label") == "init_create_user"
        and isinstance(e.get("source_user"), str)
        and e.get("source_user") not in {"", "system"}
    }

    fixed_users = _extract_fixed_usernames_from_config(effective_config)
    excluded_users = fixed_users | set(extra_excluded_users)
    eval_users = sorted(u for u in init_users if u not in excluded_users)

    num_eval_agents = len(eval_users)
    eval_user_set = set(eval_users)

    num_steps_cfg = _safe_int(effective_config.get("sim", {}).get("num_steps"), 0)
    if num_steps_cfg <= 0:
        num_steps_cfg = max((_safe_int(e.get("episode"), 0) for e in events), default=0)
    num_steps = max(num_steps_cfg, 0)

    label_counts: Counter[str] = Counter()
    social_actions_by_step: dict[int, int] = defaultdict(int)
    active_users_by_step: dict[int, set[str]] = defaultdict(set)
    timeline_returned_posts: list[float] = []

    for e in events:
        episode = _safe_int(e.get("episode"), 0)
        if episode < 1:
            continue
        if exclude_final_episode and num_steps > 0 and episode == num_steps:
            continue

        source_user = str(e.get("source_user", ""))
        if source_user not in eval_user_set:
            continue

        label = str(e.get("label", ""))

        if label in SOCIAL_LABELS:
            label_counts[label] += 1
            social_actions_by_step[episode] += 1
            active_users_by_step[episode].add(source_user)

        if label == "timeline_retrieval":
            returned = _safe_float(e.get("data", {}).get("returned_posts"), -1)
            if returned >= 0:
                timeline_returned_posts.append(returned)

    post_count = label_counts["post"]
    reply_count = label_counts["reply"]
    like_count = label_counts["like"]
    repost_count = label_counts["repost"]
    interaction_count = reply_count + like_count + repost_count
    total_social_actions = post_count + interaction_count

    if exclude_final_episode and num_steps > 0:
        steps = list(range(1, num_steps))
    else:
        steps = list(range(1, num_steps + 1))
    if not steps:
        steps = sorted(social_actions_by_step.keys())

    per_step_social_actions = [social_actions_by_step.get(step, 0) for step in steps]
    per_step_actions_per_agent = [
        (count / num_eval_agents if num_eval_agents else 0.0) for count in per_step_social_actions
    ]
    per_step_actions_per_active_agent = []
    for step in steps:
        active_n = len(active_users_by_step.get(step, set()))
        count = social_actions_by_step.get(step, 0)
        per_step_actions_per_active_agent.append(count / active_n if active_n else 0.0)

    avg_actions_per_agent = total_social_actions / num_eval_agents if num_eval_agents else 0.0
    avg_actions_per_agent_per_step = (
        mean(per_step_actions_per_agent) if per_step_actions_per_agent else 0.0
    )
    avg_actions_per_active_agent_per_step = (
        mean(per_step_actions_per_active_agent) if per_step_actions_per_active_agent else 0.0
    )

    return {
        "arm": arm_name,
        "run_dir": str(run_dir),
        "num_users_total": len(init_users),
        "num_fixed_users_excluded": len(excluded_users & init_users),
        "num_eval_users": num_eval_agents,
        "configured_num_steps": num_steps,
        "num_steps": len(steps),
        "exclude_final_episode": bool(exclude_final_episode),
        "post_count": post_count,
        "reply_count": reply_count,
        "like_count": like_count,
        "repost_count": repost_count,
        "interaction_count": interaction_count,
        "total_social_actions": total_social_actions,
        "avg_actions_per_agent": avg_actions_per_agent,
        "avg_actions_per_agent_per_step": avg_actions_per_agent_per_step,
        "avg_actions_per_active_agent_per_step": avg_actions_per_active_agent_per_step,
        "timeline_retrieval_events": len(timeline_returned_posts),
        "avg_returned_posts_per_timeline": (
            mean(timeline_returned_posts) if timeline_returned_posts else 0.0
        ),
        "per_step_social_actions": per_step_social_actions,
        "per_step_actions_per_agent": per_step_actions_per_agent,
    }


def _fmt(x: float) -> str:
    return f"{x:.4f}"


def print_summary(results: list[dict[str, Any]]) -> None:
    by_arm = {r["arm"]: r for r in results}
    ordered = ["chronological", "recsys_twitter", "recsys_twhin"]

    print("\n=== Arm Metrics ===")
    for arm in ordered:
        if arm not in by_arm:
            continue
        r = by_arm[arm]
        print(
            f"{arm}: users={r['num_eval_users']} steps={r['num_steps']} "
            f"post={r['post_count']} reply={r['reply_count']} like={r['like_count']} repost={r['repost_count']} "
            f"interaction={r['interaction_count']} total_social={r['total_social_actions']} "
            f"avg_actions_per_agent={_fmt(r['avg_actions_per_agent'])} "
            f"avg_actions_per_agent_per_step={_fmt(r['avg_actions_per_agent_per_step'])}"
        )

    if "chronological" in by_arm:
        c = by_arm["chronological"]
        print("\n=== Deltas vs chronological ===")
        for arm in ("recsys_twitter", "recsys_twhin"):
            if arm not in by_arm:
                continue
            r = by_arm[arm]
            print(
                f"{arm}: "
                f"interaction_delta={r['interaction_count'] - c['interaction_count']} "
                f"total_social_delta={r['total_social_actions'] - c['total_social_actions']} "
                f"avg_actions_per_agent_delta={_fmt(r['avg_actions_per_agent'] - c['avg_actions_per_agent'])} "
                f"avg_actions_per_agent_per_step_delta={_fmt(r['avg_actions_per_agent_per_step'] - c['avg_actions_per_agent_per_step'])}"
            )

    print("\n=== Per-step avg actions per agent ===")
    for arm in ordered:
        if arm not in by_arm:
            continue
        r = by_arm[arm]
        seq = ", ".join(_fmt(v) for v in r["per_step_actions_per_agent"])
        print(f"{arm}: [{seq}]")


def write_json(path: Path, results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"results": results}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_csv(path: Path, results: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "arm",
        "run_dir",
        "num_users_total",
        "num_fixed_users_excluded",
        "num_eval_users",
        "configured_num_steps",
        "num_steps",
        "exclude_final_episode",
        "post_count",
        "reply_count",
        "like_count",
        "repost_count",
        "interaction_count",
        "total_social_actions",
        "avg_actions_per_agent",
        "avg_actions_per_agent_per_step",
        "avg_actions_per_active_agent_per_step",
        "timeline_retrieval_events",
        "avg_returned_posts_per_timeline",
    ]
    lines = [",".join(header)]
    for r in results:
        row = [
            str(r["arm"]),
            str(r["run_dir"]),
            str(r["num_users_total"]),
            str(r["num_fixed_users_excluded"]),
            str(r["num_eval_users"]),
            str(r["configured_num_steps"]),
            str(r["num_steps"]),
            str(r["exclude_final_episode"]),
            str(r["post_count"]),
            str(r["reply_count"]),
            str(r["like_count"]),
            str(r["repost_count"]),
            str(r["interaction_count"]),
            str(r["total_social_actions"]),
            _fmt(float(r["avg_actions_per_agent"])),
            _fmt(float(r["avg_actions_per_agent_per_step"])),
            _fmt(float(r["avg_actions_per_active_agent_per_step"])),
            str(r["timeline_retrieval_events"]),
            _fmt(float(r["avg_returned_posts_per_timeline"])),
        ]
        lines.append(",".join(item.replace(",", " ") for item in row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare world run metrics across arms.")
    parser.add_argument(
        "--chronological", required=True, help="Path to chronological arm run dir or parent dir"
    )
    parser.add_argument(
        "--recsys-twitter", required=True, help="Path to twitter recsys arm run dir or parent dir"
    )
    parser.add_argument(
        "--recsys-twhin", required=True, help="Path to twhin recsys arm run dir or parent dir"
    )
    parser.add_argument(
        "--exclude-user",
        action="append",
        default=[],
        help="Additional source_user name to exclude from agent-level metrics (repeatable)",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to write full JSON results",
    )
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional path to write tabular CSV results",
    )
    parser.add_argument(
        "--include-final-episode",
        action="store_true",
        help=(
            "Include the final configured episode in metric calculations. "
            "By default, the final episode is excluded."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    excluded = set(args.exclude_user)

    arms = {
        "chronological": _find_run_dir(args.chronological),
        "recsys_twitter": _find_run_dir(args.recsys_twitter),
        "recsys_twhin": _find_run_dir(args.recsys_twhin),
    }

    results = [
        compute_arm_metrics(
            run_dir=run_dir,
            arm_name=arm_name,
            extra_excluded_users=excluded,
            exclude_final_episode=(not args.include_final_episode),
        )
        for arm_name, run_dir in arms.items()
    ]

    print_summary(results)

    if args.output_json:
        write_json(Path(args.output_json).expanduser().resolve(), results)
    if args.output_csv:
        write_csv(Path(args.output_csv).expanduser().resolve(), results)


if __name__ == "__main__":
    main()
