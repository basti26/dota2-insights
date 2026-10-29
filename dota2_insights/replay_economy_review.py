"""Offline item-ID comparison against a separate, source-bound replay archive."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
from pathlib import Path

from .replay_holdout import refuse_locked_match


def _rows(path, *, limit=256 * 1024 ** 2):
    checksum, size = hashlib.sha256(), 0
    with Path(path).open("rb") as handle:
        for number in range(1, 200001):
            line = handle.readline(256 * 1024 + 1)
            if not line:
                return checksum.hexdigest()
            size += len(line)
            if len(line) > 256 * 1024 or size > limit or not line.endswith(b"\n"):
                raise ValueError("bounded_replay_review_source_required")
            checksum.update(line)
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("review_source_row_must_be_object")
            yield number, value
        raise ValueError("review_source_row_limit")


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _item(value):
    name = value.removeprefix("item_") if isinstance(value, str) else None
    return name if name and re.fullmatch(r"[a-z0-9_]+", name) else None


def _usable_slot(status, item_key, occupied_status):
    return (status == "empty" and item_key is None
            or status == occupied_status and item_key is not None)


def _source(path):
    with Path(path).open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return {"file_name": Path(path).name, "sha256": digest}


def _slot_number(slot):
    name = slot.get("slot_id", "")
    if not isinstance(name, str):
        return None
    if name in {f"main_{i}" for i in range(1, 7)}:
        return int(name[-1]) - 1
    if name in {f"backpack_{i}" for i in range(1, 4)}:
        return int(name[-1]) + 5
    return None


def _pause_clocks(clock):
    """Reproduce only freeze points from the archive's fixed clock anchors."""
    pauses = clock.get("pauses") or []
    if not pauses:
        return []
    if _number(clock.get("game_start_time_s")) is not None:
        base = clock["game_start_time_s"] * 30 - clock.get("net_tick_offset", 0)
    elif type(clock.get("game_start_tick")) is int:
        start = clock["game_start_tick"]
        prior = sum(max(0, min(start, p.get("end_tick") or start) - p["start_tick"])
                    for p in pauses if p["start_tick"] < start)
        base = start - prior
    else:
        raise ValueError("paused_archive_has_no_clock_anchor")
    elapsed, result = 0, []
    for pause in pauses:
        result.append((pause["start_tick"] - elapsed - base) / 30)
        if pause.get("end_tick") is not None:
            elapsed += pause["end_tick"] - pause["start_tick"]
    return result


def compare(observations, samples, *, lines=(), tolerance_s=1.0, max_observations=1000,
            manifest=None, max_clock_age_s=1.0):
    if (_number(tolerance_s) is None or not 0 <= tolerance_s <= 3
            or type(max_observations) is not int or not 1 <= max_observations <= 2000
            or _number(max_clock_age_s) is None or not 0 <= max_clock_age_s <= 2):
        raise ValueError("invalid_economy_review_limits")
    samples = Path(samples)
    if samples.stat().st_size > 256 * 1024 ** 2 or Path(observations).stat().st_size > 128 * 1024 ** 2:
        raise ValueError("bounded_replay_review_source_required")
    manifest = Path(manifest) if manifest is not None else samples.parent / "manifest.json"
    if manifest.stat().st_size > 1024 ** 2:
        raise ValueError("bounded_replay_manifest_required")
    manifest_source = _source(manifest)
    archive = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(archive, dict):
        raise ValueError("successful_source_bound_replay_archive_required")
    # Metadata only until both declared identities have passed the public lock.
    refuse_locked_match(archive.get("match_id"))
    refuse_locked_match(archive.get("parsed_match_id"))
    if (archive.get("kind") != "offline_replay_archive" or not archive.get("match_id")
            or archive.get("parsed_match_id") != archive.get("match_id")
            or archive.get("source_unchanged_stat") is not True
            or archive.get("parse_error") or archive.get("truncated_at_tick") is not None
            or archive.get("export_complete") is False
            or archive.get("counts", {}).get("incomplete_sample_packets", 0) != 0
            or archive.get("stop_reason") not in {"replay_eof", "requested_game_window_end"}):
        raise ValueError("successful_source_bound_replay_archive_required")
    expected_file = archive.get("files", {}).get(samples.name, {})
    sample_source = _source(samples)
    if (sample_source["sha256"] != expected_file.get("sha256")
            or samples.stat().st_size != expected_file.get("bytes")):
        raise ValueError("replay_samples_do_not_match_manifest")
    pause_clocks = _pause_clocks(archive.get("game_clock") or {})
    selected, excluded = [], Counter()
    source_stats = {Path(path): (Path(path).stat().st_size, Path(path).stat().st_mtime_ns)
                    for path in (observations, samples, manifest)}
    wanted = set(lines)
    for number, record in _rows(observations, limit=128 * 1024 ** 2):
        if wanted and number not in wanted:
            continue
        attempt = record.get("attempt") or {}
        hero = attempt.get("hero") or {}
        clock = record.get("clock_context") or {}
        time_s = _number(clock.get("clock_time_s"))
        if attempt.get("accepted") is not True or not hero.get("hero_id") or time_s is None:
            excluded["no_accepted_identity_or_game_clock"] += 1
            continue
        if record.get("match_id") != archive["match_id"]:
            excluded["different_match_id"] += 1
            continue
        clock_age = _number(clock.get("alignment_delta_s"))
        if clock_age is None or abs(clock_age) > max_clock_age_s:
            excluded["clock_alignment_too_far_or_unknown"] += 1
            continue
        if any(abs(freeze - time_s) <= tolerance_s + abs(clock_age) + 1.0
               for freeze in pause_clocks):
            excluded["paused_game_clock_ambiguous"] += 1
            continue
        if len(selected) >= max_observations:
            excluded["observation_limit"] += 1
            continue
        slots = [{"slot": _slot_number(slot), "status": slot.get("status"),
                  "item_key": _item(slot.get("item_id"))}
                 for slot in hero.get("slots", []) if _slot_number(slot) is not None]
        selection = attempt.get("economy_selection") or {}
        selected.append({"observation_line": number, "match_id": record.get("match_id"),
            "hero_id": hero["hero_id"], "game_time_s": time_s,
            "captured_at_utc": record.get("captured_at_utc"), "slots": slots,
            "hud_image": record.get("hud_image"), "hud_image_sha256": record.get("hud_image_sha256"),
            "viewer_team": clock.get("self_team"),
            "clock_basis": clock.get("basis"), "provider_alignment_delta_s": clock.get("alignment_delta_s"),
            "provider_at_utc": clock.get("provider_at_utc"),
            "observed_upgrade_value": hero.get("confirmed_upgrade_value"),
            "observed_scepter_value": hero.get("consumed_scepter_value"),
            "observed_shard_value": hero.get("consumed_shard_value"),
            "current_observed_value": hero.get("confirmed_economy_value"),
            "historical_display_value": selection.get("selected_value", hero.get("confirmed_economy_value")),
            "historical_display_captured_at_utc": selection.get("selected_captured_at_utc", record.get("captured_at_utc"))})
    if not selected:
        raise ValueError("no_comparable_observations")
    indices = defaultdict(list)
    for index, row in enumerate(selected):
        indices[(row["match_id"], row["hero_id"])].append((row["game_time_s"], index))
    for key in indices:
        indices[key].sort()
    clocks = {key: [value[0] for value in pairs] for key, pairs in indices.items()}
    nearest = {}
    paused_targets = set()
    for _, sample in _rows(samples):
        if sample.get("kind") != "hero_sample":
            raise ValueError("unexpected_replay_sample_kind")
        key = sample.get("match_id"), sample.get("hero_id")
        time_s = _number(sample.get("game_time_s"))
        if key not in indices or time_s is None:
            continue
        left = bisect_left(clocks[key], time_s - tolerance_s)
        right = bisect_right(clocks[key], time_s + tolerance_s)
        for target, index in indices[key][left:right]:
            if sample.get("paused") is True:
                paused_targets.add(index)
            rank = abs(target - time_s), time_s > target, sample.get("tick", 0)
            if index not in nearest or rank < nearest[index][0]:
                nearest[index] = rank, sample
    counts, bag_counts = Counter(), Counter()
    for index, row in enumerate(selected):
        pair = nearest.get(index)
        if index in paused_targets:
            row.update(status="paused_game_clock_ambiguous", comparisons=[])
            counts[row["status"]] += 1
            continue
        if pair is None:
            row.update(status="no_replay_sample_in_time_window", comparisons=[])
            counts[row["status"]] += 1
            continue
        sample = pair[1]
        reference_slots = [item for item in sample.get("inventory", [])
                           if type(item.get("slot")) is int and 0 <= item["slot"] <= 8]
        reference_counts = Counter(item["slot"] for item in reference_slots)
        inventory = {item["slot"]: item for item in reference_slots}
        comparisons = []
        observed_slots = row.pop("slots")
        observed_counts = Counter(item["slot"] for item in observed_slots)
        for item in observed_slots:
            truth = inventory.get(item["slot"])
            truth_key = _item(truth.get("item_key", truth.get("item_name"))) if truth else None
            if observed_counts[item["slot"]] != 1:
                status = "recognition_duplicate_slot"
            elif not _usable_slot(item["status"], item["item_key"], "known"):
                status = "recognition_unknown"
            elif reference_counts[item["slot"]] > 1:
                status = "replay_duplicate_slot"
            elif truth is None or not _usable_slot(truth.get("status"), truth_key, "occupied"):
                status = "replay_slot_unknown"
            elif item["status"] == "empty":
                status = "same_empty" if truth["status"] == "empty" else "recognized_empty_replay_occupied"
            elif truth["status"] == "occupied" and item["item_key"] == truth_key:
                status = "same_item_id"
            else:
                status = "item_id_disagreement"
            comparisons.append({**item, "replay": truth, "comparison": status})
            counts[status] += 1
        fully_known = (len(observed_slots) == 9 and len(observed_counts) == 9
                       and all(_usable_slot(item["status"], item["item_key"], "known") for item in observed_slots)
                       and len(reference_slots) == 9 and len(inventory) == 9
                       and all(_usable_slot(item.get("status"),
                           _item(item.get("item_key", item.get("item_name"))), "occupied")
                           for item in inventory.values()))
        if fully_known:
            seen_bag = Counter(item["item_key"] for item in observed_slots if item["status"] == "known")
            replay_bag = Counter(_item(item.get("item_key", item.get("item_name")))
                                 for item in inventory.values() if item["status"] == "occupied")
            bag_status = "same_item_multiset" if seen_bag == replay_bag else "different_item_multiset"
        else:
            bag_status = "incomplete_slots_not_compared"
        bag_counts[bag_status] += 1
        row.update(status="paired_for_review", replay_tick=sample.get("tick"),
            replay_game_time_s=sample["game_time_s"], time_error_s=round(sample["game_time_s"] - row["game_time_s"], 6),
            comparisons=comparisons,
            inventory_item_multiset_comparison=bag_status,
            replay_consumed_scepter=sample.get("consumed_scepter"),
            replay_consumed_shard=sample.get("consumed_shard"),
            replay_visibility_raw=sample.get("visibility"),
            upgrade_evidence=sample.get("upgrade_evidence"),
            upgrade_verdict="requires_verified_temporal_upgrade_semantics_not_final_match_flags")
        counts["paired_observations"] += 1
    final_sample_source = _source(samples)
    if final_sample_source != sample_source:
        raise ValueError("replay_samples_changed_during_comparison")
    final_manifest_source = _source(manifest)
    if final_manifest_source != manifest_source:
        raise ValueError("replay_manifest_changed_during_comparison")
    evidence = [_source(observations), final_sample_source, final_manifest_source]
    if any((path.stat().st_size, path.stat().st_mtime_ns) != signature for path, signature in source_stats.items()):
        raise ValueError("review_source_changed_during_comparison")
    return {"schema_version": 1, "kind": "offline_replay_economy_comparison",
        "tool_source_sha256": _source(Path(__file__))["sha256"],
        "sources": evidence,
        "replay_source_sha256": archive.get("source_sha256"),
        "replay_parser": archive.get("parser"),
        "replay_archive_stop_reason": archive.get("stop_reason"),
        "tolerance_s": tolerance_s, "max_clock_age_s": max_clock_age_s,
        "counts": dict(counts), "excluded": dict(excluded), "rows": selected,
        "inventory_item_multiset_counts": dict(bag_counts),
        "meaning": "paired_item_ID_agreement_for_review_not_independent_recognition_accuracy",
        "limitations": ["GSI_clock_is_nearby_integer_second_not_same_tick",
            "item_movement_or_purchases_within_alignment_window_may_cause_disagreement",
            "known_paused_clock_windows_are_excluded_no_UTC_tick_guess",
            "empty_and_unknown_reported_separately", "no_net_worth_subtraction_or_economy_accuracy_claim",
            "omniscient_inventory_can_differ_from_player_HUD_last_visible_state_check_original_image",
            "item_multiset_ignores_slot_movement_and_charges_not_a_valuation_comparison",
            "parser_semantics_and_permanent_upgrades_need_independent_confirmation",
            "postgame_only_no_runtime_input_or_source_log_rewrite"]}
