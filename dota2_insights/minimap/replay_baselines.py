"""Offline coarse-region proxy baselines from past visible samples only.

Neither this module nor its outputs are imported by the live application.
Raw replay team bits have not been validated against player minimap images.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import time

SCHEMA_VERSION = 1
MAX_SOURCE_BYTES = 512 * 1024 ** 2
GRID_MIN, GRID_MAX = 8192.0, 24576.0
HORIZONS = (10.0, 20.0)
MAX_GAP_S = 2.5
PRIOR_MIN_AGE_S, PRIOR_MAX_AGE_S = 1.0, 5.0
BASE_SPEED_MARGIN = 150.0
MISSING_BASE_SPEED_CAP = 550.0
_HERO = re.compile(r"npc_dota_hero_[a-z0-9_]{1,50}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")


def _number(value):
    return type(value) is int or (type(value) is float and math.isfinite(value))


def _mapping(value):
    if not isinstance(value, dict):
        raise ValueError('baseline_metadata_object_required')
    return value


def _visibility_state(raw, team_key):
    value = raw.get('visibility')
    if value is None:
        return None
    state = _mapping(_mapping(value).get(team_key, {})).get('state')
    if state is not None and not isinstance(state, str):
        raise ValueError('baseline_visibility_state_invalid')
    return state


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _read_object(path, limit=16 * 1024 ** 2):
    path = Path(path)
    with path.open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError("baseline_object_size_limit")
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("baseline_object_required")
    return value, {"file_name": path.name, "bytes": len(content),
                   "sha256": hashlib.sha256(content).hexdigest()}


def _grid(x, y):
    if not (_number(x) and _number(y)):
        return None
    if not (GRID_MIN <= x <= GRID_MAX and GRID_MIN <= y <= GRID_MAX):
        return "outside_grid"
    width = (GRID_MAX - GRID_MIN) / 3
    col = min(2, int((x - GRID_MIN) / width))
    row = min(2, int((y - GRID_MIN) / width))
    return f"x{col}_y{row}"


def read_base_speeds(path):
    """Use an already-downloaded, hashed public heroStats envelope only."""
    if path is None:
        return {}, {"status": "not_provided", "network_used": False}
    raw, source = _read_object(path, 4 * 1024 ** 2)
    payload = raw.get("payload")
    if (raw.get("schema_version") != 1 or raw.get("provider") != "opendota_raw"
            or raw.get("source_url") != "https://api.opendota.com/api/heroStats"
            or not isinstance(payload, list) or not 1 <= len(payload) <= 256
            or hashlib.sha256(_canonical(payload)).hexdigest() != raw.get("payload_sha256")):
        raise ValueError("baseline_hashed_hero_stats_required")
    speeds = {}
    for hero in payload:
        name, speed = hero.get("name"), hero.get("move_speed")
        if (not isinstance(name, str) or not _HERO.fullmatch(name)
                or name in speeds or not _number(speed) or not 100 <= speed <= 600):
            raise ValueError("baseline_hero_speed_schema_invalid")
        speeds[name] = speed
    return speeds, {**source, "status": "local_hashed_hero_stats", "heroes": len(speeds),
                    "fetched_at_utc": raw.get("fetched_at_utc"), "network_used": False,
                    "meaning": "base_move_speed_not_current_speed_or_mobility_bound"}


def _visible_source(directory, candidates, guard):
    """Drop hidden positions before constructing any feature/target store."""
    directory = Path(directory)
    manifest, manifest_source = _read_object(directory / "manifest.json", 1024 ** 2)
    expected = _mapping(_mapping(manifest.get('files', {})).get('hero_samples.jsonl', {}))
    candidate_source = _mapping(_mapping(candidates.get('sources', {})).get('hero_samples', {}))
    if (manifest.get("kind") != "offline_replay_archive"
            or manifest.get("source_unchanged_stat") is not True
            or manifest.get("parse_error") is not None
            or manifest.get("truncated_at_tick") is not None
            or manifest.get("stop_reason") not in ("replay_eof", "requested_game_window_end")
            or manifest.get("parsed_match_id") != manifest.get("match_id")
            or not _SHA.fullmatch(str(manifest.get("source_sha256", "")))
            or expected.get("sha256") != candidate_source.get("sha256")
            or expected.get("bytes") != candidate_source.get("bytes")
            or _mapping(candidate_source.get('manifest', {})).get("sha256") != manifest_source["sha256"]):
        raise ValueError("baseline_replay_manifest_binding_invalid")
    match_id = manifest["match_id"]
    if _mapping(candidate_source.get('original_replay', {})).get('match_id') != match_id:
        raise ValueError("baseline_candidate_match_mismatch")
    perspective = candidates.get("player_perspective_team")
    if type(perspective) is not int or perspective not in (2, 3):
        raise ValueError("baseline_perspective_team_invalid")
    team_key = "radiant" if perspective == 2 else "dire"
    bounds = _mapping(candidates.get("main_in_progress_bounds") or {})
    lower, upper = bounds.get("clock_min_s"), bounds.get("clock_max_s")
    if (candidates.get("postgame_tail_excluded") is not True
            or not _number(lower) or not _number(upper) or not 0 <= lower < upper <= 10800):
        raise ValueError("baseline_in_progress_phase_bounds_required")
    previous, segments, store, counts = {}, defaultdict(int), defaultdict(list), Counter()
    path = directory / "hero_samples.jsonl"
    signature = path.stat().st_size, path.stat().st_mtime_ns
    checksum, size = hashlib.sha256(), 0
    with path.open("rb") as handle:
        for number in range(1, 200001):
            if number % 2048 == 1:
                guard()
            line = handle.readline(256 * 1024 + 1)
            if not line:
                break
            size += len(line)
            if size > MAX_SOURCE_BYTES or len(line) > 256 * 1024 or not line.endswith(b"\n"):
                raise ValueError("baseline_replay_source_limit")
            checksum.update(line)
            raw = _mapping(json.loads(line))
            hero, tick, clock = raw.get("hero_id"), raw.get("tick"), raw.get("game_time_s")
            if (raw.get("kind") != "hero_sample" or raw.get("match_id") != match_id
                    or type(raw.get('schema_version')) is not int
                    or raw.get("schema_version") != 1 or not isinstance(hero, str)
                    or not _HERO.fullmatch(hero) or type(tick) is not int or tick < 0
                    or not _number(clock) or raw.get("game_time_basis") != "gem_pause_aware_engine_game_clock"
                    or raw.get("coordinate_basis") != "gem_cell128_plus_vec_raw_coordinates"
                    or type(raw.get("team")) is not int or raw["team"] not in (2, 3)):
                raise ValueError("baseline_replay_row_schema_invalid")
            prev = previous.get(hero)
            if prev and tick <= prev[0]:
                raise ValueError("baseline_replay_ticks_not_increasing")
            broken = prev is not None and not 0 < clock - prev[1] <= MAX_GAP_S
            previous[hero] = tick, clock
            if raw.get("team") == perspective:
                continue
            if not lower <= clock <= upper:
                segments[hero] += 1
                counts["outside_recorded_in_progress_bounds_dropped"] += 1
                continue
            counts["enemy_samples"] += 1
            state = _visibility_state(raw, team_key)
            if (state != "visible" or raw.get("alive") is not True
                    or raw.get("paused") is not False or broken):
                segments[hero] += 1
            if state != "visible" or raw.get("alive") is not True or raw.get("paused") is not False:
                # In particular, no hidden x/y is copied or used as a feature.
                counts["not_visible_alive_unpaused_dropped"] += 1
                continue
            x, y = raw.get("world_x"), raw.get("world_y")
            if not _number(x) or not _number(y):
                segments[hero] += 1
                counts["visible_coordinates_unknown"] += 1
                continue
            store[hero].append({"source_line": number, "tick": tick, "game_time_s": clock,
                "world_x": x, "world_y": y, "segment": segments[hero], "alive": True})
            counts["visible_alive_unpaused_retained"] += 1
        else:
            raise ValueError("baseline_replay_row_limit")
    if (checksum.hexdigest() != expected.get("sha256") or size != expected.get("bytes")
            or signature != (path.stat().st_size, path.stat().st_mtime_ns)):
        raise ValueError("baseline_replay_source_changed_or_checksum_mismatch")
    return store, {"match_id": match_id, "hero_samples": expected,
        "manifest": manifest_source, "original_replay_sha256": manifest["source_sha256"],
        "parser": manifest.get("parser"), "main_in_progress_bounds": bounds,
        "counts": dict(counts)}


def _endpoint(point, visible_index):
    if not isinstance(point, dict) or type(point.get("tick")) is not int:
        return None
    row = visible_index.get(point["tick"])
    if row is None or any(point.get(key) != row[key] for key in
                          ("source_line", "game_time_s", "world_x", "world_y", "alive")):
        raise ValueError("baseline_visible_endpoint_source_mismatch")
    return row


def evaluate_case(directory, candidate_path, speeds, guard):
    candidates, candidate_source = _read_object(candidate_path)
    if (candidates.get("kind") != "offline_replay_raw_visibility_candidates"
            or candidates.get("schema_version") != 1 or candidates.get("training_ready") is not False
            or candidates.get("hidden_positions_exported") is not False
            or candidates.get("visibility_semantics_verified") is not False
            or candidates.get("runtime_integration") is not False):
        raise ValueError("baseline_provisional_position_filtered_candidates_required")
    windows = candidates.get("windows")
    if not isinstance(windows, list) or len(windows) > 5000:
        raise ValueError("baseline_candidate_window_limit")
    visible, source = _visible_source(directory, candidates, guard)
    indices = {hero: {row["tick"]: row for row in rows} for hero, rows in visible.items()}
    offsets = {hero: {row["tick"]: i for i, row in enumerate(rows)} for hero, rows in visible.items()}
    excluded, results = Counter(), []
    metrics = {str(int(h)): {name: Counter() for name in ("B0", "B1_fixed", "B1_event_conditioned")}
               for h in HORIZONS}
    for number, window in enumerate(windows, 1):
        guard()
        for hidden in (window.get("first_hidden"), window.get("last_hidden")):
            if isinstance(hidden, dict) and ("world_x" in hidden or "world_y" in hidden):
                raise ValueError("baseline_hidden_coordinate_leak_in_candidates")
        if window.get("raw_transition_candidate_eligible_for_image_review") is not True:
            excluded["not_complete_alive_raw_transition_candidate"] += 1
            continue
        hero = window.get("hero_id")
        index = indices.get(hero, {})
        last = _endpoint(window.get("last_visible"), index)
        target = _endpoint(window.get("reappeared"), index)
        if last is None or target is None:
            excluded["visible_endpoint_not_alive_unpaused"] += 1
            continue
        elapsed = target["game_time_s"] - last["game_time_s"]
        if elapsed <= 0:
            raise ValueError("baseline_target_must_follow_feature")
        history = visible[hero]
        position = offsets[hero][last["tick"]]
        prior = None
        for point in reversed(history[max(0, position - 60):position]):
            age = last["game_time_s"] - point["game_time_s"]
            if point["segment"] != last["segment"] or age > PRIOR_MAX_AGE_S:
                break
            if age >= PRIOR_MIN_AGE_S:
                prior = point
                break
        vx = vy = None
        base = speeds.get(hero)
        cap = base + BASE_SPEED_MARGIN if base is not None else MISSING_BASE_SPEED_CAP
        observed_speed = None
        if prior is not None:
            dt = last["game_time_s"] - prior["game_time_s"]
            vx = (last["world_x"] - prior["world_x"]) / dt
            vy = (last["world_y"] - prior["world_y"]) / dt
            observed_speed = math.hypot(vx, vy)
            if observed_speed > cap:
                vx, vy = vx * cap / observed_speed, vy * cap / observed_speed
        b0, truth = _grid(last["world_x"], last["world_y"]), _grid(target["world_x"], target["world_y"])
        row = {"candidate_window_number": number, "hero_id": hero, "last_visible": last,
            "prior_visible_feature": prior, "reappeared_target_only": target,
            "target_elapsed_s": elapsed, "base_move_speed": base,
            "velocity_cap_units_per_s": cap, "observed_past_speed": observed_speed,
            "clipped_velocity": None if vx is None else [vx, vy],
            "last_visible_grid": b0, "target_grid": truth, "horizons": {}}
        for horizon in HORIZONS:
            bucket = metrics[str(int(horizon))]
            inside_budget = elapsed <= horizon
            prediction = {"target_visible_within_budget": inside_budget, "B0": b0,
                "B1_fixed": None if vx is None else _grid(last["world_x"] + vx * horizon,
                                                         last["world_y"] + vy * horizon),
                "B1_event_conditioned": None if vx is None else _grid(last["world_x"] + vx * elapsed,
                                                                     last["world_y"] + vy * elapsed)}
            row["horizons"][str(int(horizon))] = prediction
            for name, counter in bucket.items():
                counter["complete_alive_event_windows"] += 1
                if not inside_budget:
                    counter["no_reappearance_target_within_budget"] += 1
                elif truth in (None, "outside_grid") or b0 in (None, "outside_grid"):
                    counter["target_or_last_visible_outside_illustrative_grid"] += 1
                elif prediction[name] is None:
                    counter["past_visible_velocity_unavailable"] += 1
                else:
                    counter["scored_proxy_events"] += 1
                    counter["predicted_outside_grid"] += prediction[name] == "outside_grid"
                    counter["same_grid_as_reappearance_proxy"] += prediction[name] == truth
        results.append(row)
    summary = {}
    for horizon, values in metrics.items():
        summary[horizon] = {}
        for name, values_counter in values.items():
            total = values_counter["scored_proxy_events"]
            summary[horizon][name] = {**dict(values_counter),
                "same_grid_proxy_fraction": values_counter["same_grid_as_reappearance_proxy"] / total if total else None}
    paired = {}
    for horizon in metrics:
        pairs = [row for row in results if row["horizons"][horizon]["target_visible_within_budget"]
                 and row["target_grid"] not in (None, "outside_grid")
                 and row["last_visible_grid"] not in (None, "outside_grid")
                 and row["horizons"][horizon]["B1_fixed"] is not None]
        paired[horizon] = {"same_input_event_count": len(pairs), **{
            name: sum(row["horizons"][horizon][name] == row["target_grid"] for row in pairs)
            for name in ("B0", "B1_fixed", "B1_event_conditioned")}}
    return {"sources": {**source, "candidates": candidate_source}, "excluded": dict(excluded),
            "summary": summary, "paired_same_input_events": paired, "events": results}


def evaluate(cases, *, hero_stats=None, guard=lambda: None, max_wall_seconds=120):
    """Read existing archives; never decode a replay or perform network I/O."""
    if not 1 <= len(cases) <= 4:
        raise ValueError("baseline_case_count_invalid")
    started = time.monotonic()
    def bounded_guard():
        guard()
        if time.monotonic() - started > max_wall_seconds:
            raise ValueError("baseline_wall_time_limit")
    speeds, speed_source = read_base_speeds(hero_stats)
    results = [evaluate_case(directory, candidate, speeds, bounded_guard)
               for directory, candidate in cases]
    return {"schema_version": SCHEMA_VERSION, "kind": "offline_replay_coarse_region_baseline_proxy",
        "training_ready": False, "visibility_semantics_verified": False,
        "runtime_integration": False, "online_warning_enabled": False,
        "parameters_fixed_before_reading_results": {"horizons_s": HORIZONS,
            "grid_raw_coordinate_bounds": [GRID_MIN, GRID_MAX, GRID_MIN, GRID_MAX], "grid_size": [3, 3],
            "max_visible_sample_gap_s": MAX_GAP_S, "prior_age_s": [PRIOR_MIN_AGE_S, PRIOR_MAX_AGE_S],
            "base_speed_margin": BASE_SPEED_MARGIN, "missing_base_speed_cap": MISSING_BASE_SPEED_CAP},
        "methods": {"B0": "last visible grid",
            "B1_fixed": "past visible velocity extrapolated to fixed 10/20 second budget",
            "B1_event_conditioned": "same past velocity extrapolated using known reappearance elapsed time; hindsight proxy only"},
        "hero_speed_source": speed_source, "cases": results, "elapsed_seconds": time.monotonic() - started,
        "limitations": ["No player-minimap ground truth; fractions are raw-bit pipeline proxies, not detection/prediction accuracy.",
            "Both games are descriptive development data; no train/holdout claim or parameter tuning on either game.",
            "Only past visible/alive/unpaused samples are feature inputs; hidden world positions never enter feature stores.",
            "Candidate selection itself requires future reappearance and alive evidence; this is not an online alert cohort.",
            "First visible-again position is the target. No reappearance within budget is unscored, not a correct negative.",
            "Fixed-horizon position versus first reappearance within budget is an event-conditioned proxy, not position truth at the horizon.",
            "3x3 bounds are fixed illustrative coordinates, not calibrated playable boundaries, lanes or minimap pixels.",
            "Out-of-grid predicted positions count as misses on in-grid targets; missing velocity coverage is reported separately.",
            "Base speed plus margin is a preset extrapolation heuristic, not a valid upper bound with abilities/items/TP.",
            "No warning, role assignment, current hidden-position estimate, model training or live assistant integration."]}
