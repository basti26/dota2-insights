"""Compare disputed HUDs to the latest prior raw-visible inventory, never best fit."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.prepare_replay_item_audit import bounded_rows, digest, game_guard

LOOKBACK_S = 120.0
CLASSES = {"image_vs_omniscient_stable_unresolved", "ward_entity_and_icon_semantics_pending"}


def compare_slots(original, reference):
    if reference is None:
        return {"status": "no_reference"}
    inventory = {item["slot"]: item for item in reference["inventory"] if 0 <= item["slot"] <= 8}
    pairs, observed, actual = [], Counter(), Counter()
    unknown = 0
    for item in original:
        slot_id = item["slot_id"]
        slot = int(slot_id[-1]) - 1 if slot_id.startswith("main_") else int(slot_id[-1]) + 5
        if item["status"] not in ("known", "empty"):
            unknown += 1
            continue
        target = inventory.get(slot)
        if target is None or target["status"] not in ("occupied", "empty"):
            status = "reference_unknown"
        elif item["status"] == "empty":
            status = "same" if target["status"] == "empty" else "different"
        else:
            status = "same" if item["item_id"] == target.get("item_key") else "different"
        pairs.append({"slot": slot, "observed": item.get("item_id"),
                      "reference": target.get("item_key") if target else None, "status": status})
        if item["status"] == "known":
            observed[item["item_id"]] += 1
    for item in inventory.values():
        if item["status"] == "occupied":
            actual[item["item_key"]] += 1
    return {"status": "compared_known_observed_slots", "observed_unknown_slots": unknown,
        "slot_counts": dict(Counter(pair["status"] for pair in pairs)), "slot_pairs": pairs,
        "observed_known_items_subset_of_reference": not (observed - actual),
        "all_9_slot_item_multisets_equal": observed == actual if unknown == 0 else None}


def analyze(review_path, archives):
    game_guard()
    review_path = Path(review_path)
    if review_path.stat().st_size > 16 * 1024 ** 2:
        raise ValueError("last_visible_review_size_limit")
    review_hash = digest(review_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    archive_map = {}
    for directory in map(Path, archives):
        manifest_path = directory / "manifest.json"
        if manifest_path.stat().st_size > 1024 ** 2:
            raise ValueError("last_visible_manifest_size_limit")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("parse_error") is not None or manifest.get("source_unchanged_stat") is not True
                or manifest.get("truncated_at_tick") is not None or manifest.get("stop_reason") != "replay_eof"
                or manifest.get("export_complete") is False
                or manifest.get("parsed_match_id") != manifest.get("match_id")):
            raise ValueError("last_visible_successful_archive_required")
        archive_map[manifest["match_id"]] = directory, manifest, manifest_path
    cases = []
    for case in review["cases"]:
        selected = [frame for frame in case["frames"] if frame["classification"] in CLASSES]
        directory, manifest, manifest_path = archive_map[case["match_id"]]
        path = directory / "hero_samples.jsonl"
        if path.stat().st_size > 512 * 1024 ** 2:
            raise ValueError("last_visible_sample_size_limit")
        declared = {source["file_name"]: source["sha256"] for source in case["sources"]}
        sample_hash = digest(path)
        if (sample_hash != declared["hero_samples.jsonl"] or digest(manifest_path) != declared["manifest.json"]
                or sample_hash != manifest["files"][path.name]["sha256"]
                or path.stat().st_size != manifest["files"][path.name]["bytes"]):
            raise ValueError("last_visible_archive_source_mismatch")
        source_stat = path.stat().st_size, path.stat().st_mtime_ns
        by_hero = defaultdict(list)
        for frame in selected:
            if frame["self_team"] not in ("radiant", "dire"):
                raise ValueError("last_visible_observer_team_unknown")
            frame = {k: frame[k] for k in ("observation_line", "match_id", "hero_id", "clock_time_s",
                "captured_at_utc", "provider_at_utc", "alignment_delta_s", "self_team", "roster_relation",
                "hud_image", "hud_image_sha256", "original_slots", "disputed_slots", "classification")}
            frame.update(last_prior_raw_visible=None, latest_prior_sample=None)
            by_hero[frame["hero_id"]].append(frame)
        for number, sample in bounded_rows(path):
            if sample["match_id"] != case["match_id"]:
                raise ValueError("last_visible_row_match_mismatch")
            for frame in by_hero.get(sample["hero_id"], []):
                age = frame["clock_time_s"] - sample["game_time_s"]
                if not 0 <= age <= LOOKBACK_S:
                    continue
                state = sample["visibility"][frame["self_team"]]["state"]
                projected = {"source_line": number, "tick": sample["tick"],
                    "game_time_s": sample["game_time_s"], "age_to_integer_gsi_clock_s": age,
                    "raw_visibility": state, "alive": sample.get("alive"), "paused": sample.get("paused"),
                    "inventory": sample["inventory"]}
                if (frame["latest_prior_sample"] is None
                        or projected["tick"] > frame["latest_prior_sample"]["tick"]):
                    frame["latest_prior_sample"] = projected
                if state == "visible" and (frame["last_prior_raw_visible"] is None
                        or projected["tick"] > frame["last_prior_raw_visible"]["tick"]):
                    frame["last_prior_raw_visible"] = projected
        frames = sorted((f for group in by_hero.values() for f in group), key=lambda x:x["observation_line"])
        for frame in frames:
            for key in ("last_prior_raw_visible", "latest_prior_sample"):
                frame[key + "_comparison"] = compare_slots(frame["original_slots"], frame[key])
        if source_stat != (path.stat().st_size, path.stat().st_mtime_ns):
            raise ValueError("last_visible_archive_changed")
        cases.append({"match_id": case["match_id"], "source_samples_sha256": sample_hash,
                      "frames": frames})
    if digest(review_path) != review_hash:
        raise ValueError("last_visible_review_changed")
    return {"schema_version": 1, "kind": "fixed_rule_last_prior_raw_visible_inventory_review",
        "tool_source_sha256": digest(Path(__file__)),
        "visual_review_source_sha256": review_hash,
        "rule": {"lookback_s": LOOKBACK_S, "time_reference": "reported_integer_GSI_game_clock",
            "visible_selection": "latest_tick_with_raw_observer_team_bit_visible_at_or_before_clock",
            "current_state_selection": "latest_tick_at_or_before_clock_in_same_fixed_window",
            "item_similarity_used_to_select_time": False,
            "future_samples_used": False, "causal_proof": False}, "cases": cases,
        "limitations": ["Raw visibility remains a replay candidate; correspondence to client inventory display is unverified.",
            "Integer GSI clock and provider age do not identify the exact screenshot tick.",
            "Latest prior raw-visible sample is selected by a fixed time rule even if its inventory does not match.",
            "Unknown observed slots are excluded from known-slot comparisons; item multisets ignore charges.",
            "Matching past inventory suggests a client last-seen candidate but does not prove its cause.",
            "No model inference, live changes, test suite, game capture, network query or replay decoding."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--archive", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if (not 1 <= len(args.archive) <= 4 or output.exists() or output.suffix != ".json"
                or not output.is_relative_to((ROOT / "diagnostics").resolve())):
            raise ValueError("last_visible_new_diagnostics_output_required")
        result = analyze(args.review, args.archive)
        body = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        if len(body.encode("utf-8")) > 16 * 1024 ** 2:
            raise ValueError("last_visible_output_size_limit")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(body)
        print(json.dumps({"status": "reviewed", "frames": sum(len(c["frames"]) for c in result["cases"])}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)[:160]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
