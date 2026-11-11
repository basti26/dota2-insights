"""Independent reader of optional bounded launcher window-request metadata."""
from collections import Counter, defaultdict
import re

from .helper_handoff import utc
from .replay_holdout import refuse_locked_match

REASONS = frozenset(('waiting', 'user_hidden', 'gsi_waiting', 'gsi_stale',
    'game_not_started', 'clock_missing', 'clock_before_30s', 'window_unavailable',
    'game_not_foreground', 'game_minimized', 'placement_requested', 'preview'))
FIELDS = frozenset(('reason', 'window_requested', 'match_id', 'observed_at_utc'))
SCOPE = 'window_request_only_not_pixel_visibility'


def summarize_registry(document, allowed_matches, *, started_at_utc, closed_at_utc):
    keys = ('economy_display', 'economy_display_transitions', 'economy_display_history_limited')
    if not any(k in document for k in keys):
        return {'status': 'not_recorded_unknown', 'transition_count': None,
                'by_explicit_match': {}, 'latest': None, 'pixel_visibility_verified': False}
    history = document.get('economy_display_transitions')
    limited = document.get('economy_display_history_limited', False)
    if not isinstance(history, list) or not 1 <= len(history) <= 128 or type(limited) is not bool:
        raise ValueError('economy_display_history_invalid')
    if limited and len(history) != 128:
        raise ValueError('economy_display_limit_inconsistent')
    start, end = utc(started_at_utc), utc(closed_at_utc)
    if end < start:
        raise ValueError('economy_display_clock_reversed')

    def entry(value, *, latest=False):
        fields = FIELDS | {'scope'} if latest else FIELDS
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError('economy_display_fields_invalid')
        reason, requested = value['reason'], value['window_requested']
        if (not isinstance(reason, str) or reason not in REASONS or type(requested) is not bool
                or requested != (reason in {'placement_requested', 'preview'})):
            raise ValueError('economy_display_state_invalid')
        if latest and value['scope'] != SCOPE:
            raise ValueError('economy_display_scope_invalid')
        mid = value['match_id']
        if mid is not None:
            if not isinstance(mid, str) or not re.fullmatch(r'[1-9][0-9]{0,31}', mid):
                raise ValueError('economy_display_match_invalid')
            refuse_locked_match(mid)
            if mid not in allowed_matches:
                raise ValueError('economy_display_foreign_match')
        at = utc(value['observed_at_utc'])
        if not start <= at <= end:
            raise ValueError('economy_display_time_outside_run')
        return {k: value[k] for k in FIELDS}, at

    counts, by_match, unbound = Counter(), defaultdict(Counter), Counter()
    previous, previous_at = None, start
    for raw in history:
        row, at = entry(raw)
        state = (row['reason'], row['window_requested'], row['match_id'])
        if at < previous_at or state == previous:
            raise ValueError('economy_display_order_or_duplicate')
        previous, previous_at = state, at
        counts[row['reason']] += 1
        (by_match[row['match_id']] if row['match_id'] else unbound)[row['reason']] += 1
    latest = document.get('economy_display')
    if latest is not None:
        latest, at = entry(latest, latest=True)
        state = (latest['reason'], latest['window_requested'], latest['match_id'])
        if at < previous_at or not limited and state != previous:
            raise ValueError('economy_display_latest_inconsistent')
    return {'status': 'recorded_window_requests_only', 'transition_count': len(history),
            'history_limited': limited, 'reason_transition_counts': dict(counts),
            'by_explicit_match': {mid: dict(c) for mid, c in by_match.items()},
            'unbound_reason_transition_counts': dict(unbound), 'latest': latest,
            'pixel_visibility_verified': False,
            'limitations': ['Registry observation times are not pixel presentation times.',
                'Transition counts are not durations, frames, attempts or user views.',
                'Missing match IDs remain unbound; no UTC attribution is performed.']}
