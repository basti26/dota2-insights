"""Independent reader for immutable per-match GSI journals; no helper imports."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re

from .helper_handoff import utc
from .replay_holdout import refuse_locked_match

MAX_BYTES = 64 * 1024**2


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_match_journal_key')
            result[key] = value
        return result
    def invalid(_):
        raise ValueError('nonfinite_match_journal_value')
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError('match_journal_object_required')
    return value


def _read(path, limit):
    if path.is_symlink():
        raise ValueError('match_journal_symlink_refused')
    with path.open('rb') as stream:
        before = path.stat()
        raw = stream.read(limit + 1)
        after = path.stat()
    if len(raw) > limit or (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('match_journal_size_or_change')
    return raw


def verify(folder):
    folder = Path(folder)
    # Check public identity before opening a manifest or payload body.
    mid = folder.parent.name.removeprefix('match_')
    if not re.fullmatch(r'[1-9][0-9]{0,19}', mid) or not re.fullmatch(r'game_[a-f0-9]{32}', folder.name):
        raise ValueError('match_journal_layout_invalid')
    refuse_locked_match(mid)
    raw = _read(folder / 'manifest.json', 8192)
    digest = hashlib.sha256(raw).hexdigest()
    if _read(folder / 'COMPLETE', 66).strip() != digest.encode('ascii'):
        raise ValueError('match_journal_not_complete')
    document = _json(raw)
    if (document.get('schema_version') != 1 or type(document.get('schema_version')) is not int
            or document.get('kind') != 'foresight_match_journal'
            or document.get('channel') != 'self_economy'
            or document.get('match_id') != mid or document.get('game_session_id') != folder.name
            or not re.fullmatch(r'run_[a-f0-9]{32}', str(document.get('record_session_id', '')))
            or not re.fullmatch(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', str(document.get('run_id', '')))
            or document.get('match_end_evidence') != 'own_recorded_gsi_post_game'
            or document.get('scope') != 'received_gsi_samples_until_first_post_game_not_whole_match_proof'
            or document.get('other_channels') != 'not_closed_by_this_manifest'):
        raise ValueError('match_journal_contract_invalid')
    if folder.parents[1].name != 'closed-matches' or folder.parents[2].name != 'self-economy-' + document['run_id']:
        raise ValueError('match_journal_run_layout_conflict')
    first = utc(document.get('first_received_at_utc'))
    last = utc(document.get('last_received_at_utc'))
    if first > last or utc(document.get('closed_at_utc')) < last:
        raise ValueError('match_journal_time_invalid')
    dropped, limited = document.get('upstream_dropped_at_close'), document.get('upstream_limited_at_close')
    if type(dropped) is not int or dropped < 0 or type(limited) is not bool:
        raise ValueError('match_journal_health_invalid')
    if document.get('recording_integrity') != ('complete' if dropped == 0 and not limited else 'gaps'):
        raise ValueError('match_journal_health_disagrees')
    payload = document.get('payload') or {}
    if (payload.get('file') != 'samples.jsonl' or type(payload.get('bytes')) is not int
            or not 0 < payload['bytes'] <= MAX_BYTES or type(payload.get('rows')) is not int
            or not 1 <= payload['rows'] <= 100000):
        raise ValueError('match_journal_payload_invalid')
    data = _read(folder / 'samples.jsonl', payload['bytes'])
    if len(data) != payload['bytes'] or hashlib.sha256(data).hexdigest() != payload.get('sha256'):
        raise ValueError('match_journal_payload_changed')
    if not data.endswith(b'\n'):
        raise ValueError('match_journal_partial_tail')
    lines = data.splitlines(keepends=True)
    if len(lines) != payload['rows']:
        raise ValueError('match_journal_row_count')
    previous, seen_first, seen_last = 0, None, None
    for line in lines:
        if len(line) > 8192:
            raise ValueError('match_journal_line_limit')
        row = _json(line)
        if any(row.get(key) != document[key] for key in ('run_id', 'match_id', 'game_session_id', 'record_session_id')):
            raise ValueError('match_journal_identity_conflict')
        if type(row.get('event_version')) is not int or row['event_version'] != 1 or row.get('event_kind') != 'self_economy':
            raise ValueError('match_journal_event_invalid')
        seq = row.get('sequence')
        if type(seq) is not int or seq <= previous:
            raise ValueError('match_journal_sequence_invalid')
        previous = seq
        received = row.get('received_at_utc')
        utc(received)
        seen_first = received if seen_first is None else seen_first
        seen_last = received
    if (seen_first != document['first_received_at_utc'] or seen_last != document['last_received_at_utc']
            or (row.get('map_context') or {}).get('game_state') != 'DOTA_GAMERULES_STATE_POST_GAME'):
        raise ValueError('match_journal_end_not_proved')
    return document, digest


@contextmanager
def open_rows(folder, *, expected_package_id=None):
    document, digest = verify(folder)
    if expected_package_id is not None and digest != expected_package_id:
        raise ValueError('match_journal_index_source_changed')
    active = True
    def rows():
        if not active:
            raise ValueError('match_journal_reader_closed')
        with (Path(folder) / 'samples.jsonl').open('rb') as stream:
            remaining = document['payload']['bytes']
            while active and remaining:
                line = stream.readline(min(8193, remaining + 1))
                if not line or len(line) > min(8192, remaining) or not line.endswith(b'\n'):
                    raise ValueError('match_journal_changed_during_read')
                remaining -= len(line)
                yield _json(line)
            if active and remaining == 0:
                if stream.read(1):
                    raise ValueError('match_journal_grew_during_read')
                return
            raise ValueError('match_journal_reader_closed')
    try:
        yield document, rows()
    finally:
        active = False
        if verify(folder)[1] != digest:
            raise ValueError('match_journal_manifest_changed')


def scan(root, *, latest=20):
    """Source checkout history only; no mutable launcher status is required."""
    root = Path(root).resolve()
    if type(latest) is not int or not 1 <= latest <= 128:
        raise ValueError('match_journal_scan_limit_invalid')
    results = []
    paths = sorted(root.glob('self-economy/self-economy-*/closed-matches/match_*/game_*'),
                   key=lambda p: p.stat().st_mtime_ns, reverse=True)[:latest]
    for folder in paths:
        relative = folder.relative_to(root).as_posix()
        try:
            if not folder.resolve().is_relative_to(root) or any(p.is_symlink() for p in (folder, *folder.parents) if p.is_relative_to(root)):
                raise ValueError('match_journal_outside_history')
            document, digest = verify(folder)
            results.append({'state': 'ready_with_gaps', 'source': relative, 'package_id': digest,
                            'match_id': document['match_id'], 'channel': 'self_economy',
                            'recording_integrity': document['recording_integrity'],
                            'rows': document['payload']['rows'],
                            'other_channels': 'not_closed_by_this_manifest'})
        except (OSError, ValueError, TypeError, KeyError) as exc:
            results.append({'state': 'blocked', 'source': relative, 'error_type': type(exc).__name__})
    return results
