"""Bounded postgame joins of replay samples and immutable minimap side evidence.

This is an offline diagnostic. Replay world positions are omniscient references,
never labels saying that a player saw a hero. It imports no main application code.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median

from .dataset import SampleDataset

_HERO = re.compile(r"npc_dota_hero_[a-z0-9_]{1,50}")
_MATCH = re.compile(r"[0-9]{1,32}")
_SHA = re.compile(r"[a-f0-9]{64}")
MAX_SOURCE_BYTES = 256 * 1024 ** 2


def _utc(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("alignment_invalid_utc")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ValueError("alignment_requires_utc")
    return stamp


def _number(value, bound=1000000):
    return (type(value) in (int, float) and math.isfinite(value)
            and abs(value) <= bound)


def _digest(path, limit=MAX_SOURCE_BYTES):
    size, checksum = 0, hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 ** 2):
            size += len(chunk)
            if size > limit:
                raise ValueError("alignment_source_size_limit")
            checksum.update(chunk)
    return checksum.hexdigest(), size


def _json(path):
    with Path(path).open("rb") as handle:
        content = handle.read(MAX_SOURCE_BYTES + 1)
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("alignment_source_size_limit")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("alignment_expected_object")
    return value, {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}


def read_replay_samples(path):
    """Project only documented exporter fields; never retain arbitrary entities."""
    path = Path(path)
    source_hash, source_size = _digest(path)
    manifest, manifest_hash = _json(path.with_name("manifest.json"))
    declared = manifest.get("files", {}).get(path.name, {})
    if (manifest.get("kind") != "offline_replay_archive"
            or declared.get("sha256") != source_hash or declared.get("bytes") != source_size
            or not _SHA.fullmatch(str(manifest.get("source_sha256", "")))
            or manifest.get("parse_error") is not None
            or manifest.get("source_unchanged_stat") is not True
            or manifest.get("export_complete") is False
            or manifest.get("truncated_at_tick") is not None
            or manifest.get("parsed_match_id") != manifest.get("match_id")
            or manifest.get("stop_reason") not in ("replay_eof", "requested_game_window_end")):
        raise ValueError("alignment_replay_manifest_mismatch")
    rows, seen, counts = [], set(), Counter()
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, 1):
            if line_number > 200000 or len(line) > 256 * 1024:
                raise ValueError("alignment_replay_row_limit")
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("alignment_replay_row_not_object")
            if raw.get("kind") != "hero_sample":
                counts["non_hero_rows_skipped"] += 1
                continue
            match, hero = raw.get("match_id"), raw.get("hero_id")
            tick, clock = raw.get("tick"), raw.get("game_time_s")
            if (not isinstance(match, str) or not _MATCH.fullmatch(match) or int(match) <= 0
                    or not isinstance(hero, str) or not _HERO.fullmatch(hero)
                    or type(tick) is not int or tick < 0 or not _number(clock, 86400)
                    or raw.get("team") not in (2, 3) or match != manifest.get("match_id")
                    or raw.get("game_time_basis") != "gem_pause_aware_engine_game_clock"
                    or raw.get("coordinate_basis") != "gem_cell128_plus_vec_raw_coordinates"
                    or raw.get("packet_boundary") != "after_entity_updates"
                    or not _number(raw.get("world_x")) or not _number(raw.get("world_y"))
                    or raw.get("alive") is not None and type(raw["alive"]) is not bool):
                raise ValueError("alignment_replay_schema_invalid")
            key = match, hero, tick
            if key in seen:
                raise ValueError("alignment_duplicate_replay_hero_tick")
            seen.add(key)
            visibility = {}
            reported_visibility = raw.get("visibility")
            if isinstance(reported_visibility, dict):
                for team in ("radiant", "dire"):
                    value = reported_visibility.get(team)
                    if not isinstance(value, dict) or value.get("state") not in ("visible", "hidden", "unknown"):
                        continue
                    visibility[team] = {"state": value["state"],
                        "word_index": value.get("word_index") if type(value.get("word_index")) is int else None,
                        "bit": value.get("bit") if type(value.get("bit")) is int and 0 <= value["bit"] < 64 else None,
                        "word_value": value.get("word_value") if type(value.get("word_value")) is int
                            and 0 <= value["word_value"] < 2 ** 64 else None}
            rows.append({"source_line": line_number, "match_id": match, "hero_id": hero,
                "tick": tick, "game_time_s": clock, "team": raw["team"],
                "world_x": raw["world_x"], "world_y": raw["world_y"],
                "alive": raw.get("alive"), "paused": raw.get("paused") if type(raw.get("paused")) is bool else None,
                "visibility_raw": visibility,
                "visibility_semantics": "exporter_team_bit_semantics_not_verified_against_player_minimap",
                "position_meaning": "omniscient_replay_world_reference_not_player_visibility"})
    if not rows:
        raise ValueError("alignment_no_hero_samples")
    if _digest(path) != (source_hash, source_size):
        raise ValueError("alignment_replay_changed_during_read")
    return rows, {"sha256": source_hash, "bytes": source_size,
        "hero_rows": len(rows), "counts": dict(counts), "manifest": manifest_hash,
        "original_replay": {key: manifest.get(key) for key in ("source_file", "source_bytes", "source_sha256", "match_id")},
        "archive_window": {key: manifest.get(key) for key in ("stop_reason", "first_exported_game_time_s", "last_exported_game_time_s")},
        "parser": {key: manifest.get("parser", {}).get(key) for key in ("name", "version", "upstream_commit")},
        "game_clock": manifest.get("game_clock")}


def _timeline(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault((row["match_id"], row["hero_id"]), []).append(row)
    result = {}
    for key, values in grouped.items():
        values.sort(key=lambda value: (value["game_time_s"], value["tick"]))
        if any(a["game_time_s"] == b["game_time_s"] for a, b in zip(values, values[1:])):
            # Clock equality can be a real pause. Do not arbitrarily pick one
            # replay tick for the same game clock without a pause disambiguator.
            duplicate_clocks = {a["game_time_s"] for a, b in zip(values, values[1:])
                                if a["game_time_s"] == b["game_time_s"]}
        else:
            duplicate_clocks = set()
        result[key] = ([row["game_time_s"] for row in values], values, duplicate_clocks)
    return result


def _near(timeline, clock, max_delta):
    if timeline is None:
        return {"status": "hero_or_match_not_in_replay"}
    times, rows, duplicate_clocks = timeline
    index = bisect_left(times, clock)
    candidates = rows[max(0, index - 1): min(len(rows), index + 1)]
    chosen = min(candidates, key=lambda row: (abs(row["game_time_s"] - clock), row["tick"]))
    delta = chosen["game_time_s"] - clock
    if abs(delta) > max_delta:
        return {"status": "replay_sampling_gap", "nearest_clock_delta_s": round(delta, 6)}
    if chosen["game_time_s"] in duplicate_clocks:
        return {"status": "ambiguous_repeated_game_clock_requires_pause_evidence",
                "nearest_clock_delta_s": round(delta, 6)}
    return {"status": "matched_by_reported_game_clock", "replay": chosen,
            "nearest_clock_delta_s": round(delta, 6)}


def align_context(*, replay_path, context_path, minimap_run, diagnostics_root,
                  max_clock_delta_s=0.6, comparison_heroes=(), translation_calibration=None,
                  visibility_transitions=None, map_calibration=None,
                  start_sequence=None, max_frames=None):
    """Compare fixed zero-offset game clocks; never optimize time by position.

    The replay exporter must already express real Dota game clock seconds.
    Replay time correction, affine map fitting and future GSI are not performed.
    """
    if not _number(max_clock_delta_s, 2) or not 0 < max_clock_delta_s <= 2:
        raise ValueError("alignment_clock_tolerance_invalid")
    if (len(comparison_heroes) > 10 or any(not isinstance(hero, str) or not _HERO.fullmatch(hero)
                                         for hero in comparison_heroes)):
        raise ValueError("alignment_comparison_hero_invalid")
    replay, replay_source = read_replay_samples(replay_path)
    from .visibility_time_audit import VisibilityTimeAudit, clock_search_window
    visibility_audit = (VisibilityTimeAudit(visibility_transitions, expected_source=replay_source)
                        if visibility_transitions is not None else None)
    context, context_source = _json(context_path)
    translation = (0, 0)
    calibration_source = None
    calibration = None
    if translation_calibration is not None:
        calibration, calibration_source = _json(translation_calibration)
        if calibration.get("kind") != "offline_replay_translation_calibration":
            raise ValueError("alignment_translation_calibration_invalid")
        translation = calibration.get("translation_to_gsi_world")
        if (not isinstance(translation, list) or len(translation) != 2
                or any(not _number(value, 65536) for value in translation)
                or calibration.get("clock_offset_s") != 0):
            raise ValueError("alignment_translation_calibration_invalid")
    if context.get("kind") != "independent_minimap_postgame_context":
        raise ValueError("alignment_context_kind_invalid")
    frames = context.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 30000:
        raise ValueError("alignment_context_frame_limit")
    selection = None
    if start_sequence is not None or max_frames is not None:
        if (type(start_sequence) is not int or start_sequence < 1
                or type(max_frames) is not int or not 1 <= max_frames <= 2000):
            raise ValueError("alignment_explicit_slice_invalid")
        original_count = len(frames)
        frames = [frame for frame in frames if frame.get('sequence',0)>=start_sequence][:max_frames]
        selection = {'start_sequence':start_sequence,'max_frames':max_frames,
                     'source_context_frames':original_count,'selected_frames':len(frames)}
    if not 1 <= len(frames) <= 2000:
        raise ValueError("alignment_context_frame_limit")
    root = Path(diagnostics_root).resolve()
    checked_sources = []
    for source in context.get("sources", []):
        label = source.get("local_evidence_label")
        if not isinstance(label, str) or "\\" in label or ":" in label:
            raise ValueError("alignment_context_source_label_invalid")
        path = (root / label).resolve()
        if not path.is_relative_to(root) or not _SHA.fullmatch(str(source.get("sha256", ""))):
            raise ValueError("alignment_context_source_outside_root")
        sha, size = _digest(path)
        if sha != source["sha256"] or size != source.get("bytes"):
            raise ValueError("alignment_context_source_changed")
        checked_sources.append({"source_id": source["source_id"], "local_evidence_label": label,
                                "sha256": sha, "bytes": size})
    dataset = SampleDataset(minimap_run)
    geometry = geometry_source = None
    if map_calibration is not None:
        from .static_map_calibration import load_geometry, check_frame_geometry, project_visible_window
        geometry, geometry_source = load_geometry(map_calibration, index_sha256=dataset.index_sha256)
    indexed = {row["sequence"]: row for row in dataset.rows}
    if not any(item["sha256"] == dataset.index_sha256
               and item["local_evidence_label"].endswith("/samples.jsonl")
               for item in checked_sources):
        raise ValueError("alignment_minimap_index_not_in_context_sources")
    timelines = _timeline(replay)
    output, counts, own_unique = [], Counter(), {}
    for frame in frames:
        row = indexed.get(frame.get("sequence"))
        if (row is None or row["captured_at_utc"] != frame.get("captured_at_utc")
                or row["image_sha256"] != frame.get("image_sha256")
                or row["image"] != frame.get("image")):
            raise ValueError("alignment_minimap_reference_changed")
        native_image = dataset.image(row)  # Checks byte hash, dimensions and pixel decoding.
        result = {key: frame[key] for key in ("sequence", "captured_at_utc", "image", "image_sha256")}
        result["match_id"] = frame.get("match_id")
        result["pixel_world_mapping"] = "not_calibrated_no_name_center_as_marker"
        own = (frame.get("context") or {}).get("self")
        if not isinstance(own, dict):
            result["status"] = "no_past_own_gsi_context"
        else:
            captured, available = _utc(frame["captured_at_utc"]), _utc(own["available_at_utc"])
            provider = _utc(own["source_provider_at_utc"])
            position = own.get("own_position") or {}
            clock = own.get("clock_time_s")
            if (not 0 <= (captured - available).total_seconds() <= 3
                    or not 0 <= (captured - provider).total_seconds() <= 3
                    or own.get("freshness") != "fresh"
                    or frame.get("match_id") != own.get("match_id")
                    or frame.get("match_association_status") != "associated_fresh_source"):
                result["status"] = "own_gsi_not_fresh_past_same_match"
            elif not _number(clock, 86400) or position.get("status") != "reported":
                result["status"] = "own_game_clock_or_position_unknown"
            elif not _number(position.get("xpos")) or not _number(position.get("ypos")):
                result["status"] = "own_position_invalid"
            else:
                gsi = {key: own.get(key) for key in ("source", "clock_time_s", "source_provider_at_utc",
                    "source_received_at_utc", "own_hero_id", "own_alive", "paused")}
                gsi.update({"world_x": position["xpos"], "world_y": position["ypos"],
                    "available_age_s": round((captured - available).total_seconds(), 6),
                    "provider_age_s": round((captured - provider).total_seconds(), 6)})
                window = clock_search_window(clock, gsi["provider_age_s"])
                result["capture_game_clock_search_window"] = window
                result["single_nearest_sample_visibility_is_capture_truth"] = False
                if visibility_audit is not None:
                    side = own.get("own_team")
                    side_basis = "own_gsi_reported_team"
                    if side not in {"radiant", "dire"}:
                        side = (own.get("map_context") or {}).get("own_team")
                    replay_side = visibility_audit.team_at_reported_clock(own.get("own_hero_id"), clock)
                    conflict = side in {"radiant", "dire"} and replay_side is not None and replay_side != side
                    if conflict:
                        side = None
                    elif side not in {"radiant", "dire"}:
                        side = replay_side
                        side_basis = "postgame_replay_team_of_own_GSI_hero_at_or_before_reported_clock"
                    if side in {"radiant", "dire"}:
                        result["visibility_perspective_basis"] = side_basis
                        result["visibility_time_audit"] = {
                            hero: visibility_audit.query(hero, side, window)
                            for hero in dict.fromkeys((own.get("own_hero_id"), *comparison_heroes))
                            if isinstance(hero, str)}
                    else:
                        result["visibility_time_audit_status"] = "viewer_team_conflict" if conflict else "viewer_team_unknown"
                match = _near(timelines.get((own["match_id"], own.get("own_hero_id"))),
                              clock, max_clock_delta_s)
                if calibration is not None:
                    bounds = calibration.get("game_clock_bounds_by_match", {}).get(own["match_id"])
                    if (not isinstance(bounds, list) or len(bounds) != 2
                            or any(not _number(value, 86400) for value in bounds)
                            or bounds[0] <= clock <= bounds[1]):
                        raise ValueError("alignment_calibration_not_separate_from_evaluation")
                result.update({"status": match["status"], "own_gsi": gsi, "own_comparison": match})
                if "replay" in match:
                    sample = match["replay"]
                    match["translated_world_x"] = sample["world_x"] + translation[0] if calibration is not None else None
                    match["translated_world_y"] = sample["world_y"] + translation[1] if calibration is not None else None
                    match["raw_gsi_minus_replay_x"] = position["xpos"] - sample["world_x"]
                    match["raw_gsi_minus_replay_y"] = position["ypos"] - sample["world_y"]
                    match["coordinate_comparison_status"] = ("independent_origin_calibration_applied"
                        if calibration is not None else "origin_not_calibrated_distance_not_comparable")
                    match["world_distance_units"] = round(math.hypot(
                        match["translated_world_x"] - position["xpos"],
                        match["translated_world_y"] - position["ypos"]), 3) if calibration is not None else None
                    match["alive_agrees"] = (sample["alive"] == own["own_alive"]
                        if type(sample["alive"]) is bool and type(own.get("own_alive")) is bool else None)
                    key = (own["source"]["source_id"], own["source"]["line"])
                    own_unique[key] = match
                result["comparison_heroes"] = {
                    hero: _near(timelines.get((own["match_id"], hero)), clock, max_clock_delta_s)
                    for hero in comparison_heroes}
                if geometry is not None:
                    side = own.get("own_team")
                    team = 2 if side == "radiant" else 3 if side == "dire" else match.get("replay", {}).get("team")
                    result["stationary_pixel_geometry"] = check_frame_geometry(native_image, geometry, viewer_team=team)
                    if result["stationary_pixel_geometry"]["supported"]:
                        result["pixel_world_mapping"] = "stationary_tower_geometry_supported_hero_points_unverified"
                        result["visible_pixel_reference_windows"] = {
                            hero: project_visible_window(timelines.get((own["match_id"], hero)), geometry, window,
                                                         viewer_team=team)
                            for hero in dict.fromkeys((own.get("own_hero_id"), *comparison_heroes))
                            if isinstance(hero, str)}
        counts[result["status"]] += 1
        output.append(result)
    distances = [row["world_distance_units"] for row in own_unique.values()
                 if _number(row["world_distance_units"])]
    alive = [row["alive_agrees"] for row in own_unique.values() if row["alive_agrees"] is not None]
    both_alive = [row["world_distance_units"] for row in own_unique.values()
                  if row["replay"]["alive"] is True and row["alive_agrees"] is True
                  and _number(row["world_distance_units"])]
    both_dead = [row["world_distance_units"] for row in own_unique.values()
                 if row["replay"]["alive"] is False and row["alive_agrees"] is True
                 and _number(row["world_distance_units"])]
    # Refuse evidence modified while decoding the image subset or writing joins.
    if _digest(replay_path) != (replay_source["sha256"], replay_source["bytes"]):
        raise ValueError("alignment_replay_changed_during_join")
    if _digest(context_path) != (context_source["sha256"], context_source["bytes"]):
        raise ValueError("alignment_context_changed_during_join")
    for source in checked_sources:
        if _digest(root / source["local_evidence_label"]) != (source["sha256"], source["bytes"]):
            raise ValueError("alignment_context_source_changed_during_join")
    if visibility_audit is not None:
        visibility_audit.verify_unchanged()
    if geometry_source is not None and _digest(map_calibration) != (geometry_source["sha256"], geometry_source["bytes"]):
        raise ValueError("map_calibration_changed_during_alignment")
    return {"schema_version": 1, "kind": "offline_minimap_replay_alignment",
        "frame_selection":selection,
        "tool_source_sha256": _digest(Path(__file__))[0],
        "runtime_integration": False, "online_warning_enabled": False,
        "clock_policy": {"offset_s": 0, "fit_or_optimization_performed": False,
            "max_nearest_sample_delta_s": max_clock_delta_s,
            "gsi_integer_seconds_uncertainty_s": 1,
            "capture_time_interpolation_performed": False,
            "pause_disambiguation": "repeated_replay_game_clocks_refused_no_assumption_from_wall_time"},
        "visibility_policy": "replay_world_position_not_player_visibility_not_detection_ground_truth",
        "coordinates": {"translation_to_gsi_world": list(translation),
            "calibration_source": calibration_source, "rotation_or_scale_fit": False,
            "pixel_mapping": "stationary_tower_geometry_checked_per_frame" if geometry is not None else "not_calibrated"},
        "sources": {"replay_samples": replay_source, "context": context_source,
                    "stationary_map_calibration": geometry_source,
                    "visibility_transitions": visibility_audit.source if visibility_audit is not None else None,
                    "verified_context_sources": checked_sources},
        "counts": {**dict(counts), "frames": len(output), "unique_own_gsi_compared": len(own_unique)},
        "own_comparison_summary": {"median_world_distance_units": median(distances) if distances else None,
            "max_world_distance_units": max(distances) if distances else None,
            "alive_comparable": len(alive), "alive_disagreements": alive.count(False),
            "position_distance_comparable": len(distances),
            "both_alive": {"count": len(both_alive), "median_world_distance_units": median(both_alive) if both_alive else None,
                           "max_world_distance_units": max(both_alive) if both_alive else None},
            "both_dead": {"count": len(both_dead), "median_world_distance_units": median(both_dead) if both_dead else None},
            "meaning": "quantized_clock_consistency_check_not_exact_per_frame_position_accuracy"},
        "frames": output}


def calibrate_translation(report):
    """Propose a 128-unit cell-origin translation from a separate saved window.

    This proposes an origin offset only. A second, disjoint evaluation window
    must determine whether the fixed translation is consistent with real GSI.
    """
    if (report.get("kind") != "offline_minimap_replay_alignment"
            or report.get("coordinates", {}).get("translation_to_gsi_world") != [0, 0]):
        raise ValueError("alignment_requires_untranslated_calibration_report")
    unique, bounds = {}, {}
    for frame in report["frames"]:
        comparison, own = frame.get("own_comparison") or {}, frame.get("own_gsi") or {}
        if "replay" not in comparison:
            continue
        key = (own["source"]["source_id"], own["source"]["line"])
        unique[key] = comparison
        bounds.setdefault(frame["match_id"], []).append(own["clock_time_s"])
    if len(unique) < 5:
        raise ValueError("alignment_insufficient_calibration_pairs")
    medians = [median([row["raw_gsi_minus_replay_" + axis] for row in unique.values()]) for axis in ("x", "y")]
    translation = [round(value / 128) * 128 for value in medians]
    residuals = [math.hypot(row["raw_gsi_minus_replay_x"] - translation[0],
                            row["raw_gsi_minus_replay_y"] - translation[1]) for row in unique.values()]
    return {"schema_version": 1, "kind": "offline_replay_translation_calibration",
        "clock_offset_s": 0, "unique_own_gsi_pairs": len(unique),
        "game_clock_bounds_by_match": {match: [min(clocks), max(clocks)] for match, clocks in bounds.items()},
        "raw_offset_median_units": medians, "translation_to_gsi_world": translation,
        "origin_translation_basis": "independent_window_median_rounded_to_source_cell_width_128",
        "median_calibration_residual_units": median(residuals),
        "max_calibration_residual_units": max(residuals),
        "sources": report["sources"],
        "meaning": "candidate_origin_translation_not_independently_evaluated_until_disjoint_window"}
