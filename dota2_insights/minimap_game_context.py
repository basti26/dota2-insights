"""Independent validation of optional record-only minimap IPC context v1."""
import math
import re
from .helper_handoff import utc
from .replay_holdout import refuse_locked_match

BASIS = 'last_explicit_GSI_context_before_capture_not_frame_atomic'
ACTIVE = {'DOTA_GAMERULES_STATE_PRE_GAME', 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'}


def summary(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'schema_version','relay_id','basis','declared_match_ids'}
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('basis') != BASIS or not isinstance(value.get('relay_id'), str)
            or not re.fullmatch('[a-f0-9]{32}', value['relay_id'])
            or not isinstance(value.get('declared_match_ids'), list) or len(value['declared_match_ids']) > 32):
        raise ValueError('minimap_frame_context_summary_invalid')
    ids = value['declared_match_ids']
    for mid in ids:
        if not isinstance(mid, str) or not re.fullmatch('[1-9][0-9]{0,19}', mid):
            raise ValueError('minimap_frame_context_match_invalid')
        refuse_locked_match(mid)  # Before opening stream bodies or any image.
    if ids != sorted(set(ids)):
        raise ValueError('minimap_frame_context_match_set_invalid')
    return value


def binding(row, header):
    value = row.get('game_context_binding')
    if value is None:
        return None
    context = value.get('context') if isinstance(value, dict) else None
    if isinstance(context, dict) and context.get('match_id') is not None:
        refuse_locked_match(context['match_id'])
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or row.get('kind') != 'sample' or header is None):
        raise ValueError('minimap_frame_context_contract_invalid')
    if value.get('status') == 'unknown':
        if (set(value) != {'schema_version','status','reason'} or not isinstance(value.get('reason'), str)
                or not re.fullmatch('[a-z0-9_]{1,80}', value['reason'])):
            raise ValueError('minimap_frame_context_unknown_invalid')
        return value
    if (value.get('status') != 'explicit_last_context' or value.get('basis') != BASIS
            or set(value) != {'schema_version','status','basis','relay_id','sequence','sent_monotonic_ns',
                              'capture_monotonic_ns','provider_age_at_capture_s','context'}
            or value.get('relay_id') != header['relay_id'] or not isinstance(context, dict)
            or set(context) not in (
                {'match_id','game_session_id','record_session_id','provider_at_utc','game_state','clock_time_s'},
                {'match_id','game_session_id','record_session_id','provider_at_utc','game_state','clock_time_s','gsi_source_id'})
            or context.get('match_id') is not None and context.get('match_id') not in header['declared_match_ids']
            or context.get('game_state') not in ACTIVE or 'source_video_time_s' in row):
        raise ValueError('minimap_frame_context_identity_conflict')
    if 'gsi_source_id' in context and (not isinstance(context['gsi_source_id'], str)
            or not re.fullmatch(r'gsi_[a-f0-9]{32}', context['gsi_source_id'])):
        raise ValueError('minimap_frame_context_source_id_invalid')
    for key, pattern in (('game_session_id', 'game_[a-f0-9]{32}'), ('record_session_id', 'run_[a-f0-9]{32}')):
        if not isinstance(context.get(key), str) or not re.fullmatch(pattern, context[key]):
            raise ValueError('minimap_frame_context_session_invalid')
    if context['clock_time_s'] is not None and (type(context['clock_time_s']) not in (int,float)
                                              or not math.isfinite(context['clock_time_s'])):
        raise ValueError('minimap_frame_context_clock_invalid')
    for key in ('sequence','sent_monotonic_ns','capture_monotonic_ns'):
        if type(value.get(key)) is not int or not 0 < value[key] < 2**63:
            raise ValueError('minimap_frame_context_monotonic_invalid')
    captured = row.get('captured_monotonic')
    if (type(captured) not in (int,float) or not math.isfinite(captured) or captured <= 0
            or round(captured*1e9) != value['capture_monotonic_ns']
            or not 0 <= value['capture_monotonic_ns']-value['sent_monotonic_ns'] <= 2_500_000_000):
        raise ValueError('minimap_frame_context_future_or_expired')
    age = (utc(row.get('captured_at_utc'))-utc(context['provider_at_utc'])).total_seconds()
    claimed = value.get('provider_age_at_capture_s')
    if (not 0 <= age <= 5 or type(claimed) not in (int,float)
            or not math.isfinite(claimed) or abs(claimed-age) > 1e-6):
        raise ValueError('minimap_frame_context_provider_not_fresh')
    return value
