"""Prepare source-bound disputed HUD slots and fixed +/-3s replay inventories.

This does not rerun recognition, select a best-matching time, or infer accuracy.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.evaluate_replay_baselines import game_guard

WINDOW_S = 3.0
DISPUTED = {"item_id_disagreement", "recognized_empty_replay_occupied"}


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def bounded_rows(path, limit=512 * 1024 ** 2):
    size = 0
    with Path(path).open("rb") as handle:
        for number in range(1, 200001):
            if number % 4096 == 1:
                game_guard()
            line = handle.readline(256 * 1024 + 1)
            if not line:
                return
            size += len(line)
            if len(line) > 256 * 1024 or size > limit or not line.endswith(b"\n"):
                raise ValueError("item_audit_source_limit")
            yield number, json.loads(line)
        raise ValueError("item_audit_row_limit")


def prepare(report_path, observations_path, archive_directory):
    report_path, observations_path, archive_directory = map(Path, (report_path, observations_path, archive_directory))
    if report_path.stat().st_size > 16 * 1024 ** 2:
        raise ValueError("item_audit_report_limit")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest_path, samples_path = archive_directory / "manifest.json", archive_directory / "hero_samples.jsonl"
    if (manifest_path.stat().st_size > 1024 ** 2 or samples_path.stat().st_size > 512 * 1024 ** 2
            or observations_path.stat().st_size > 128 * 1024 ** 2):
        raise ValueError("item_audit_source_size_limit")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_paths = (report_path, observations_path, manifest_path, samples_path)
    signatures = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in source_paths}
    hashes = {p: digest(p) for p in source_paths}
    declared = {r["file_name"]: r["sha256"] for r in report["sources"]}
    if (report.get("kind") != "offline_replay_economy_comparison"
            or declared.get("observations.jsonl") != hashes[observations_path]
            or declared.get("hero_samples.jsonl") != hashes[samples_path]
            or declared.get("manifest.json") != hashes[manifest_path]
            or manifest.get("parse_error") is not None or manifest.get("source_unchanged_stat") is not True
            or manifest.get("truncated_at_tick") is not None or manifest.get("export_complete") is False
            or manifest.get("parsed_match_id") != manifest.get("match_id")
            or manifest.get("stop_reason") != "replay_eof"
            or manifest.get("files", {}).get("hero_samples.jsonl", {}).get("sha256") != hashes[samples_path]
            or manifest.get("files", {}).get("hero_samples.jsonl", {}).get("bytes") != samples_path.stat().st_size):
        raise ValueError("item_audit_sources_do_not_match_report")
    disputed = {row["observation_line"]: row for row in report["rows"]
                if any(item.get("comparison") in DISPUTED for item in row.get("comparisons", []))}
    if len(disputed) > 200:
        raise ValueError("item_audit_disputed_frame_limit")
    rows = []
    for number, record in bounded_rows(observations_path, 128 * 1024 ** 2):
        if number not in disputed:
            continue
        original = disputed[number]
        attempt, clock = record["attempt"], record.get("clock_context") or {}
        hero = attempt["hero"]
        if record.get("match_id") != manifest["match_id"] or hero["hero_id"] != original["hero_id"]:
            raise ValueError("item_audit_original_identity_mismatch")
        image_path = (observations_path.parent / record["hud_image"]).resolve()
        if not image_path.is_relative_to(observations_path.parent.resolve()):
            raise ValueError("item_audit_original_image_path_invalid")
        sha = digest(image_path)
        if sha != record["hud_image_sha256"]:
            raise ValueError("item_audit_original_image_checksum_mismatch")
        rows.append({"observation_line": number, "match_id": record["match_id"], "hero_id": hero["hero_id"],
            "captured_at_utc": record["captured_at_utc"], "clock_time_s": clock.get("clock_time_s"),
            "provider_at_utc": clock.get("provider_at_utc"), "alignment_delta_s": clock.get("alignment_delta_s"),
            "self_team": clock.get("self_team"), "roster_relation": (attempt.get("roster_evidence") or {}).get("team_relation"),
            "hud_image": record["hud_image"], "hud_image_sha256": sha,
            "hud_relative_to_project": image_path.relative_to(ROOT).as_posix(),
            "frame_geometry": record.get("frame_geometry"), "identity_candidates": attempt.get("candidates"),
            "original_slots": hero.get("slots"),
            "current_economy": {k: hero.get(k) for k in ("confirmed_main_value", "confirmed_backpack_value",
                "confirmed_visible_item_value", "confirmed_upgrade_value", "visible_cash", "confirmed_economy_value")},
            "disputed_slots": [item for item in original["comparisons"] if item["comparison"] in DISPUTED],
            "fixed_replay_window_s": [clock["clock_time_s"] - WINDOW_S, clock["clock_time_s"] + WINDOW_S],
            "replay_window": []})
    by_hero = defaultdict(list)
    for row in rows:
        by_hero[row["hero_id"]].append(row)
    for number, sample in bounded_rows(samples_path):
        for row in by_hero.get(sample.get("hero_id"), []):
            if abs(sample["game_time_s"] - row["clock_time_s"]) <= WINDOW_S:
                row["replay_window"].append({"source_line": number, "tick": sample["tick"],
                    "game_time_s": sample["game_time_s"], "time_error_s": sample["game_time_s"] - row["clock_time_s"],
                    "team": sample.get("team"), "alive": sample.get("alive"), "paused": sample.get("paused"),
                    "visibility": sample.get("visibility"), "inventory": sample.get("inventory"),
                    "consumed_scepter": sample.get("consumed_scepter"), "consumed_shard": sample.get("consumed_shard")})
    if len(rows) != len(disputed) or any(not row["replay_window"] for row in rows):
        raise ValueError("item_audit_missing_disputed_sources")
    if any((p.stat().st_size, p.stat().st_mtime_ns) != signature for p, signature in signatures.items()):
        raise ValueError("item_audit_source_changed_during_read")
    return {"match_id": manifest["match_id"], "source_replay_sha256": manifest["source_sha256"],
        "sources": [{"file_name": p.name, "sha256": hashes[p]} for p in source_paths],
        "frames": rows, "disputed_slot_count": sum(len(row["disputed_slots"]) for row in rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", nargs=3, action="append", required=True,
        metavar=("ECONOMY_REPORT_JSON", "OBSERVATIONS_JSONL", "REPLAY_ARCHIVE"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        if (not 1 <= len(args.case) <= 4 or output.exists() or output.suffix != ".json"
                or not output.is_relative_to((ROOT / "diagnostics").resolve())):
            raise ValueError("item_audit_new_diagnostics_output_required")
        game_guard()
        result = {"schema_version": 1, "kind": "original_hud_fixed_window_item_audit_evidence",
            "tool_source_sha256": digest(Path(__file__)),
            "window_policy": "all_samples_within_fixed_plus_minus_3_seconds_no_best_item_time_selection",
            "visual_reviews_complete": False, "global_accuracy_claim": False,
            "cases": [prepare(*case) for case in args.case]}
        body = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
        if len(body.encode("utf-8")) > 16 * 1024 ** 2:
            raise ValueError("item_audit_output_limit")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(body + "\n")
        print(json.dumps({"status": "prepared", "frames": sum(len(c["frames"]) for c in result["cases"]),
            "disputed_slots": sum(c["disputed_slot_count"] for c in result["cases"])}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"status": "failed", "reason": str(error)[:160]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
