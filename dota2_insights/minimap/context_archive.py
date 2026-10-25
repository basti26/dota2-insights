"""Postgame-only, allowlisted context for independent minimap evidence.

This module never imports the main runtime or opens a GSI receiver. Closed,
explicitly supplied local journals are projected into a separate archive.
Only data available before a minimap capture may enter its causal context.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from .dataset import SampleDataset

_MAX_JOURNAL = 128 * 1024 ** 2
_HERO = re.compile(r"npc_dota_hero_[a-z0-9_]{1,50}")
_TEAM = {"radiant", "dire"}
_PHASES = {"waiting", "ban", "selection", "strategy", "in_game", "finished"}
_STATES = {"DOTA_GAMERULES_STATE_" + name for name in (
    "INIT", "WAIT_FOR_PLAYERS_TO_LOAD", "CUSTOM_GAME_SETUP", "HERO_SELECTION",
    "STRATEGY_TIME", "TEAM_SHOWCASE", "WAIT_FOR_MAP_TO_LOAD", "PRE_GAME",
    "GAME_IN_PROGRESS", "POST_GAME", "DISCONNECT", "SCENARIO_SETUP")}
_OWN_FLAGS = ("alive", "smoked", "magicimmune", "channeling", "teleporting")
_OWN_WATCHED_ITEMS = {"item_black_king_bar", "item_tpscroll", "item_invis_sword",
                      "item_silver_edge", "item_smoke_of_deceit"}


def utc(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("context_time_missing_or_invalid")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("context_time_must_be_utc")
    return result


def _optional_time(value):
    if value is None:
        return None
    utc(value)
    return value


def _number(value, *, limit=2 ** 53):
    return value if type(value) in (int, float) and math.isfinite(value) and abs(value) <= limit else None


def _hero(value):
    return value if isinstance(value, str) and _HERO.fullmatch(value) else None


def _match(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9]{1,32}", value) and int(value) > 0 else None


def _boolean(value):
    return value if type(value) is bool else None


def _sha(path, limit):
    checksum, size = hashlib.sha256(), 0
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 ** 2):
            size += len(chunk)
            if size > limit:
                raise ValueError("context_source_size_limit")
            checksum.update(chunk)
    return checksum.hexdigest(), size


class Sources:
    """Hash exactly the explicit sources, and check they stayed immutable."""
    def __init__(self):
        self.items, self.paths = [], []

    def read_json(self, path):
        path = Path(path).resolve()
        with path.open("rb") as handle:
            content = handle.read(256 * 1024 + 1)
        if len(content) > 256 * 1024:
            raise ValueError("context_manifest_size_limit")
        self._remember(path, hashlib.sha256(content).hexdigest(), len(content), 256 * 1024)
        value = json.loads(content)
        if not isinstance(value, dict):
            raise ValueError("context_manifest_must_be_object")
        status = value.get("run_recording_status") or value.get("recording") or value
        if not isinstance(status, dict):
            status = {}
        # Closed does not mean a complete match: retain cap/drop failures as
        # scalar source health instead of silently treating all files as full.
        health = {key: value.get(key) for key in ("closed", "complete", "evidence_complete", "evidence_queue_drained")
                  if type(value.get(key)) is bool}
        for key in ("closed", "complete", "limit_reached", "limited"):
            if type(status.get(key)) is bool:
                health["writer_" + key] = status[key]
        for key in ("written", "dropped", "sampled_out", "pending", "max_bytes", "roster_images_skipped"):
            if type(status.get(key)) is int and status[key] >= 0:
                health[key] = status[key]
        if isinstance(status.get("limits_reached"), list):
            health["writer_has_limits"] = bool(status["limits_reached"])
        health["writer_error_present"] = bool(status.get("error") or status.get("errors") or status.get("close_error"))
        if health:
            self.items[-1]["source_health"] = health
        return value

    def _remember(self, path, checksum, size, limit):
        source_id = "source_" + str(len(self.items) + 1)
        # The relative evidence label is sufficient for local lookup; no home,
        # credential or account paths are copied into the derived archive.
        diagnostics = next((parent for parent in path.parents if parent.name == "diagnostics"), None)
        # Nested matches/sessions sources need their full diagnostics-relative
        # path. Keeping only three components loses matches/ and breaks joins.
        label = path.relative_to(diagnostics).as_posix() if diagnostics else "/".join(path.parts[-3:])
        self.items.append({"source_id": source_id, "local_evidence_label": label,
                           "sha256": checksum, "bytes": size})
        self.paths.append((path, checksum, size, limit))
        return source_id

    def journal(self, path, *, max_rows=100000, max_line=256 * 1024):
        path = Path(path).resolve()
        source_id = "source_" + str(len(self.items) + 1)

        def read():
            checksum, size, line_number = hashlib.sha256(), 0, 0
            with path.open("rb") as handle:
                while line := handle.readline(max_line + 1):
                    size += len(line)
                    line_number += 1
                    if len(line) > max_line or size > _MAX_JOURNAL or line_number > max_rows:
                        raise ValueError("context_journal_limit")
                    if not line.endswith(b"\n"):
                        raise ValueError("closed_context_journal_has_partial_line")
                    checksum.update(line)
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("context_journal_row_must_be_object")
                    yield line_number, row
            self._remember(path, checksum.hexdigest(), size, _MAX_JOURNAL)

        return source_id, read()

    def verify_unchanged(self):
        for path, checksum, size, limit in self.paths:
            if _sha(path, limit) != (checksum, size):
                raise ValueError("closed_context_source_changed")


def _reference(source_id, line):
    return {"source_id": source_id, "line": line}


def _closed_journal(sources, folder):
    summary = sources.read_json(Path(folder) / "recording-summary.json")
    status = summary.get("run_recording_status") or {}
    if (not _optional_time(summary.get("closed_at_utc"))
            or summary.get("evidence_queue_drained") is not True
            or status.get("pending") not in (None, 0)
            or status.get("queue_depth") not in (None, 0)):
        raise ValueError("context_source_not_closed_and_drained")
    return summary


def _timeline(rows):
    rows.sort(key=lambda row: (utc(row["available_at_utc"]), row["source"]["line"]))
    return [utc(row["available_at_utc"]) for row in rows], rows


def _previous(timeline, captured):
    clocks, rows = timeline
    index = bisect_right(clocks, captured) - 1
    if index < 0:
        return None
    result = rows[index]
    return {**result, "available_age_s": round((captured - clocks[index]).total_seconds(), 6)}


def _previous_draft(timelines, match, captured):
    candidates = [row for timeline in timelines if (row := _previous(timeline, captured))]
    if not candidates:
        return None
    teams = {row["own_team_evidence"]["team"] for row in candidates if row["own_team_evidence"]["team"]}
    sides = {row["own_side"] for row in candidates if row["own_side"]}
    phases = {row["phase"] for row in candidates if row["phase"] != "unknown"}
    slot_heroes = {}
    for row in candidates:
        for slot in row["slots"]:
            if slot["public"] is True and slot["conflict"] is not True and slot["hero_id"]:
                slot_heroes.setdefault(slot["slot"], set()).add(slot["hero_id"])
    conflict = len(teams) > 1 or len(sides) > 1 or len(phases) > 1 or any(len(v) > 1 for v in slot_heroes.values())
    latest = max(candidates, key=lambda row: utc(row["available_at_utc"]))
    evidence = [{key: row[key] for key in ("source", "available_at_utc", "available_age_s")}
                for row in candidates]
    if conflict:
        return {"kind": "draft_context", "status": "source_conflict", "match_id": match,
            "available_at_utc": latest["available_at_utc"], "available_age_s": latest["available_age_s"],
            "phase": "unknown", "own_side": None, "slots": [],
            "own_team_evidence": {"team": None, "basis": "source_conflict", "provider_timestamp": None},
            "candidate_sources": evidence}
    return {**latest, "status": "associated_side_evidence", "candidate_sources": evidence}


def _self_rows(sources, folder, registry, sessions, counts):
    folder = Path(folder).resolve()
    expected = (registry.get("recorders", {}).get("self_recording") or {}).get("run_id")
    if folder.name != "self-economy-" + str(expected):
        raise ValueError("self_run_not_in_closed_registry")
    summary = _closed_journal(sources, folder)
    if summary.get("run_id") != expected:
        raise ValueError("self_summary_identity_mismatch")
    source_id, raw = sources.journal(folder / "samples.jsonl", max_line=16 * 1024)
    rows = []
    for line, row in raw:
        match = _match(row.get("match_id"))
        game = row.get("game_session_id")
        association = sessions.get(game, {})
        if (row.get("event_kind") != "self_economy" or row.get("run_id") != expected
                or match != association.get("match_id")
                or row.get("record_session_id") != association.get("record_session_id") or match is None):
            counts["self_rows_without_explicit_session_association"] += 1
            continue
        received, provider = row.get("received_at_utc"), row.get("provider_at_utc")
        utc(received)
        _optional_time(provider)
        probe = row.get("gsi_probe") if isinstance(row.get("gsi_probe"), dict) else {}
        status = row.get("self_status") if isinstance(row.get("self_status"), dict) else {}
        position = probe.get("own_position") if isinstance(probe.get("own_position"), dict) else {}
        hero = _hero(position.get("hero_id")) or _hero(status.get("hero_id"))
        reported = position.get("status") == "reported" and _hero(position.get("hero_id")) == hero
        x, y = (_number(position.get(axis), limit=1000000) if reported else None for axis in ("xpos", "ypos"))
        map_context = row.get("map_context") if isinstance(row.get("map_context"), dict) else {}
        state = map_context.get("game_state")
        team = map_context.get("own_team")
        flags = status.get("flags") if isinstance(status.get("flags"), dict) else {}
        own_flags = {key: _boolean(flags.get(key)) for key in _OWN_FLAGS}
        watched = []
        for item in (status.get("watched_items") or [])[:17]:
            if (not isinstance(item, dict) or item.get("item_id") not in _OWN_WATCHED_ITEMS
                    or not re.fullmatch(r"slot[0-8]|stash[0-5]|teleport0|neutral0", str(item.get("slot", "")))):
                continue
            cooldown, charges = _number(item.get("cooldown_s"), limit=86400), _number(item.get("charges"), limit=100000)
            watched.append({"slot": item["slot"], "item_id": item["item_id"],
                "cooldown_s": cooldown if cooldown is not None and cooldown >= 0 else None,
                "can_cast": _boolean(item.get("can_cast")),
                "charges": charges if type(charges) is int and charges >= 0 else None})
        rows.append({"kind": "own_gsi_context", "source": _reference(source_id, line),
            "match_id": match, "game_session_id": game, "record_session_id": row["record_session_id"],
            "source_provider_at_utc": provider, "source_received_at_utc": received,
            "available_at_utc": received, "clock_time_s": _number(row.get("clock_time_s"), limit=86400),
            "game_time_s": _number(map_context.get("game_time_s"), limit=86400),
            "paused": _boolean(map_context.get("paused")),
            "game_state": state if state in _STATES else None,
            "own_team": team if team in _TEAM else None, "own_hero_id": hero,
            "own_alive": own_flags["alive"], "own_status_flags": own_flags,
            "own_watched_items": watched,
            "own_status_meaning": "reported_own_gsi_only_not_enemy_smoke_invisibility_or_full_modifiers",
            "own_position": {"status": "reported" if x is not None and y is not None else "unknown",
                             "xpos": x, "ypos": y,
                             "meaning": "own_local_hero_world_coordinates_not_enemy_ground_truth"}})
    counts["self_rows_projected"] += len(rows)
    return rows


def _draft_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("draft_snapshot_missing")
    slots = snapshot.get("slots")
    if not isinstance(slots, list) or len(slots) > 10:
        raise ValueError("draft_slots_invalid")
    clean, seen = [], set()
    for slot in slots:
        if not isinstance(slot, dict) or not re.fullmatch(r"(?:left|right)[1-5]", str(slot.get("slot", ""))):
            raise ValueError("draft_slot_invalid")
        if slot["slot"] in seen:
            raise ValueError("draft_slot_duplicate")
        seen.add(slot["slot"])
        public, conflict = _boolean(slot.get("public")), _boolean(slot.get("conflict"))
        clean.append({"slot": slot["slot"], "hero_id": _hero(slot.get("hero_id")),
            "public": public, "conflict": conflict, "source": slot.get("source")
            if slot.get("source") in {"portrait", "manual", "chat", "gsi"} else "unknown",
            "source_observed_at_utc": _optional_time(slot.get("observed_at_utc")),
            "first_visible_at_utc": _optional_time(slot.get("first_visible_at_utc")),
            "meaning": "recorded_identity_evidence_not_manually_verified_ground_truth"})
    phase = snapshot.get("phase")
    return {"phase": phase if phase in _PHASES else "unknown",
            "own_side": snapshot.get("own_side") if snapshot.get("own_side") in {"left", "right"} else None,
            "source_observed_at_utc": _optional_time(snapshot.get("last_observed_at_utc")), "slots": clean}


def _draft_rows(sources, folder, match_ids, registry, counts):
    folder = Path(folder).resolve()
    if not re.fullmatch(r"session_[a-f0-9]{32}", folder.name):
        raise ValueError("invalid_draft_folder")
    end = sources.read_json(folder / "session-end.json")
    recording = end.get("recording") or {}
    if recording.get("closed") is not True or end.get("session_id") != folder.name[8:]:
        raise ValueError("draft_source_not_closed")
    utc(end.get("closed_at_utc"))
    final_match = _match((end.get("snapshot") or {}).get("match_id"))
    if final_match not in match_ids:
        raise ValueError("draft_match_not_in_closed_registry")
    source_id, raw = sources.journal(folder / "events.jsonl", max_rows=10000)
    rows = []
    for line, row in raw:
        snapshot = row.get("snapshot") or {}
        match = _match(snapshot.get("match_id"))
        written = row.get("written_at_utc")
        clock = utc(written)
        if not utc(registry["started_at_utc"]) <= clock <= utc(registry["closed_at_utc"]):
            raise ValueError("draft_event_outside_closed_registry")
        if snapshot.get("session_id") != end["session_id"] or match not in (None, final_match):
            raise ValueError("draft_event_identity_mismatch")
        if match is None:
            counts["draft_rows_without_at_time_match_id"] += 1
            continue
        projected = _draft_snapshot(snapshot)
        observed = projected["source_observed_at_utc"]
        if observed is not None and utc(observed) > clock:
            raise ValueError("draft_observed_time_after_available_time")
        if any(slot["source_observed_at_utc"] and utc(slot["source_observed_at_utc"]) > clock
               for slot in projected["slots"]):
            raise ValueError("draft_slot_from_future")
        boundary = row.get("gsi_boundary") or {}
        side = boundary.get("side_evidence") or {}
        team = side.get("self_team")
        explicit_team = (side.get("source") == "gsi_player_team" and side.get("match_id") == match
                         and side.get("session_id") == end["session_id"] and team in _TEAM)
        rows.append({"kind": "draft_context", "source": _reference(source_id, line),
            "match_id": match, "draft_session_id": end["session_id"], "available_at_utc": written,
            "association_basis": "explicit_file_same_match_and_registry_interval_not_direct_session_link",
            "source_written_at_utc": written, **projected,
            "own_team_evidence": {"team": team if explicit_team else None,
                                  "basis": "recorded_gsi_player_team" if explicit_team else "unknown",
                                  "provider_timestamp": _number(side.get("provider_timestamp")) if explicit_team else None}})
    sources.items[-1]["association_basis"] = "explicit_file_same_match_and_registry_interval_not_direct_session_link"
    counts["draft_rows_projected"] += len(rows)
    return rows, {"draft_session_id": end["session_id"], "match_id": final_match,
        "available_at_utc": end["closed_at_utc"], "use": "postgame_reference_only_never_as_of_frame",
        "snapshot": _draft_snapshot(end.get("snapshot"))}


def _session_rows(sources, folder, sessions, counts):
    folder = Path(folder).resolve()
    games = {game: info for game, info in sessions.items() if info.get("record_session_id") == folder.name}
    if len(games) != 1:
        raise ValueError("session_run_not_uniquely_in_registry")
    _closed_journal(sources, folder)
    game, association = next(iter(games.items()))
    source_id, raw = sources.journal(folder / "context.jsonl", max_rows=10000)
    contexts = []
    for line, row in raw:
        if (row.get("game_session_id") != game or row.get("record_session_id") != folder.name
                or _match(row.get("match_id")) != association["match_id"]):
            raise ValueError("session_context_identity_mismatch")
        recorded, provider = row.get("recorded_at_utc"), row.get("provider_at_utc")
        utc(recorded)
        _optional_time(provider)
        state = row.get("game_state")
        contexts.append({"kind": "session_context", "source": _reference(source_id, line),
            "match_id": association["match_id"], "game_session_id": game, "record_session_id": folder.name,
            "available_at_utc": recorded, "source_recorded_at_utc": recorded,
            "source_provider_at_utc": provider, "game_state": state if state in _STATES else None})
    rosters = []
    roster_path = folder / "roster-evidence.jsonl"
    if roster_path.exists():
        source_id, raw = sources.journal(roster_path, max_rows=10000)
        for line, row in raw:
            if row.get("record_session_id") != folder.name:
                raise ValueError("roster_context_identity_mismatch")
            captured, recorded = row.get("captured_at_utc"), row.get("recorded_at_utc")
            if utc(captured) > utc(recorded):
                raise ValueError("roster_captured_after_available")
            slots = []
            relations = row.get("relations") or {}
            if not isinstance(relations, dict) or len(relations) > 10:
                raise ValueError("roster_relation_limit")
            for hero, relation in relations.items():
                if not _hero(hero) or not isinstance(relation, dict):
                    continue
                team = relation.get("team")
                slot = relation.get("screen_slot")
                observed = _optional_time(relation.get("observed_at_utc"))
                if observed and utc(observed) > utc(recorded):
                    raise ValueError("roster_relation_from_future")
                slots.append({"hero_id": hero, "team": team if team in _TEAM else None,
                    "screen_slot": slot if type(slot) is int and 1 <= slot <= 10 else None,
                    "team_relation": relation.get("team_relation") if relation.get("team_relation") in {"ally", "enemy", "unknown"} else "unknown",
                    "source_observed_at_utc": observed,
                    "meaning": "accepted_main_portrait_evidence_not_minimap_identity_truth"})
            rosters.append({"kind": "roster_context", "source": _reference(source_id, line),
                "match_id": association["match_id"], "record_session_id": folder.name,
                "available_at_utc": recorded, "source_captured_at_utc": captured,
                "source_recorded_at_utc": recorded, "own_team": row.get("self_team") if row.get("self_team") in _TEAM else None,
                "slots": slots})
    counts["session_rows_projected"] += len(contexts)
    counts["roster_rows_projected"] += len(rosters)
    return contexts, rosters


def build_archive(*, minimap_run, registry_path, self_run=None, draft_runs=(), session_runs=(),
                  start_sequence=1, stop_sequence=None, max_frames=600, own_fresh_s=3.0):
    """Project a bounded explicit frame range using only past available rows."""
    if (type(max_frames) is not int or not 1 <= max_frames <= 30000
            or type(start_sequence) is not int or start_sequence < 1
            or stop_sequence is not None and (type(stop_sequence) is not int or stop_sequence < start_sequence)
            or _number(own_fresh_s, limit=60) is None or not 0 < own_fresh_s <= 60
            or len(draft_runs) > 8 or len(session_runs) > 8):
        raise ValueError("invalid_context_archive_bounds")
    sources, counts = Sources(), Counter()
    registry_path = Path(registry_path).resolve()
    if registry_path.name != "recorders.json":
        raise ValueError("explicit_closed_recorders_json_required")
    registry = sources.read_json(registry_path)
    if registry.get("scope") != "launcher_recording_status_only" or registry.get("closed") is not True:
        raise ValueError("launcher_registry_not_closed")
    utc(registry.get("started_at_utc"))
    utc(registry.get("closed_at_utc"))
    mini = Path(minimap_run).resolve()
    if mini.name not in (registry.get("local_run_folders") or {}).get("minimap", []):
        raise ValueError("minimap_run_not_in_closed_registry")
    dataset = SampleDataset(mini)
    manifest = sources.read_json(mini / "session.json")
    if manifest.get("closed") is not True:
        raise ValueError("minimap_source_not_closed")
    mini_source = sources._remember(mini / "samples.jsonl", dataset.index_sha256,
                                    (mini / "samples.jsonl").stat().st_size, _MAX_JOURNAL)
    sessions = registry.get("game_sessions")
    if not isinstance(sessions, dict) or not 1 <= len(sessions) <= 128:
        raise ValueError("registry_game_associations_missing")
    match_ids = set()
    for game, association in sessions.items():
        if (not re.fullmatch(r"game_[a-f0-9]{32}", game) or not isinstance(association, dict)
                or not re.fullmatch(r"run_[a-f0-9]{32}", str(association.get("record_session_id", "")))
                or _match(association.get("match_id")) is None):
            raise ValueError("registry_game_association_invalid")
        match_ids.add(association["match_id"])
    timelines = {kind: [] for kind in ("self", "draft", "session", "roster")}
    if self_run is not None:
        timelines["self"] = _self_rows(sources, self_run, registry, sessions, counts)
    final_references = []
    for folder in draft_runs:
        rows, final = _draft_rows(sources, folder, match_ids, registry, counts)
        timelines["draft"].extend(rows)
        final_references.append(final)
    for folder in session_runs:
        contexts, rosters = _session_rows(sources, folder, sessions, counts)
        timelines["session"].extend(contexts)
        timelines["roster"].extend(rosters)
    by_match = {kind: {match: _timeline([row for row in rows if row["match_id"] == match])
                       for match in match_ids} for kind, rows in timelines.items()}
    draft_by_match = {match: [_timeline([row for row in timelines["draft"] if row["match_id"] == match
                                        and row["source"]["source_id"] == source_id])
                             for source_id in {row["source"]["source_id"] for row in timelines["draft"]
                                               if row["match_id"] == match}]
                      for match in match_ids}
    # Session identity is itself chosen from past received/recorded events.
    association_rows = timelines["self"] + timelines["session"]
    association_timeline = _timeline(association_rows)
    selected = [row for row in dataset.rows if row["sequence"] >= start_sequence
                and (stop_sequence is None or row["sequence"] <= stop_sequence)][:max_frames]
    if not selected:
        raise ValueError("no_minimap_frames_in_requested_range")
    aligned = []
    for frame in selected:
        captured = utc(frame["captured_at_utc"])
        if not utc(registry["started_at_utc"]) <= captured <= utc(registry["closed_at_utc"]):
            raise ValueError("minimap_frame_outside_registry")
        association = _previous(association_timeline, captured)
        provider = association.get("source_provider_at_utc") if association else None
        provider_age = (captured - utc(provider)).total_seconds() if provider else None
        association_fresh = bool(association and association["available_age_s"] <= own_fresh_s
                                 and provider_age is not None and 0 <= provider_age <= own_fresh_s)
        match = association["match_id"] if association_fresh else None
        counts["frames_with_fresh_match_association" if association_fresh
               else "frames_without_fresh_match_association"] += 1
        historical_association = {"match_id": association["match_id"], "source": association["source"],
            "available_at_utc": association["available_at_utc"],
            "available_age_s": association["available_age_s"], "source_provider_at_utc": provider,
            "provider_age_s": round(provider_age, 6) if provider_age is not None else None,
            "use": "historical_reference_only_not_current_match"} if association and not association_fresh else None
        context = {kind: _previous(rows.get(match, ([], [])), captured) for kind, rows in by_match.items()}
        context["draft"] = _previous_draft(draft_by_match.get(match, []), match, captured)
        if context["draft"] and context["draft"]["status"] == "source_conflict":
            counts["frames_with_conflicting_draft_sources"] += 1
        own = context["self"]
        if own:
            provider = own.get("source_provider_at_utc")
            delta = (captured - utc(provider)).total_seconds() if provider else None
            own["provider_age_s"] = round(delta, 6) if delta is not None else None
            own["freshness"] = "fresh" if (delta is not None and 0 <= delta <= own_fresh_s
                and own["available_age_s"] <= own_fresh_s) else "stale_or_unknown"
            counts["frames_with_" + own["freshness"] + "_own_gsi"] += 1
        else:
            counts["frames_without_prior_own_gsi"] += 1
        for kind, item in context.items():
            if item:
                counts["frames_with_prior_" + kind] += 1
        aligned.append({"sequence": frame["sequence"], "captured_at_utc": frame["captured_at_utc"],
            "image": frame["image"], "image_sha256": frame["image_sha256"],
            "pixel_sha256": frame.get("pixel_sha256"), "source_index": mini_source,
            "capture_gap_before": frame.get("capture_gap_before"), "map_validity": frame["map_validity"],
            "match_id": match,
            "match_association_status": "associated_fresh_source" if association_fresh else "stale_or_unknown",
            "match_association_source": association["source"] if association_fresh else None,
            "historical_match_association": historical_association,
            "context": context, "enemy_hidden_position_known": False,
            "unobserved_meaning": "unobserved_is_compatible_with_invisibility_smoke_fog_not_proof",
            "human_identity_ground_truth": False})
    sources.verify_unchanged()
    return {"schema_version": 1, "kind": "independent_minimap_postgame_context",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_integration": False, "online_warning_enabled": False,
        "join_policy": "latest_prior_by_received_written_recorded_utc_no_future_values_current_match_requires_fresh_available_and_provider_clocks",
        "match_association_eligibility": {"source_kinds": ["own_gsi_context", "session_context"],
            "available_age_s_max": own_fresh_s, "provider_age_s_min": 0,
            "provider_age_s_max": own_fresh_s, "provider_time_required": True,
            "on_failure": "current_match_null_all_current_context_empty_historical_reference_only"},
        "same_frame_evidence": False,
        "own_gsi_freshness_threshold_s": own_fresh_s,
        "clock_limitations": "provider_utc_may_have_integer_second_precision_no_latency_correction_or_interpolation",
        "meaning": "local_readonly_side_evidence_not_enemy_visibility_truth_or_prediction_validation",
        "sources": sources.items, "source_index_sha256": dataset.index_sha256,
        "minimap_run_id": manifest.get("run_id"),
        "selection": {"start_sequence": start_sequence, "stop_sequence": stop_sequence,
            "max_frames": max_frames, "frames_selected": len(aligned),
            "first_captured_at_utc": aligned[0]["captured_at_utc"],
            "last_captured_at_utc": aligned[-1]["captured_at_utc"]},
        "counts": dict(counts), "postgame_final_draft_references": final_references, "frames": aligned}
