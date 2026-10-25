"""Change-only replay team bits at every completed packet, for offline audits.

No geometry is accepted into the output.  A raw team bit is evidence about that
replay entity; it is not automatically a player minimap visibility label.
"""
from __future__ import annotations

from typing import Any

_STATE_FIELDS = (
    "hero_id", "team", "entity_index", "entity_serial", "entity_active",
    "alive", "life_state_raw", "radiant_state", "dire_state",
)


def _state_signature(observation: dict[str, Any]) -> tuple[Any, ...]:
    visibility = observation["visibility"]
    return tuple(observation.get(field) for field in _STATE_FIELDS[:-2]) + (
        visibility["radiant"]["state"], visibility["dire"]["state"],
    )


class ReplayVisibilityRecorder:
    """Keep the latest ten canonical identities and write only state changes."""

    def __init__(self, match_id: str) -> None:
        self.match_id = match_id
        self._previous: dict[int, tuple[Any, ...]] = {}
        self.first_tick: int | None = None
        self.last_tick: int | None = None
        self.first_game_time_s: float | None = None
        self.last_game_time_s: float | None = None
        self.completed_packets = 0
        self.max_packet_gap_s = 0.0
        self.max_packet_gap_ticks = 0
        self.clock_regressions = 0
        self.transitions = 0
        self.initial_states = 0
        self.observed_state_changes = 0
        self.missing_hero_observations = 0

    def observe_packet(
        self, *, tick: int, net_tick: int, game_time_s: float,
        observations: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Observe one in-window packet; preceding replay state is reconstructed."""
        if self.first_tick is None:
            self.first_tick = tick
            self.first_game_time_s = game_time_s
        if self.last_tick is not None:
            gap = game_time_s - self.last_game_time_s
            self.max_packet_gap_s = max(self.max_packet_gap_s, gap)
            self.max_packet_gap_ticks = max(self.max_packet_gap_ticks, tick - self.last_tick)
            if gap < 0 or tick < self.last_tick:
                self.clock_regressions += 1
        self.last_tick = tick
        self.last_game_time_s = game_time_s
        self.completed_packets += 1
        result = []
        for observation in observations:
            slot = observation["player_slot"]
            signature = _state_signature(observation)
            previous = self._previous.get(slot)
            if observation.get("hero_id") is None:
                self.missing_hero_observations += 1
            if previous == signature:
                continue
            initial = previous is None
            changed = list(_STATE_FIELDS) if initial else [
                name for name, old, new in zip(_STATE_FIELDS, previous, signature)
                if old != new
            ]
            # Project an explicit whitelist; a caller cannot leak coordinates or
            # inventory by adding fields to its observations.
            row: dict[str, Any] = {
                "kind": "visibility_transition", "schema_version": 1,
                "match_id": self.match_id, "tick": tick, "net_tick": net_tick,
                "game_time_s": game_time_s, "player_slot": slot,
                "initial_state": initial, "changed_fields": changed,
            }
            row.update({field: observation.get(field) for field in _STATE_FIELDS[:-2]})
            row["visibility"] = {
                "basis": "team_entity_m_bNPCVisibleState_raw_bit; player_minimap_semantics_unverified",
                "boundary": "completed_packet",
                **{
                    team: {key: observation["visibility"][team].get(key)
                           for key in ("state", "word_index", "bit", "word_value")}
                    for team in ("radiant", "dire")
                },
            }
            result.append(row)
            self._previous[slot] = signature
            self.observed_state_changes += 1
        return result

    def record_written(self, row: dict[str, Any]) -> None:
        """Count persisted rows after the shared output-size guard accepts them."""
        self.transitions += 1
        self.initial_states += bool(row["initial_state"])

    def manifest(self, *, export_succeeded: bool) -> dict[str, Any]:
        return {
            "first_game_time_s": self.first_game_time_s,
            "last_game_time_s": self.last_game_time_s,
            "first_tick": self.first_tick, "last_tick": self.last_tick,
            "completed_packets": self.completed_packets,
            "max_packet_gap_s": self.max_packet_gap_s,
            "max_packet_gap_ticks": self.max_packet_gap_ticks,
            "clock_regressions": self.clock_regressions,
            "transitions": self.transitions, "initial_states": self.initial_states,
            "observed_state_changes": self.observed_state_changes,
            "missing_hero_observations": self.missing_hero_observations,
            "recording_semantics": "every_completed_packet_state_changes_only",
            "complete_for_exported_window": (
                export_succeeded and self.completed_packets > 0
                and self.missing_hero_observations == 0 and self.clock_regressions == 0
                and self.transitions == self.observed_state_changes
            ),
            "coordinate_fields_exported": False,
            "bootstrap_semantics": "first in-window packet records current state for every logical slot",
            "limitation": "raw replay team bits and entity state; not independently labelled player visibility; no reconstruction between packets",
        }
