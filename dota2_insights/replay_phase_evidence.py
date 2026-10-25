"""Bounded, in-memory game-rules phase observations from completed packets.

The caller supplies raw values after each packet's entity updates, before any
hero sampling filter. This module never opens a replay or obtains a clock.
"""
from __future__ import annotations

import copy
import math
from typing import Any


SCHEMA_VERSION = 1
IN_PROGRESS = 5
POST_GAME = 6
DISCONNECT = 7
ENUM_BASIS = 'pinned_gem_dota_shared_enums_proto'
KIND = 'replay_phase_evidence'
BASIS = 'completed_packet_gamerules_m_nGameState'
FIELD = 'm_pGameRules.m_nGameState'
SAMPLING = 'every_completed_packet_before_sample_filter'
ORDERING_SCOPE = 'from_first_observed_in_progress_or_post_game'
INTERVAL_SEMANTICS = 'last_observed_in_progress_to_first_observed_post_game'
MAX_ABS_GAME_TIME_S = 7 * 24 * 60 * 60
MAX_TICK = 2 ** 63 - 1
MAX_STATE = 2 ** 31 - 1
MAX_CHANGE_BUDGET = 4096
COMPLETE_STOP_REASONS = (None, 'replay_eof', 'requested_game_window_end')


def _integer(value: Any, name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError('phase_' + name + '_invalid')
    return value


def _clock(value: Any) -> int | float | None:
    if value is None:
        return None
    if (type(value) not in (int, float)
            or not -MAX_ABS_GAME_TIME_S <= value <= MAX_ABS_GAME_TIME_S
            or not math.isfinite(value)):
        raise ValueError('phase_game_time_s_invalid')
    return value


class PhaseTracker:
    """Track raw phase changes and the first observed active-to-post bracket.

    Defaults come from pinned gem.proto.dota_shared_enums_pb2: GAME_IN_PROGRESS=5,
    POST_GAME=6, DISCONNECT=7. Disconnect follows POST_GAME and does not move the
    first-post endpoint. A missing
    value stays missing. Memory is O(max_changes); steady phases only update a
    single candidate endpoint. Clock/tick regressions or concrete phase reversals
    invalidate this observation stream rather than opening a guessed new epoch.
    """

    def __init__(self, in_progress: int = IN_PROGRESS, post_game: int = POST_GAME,
                 max_changes: int = 64):
        self.in_progress = _integer(in_progress, 'in_progress', 0, MAX_STATE)
        self.post_game = _integer(post_game, 'post_game', 0, MAX_STATE)
        if self.in_progress == self.post_game:
            raise ValueError('phase_enums_must_differ')
        self.max_changes = _integer(max_changes, 'max_changes', 1, MAX_CHANGE_BUDGET)
        self._changes: list[dict[str, Any]] = []
        self._packets = self._clock_regressions = self._tick_regressions = 0
        self._state_reversals = self._unknown_states = self._missing_clocks = 0
        self._changes_truncated = False
        self._last_tick: int | None = None
        self._last_finite_clock: int | float | None = None
        self._last_state: int | None = None
        self._last_active: dict[str, Any] | None = None
        self._first_post: dict[str, Any] | None = None
        self._boundary_clock_complete = True
        self._ordered_phase_seen = False
        self._disconnect_seen = False
        self._disconnect = (DISCONNECT if (self.in_progress, self.post_game)
                            == (IN_PROGRESS, POST_GAME) else None)

    def observe(self, tick: int, game_time_s: int | float | None,
                state_raw: int | None) -> None:
        """Observe one completed packet; malformed input raises before mutation."""
        tick = _integer(tick, 'tick', -MAX_TICK, MAX_TICK)
        clock = _clock(game_time_s)
        if state_raw is not None:
            _integer(state_raw, 'state_raw', 0, MAX_STATE)
        self._packets += 1
        observation = {'completed_packet': self._packets, 'tick': tick,
                       'game_time_s': clock, 'state_raw': state_raw,
                       'source_ref': f'completed_packet:{self._packets}'}
        if not self._ordered_phase_seen and state_raw in (self.in_progress, self.post_game):
            self._ordered_phase_seen = True
            self._last_tick = self._last_finite_clock = None
        if self._ordered_phase_seen and self._last_tick is not None and tick < self._last_tick:
            self._tick_regressions += 1
        self._last_tick = tick
        if clock is not None:
            if (self._ordered_phase_seen and self._last_finite_clock is not None
                    and clock < self._last_finite_clock):
                self._clock_regressions += 1
            self._last_finite_clock = clock
        else:
            self._missing_clocks += 1
        if state_raw is None:
            self._unknown_states += 1
        if self._packets == 1 or state_raw != self._last_state:
            if len(self._changes) < self.max_changes:
                self._changes.append(observation)
            else:
                self._changes_truncated = True
        self._last_state = state_raw
        if self._first_post is not None:
            if state_raw is not None:
                if (self._disconnect_seen and state_raw != self._disconnect
                        or not self._disconnect_seen
                        and state_raw not in (self.post_game, self._disconnect)):
                    self._state_reversals += 1
                if state_raw == self._disconnect:
                    self._disconnect_seen = True
            return
        if state_raw == self.in_progress:
            self._last_active = observation
            self._boundary_clock_complete = clock is not None
        else:
            if self._last_active is not None:
                if clock is None:
                    self._boundary_clock_complete = False
                if state_raw is not None and state_raw != self.post_game:
                    self._state_reversals += 1
            if state_raw == self.post_game:
                self._first_post = observation

    def manifest(self, *, observation_complete: bool,
                 stop_reason: str | None = None) -> dict[str, Any]:
        """Return detached source observations; explicitly declare stream health.

        ``observation_complete=False`` is required for resource/cancel/error stops.
        A later valid clock never repairs a missing first-post clock in this report.
        """
        if type(observation_complete) is not bool:
            raise ValueError('phase_observation_complete_bool_required')
        if stop_reason is not None and (type(stop_reason) is not str
                or not 0 < len(stop_reason) <= 256
                or any(ord(char) < 32 for char in stop_reason)):
            raise ValueError('phase_stop_reason_invalid')
        invalid = []
        if not observation_complete:
            invalid.append('observation_incomplete')
        if stop_reason not in COMPLETE_STOP_REASONS:
            invalid.append('stop_reason_not_complete')
        if self._changes_truncated:
            invalid.append('phase_change_budget_exceeded')
        if self._tick_regressions:
            invalid.append('tick_regression')
        if self._clock_regressions:
            invalid.append('clock_regression')
        if self._state_reversals:
            invalid.append('phase_state_reversal_or_unexpected_transition')
        reasons = invalid[:]
        if self._first_post is None:
            reasons.append('first_post_game_not_observed')
        elif self._last_active is None:
            reasons.append('prior_in_progress_not_observed')
        if self._first_post is not None and self._last_active is not None and (
                not self._boundary_clock_complete or self._last_active['game_time_s'] is None
                or self._first_post['game_time_s'] is None):
            reasons.append('boundary_clock_missing')
        status = 'invalid' if invalid else 'unknown' if reasons else 'observed_interval'
        window = None
        if status == 'observed_interval':
            window = {key: [self._last_active[key], self._first_post[key]]
                      for key in ('game_time_s', 'tick', 'completed_packet')}
            window.update(interval_semantics=INTERVAL_SEMANTICS, exact_end_verified=False)
        return copy.deepcopy({
            'schema_version': SCHEMA_VERSION, 'kind': KIND, 'basis': BASIS,
            'field': FIELD, 'clock_field': 'game_time_s', 'sampling': SAMPLING,
            'ordering_scope': ORDERING_SCOPE,
            'state_enums': {'in_progress': self.in_progress, 'post_game': self.post_game},
            'completed_packets': self._packets, 'max_changes': self.max_changes,
            'changes': self._changes, 'changes_truncated': self._changes_truncated,
            'observation_complete': observation_complete, 'stop_reason': stop_reason,
            'status': status, 'reasons': reasons,
            'clock_regressions': self._clock_regressions,
            'tick_regressions': self._tick_regressions,
            'state_reversals': self._state_reversals,
            'unknown_state_packets': self._unknown_states,
            'missing_clock_packets': self._missing_clocks,
            'boundary_clock_complete': self._boundary_clock_complete,
            'last_in_progress': self._last_active, 'first_post_game': self._first_post,
            'end_window': window,
            'limitations': [
                'Raw game-rules observations after completed packet entity updates.',
                'The interval brackets observations; it does not prove an exact real end time.',
                'Clocks are caller-supplied and do not certify a global clock mapping.',
                'Signon/loading observations before the first active/post phase do not '
                'establish ordering; raw negative ticks and missing values are retained.',
                'Unknown fields stay unknown; no future-value backfill or last-tick duration.',
            ],
        })
