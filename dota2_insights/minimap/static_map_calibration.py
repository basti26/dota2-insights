"""Offline map geometry from stationary tower squares, not hero name centers."""
from __future__ import annotations

import itertools
import hashlib
import json
import math
from pathlib import Path
import re

import cv2
import numpy as np


def tower_points(archive, team=None):
    rows = []
    for row in archive["landmarks"]:
        if (row["class_name"] == "CDOTA_BaseNPC_Tower"
                and re.fullmatch(r"dota_(?:goodguys|badguys)_tower[123]_(?:top|bot)", row["name"])
                and (team is None or row["team"] == team)):
            rows.append(row)
    if len(rows) != (12 if team is None else 6):
        raise ValueError("six_outer_lane_towers_per_team_required")
    return sorted(rows, key=lambda row: row["name"])


def square_candidates(image):
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim != 3 or image.shape[2] != 3
            or not 64 <= image.shape[0] <= 1024
            or not 64 <= image.shape[1] <= 1024
            or abs(image.shape[0] - image.shape[1]) > 2):
        raise ValueError("square_native_minimap_required")
    height, width = image.shape[:2]
    scale = width / 372
    blue, green, red = cv2.split(image)
    masks = {
        "green": (green > 180) & (red < 195) & (blue < 90)
                 & (green.astype(np.int16) - red.astype(np.int16) > 45),
        "red": (red > 180) & (green < 90) & (blue < 90),
    }
    found = []
    for color, mask in masks.items():
        _, _, stats, centers = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
        for stat, center in zip(stats[1:], centers[1:]):
            x, y, w, h, area = map(int, stat)
            if (8 * scale <= w <= 15 * scale and 8 * scale <= h <= 15 * scale
                    and .75 <= w / h <= 1.33 and area / (w * h) >= .8):
                found.append({"color": color, "pixel_x": float(center[0]), "pixel_y": float(center[1]),
                              "rect": [x, y, w, h], "fill_fraction": area / (w * h)})
    if len(found) > 64:
        raise ValueError("ambiguous_tower_square_count")
    return found


def project(model, x, y):
    return model["scale_x"] * x + model["offset_x"], model["scale_y"] * y + model["offset_y"]


def match_points(world, pixels, model, *, max_error_px=2):
    pairs = []
    for wi, row in enumerate(world):
        x, y = project(model, row["world_x"], row["world_y"])
        for pi, point in enumerate(pixels):
            error = math.hypot(point["pixel_x"] - x, point["pixel_y"] - y)
            if error <= max_error_px:
                pairs.append((error, wi, pi))
    used_world, used_pixels, matches = set(), set(), []
    for error, wi, pi in sorted(pairs):
        if wi in used_world or pi in used_pixels:
            continue
        used_world.add(wi)
        used_pixels.add(pi)
        row, point = world[wi], pixels[pi]
        x, y = project(model, row["world_x"], row["world_y"])
        matches.append({"name": row["name"], "team": row["team"],
                        "entity_index": row["entity_index"], "entity_serial": row["entity_serial"],
                        "world_x": row["world_x"], "world_y": row["world_y"],
                        "pixel_x": point["pixel_x"], "pixel_y": point["pixel_y"],
                        "predicted_x": x, "predicted_y": y, "error_px": error,
                        "candidate_rect": point["rect"]})
    return {"matches": matches, "matched": len(matches), "world_count": len(world),
            "pixel_candidates": len(pixels), "unmatched_world": [world[i]["name"] for i in range(len(world)) if i not in used_world],
            "max_error_px": max((row["error_px"] for row in matches), default=None),
            "rms_error_px": math.sqrt(sum(row["error_px"] ** 2 for row in matches) / len(matches)) if matches else None,
            "meaning": "stationary_landmark_correspondence_not_hero_detection_accuracy"}


def fit_tower_geometry(world, pixels, *, image_size):
    """Generate transforms on one team only, then refine its matched anchors.

    Opposite-team landmarks and later images must be evaluated by the caller
    with the frozen model. They are never used to choose or refine this model.
    """
    if len(world) != 6 or not 4 <= len(pixels) <= 12:
        raise ValueError("insufficient_or_ambiguous_training_anchors")
    candidates = []
    for first, second in itertools.combinations(world, 2):
        dx, dy = second["world_x"] - first["world_x"], second["world_y"] - first["world_y"]
        if abs(dx) < 2000 or abs(dy) < 2000:
            continue
        for left, right in itertools.permutations(pixels, 2):
            sx = (right["pixel_x"] - left["pixel_x"]) / dx
            sy = (right["pixel_y"] - left["pixel_y"]) / dy
            if not image_size / 24000 <= sx <= image_size / 12000 or not .9 <= -sy / sx <= 1.1:
                continue
            model = {"scale_x": sx, "offset_x": left["pixel_x"] - sx * first["world_x"],
                     "scale_y": sy, "offset_y": left["pixel_y"] - sy * first["world_y"]}
            evaluation = match_points(world, pixels, model)
            if evaluation["matched"] >= 4:
                candidates.append((evaluation, model))
    if not candidates:
        raise ValueError("no_supported_tower_transform")
    candidates.sort(key=lambda pair: (-pair[0]["matched"], pair[0]["rms_error_px"]))
    best, _ = candidates[0]
    pairs = best["matches"]
    model = {}
    for axis in ("x", "y"):
        design = np.array([[row["world_" + axis], 1] for row in pairs], dtype=np.float64)
        target = np.array([row["pixel_" + axis] for row in pairs], dtype=np.float64)
        slope, offset = np.linalg.lstsq(design, target, rcond=None)[0]
        model["scale_" + axis], model["offset_" + axis] = float(slope), float(offset)
    fitted = match_points(world, pixels, model)
    if fitted["matched"] != 6 or fitted["max_error_px"] > 1:
        raise ValueError("training_tower_fit_not_supported")
    best_names = {(row["name"], tuple(row["candidate_rect"])) for row in fitted["matches"]}
    for other, _ in candidates[1:]:
        if other["matched"] == 6 and other["rms_error_px"] <= fitted["rms_error_px"] + .25:
            if {(row["name"], tuple(row["candidate_rect"])) for row in other["matches"]} != best_names:
                raise ValueError("competing_tower_correspondence")
    return {"schema_version": 1, "kind": "axis_aligned_stationary_tower_geometry_candidate",
            "coordinate_basis": "gem_cell128_plus_vec_raw_coordinates",
            "matrix": model, "training": fitted, "image_size": [image_size, image_size],
            "rotation_or_shear_fitted": False, "heldout_points_used_for_fit": False,
            "live_integration": False, "hero_pixel_accuracy_verified": False,
            "validity": "same_crop_and_map_build_only_requires_independent_heldout_checks"}


def load_geometry(path, *, index_sha256):
    path = Path(path)
    with path.open("rb") as handle:
        body = handle.read(1024 ** 2 + 1)
    if not body or len(body) > 1024 ** 2:
        raise ValueError("bounded_map_calibration_required")
    model = json.loads(body)
    if (model.get("kind") != "axis_aligned_stationary_tower_geometry_candidate"
            or model.get("stationary_heldout_supported") is not True
            or model.get("source_index_sha256") != index_sha256
            or model.get("coordinate_basis") != "gem_cell128_plus_vec_raw_coordinates"
            or model.get("heldout_points_used_for_fit") is not False):
        raise ValueError("source_bound_heldout_map_calibration_required")
    values = model.get("matrix")
    if (not isinstance(values, dict) or set(values) != {"scale_x", "offset_x", "scale_y", "offset_y"}
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in values.values())
            or not 0 < values["scale_x"] < .1 or not -.1 < values["scale_y"] < 0):
        raise ValueError("invalid_axis_aligned_map_matrix")
    unique = {row["name"]: row for row in model["training"]["matches"]}
    for team in model["checks"][0]["teams"]:
        unique.update({row["name"]: row for row in team["matches"]})
    if len(unique) != 12 or {row["team"] for row in unique.values()} != {2, 3}:
        raise ValueError("twelve_source_bound_stationary_anchors_required")
    model["anchor_points"] = list(unique.values())
    return model, {"sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body),
                   "meaning": "stationary_landmark_geometry_not_hero_identity_or_visibility_labels"}


def check_frame_geometry(image, model, *, viewer_team):
    if list(image.shape[:2][::-1]) != model["image_size"] or viewer_team not in (2, 3):
        return {"status": "geometry_or_viewer_team_unknown", "supported": False}
    candidates = square_candidates(image)
    results = []
    for team in (2, 3):
        color = "green" if team == viewer_team else "red"
        results.append(match_points([p for p in model["anchor_points"] if p["team"] == team],
                                   [p for p in candidates if p["color"] == color],
                                   model["matrix"], max_error_px=1))
    matches = [point for result in results for point in result["matches"]]
    spread_x = max((p["pixel_x"] for p in matches), default=0) - min((p["pixel_x"] for p in matches), default=0)
    spread_y = max((p["pixel_y"] for p in matches), default=0) - min((p["pixel_y"] for p in matches), default=0)
    supported = len(matches) >= 4 and spread_x >= image.shape[1] * .4 and spread_y >= image.shape[0] * .4
    return {"status": "stationary_frame_geometry_supported" if supported else "insufficient_stationary_frame_support",
            "supported": supported, "matched_anchors": len(matches), "spread_px": [spread_x, spread_y],
            "max_error_px": max((p["error_px"] for p in matches), default=None),
            "hero_pixel_accuracy_verified": False}


def project_visible_window(timeline, model, window, *, viewer_team):
    """Reference points only; hidden coordinates never enter this projection."""
    from bisect import bisect_left, bisect_right
    if timeline is None:
        return {"status": "hero_not_in_replay_archive", "points": []}
    times, rows, _ = timeline
    start, end = window["start_game_time_s"], window["end_game_time_s"]
    fully_inside = bool(times) and start >= times[0] and end <= times[-1]
    selected = rows[bisect_left(times, start):bisect_right(times, end)]
    side = "radiant" if viewer_team == 2 else "dire"
    points, excluded = [], 0
    width, height = model["image_size"]
    for row in selected:
        if row.get("alive") is not True or row["visibility_raw"].get(side, {}).get("state") != "visible":
            excluded += 1
            continue
        x, y = project(model["matrix"], row["world_x"], row["world_y"])
        inside = 0 <= x < width and 0 <= y < height
        region = f"x{min(2, int(x / (width / 3)))}_y{min(2, int(y / (height / 3)))}" if inside else None
        points.append({"source_line": row["source_line"], "tick": row["tick"],
                       "game_time_s": row["game_time_s"], "pixel_x": x, "pixel_y": y,
                       "inside_native_map": inside, "coarse_pixel_region_3x3": region})
    return {"status": "projected_raw_visible_reference_window" if points else "no_raw_visible_reference_samples_in_window",
            "points": points, "excluded_hidden_dead_or_unknown": excluded,
            "search_window_inside_sample_archive": fully_inside,
            "sample_archive_game_clock_bounds": [times[0], times[-1]] if times else None,
            "sampled_positions_only": True, "all_possible_positions_covered": False,
            "possible_pixel_regions_3x3": sorted({p["coarse_pixel_region_3x3"] for p in points if p["coarse_pixel_region_3x3"]}),
            "exact_capture_position": None, "player_visibility_verified": False,
            "training_ready": False, "runtime_input": False,
            "meaning": "offline_quantized_time_reference_not_detected_hero_markers"}
