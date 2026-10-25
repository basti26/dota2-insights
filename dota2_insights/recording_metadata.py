"""Bounded blind navigation and user markers; never import program answers."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re


def read_bounded_json(path, limit):
    with Path(path).open('rb') as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError('recording_metadata_size_limit')
    return json.loads(body), body


def blind_queue(value):
    if (not isinstance(value, dict) or value.get('kind') != 'blind_ward_review_queue'
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or not isinstance(value.get('samples'), list)
            or not 1 <= len(value['samples']) <= 200):
        raise ValueError('invalid_blind_navigation')
    rows, seen = [], set()
    for row in value['samples']:
        if not isinstance(row, dict):
            raise ValueError('invalid_blind_navigation_row')
        mid, key = row.get('match_id'), row.get('sample_id')
        nav, index = row.get('navigation_game_s'), row.get('record_index')
        if (not isinstance(mid, str) or not re.fullmatch(r'[0-9]{1,16}', mid)
                or not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', key)
                or type(index) is not int or index < 0 or (mid, key) in seen
                or row.get('group') not in ('random', 'special') or 'navigation_game_s' not in row
                or (nav is not None and (type(nav) not in (int, float) or not math.isfinite(nav) or abs(nav) > 86400))
                or row.get('end_navigation_game_s') is not None
                or not isinstance(row.get('package_sha256'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', row['package_sha256'])):
            raise ValueError('invalid_blind_navigation_row')
        seen.add((mid, key))
        rows.append({name: row[name] for name in ('match_id', 'sample_id', 'record_index',
                     'group', 'package_sha256', 'navigation_game_s')})
        rows[-1]['end_navigation_game_s'] = None
    return {'schema_version': 1, 'kind': 'blind_ward_review_queue', 'sample_count': len(rows),
            'samples': rows, 'independent_validation_passed': False}


def single_match_queue(value, match_id=None):
    """A recording's navigation must belong to that one declared replay."""
    clean = blind_queue(value)
    identities = {row['match_id'] for row in clean['samples']}
    if len(identities) != 1:
        raise ValueError('请使用单场比赛的导航文件，不能把多场导航混入一次录制。')
    actual = next(iter(identities))
    if match_id is not None and match_id != actual:
        raise ValueError('导航文件的比赛编号与当前录制不一致，请先核对比赛。')
    return clean, actual


def read_markers(path):
    with Path(path).open('rb') as stream:
        body = stream.read(1024**2 + 1)
    if len(body) > 1024**2:
        raise ValueError('recording_marker_size_limit')
    lines = body.splitlines()
    if len(lines) > 10000:
        raise ValueError('recording_marker_count_limit')
    rows = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict) or row.get('kind') != 'manual_review_marker' or row.get('basis') != 'user_marker_receipt_not_game_event_time':
            raise ValueError('invalid_user_marker')
        index = row.get('last_published_frame_index')
        mapping = row.get('recovery_mapping')
        if not (type(index) is int and 0 <= index <= 14400) and not (index is None and mapping == 'source_frame_missing'):
            raise ValueError('invalid_user_marker_frame')
        try:
            stamp = datetime.fromisoformat(row['at_utc'])
            if stamp.utcoffset() != timezone.utc.utcoffset(stamp):
                raise ValueError('marker_utc_required')
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('invalid_user_marker_time') from exc
        item = {key: row[key] for key in ('kind', 'basis', 'at_utc', 'last_published_frame_index')}
        if mapping is not None:
            source = row.get('source_frame_index')
            if mapping not in ('exact_source_frame', 'source_frame_missing') or type(source) is not int or not 0 <= source <= 14400:
                raise ValueError('invalid_marker_recovery_mapping')
            if ((mapping == 'source_frame_missing' and index is not None)
                    or (mapping == 'exact_source_frame' and (type(index) is not int or index < 1))):
                raise ValueError('inconsistent_marker_recovery_mapping')
            item.update(recovery_mapping=mapping, source_frame_index=source)
        rows.append(item)
    return rows, body
