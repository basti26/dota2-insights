"""Optional Foresight callback identities; not audio timing or visibility truth."""
from datetime import datetime, timezone
import re

from .replay_holdout import refuse_locked_match

SCOPE_KEYS = {'match_id', 'game_session_id', 'record_session_id'}
CONTEXT_KEYS = SCOPE_KEYS | {'schema_version', 'provider_at_utc', 'basis', 'gsi_source_id'}


def validate_scope(value, *, expected_match):
    if value is None:
        return None  # Old records remain unbound; no timestamp-based backfill.
    if not isinstance(value, dict) or set(value) != SCOPE_KEYS:
        raise ValueError('audio_recorded_source_scope_invalid')
    mid = value['match_id']
    if mid is not None:
        if not isinstance(mid, str) or not re.fullmatch(r'[1-9][0-9]{0,31}', mid):
            raise ValueError('audio_recorded_source_match_invalid')
        refuse_locked_match(mid)
    if mid != expected_match:
        raise ValueError('audio_recorded_source_match_conflict')
    for key, prefix in (('game_session_id', 'game_'), ('record_session_id', 'run_')):
        if not isinstance(value[key], str) or not re.fullmatch(prefix+r'[a-f0-9]{32}', value[key]):
            raise ValueError('audio_recorded_source_session_invalid')
    return dict(value)


def validate_reference_context(context):
    value = context.get('recorded_source_context')
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != CONTEXT_KEYS:
        raise ValueError('audio_recorded_source_context_invalid')
    scope = {key: value[key] for key in SCOPE_KEYS}
    validate_scope(scope, expected_match=context.get('match_id'))
    stamp = context.get('provider_timestamp')
    if type(stamp) is not int:
        raise ValueError('audio_recorded_source_provider_invalid')
    try:
        observed = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError('audio_recorded_source_provider_invalid') from exc
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['basis'] != 'accepted_same_GSI_callback_not_audio_sample_time'
            or value['provider_at_utc'] != observed
            or not isinstance(value['gsi_source_id'], str)
            or not re.fullmatch(r'gsi_[a-f0-9]{32}', value['gsi_source_id'])):
        raise ValueError('audio_recorded_source_context_invalid')
    return dict(value)
