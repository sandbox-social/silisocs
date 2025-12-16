"""
replay_actions.py

Read a JSON-lines dump of action_events, skip inner_actions,
and replay each action (follow, post, reply, boost, like, unfollow).
Maintain a mapping of (old_toot_id → new_toot_id) so replies/boosts/likes
point at the newly created statuses.
"""

import json
import sys
import threading
from collections import OrderedDict

from mastodon_sim.mastodon_ops import check_env, clear_mastodon_server
from mastodon_sim.mastodon_ops.boost import boost_toot
from mastodon_sim.mastodon_ops.follow import follow
from mastodon_sim.mastodon_ops.get_client import get_client
from mastodon_sim.mastodon_ops.like import like_toot
from mastodon_sim.mastodon_ops.login import login
from mastodon_sim.mastodon_ops.post_status import post_status
from mastodon_sim.mastodon_ops.unfollow import unfollow
from mastodon_sim.mastodon_utils.get_users_from_env import get_users_from_env


def build_display_mapping(displays):
    """
    Given an ordered set of display names (first words),
    map each to one of the env-defined users (user0001, user0002, …).
    """
    available = get_users_from_env()
    mapping = {}
    for i, name in enumerate(displays):
        if i < len(available):
            mapping[name] = available[i]
        else:
            # reuse last if we run out
            mapping[name] = available[-1]
    return mapping


def execute_event(evt, disp2login, toot_id_map):
    label = evt.get("label")
    src_disp = evt["source_user"].split()[0]
    login_user = disp2login[src_disp]
    # get a fresh client & token
    token = login(login_user)
    masto = get_client()
    masto.access_token = token

    print(f"→ {label} by {login_user}")

    data = evt["data"]
    if label == "follow":
        tgt_disp = data["target_user"].split()[0]
        follow_user = disp2login.get(tgt_disp)
        follow(login_user, follow_user)

    elif label == "unfollow":
        tgt_disp = data["target_user"].split()[0]
        unfollow(login_user, disp2login.get(tgt_disp))

    elif label == "post":
        old = str(data["toot_id"])
        text = data["post_text"]
        status = post_status(login_user, text)
        if status and "id" in status:
            toot_id_map[old] = status["id"]

    elif label == "reply":
        old_reply_to = str(evt["data"]["reply_to"]["toot_id"])
        new_parent = toot_id_map.get(old_reply_to)
        if new_parent is None:
            print(f"WARNING: no mapping for parent {old_reply_to}; skipping")
            return
        text = data["post_text"]
        status = post_status(login_user, text, in_reply_to_id=new_parent)
        if status and "id" in status:
            toot_id_map[str(data["toot_id"])] = status["id"]

    elif label in ("boost_toot", "boost"):
        old = str(data["toot_id"])
        new_id = toot_id_map.get(old)
        if new_id is None:
            print(f"WARNING: no mapping for boost target {old}; skipping")
            return
        tgt_disp = data.get("target_user", "").split()[0]
        boost_toot(login_user, disp2login.get(tgt_disp), new_id)

    elif label in ("like_toot", "like"):
        old = str(data["toot_id"])
        new_id = toot_id_map.get(old)
        if new_id is None:
            print(f"WARNING: no mapping for like target {old}; skipping")
            return
        tgt_disp = data.get("target_user", "").split()[0]
        like_toot(login_user, disp2login.get(tgt_disp), new_id)

    elif label == "update_profile":
        # Only update the bio (note) using the "new_bio" field
        new_bio = data.get("new_bio")
        token = login(login_user)
        masto = get_client()
        masto.access_token = token
        try:
            masto.account_update_credentials(note=new_bio)
            print(f"Updated bio for {login_user}")
        except Exception as e:
            print(f"Failed to update bio for {login_user}: {e}")

    else:
        print(f"-- unhandled label '{label}', skipping")


def main(events_file, target_episode=None, target_name=None):
    # 1) load and filter
    raw = []
    displays = OrderedDict()
    with open(events_file, encoding="utf-8") as f:
        for line in f:
            evt = json.loads(line)
            if evt.get("event_type") != "action":
                continue
            label = evt.get("label")
            if label in ("inner_actions", "read_profile", "episode_plan"):
                continue

            # Check if we should stop at this episode/target
            if target_episode is not None:
                current_episode = evt.get("episode", 0)
                if current_episode > target_episode:
                    break
                if current_episode == target_episode and target_name:
                    src_user = evt["source_user"].split()[0].lower()
                    if src_user == target_name.lower():
                        break

            raw.append(evt)
            # record display → first token
            src = evt["source_user"].split()[0]
            displays[src] = None
            if label in ("reply",):
                tgt = evt["data"]["reply_to"]["target_user"].split()[0]
                displays[tgt] = None
            elif label in ("boost_toot", "like_toot", "follow", "unfollow"):
                tgt = evt["data"].get("target_user", "")
                if tgt:
                    displays[tgt.split()[0]] = None

    # 2) build a display_name -> login_user map
    disp2login = build_display_mapping(displays.keys())
    print("user mapping:", disp2login)
    # 3) clear Mastodon server before replay (like main.py)
    check_env()
    clear_mastodon_server(len(disp2login))

    # 3) prepare toot_id mapping
    toot_id_map = {}

    # --- Parallel follows, then sequential actions ---
    threads = []
    idx = 0
    for idx, evt in enumerate(raw):
        if evt.get("label") != "follow":
            break
        t = threading.Thread(target=execute_event, args=(evt, disp2login, toot_id_map))
        t.start()
        threads.append(t)
    else:
        idx += 1  # all events were follows

    # Wait for all follow threads to finish
    for t in threads:
        t.join()

    # Sequentially execute the rest
    for evt in raw[idx:]:
        execute_event(evt, disp2login, toot_id_map)

    print("Done. toot_id mapping:", toot_id_map)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python replay_actions.py path/to/action_events.jsonl [episode] [target_name]")
        print("  episode: Stop after this episode number (optional)")
        print("  target_name: Stop before this character's action in the target episode (optional)")
        sys.exit(1)

    events_file = sys.argv[1]
    target_episode = None
    target_name = None

    if len(sys.argv) >= 3:
        try:
            target_episode = int(sys.argv[2])
        except ValueError:
            print(f"Warning: Invalid episode number '{sys.argv[2]}', ignoring")

    if len(sys.argv) >= 4:
        target_name = sys.argv[3]

    print(f"Replaying events from: {events_file}")
    if target_episode is not None:
        print(f"Stopping after episode: {target_episode}")
        if target_name:
            print(f"Stopping before target character: {target_name}")

    main(events_file, target_episode, target_name)
