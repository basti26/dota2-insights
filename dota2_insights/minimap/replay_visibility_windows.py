"""Mine replay team-bit transitions for offline review, without live alerts.

The words visible/hidden below name the exporter's raw team-bit states. They
do not certify that the player's minimap displayed an icon or a readable name.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from .replay_alignment import _digest, _json, _number, _utc, read_replay_samples


def _point(row):
    if row is None:
        return None
    return {key: row.get(key) for key in ("source_line", "tick", "game_time_s", "world_x", "world_y", "alive")}


def _hidden_reference(row):
    return {key: row.get(key) for key in ("source_line", "tick", "game_time_s", "alive")}


def _event_category(inflictor):
    value = inflictor.lower()
    if any(part in value for part in ("smoke_of_deceit", "smoked")):
        return "smoke_related_event"
    if any(part in value for part in ("teleport", "tpscroll", "travel_boots", "relocate")):
        return "teleport_related_event"
    if any(part in value for part in ("wind_walk", "shadow_walk", "skeleton_walk", "invis", "silver_edge", "moonlight_shadow", "glimmer")):
        return "invisibility_related_event"
    return None


def read_event_candidates(replay_path):
    """Keep a small whitelist; old packet-clock events remain approximate."""
    path = Path(replay_path).with_name("events.jsonl")
    manifest, _ = _json(path.with_name("manifest.json"))
    expected = manifest.get("files", {}).get(path.name, {})
    sha, size = _digest(path)
    if expected.get("sha256") != sha or expected.get("bytes") != size:
        raise ValueError("visibility_event_source_changed")
    events, counts = [], Counter()
    with path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            if number > 300000 or len(line) > 256 * 1024:
                raise ValueError("visibility_event_source_limit")
            row = json.loads(line)
            if not isinstance(row, dict) or row.get("kind") != "combat_event":
                counts["other_events_skipped"] += 1
                continue
            if row.get("match_id") != manifest.get("match_id"):
                raise ValueError("visibility_event_match_mismatch")
            inflictor = row.get("inflictor")
            category = _event_category(inflictor) if isinstance(inflictor, str) and len(inflictor) <= 128 else None
            if category is None:
                continue
            event_clock = row.get("event_game_time_s")
            if _number(event_clock, 86400):
                clock, basis = event_clock, "exporter_reported_event_game_time_s"
                precise = False  # Integer event clocks still have quantization.
            elif _number(row.get("game_time_s"), 86400):
                clock, basis = row["game_time_s"], "packet_clock_only_not_exact_event_occurrence"
                precise = False
            else:
                counts["relevant_events_without_clock"] += 1
                continue
            actors = [row.get(key) for key in ("attacker", "target")]
            actors = [value for value in actors if isinstance(value, str) and value.startswith("npc_dota_hero_")
                      and len(value) <= 64 and all(character.isalnum() or character == "_" for character in value)]
            if not actors:
                continue
            events.append({"source_line": number, "category": category, "clock_for_coarse_join_s": clock,
                "time_basis": basis, "exact_event_time_verified": precise,
                "packet_game_time_s": row.get("game_time_s"),
                "event_game_time_s": event_clock if _number(event_clock, 86400) else None,
                "tick": row.get("tick") if type(row.get("tick")) is int else None,
                "event_type": row.get("event_type") if isinstance(row.get("event_type"), str) and len(row["event_type"]) <= 64 else None,
                "inflictor": inflictor, "hero_actors": sorted(set(actors)),
                "meaning": "nearby_event_candidate_not_proof_of_hidden_cause"})
            counts[basis] += 1
    if _digest(path) != (sha, size):
        raise ValueError("visibility_event_changed_during_read")
    return events, {"sha256": sha, "bytes": size, "relevant_events": len(events), "counts": dict(counts)}


def read_active_bounds(*, context_path, self_samples, match_id):
    """Bound archive review by explicitly recorded main-session phase evidence."""
    context_path, self_samples = Path(context_path), Path(self_samples)
    sources = {}
    for label, path in (("main_context", context_path), ("self_samples", self_samples)):
        summary, summary_hash = _json(path.with_name("recording-summary.json"))
        if (not summary.get("closed_at_utc") or summary.get("evidence_queue_drained") is not True
                or summary.get("run_recording_status", {}).get("error") is not None):
            raise ValueError("visibility_main_evidence_not_closed")
        sha, size = _digest(path)
        sources[label] = {"sha256": sha, "bytes": size, "summary": summary_hash,
                          "evidence_complete": summary.get("evidence_complete")}
    states = []
    with context_path.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            if number > 10000 or len(line) > 256 * 1024:
                raise ValueError("visibility_main_context_limit")
            row = json.loads(line)
            if row.get("match_id") == match_id and row.get("record_session_id") == context_path.parent.name:
                if row.get("game_state") in ("DOTA_GAMERULES_STATE_GAME_IN_PROGRESS", "DOTA_GAMERULES_STATE_POST_GAME"):
                    states.append({"source_line": number, "recorded_at_utc": row["recorded_at_utc"],
                        "provider_at_utc": row["provider_at_utc"], "game_state": row["game_state"]})
    active = [row for row in states if row["game_state"] == "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"]
    if not active:
        raise ValueError("visibility_no_main_in_progress_evidence")
    start = min(active, key=lambda row: _utc(row["provider_at_utc"]))
    post = [row for row in states if row["game_state"] == "DOTA_GAMERULES_STATE_POST_GAME"
            and _utc(row["provider_at_utc"]) > _utc(start["provider_at_utc"])]
    if not post:
        raise ValueError("visibility_no_main_post_game_boundary")
    end = min(post, key=lambda row: _utc(row["provider_at_utc"]))
    clock_rows = []
    with self_samples.open("rb") as handle:
        for number, line in enumerate(handle, 1):
            if number > 100000 or len(line) > 32 * 1024:
                raise ValueError("visibility_self_source_limit")
            row = json.loads(line)
            if (row.get("match_id") != match_id or row.get("record_session_id") != context_path.parent.name
                    or row.get("event_kind") != "self_economy" or not _number(row.get("clock_time_s"), 86400)):
                continue
            provider, received = _utc(row["provider_at_utc"]), _utc(row["received_at_utc"])
            if (_utc(start["provider_at_utc"]) <= provider < _utc(end["provider_at_utc"])
                    and _utc(start["recorded_at_utc"]) <= received < _utc(end["recorded_at_utc"])):
                clock_rows.append({"source_line": number, "clock_time_s": row["clock_time_s"],
                                   "provider_at_utc": row["provider_at_utc"]})
    if not clock_rows:
        raise ValueError("visibility_no_self_clocks_within_active_boundary")
    for label, path in (("main_context", context_path), ("self_samples", self_samples)):
        if _digest(path) != (sources[label]["sha256"], sources[label]["bytes"]):
            raise ValueError("visibility_main_evidence_changed")
    return {"clock_min_s": min(row["clock_time_s"] for row in clock_rows),
        "clock_max_s": max(row["clock_time_s"] for row in clock_rows),
        "start_phase_evidence": start, "post_game_phase_evidence": end,
        "first_clock_evidence": clock_rows[0], "last_clock_evidence": clock_rows[-1],
        "sources": sources,
        "meaning": "recorded_main_phase_bounds_with_gsi_quantization_not_exact_match_start_end_ticks"}


def read_self_active_bounds(path, match_id):
    """Explicit fallback for newer runs without the old main context journal."""
    path=Path(path)
    summary,summary_hash=_json(path.with_name('recording-summary.json'))
    if (not summary.get('closed_at_utc') or summary.get('evidence_queue_drained') is not True
            or summary.get('run_recording_status',{}).get('error') is not None):
        raise ValueError('visibility_self_evidence_not_closed')
    checksum,size=_digest(path)
    active,post,sessions=[],[],set()
    with path.open('rb') as stream:
        for number,line in enumerate(stream,1):
            if number>100000 or len(line)>32*1024:raise ValueError('visibility_self_source_limit')
            row=json.loads(line)
            if row.get('event_kind')!='self_economy' or row.get('match_id')!=match_id:continue
            clock=row.get('clock_time_s'); state=(row.get('map_context') or {}).get('game_state')
            if not _number(clock,86400):continue
            provider,received=_utc(row['provider_at_utc']),_utc(row['received_at_utc'])
            if not 0 <= (received-provider).total_seconds() <= 3:continue
            sessions.add((row.get('game_session_id'),row.get('record_session_id')))
            item={'source_line':number,'clock_time_s':clock,'provider_at_utc':row['provider_at_utc'],
                  'recorded_at_utc':row['received_at_utc'],'game_state':state}
            if state=='DOTA_GAMERULES_STATE_GAME_IN_PROGRESS':active.append(item)
            elif state=='DOTA_GAMERULES_STATE_POST_GAME':post.append(item)
    if len(sessions)!=1 or any(not value for value in next(iter(sessions),())) or not active or not post:
        raise ValueError('visibility_self_unique_closed_match_phases_required')
    start=min(active,key=lambda r:_utc(r['provider_at_utc']))
    end=min((r for r in post if _utc(r['provider_at_utc'])>_utc(start['provider_at_utc'])),
            key=lambda r:_utc(r['provider_at_utc']),default=None)
    if end is None:raise ValueError('visibility_self_post_game_boundary_missing')
    active=[r for r in active if _utc(r['provider_at_utc'])<_utc(end['provider_at_utc'])]
    if _digest(path)!=(checksum,size):raise ValueError('visibility_self_evidence_changed')
    return {'clock_min_s':min(r['clock_time_s'] for r in active),'clock_max_s':max(r['clock_time_s'] for r in active),
        'start_phase_evidence':start,'post_game_phase_evidence':end,
        'sources':{'self_samples':{'sha256':checksum,'bytes':size,'summary':summary_hash}},
        'meaning':'closed_self_GSI_phase_bounds_not_legacy_main_context_or_exact_ticks'}


def prepare_windows(*, replay_path, player_team, max_sample_gap_s=2.5,
                    minimum_hidden_s=3.0, assumed_walk_speed=800.0,
                    game_context=None, self_samples=None):
    if type(player_team) is not int or player_team not in (2, 3):
        raise ValueError("visibility_player_team_required")
    if (not _number(max_sample_gap_s, 10) or not .1 <= max_sample_gap_s <= 10
            or not _number(minimum_hidden_s, 600) or minimum_hidden_s < 0
            or not _number(assumed_walk_speed, 2000) or not 100 <= assumed_walk_speed <= 2000):
        raise ValueError("visibility_options_out_of_bounds")
    rows, source = read_replay_samples(replay_path)
    events, event_source = read_event_candidates(replay_path)
    if game_context is not None and self_samples is None:
        raise ValueError("visibility_active_bounds_need_both_main_sources")
    active_bounds = (read_active_bounds(context_path=game_context, self_samples=self_samples,
                     match_id=source["original_replay"]["match_id"]) if game_context is not None else
                     read_self_active_bounds(self_samples,source['original_replay']['match_id']) if self_samples is not None else None)
    side = "radiant" if player_team == 2 else "dire"
    heroes, state_counts = defaultdict(list), Counter()
    for row in rows:
        if active_bounds is not None and not active_bounds["clock_min_s"] <= row["game_time_s"] <= active_bounds["clock_max_s"]:
            continue
        if row["team"] == player_team:
            continue
        state = row["visibility_raw"].get(side, {}).get("state", "unknown")
        state_counts[state] += 1
        heroes[row["hero_id"]].append(row)
    if not heroes or len(heroes) > 5:
        raise ValueError("visibility_enemy_roster_invalid")
    windows, transition_counts = [], Counter()
    for hero, samples in sorted(heroes.items()):
        samples.sort(key=lambda row: row["tick"])
        active, last_visible, previous = None, None, None

        def finish(reappeared, boundary_reason):
            nonlocal active
            if active is None:
                return
            first, last = active["hidden_samples"][0], active["hidden_samples"][-1]
            start = active["last_visible"]
            duration = max(0, last["game_time_s"] - first["game_time_s"])
            boundary_complete = start is not None and reappeared is not None
            life = [sample["alive"] for sample in active["hidden_samples"]]
            if any(value is False for value in life) or (start and start["alive"] is False) or (reappeared and reappeared["alive"] is False):
                life_class = "contains_dead_state"
            elif (any(value is not True for value in life)
                    or start is not None and start["alive"] is not True
                    or reappeared is not None and reappeared["alive"] is not True):
                life_class = "life_state_unknown"
            else:
                life_class = "all_hidden_samples_alive"
            eligible = boundary_complete and boundary_reason == "raw_bit_visible_again" and life_class == "all_hidden_samples_alive"
            transition_counts[boundary_reason] += 1
            if duration < minimum_hidden_s:
                transition_counts["below_minimum_duration_omitted"] += 1
                active = None
                return
            interval_start = start["game_time_s"] if start else first["game_time_s"]
            interval_end = reappeared["game_time_s"] if reappeared else last["game_time_s"]
            nearby = [event for event in events if hero in event["hero_actors"]
                      and interval_start - 2 <= event["clock_for_coarse_join_s"] <= interval_end + 2]
            elapsed = interval_end - interval_start
            displacement = math.hypot(reappeared["world_x"] - start["world_x"],
                                      reappeared["world_y"] - start["world_y"]) if boundary_complete else None
            windows.append({"hero_id": hero, "team": samples[0]["team"], "player_perspective_team": player_team,
                "last_visible": _point(start), "first_hidden": _hidden_reference(first), "last_hidden": _hidden_reference(last),
                "reappeared": _point(reappeared), "hidden_sample_count": len(active["hidden_samples"]),
                "training_ready": False,
                "feature_policy": "hidden_positions_excluded_last_visible_only_future_reappearance_is_evaluation_target",
                "hidden_observed_span_lower_bound_s": round(duration, 6),
                "disappearance_boundary_game_time_s": [start["game_time_s"], first["game_time_s"]] if start else None,
                "reappearance_boundary_game_time_s": [last["game_time_s"], reappeared["game_time_s"]] if reappeared else None,
                "endpoint_elapsed_s": round(elapsed, 6),
                "boundary_reason": boundary_reason, "life_state_class": life_class,
                "both_transition_boundaries_observed": boundary_complete,
                "raw_transition_candidate_eligible_for_image_review": eligible,
                "straight_line_endpoint_displacement_units": round(displacement, 3) if displacement is not None else None,
                "exceeds_assumed_constant_walk_speed": displacement > assumed_walk_speed * elapsed
                    if displacement is not None and elapsed > 0 else None,
                "movement_meaning": "endpoint_lower_bound_only_mobility_abilities_tp_respawn_and_coordinate_errors_possible",
                "nearby_event_candidates": nearby[:100], "nearby_events_truncated": len(nearby) > 100,
                "event_feature_policy": "omniscient_replay_events_for_postgame_review_not_live_features",
                "cause": None, "player_minimap_visibility_verified": False,
                "live_warning_eligible": False})
            active = None

        for row in samples:
            state = row["visibility_raw"].get(side, {}).get("state", "unknown")
            gap = previous is not None and (row["game_time_s"] <= previous["game_time_s"]
                                            or row["game_time_s"] - previous["game_time_s"] > max_sample_gap_s)
            if gap:
                finish(None, "sample_gap_or_repeated_game_clock")
                last_visible = None
                transition_counts["sample_gap_or_repeated_clock"] += 1
            if state == "hidden":
                if active is None:
                    active = {"last_visible": last_visible, "hidden_samples": []}
                active["hidden_samples"].append(row)
            elif state == "visible":
                finish(row, "raw_bit_visible_again")
                last_visible = row
            else:
                finish(None, "unknown_visibility_state")
                last_visible = None
                transition_counts["unknown_visibility_samples"] += 1
            previous = row
        finish(None, "archive_or_requested_window_end")
    counts = Counter(window["life_state_class"] for window in windows)
    review = [window for window in windows if window["raw_transition_candidate_eligible_for_image_review"]]
    if _digest(replay_path) != (source["sha256"], source["bytes"]):
        raise ValueError("visibility_replay_changed_during_join")
    return {"schema_version": 1, "kind": "offline_replay_raw_visibility_candidates",
        "runtime_integration": False, "online_warning_enabled": False,
        "player_perspective_team": player_team, "visibility_semantics_verified": False,
        "training_ready": False, "hidden_positions_exported": False,
        "sources": {"hero_samples": source, "events": event_source},
        "main_in_progress_bounds": active_bounds,
        "postgame_tail_excluded": active_bounds is not None,
        "options": {"max_sample_gap_s": max_sample_gap_s, "minimum_hidden_s": minimum_hidden_s,
            "assumed_walk_speed_units_per_s": assumed_walk_speed,
            "walk_speed_basis": "illustrative_constant_ceiling_not_verified_hero_or_item_stats",
            "event_join_padding_s": 2, "event_join_meaning": "coarse_candidate_association_no_causal_claim"},
        "counts": {"enemy_hero_samples": sum(state_counts.values()), "raw_states": dict(state_counts),
            "windows": len(windows), "life_state_classes": dict(counts),
            "complete_alive_raw_transition_candidates": len(review),
            "exceeds_assumed_walk_speed_candidates": sum(window["exceeds_assumed_constant_walk_speed"] is True for window in review),
            "transitions": dict(transition_counts)},
        "limitations": ["Raw team bits require comparison with original player minimap captures.",
            "Replay hidden positions are not inputs to a live assistant or human visibility labels.",
            "Invisibility, Shadow Blade, smoke and ordinary fog are normal; cause remains unknown.",
            "Movement thresholds cannot distinguish teleport from hero abilities or respawn.",
            "Old exports only retain packet clock for combat events; exact event timing is unverified.",
            "This mines review candidates and does not measure detection or warning accuracy."],
        "windows": windows}
