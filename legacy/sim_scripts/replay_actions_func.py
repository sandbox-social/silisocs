import json
import threading
from collections import OrderedDict


def replay_actions(events_file, mastodon_apps=None, target_episode=None, target_name=None):
    """
    Replay actions from a JSON-lines file. If mastodon_apps is provided, use it for all actions.
    Otherwise, fall back to the original logic (for CLI usage).
    """
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
            src = evt["source_user"].split()[0]
            displays[src] = None
            if label in ("reply",):
                tgt = evt["data"]["reply_to"]["target_user"].split()[0]
                displays[tgt] = None
            elif label in ("boost_toot", "like_toot", "follow", "unfollow"):
                tgt = evt["data"].get("target_user", "")
                if tgt:
                    displays[tgt.split()[0]] = None

    toot_id_map = {}

    def execute_event_with_apps(evt, mastodon_apps, toot_id_map):
        label = evt.get("label")
        src_disp = evt["source_user"].split()[0]
        app = mastodon_apps.get(src_disp)
        if not app:
            print(f"No Mastodon app for {src_disp}, skipping event {label}")
            return
        data = evt["data"]
        if label == "follow":
            tgt_disp = data["target_user"].split()[0]
            app.follow_user(tgt_disp)
        elif label == "unfollow":
            tgt_disp = data["target_user"].split()[0]
            app.unfollow_user(tgt_disp)
        elif label == "post":
            old = str(data["toot_id"])
            text = data["post_text"]
            status = app.post_status(text)
            if status and "id" in status:
                toot_id_map[old] = status["id"]
        elif label == "reply":
            old_reply_to = str(evt["data"]["reply_to"]["toot_id"])
            new_parent = toot_id_map.get(old_reply_to)
            if new_parent is None:
                print(f"WARNING: no mapping for parent {old_reply_to}; skipping")
                return
            text = data["post_text"]
            status = app.post_status(text, in_reply_to_id=new_parent)
            if status and "id" in status:
                toot_id_map[str(data["toot_id"])] = status["id"]
        elif label in ("boost_toot", "boost"):
            old = str(data["toot_id"])
            new_id = toot_id_map.get(old)
            if new_id is None:
                print(f"WARNING: no mapping for boost target {old}; skipping")
                return
            tgt_disp = data.get("target_user", "").split()[0]
            app.boost_toot(tgt_disp, new_id)
        elif label in ("like_toot", "like"):
            old = str(data["toot_id"])
            new_id = toot_id_map.get(old)
            if new_id is None:
                print(f"WARNING: no mapping for like target {old}; skipping")
                return
            tgt_disp = data.get("target_user", "").split()[0]
            app.like_toot(tgt_disp, new_id)
        elif label == "update_profile":
            new_bio = data.get("new_bio")
            app.update_profile(new_bio)
        else:
            print(f"-- unhandled label '{label}', skipping")

    # --- Parallel follows, then sequential actions ---
    threads = []
    idx = 0
    for idx, evt in enumerate(raw):
        if evt.get("label") != "follow":
            break
        if mastodon_apps:
            t = threading.Thread(
                target=execute_event_with_apps, args=(evt, mastodon_apps, toot_id_map)
            )
        else:
            from sim.scripts.replay_actions import build_display_mapping, execute_event

            disp2login = build_display_mapping(displays.keys())
            t = threading.Thread(target=execute_event, args=(evt, disp2login, toot_id_map))
        t.start()
        threads.append(t)
    else:
        idx += 1
    for t in threads:
        t.join()
    for evt in raw[idx:]:
        if mastodon_apps:
            execute_event_with_apps(evt, mastodon_apps, toot_id_map)
        else:
            from sim.scripts.replay_actions import build_display_mapping, execute_event

            disp2login = build_display_mapping(displays.keys())
            execute_event(evt, disp2login, toot_id_map)
    print("Done. toot_id mapping:", toot_id_map)
