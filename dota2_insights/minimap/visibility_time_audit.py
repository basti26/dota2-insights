"""Postgame raw visibility over a clock uncertainty window, never live truth."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from .replay_alignment import _digest, _json, _number


def clock_search_window(clock, provider_age_s):
    """A conservative search interval, not a reconstructed capture timestamp.

    The game clock and provider UTC are integer valued. Their rounding and
    the within-second generation time are not known from these old logs.
    Include one second either side for game-clock quantization, plus wall age
    and one additional second for the provider timestamp's unknown rounding.
    A pause can reduce advancement; no interpolation is performed.
    """
    if (not _number(clock, 86400) or not _number(provider_age_s, 3)
            or not 0 <= provider_age_s <= 3):
        raise ValueError("visibility_clock_context_invalid")
    return {
        "start_game_time_s": clock - 1.0,
        "end_game_time_s": clock + 2.0 + provider_age_s,
        "reported_integer_clock_s": clock,
        "provider_age_s": provider_age_s,
        "integer_rounding_allowance_s": 1.0,
        "provider_timestamp_rounding_allowance_s": 1.0,
        "meaning": "conservative_search_window_not_exact_capture_game_time",
        "capture_time_interpolation_performed": False,
        "pause_assumed_absent": False,
    }


class VisibilityTimeAudit:
    """Query changes recorded at every completed packet in a separate archive."""

    def __init__(self, path, *, expected_source):
        path = Path(path)
        manifest_path = path.with_name("manifest.json")
        if manifest_path.stat().st_size > 1024 ** 2:
            raise ValueError("visibility_transition_manifest_size_limit")
        manifest, manifest_source = _json(manifest_path)
        digest, size = _digest(path, limit=64 * 1024 ** 2)
        declared = manifest.get("files", {}).get(path.name, {})
        original = expected_source.get("original_replay") or {}
        capture = manifest.get("visibility_capture") or {}
        if (manifest.get("kind") != "offline_replay_archive"
                or manifest.get("source_unchanged_stat") is not True
                or manifest.get("export_complete") is not True
                or manifest.get("parse_error") is not None
                or manifest.get("truncated_at_tick") is not None
                or manifest.get("stop_reason") not in {"replay_eof", "requested_game_window_end"}
                or declared.get("sha256") != digest or declared.get("bytes") != size
                or manifest.get("match_id") != original.get("match_id")
                or manifest.get("source_sha256") != original.get("source_sha256")
                or manifest.get("game_clock") != expected_source.get("game_clock")
                or capture.get("recording_semantics") != "every_completed_packet_state_changes_only"
                or capture.get("complete_for_exported_window") is not True):
            raise ValueError("visibility_transition_archive_binding_invalid")
        first, last = capture.get("first_game_time_s"), capture.get("last_game_time_s")
        if not _number(first, 86400) or not _number(last, 86400) or first > last:
            raise ValueError("visibility_transition_coverage_invalid")
        self.rows = defaultdict(list)
        previous = {}
        initial = set()
        checksum = hashlib.sha256()
        with path.open("rb") as handle:
            for line_number in range(1, 200001):
                line = handle.readline(64 * 1024 + 1)
                if not line:
                    break
                if len(line) > 64 * 1024 or not line.endswith(b"\n"):
                    raise ValueError("visibility_transition_line_limit")
                checksum.update(line)
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("visibility_transition_object_required")
                hero, tick, clock = row.get("hero_id"), row.get("tick"), row.get("game_time_s")
                if (row.get("kind") != "visibility_transition" or row.get("schema_version") != 1
                        or row.get("match_id") != original["match_id"]
                        or not isinstance(hero, str) or not hero.startswith("npc_dota_hero_")
                        or len(hero) > 64 or type(tick) is not int or tick < 0
                        or not _number(clock, 86400) or not first <= clock <= last
                        or type(row.get("entity_index")) is not int
                        or type(row.get("entity_serial")) is not int):
                    raise ValueError("visibility_transition_schema_invalid")
                if hero in previous and (tick <= previous[hero][0] or clock < previous[hero][1]):
                    raise ValueError("visibility_transition_time_order_invalid")
                if hero not in previous and row.get("initial_state") is not True:
                    raise ValueError("visibility_transition_initial_state_missing")
                if row.get("initial_state") is True:
                    initial.add(hero)
                for side in ("radiant", "dire"):
                    if (row.get("visibility") or {}).get(side, {}).get("state") not in {"visible", "hidden", "unknown"}:
                        raise ValueError("visibility_transition_state_invalid")
                previous[hero] = tick, clock
                self.rows[hero].append({**row, "source_line": line_number})
            else:
                raise ValueError("visibility_transition_row_limit")
        if checksum.hexdigest() != digest or len(initial) != 10:
            raise ValueError("visibility_transition_content_invalid")
        self.times = {hero: [row["game_time_s"] for row in rows] for hero, rows in self.rows.items()}
        self.coverage = capture
        self.path, self.manifest_path = path, manifest_path
        self.source = {"sha256": digest, "bytes": size, "manifest": manifest_source,
                       "auditor_source_sha256": _digest(Path(__file__))[0],
                       "match_id": manifest["match_id"], "source_sha256": manifest["source_sha256"],
                       "adapter_sha256": manifest.get("adapter_sha256"), "coverage": capture}
        self.verify_unchanged()

    def verify_unchanged(self):
        if (_digest(self.path, limit=64 * 1024 ** 2) != (self.source["sha256"], self.source["bytes"])
                or _digest(self.manifest_path) != (self.source["manifest"]["sha256"], self.source["manifest"]["bytes"])):
            raise ValueError("visibility_transition_source_changed")

    def team_at_reported_clock(self, hero, clock):
        """Past replay team of the GSI own hero; not a future final roster."""
        rows, times = self.rows.get(hero, []), self.times.get(hero, [])
        index = bisect_right(times, clock) - 1
        if index < 0:
            return None
        team = rows[index].get("team")
        return "radiant" if team == 2 else "dire" if team == 3 else None

    def query(self, hero, side, window):
        if side not in {"radiant", "dire"}:
            raise ValueError("visibility_perspective_team_invalid")
        lower, upper = window["start_game_time_s"], window["end_game_time_s"]
        first, last = self.coverage["first_game_time_s"], self.coverage["last_game_time_s"]
        rows, times = self.rows.get(hero, []), self.times.get(hero, [])
        base = {"hero_id": hero, "perspective": side, "clock_search_window": window,
                "player_minimap_visibility_verified": False, "exact_capture_state": None,
                "live_warning_eligible": False}
        if not rows or upper < first or lower > last:
            return {**base, "status": "outside_transition_archive_coverage", "possible_raw_states": []}
        clipped_lower = max(lower, first)
        at_lower = bisect_left(times, clipped_lower)
        start_index = at_lower if at_lower < len(times) and times[at_lower] == clipped_lower else at_lower - 1
        stop_index = bisect_right(times, min(upper, last))
        chosen = rows[max(0, start_index):stop_index]
        states = sorted({row["visibility"][side]["state"] for row in chosen})
        identities = {(row["entity_index"], row["entity_serial"]) for row in chosen}
        complete = lower >= first and upper <= last and start_index >= 0
        if not complete:
            status = "partial_clock_window_coverage"
        elif len(identities) > 1:
            status = "entity_identity_changed_in_clock_window"
        elif len(states) > 1:
            status = "raw_state_changes_within_time_uncertainty"
        elif states == ["unknown"]:
            status = "raw_state_unknown"
        else:
            status = "raw_state_constant_in_covered_search_window"
        clock_index = bisect_right(times, window["reported_integer_clock_s"]) - 1
        at_integer = (rows[clock_index]["visibility"][side]["state"]
                      if clock_index >= 0 and first <= window["reported_integer_clock_s"] <= last else None)
        return {**base, "status": status, "possible_raw_states": states,
                "search_window_fully_covered": complete,
                "raw_state_at_reported_integer_clock": at_integer,
                "maximum_completed_packet_gap_s": self.coverage.get("max_packet_gap_s"),
                "transition_source_lines": [row["source_line"] for row in chosen],
                "changes_inside_search_window": sum(lower < row["game_time_s"] <= upper
                                                     and row.get("initial_state") is not True
                                                     for row in chosen),
                "meaning": "raw_state_set_over_search_window_not_player_image_truth"}
