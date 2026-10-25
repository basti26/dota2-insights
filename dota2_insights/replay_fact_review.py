"""Bounded facts review of existing offline archives; no DEM/parser/game access."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import stat

from .replay_death_semantics import summarize_death_semantics
from .replay_periods import PERIODS, bind_available_end_evidence, iter_period_labels


MIB = 1024 ** 2
MAX_FILE_BYTES = 256 * MIB
MAX_TOTAL_SOURCE_BYTES = 320 * MIB
MAX_METADATA_BYTES = MIB
MAX_LINE_BYTES = 64 * 1024
MAX_STREAM_ROWS = 200000
MAX_TOTAL_ROWS = 400000
MAX_OUTPUT_BYTES = 128 * MIB
MAX_HEROES = 10
_FILES = frozenset(('events.jsonl', 'hero_samples.jsonl', 'visibility_transitions.jsonl'))
_SHA = re.compile(r'[0-9a-f]{64}')
_HERO = re.compile(r'npc_dota_hero_[a-z0-9_]{1,100}')
_METRICS = ('net_worth', 'total_earned_gold', 'total_earned_xp', 'last_hits', 'denies')


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('review_duplicate_json_key')
            value[key] = item
        return value

    def nonfinite(_):
        raise ValueError('review_nonfinite_json')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=nonfinite)
        if type(value) is not dict:
            raise ValueError('review_json_object_required')
        # Reject exponent overflow, including fields not used in the report.
        json.dumps(value, allow_nan=False)
    except (UnicodeError, json.JSONDecodeError, OverflowError, RecursionError) as exc:
        raise ValueError('review_invalid_json') from exc
    return value


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')


def _safe_path(value, *, directory=False, missing=False):
    path = Path(os.path.abspath(value))
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if candidate == path and missing:
                continue
            raise
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)):
            raise ValueError('review_link_path_forbidden')
    if not missing or path.exists():
        if directory and not path.is_dir() or not directory and not path.is_file():
            raise ValueError('review_regular_path_required')
    return path


def _signature(info):
    # Windows may finalize creation-time metadata after a fresh file is closed.
    # Identity, size, mtime and exact content hashes establish the used source.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


class _Inputs:
    def __init__(self, guard):
        self.guard, self.items, self.total_bytes, self.total_rows = guard, {}, 0, 0

    def read(self, path, *, small=False, expected=None):
        path = _safe_path(path)
        before = path.stat()
        limit = min(MAX_METADATA_BYTES, MAX_FILE_BYTES) if small else MAX_FILE_BYTES
        if before.st_size > limit:
            raise ValueError('review_source_file_bytes_limit')
        if path not in self.items and self.total_bytes + before.st_size > MAX_TOTAL_SOURCE_BYTES:
            raise ValueError('review_total_source_bytes_limit')
        digest, chunks, total = hashlib.sha256(), [], 0
        with path.open('rb') as stream:
            if _signature(os.fstat(stream.fileno())) != _signature(before):
                raise ValueError('review_source_changed')
            while chunk := stream.read(min(MIB, limit + 1)):
                self.guard()
                total += len(chunk)
                if total > limit:
                    raise ValueError('review_source_file_bytes_limit')
                digest.update(chunk)
                if small:
                    chunks.append(chunk)
        if _signature(_safe_path(path).stat()) != _signature(before):
            raise ValueError('review_source_changed')
        measured = {'bytes': total, 'sha256': digest.hexdigest()}
        if expected is not None and measured != expected:
            raise ValueError('review_source_bytes_or_sha256_mismatch')
        previous = self.items.get(path)
        if previous is not None and (previous['signature'] != _signature(before)
                                     or previous['measured'] != measured):
            raise ValueError('review_source_changed')
        if previous is None:
            self.items[path] = {'signature': _signature(before), 'measured': measured}
            self.total_bytes += total
        return b''.join(chunks) if small else measured

    def rows(self, path):
        path = _safe_path(path)
        source = self.items[path]
        if _signature(path.stat()) != source['signature']:
            raise ValueError('review_source_changed')
        digest, count, total = hashlib.sha256(), 0, 0
        with path.open('rb') as stream:
            if _signature(os.fstat(stream.fileno())) != source['signature']:
                raise ValueError('review_source_changed')
            while raw := stream.readline(MAX_LINE_BYTES + 1):
                if count % 128 == 0:
                    self.guard()
                if len(raw) > MAX_LINE_BYTES or not raw.endswith(b'\n'):
                    raise ValueError('review_jsonl_line_bytes_or_termination')
                count += 1
                self.total_rows += 1
                if count > MAX_STREAM_ROWS or self.total_rows > MAX_TOTAL_ROWS:
                    raise ValueError('review_source_row_limit')
                total += len(raw)
                if total > MAX_FILE_BYTES:
                    raise ValueError('review_source_file_bytes_limit')
                digest.update(raw)
                yield _json(raw)
        if (_signature(_safe_path(path).stat()) != source['signature']
                or {'bytes': total, 'sha256': digest.hexdigest()} != source['measured']):
            raise ValueError('review_source_changed')
        self.guard()

    def verify_unchanged(self):
        for path in tuple(self.items):
            self.read(path, expected=self.items[path]['measured'])


class _Outputs:
    def __init__(self, folder):
        self.folder, self.bytes, self.files = folder, 0, {}

    def append(self, stream, data):
        # Reserve a small control record for FAILED.json on any later failure.
        if self.bytes + len(data) > MAX_OUTPUT_BYTES - min(4096, MAX_OUTPUT_BYTES // 4):
            raise ValueError('review_output_bytes_limit')
        stream.write(data)
        self.bytes += len(data)

    def write(self, name, data):
        with (self.folder / name).open('xb') as stream:
            self.files[name] = True
            self.append(stream, data)

    def failed(self, exc):
        # A failed run must not retain a successful report marker.
        for name in ('report.json', 'report.html'):
            if name in self.files:
                (self.folder / name).unlink(missing_ok=True)
        message = str(exc)
        code = message if re.fullmatch(r'[a-zA-Z0-9_]{1,200}', message) else type(exc).__name__
        data = _encoded({'schema_version': 1, 'kind': 'failed_replay_fact_review',
                         'complete': False, 'error_type': type(exc).__name__, 'error_code': code,
                         'partial_derivatives_valid': False})
        try:
            with (self.folder / 'FAILED.json').open('xb') as stream:
                stream.write(data)
        except OSError:
            pass  # Preserve the primary failure when the filesystem itself fails.


def _identity(manifest):
    mid = manifest.get('match_id')
    if (type(mid) is not str or re.fullmatch(r'[1-9][0-9]{0,19}', mid) is None
            or int(mid) > 2 ** 64 - 1):
        raise ValueError('review_match_id_invalid')
    return mid


def _validate_manifest(manifest, mid):
    if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
            or manifest.get('kind') != 'offline_replay_archive'
            or type(manifest.get('source_sha256')) is not str
            or _SHA.fullmatch(manifest['source_sha256']) is None):
        raise ValueError('review_manifest_identity_invalid')
    if ('source_file' in manifest and manifest['source_file'] != mid + '.dem'
            or manifest.get('parsed_match_id') not in (None, mid)):
        raise ValueError('review_manifest_match_mismatch')
    files = manifest.get('files')
    if type(files) is not dict or set(files) - _FILES:
        raise ValueError('review_manifest_files_invalid')
    for entry in files.values():
        if (type(entry) is not dict or set(entry) != {'bytes', 'sha256'}
                or type(entry['bytes']) is not int or not 0 <= entry['bytes'] <= MAX_FILE_BYTES
                or type(entry['sha256']) is not str or _SHA.fullmatch(entry['sha256']) is None):
            raise ValueError('review_manifest_file_identity_invalid')
    if sum(entry['bytes'] for entry in files.values()) > MAX_TOTAL_SOURCE_BYTES:
        raise ValueError('review_total_source_bytes_limit')
    if 'counts' in manifest and type(manifest['counts']) is not dict:
        raise ValueError('review_manifest_counts_invalid')


def _new_stats(status='present'):
    return {'status': status, 'rows': 0 if status == 'present' else None,
            'period_counts': dict.fromkeys(PERIODS, 0) if status == 'present' else None,
            'clock_order_regressions': 0 if status == 'present' else None,
            'tick_order_regressions': 0 if status == 'present' else None,
            'min_observed_game_time_s': None, 'max_observed_game_time_s': None}


def _labeled_rows(inputs, archive, name, manifest, evidence, outputs, stats, output_name=None):
    current, previous = [None], {'clock': None, 'tick': None}
    def rows():
        for row in inputs.rows(archive / name):
            expected = {'events.jsonl': 'combat_event', 'hero_samples.jsonl': 'hero_sample'}.get(name)
            if expected and row.get('kind') != expected:
                raise ValueError('review_stream_kind_mismatch')
            if name == 'events.jsonl' and (type(row.get('event_type')) is not str
                    or re.fullmatch(r'[A-Z][A-Z0-9_]{0,63}', row['event_type']) is None):
                raise ValueError('review_event_type_invalid')
            current[0] = row
            yield row
    target = (outputs.folder / output_name).open('xb') if output_name else None
    try:
        for label in iter_period_labels(rows(), manifest, evidence, source_name=name):
            row = current[0]
            stats['rows'] += 1
            stats['period_counts'][label['period']] += 1
            clock, tick = label['source_clocks'].get('game_time_s'), label['source_clocks'].get('tick')
            for value, key, counter in ((clock, 'clock', 'clock_order_regressions'),
                                        (tick, 'tick', 'tick_order_regressions')):
                if value is not None:
                    if previous[key] is not None and value < previous[key]:
                        stats[counter] += 1
                    previous[key] = value
            if clock is not None:
                old_min, old_max = stats['min_observed_game_time_s'], stats['max_observed_game_time_s']
                stats['min_observed_game_time_s'] = clock if old_min is None else min(clock, old_min)
                stats['max_observed_game_time_s'] = clock if old_max is None else max(clock, old_max)
            if target:
                outputs.append(target, _encoded(label))
            yield row, label
    finally:
        if target:
            target.close()
    if stats['rows'] == 0:
        stats['status'] = 'present_empty'


def _snapshot(row, label):
    snapshot = {'source_ref': label['source_ref'], 'period': label['period'],
                'source_clocks': label['source_clocks']}
    for key in ('hero_id', 'hero_name', 'hero_numeric_id', 'team', 'level'):
        if key in row:
            snapshot[key] = row[key]
    for key in ('hero_numeric_id', 'level'):
        value = row.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError('review_hero_numeric_value_invalid')
    metrics = row.get('lane_metrics')
    if metrics is not None:
        if type(metrics) is not dict:
            raise ValueError('review_hero_metrics_invalid')
        snapshot['lane_metrics'] = {key: metrics[key] for key in _METRICS if key in metrics}
        if any(value is not None and (type(value) is not int or value < 0)
               for value in snapshot['lane_metrics'].values()):
            raise ValueError('review_hero_metric_value_invalid')
    elif 'lane_metrics' in row:
        snapshot['lane_metrics'] = None
    if 'inventory' in row:
        inventory = row['inventory']
        if inventory is not None and type(inventory) is not list:
            raise ValueError('review_hero_inventory_invalid')
        if inventory is None:
            snapshot['inventory'] = None
        else:
            filtered, slots = [], set()
            for item in inventory:
                if type(item) is not dict or type(item.get('slot')) is not int:
                    raise ValueError('review_hero_inventory_slot_invalid')
                slot = item['slot']
                if not 0 <= slot <= 8:
                    continue
                if slot in slots:
                    raise ValueError('review_hero_inventory_duplicate_slot')
                slots.add(slot)
                for key in ('status', 'item_name', 'item_key'):
                    value = item.get(key)
                    if value is not None and (type(value) is not str or len(value) > 128):
                        raise ValueError('review_hero_inventory_value_invalid')
                for key in ('item_id', 'charges'):
                    value = item.get(key)
                    if value is not None and (type(value) is not int or value < 0):
                        raise ValueError('review_hero_inventory_value_invalid')
                filtered.append({key: item[key] for key in
                                 ('slot', 'status', 'item_name', 'item_key', 'item_id', 'charges')
                                 if key in item})
            snapshot['inventory'] = filtered
    return snapshot


def _heroes(rows):
    heroes, unknown, unknown_refs = {}, 0, []
    for row, label in rows:
        name, team = row.get('hero_name', row.get('hero_id')), row.get('team')
        if (type(name) is not str or _HERO.fullmatch(name) is None
                or type(team) is not int or team not in (2, 3)):
            unknown += 1
            if len(unknown_refs) < 50:
                unknown_refs.append(label['source_ref'])
            continue
        if row.get('hero_id') not in (None, name) or row.get('hero_name') not in (None, name):
            raise ValueError('review_hero_identity_mismatch')
        key = str(team) + ':' + name
        if key not in heroes:
            if len(heroes) >= MAX_HEROES:
                raise ValueError('review_hero_identity_limit')
            heroes[key] = {'hero_id': name, 'team_raw': team, 'observed_rows': 0,
                           'latest_in_match': None, 'latest_boundary': None,
                           'first_tail': None, 'latest_any': None}
        hero = heroes[key]
        value = _snapshot(row, label)
        hero['observed_rows'] += 1
        hero['latest_any'] = value
        if label['period'] == 'in_match':
            hero['latest_in_match'] = value
        elif label['period'] == 'end_boundary_uncertain':
            hero['latest_boundary'] = value
        elif label['period'] == 'post_match_tail' and hero['first_tail'] is None:
            hero['first_tail'] = value
    return {'status': 'observed' if heroes else 'no_known_identity_observed',
            'identity_basis': 'recorded_canonical_name_and_raw_team; no_roster_or_future_backfill',
            'known_hero_identities': len(heroes), 'heroes': heroes,
            'unknown_identity_rows': unknown, 'unknown_identity_refs': unknown_refs,
            'unknown_identity_refs_omitted': max(0, unknown - len(unknown_refs)),
            'selection': 'source_order; latest_any_is_not_end_of_match',
            'unknown_values': 'missing_or_null_retained; never_filled_with_zero',
            'inventory_scope': 'recorded_slots_0_through_8_only'}


def review_archive(archive, output, *, facts=None, receipt=None, guard=lambda: None):
    """Write a new independent report from verified existing files only.

    A bounded manifest read is the identity gate for random job/archive paths.
    The sealed-match guard runs before facts or any archive data stream is read.
    Success means the derivative completed, independently of source completeness.
    Existing output directories are always refused, including previous failures.
    """
    from .replay_holdout import refuse_locked_match
    archive = _safe_path(archive, directory=True)
    for part in archive.parts:
        if re.fullmatch(r'[1-9][0-9]{0,19}', part):
            refuse_locked_match(part)
    guard()
    inputs = _Inputs(guard)
    manifest_raw = inputs.read(archive / 'manifest.json', small=True)
    manifest = _json(manifest_raw)
    mid = _identity(manifest)
    refuse_locked_match(mid)
    output = _safe_path(output, directory=True, missing=True)
    if output == archive or output.is_relative_to(archive) or archive.is_relative_to(output):
        raise ValueError('review_output_must_be_separate')
    output.mkdir(exist_ok=False)
    outputs = _Outputs(output)
    try:
        _validate_manifest(manifest, mid)
        guard()
        for name, expected in manifest['files'].items():
            inputs.read(archive / name, expected=expected)
        facts_raw = inputs.read(facts, small=True) if facts is not None else None
        if facts_raw is not None:
            _json(facts_raw)
        request = _json(inputs.read(receipt, small=True)) if receipt is not None else None
        evidence = bind_available_end_evidence(
            manifest, facts_raw, request,
            facts_ref=Path(facts).name if facts is not None else 'facts.json',
            request_ref=Path(receipt).name if receipt is not None else 'request.json')
        streams = {}
        if 'events.jsonl' in manifest['files']:
            stats = streams['events'] = _new_stats()
            rows = _labeled_rows(inputs, archive, 'events.jsonl', manifest, evidence,
                                 outputs, stats, 'event-periods.jsonl')
            deaths = summarize_death_semantics(
                ({**row, 'source_ref': label['source_ref']} for row, label in rows),
                max_events_kept=100, max_refs_per_bucket=50, max_heroes=64)
            deaths['status'] = stats['status']
        else:
            streams['events'] = _new_stats('missing')
            deaths = {'status': 'missing', 'counts': None, 'reason': 'events_stream_not_declared'}
        if 'hero_samples.jsonl' in manifest['files']:
            stats = streams['hero_samples'] = _new_stats()
            heroes = _heroes(_labeled_rows(inputs, archive, 'hero_samples.jsonl', manifest,
                                           evidence, outputs, stats, 'hero-periods.jsonl'))
        else:
            streams['hero_samples'] = _new_stats('missing')
            heroes = {'status': 'missing', 'known_hero_identities': None, 'heroes': None}
        for name in manifest['files'].keys() - {'events.jsonl', 'hero_samples.jsonl'}:
            stats = streams[name] = _new_stats()
            for _ in _labeled_rows(inputs, archive, name, manifest, evidence, outputs, stats):
                pass
        for field, stream_name in (('events', 'events'), ('hero_samples', 'hero_samples')):
            expected = manifest.get('counts', {}).get(field)
            if expected is not None and (type(expected) is not int or expected < 0
                    or streams[stream_name]['rows'] is not None and expected != streams[stream_name]['rows']):
                raise ValueError('review_manifest_row_count_mismatch')
        source_complete = (manifest.get('export_complete') is True
                           and manifest.get('stop_reason') in ('replay_eof', 'requested_game_window_end')
                           and manifest.get('parse_error') is None
                           and manifest.get('truncated_at_tick') is None
                           and manifest.get('source_unchanged_stat') is not False)
        report = {'schema_version': 1, 'kind': 'offline_replay_fact_review', 'complete': True,
                  'created_at_utc': datetime.now(timezone.utc).isoformat(), 'match_id': mid,
                  'source_replay_sha256': manifest['source_sha256'],
                  'source_manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
                  'source_status': {'complete': source_complete,
                                    'export_complete': manifest.get('export_complete'),
                                    'stop_reason': manifest.get('stop_reason'),
                                    'parse_error_present': manifest.get('parse_error') is not None,
                                    'truncated_at_tick': manifest.get('truncated_at_tick')},
                  'end_evidence': evidence.as_dict(), 'streams': streams,
                  'death_semantics': deaths, 'hero_facts': heroes,
                  'input_fingerprints': {str(path): value['measured'] for path, value in inputs.items.items()},
                  'input_bytes': inputs.total_bytes, 'input_rows': inputs.total_rows,
                  'limitations': ['Period labels are derivatives of recorded clocks, not rewritten events.',
                                 'Missing streams are not measured zero; source completeness is separate.',
                                 'Death candidates are unresolved and are not a scoreboard death count.',
                                 'Latest tail state cannot replace latest in-match state.',
                                 'No network, game, DEM parsing, or live assistant input is used.']}
        inputs.verify_unchanged()
        guard()
        rendered = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                    '<title>离线录像事实复核</title><style>body{font:14px system-ui;max-width:1100px;'
                    'margin:2em auto;padding:0 1em}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
                    '<h1>离线录像事实复核</h1><p>分期和事件候选保留不确定性；候选数不是战绩死亡数。</p><pre>'
                    + html.escape(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
                    + '</pre></html>').encode('utf-8')
        outputs.write('report.html', rendered)
        outputs.write('report.json', _encoded(report))
        return {'schema_version': 1, 'complete': True, 'match_id': mid,
                'source_complete': source_complete, 'report_json': str(output / 'report.json'),
                'report_html': str(output / 'report.html'), 'streams': streams,
                'death_counts': deaths['counts'], 'end_evidence_status': evidence.status,
                'output_bytes': outputs.bytes}
    except BaseException as exc:
        outputs.failed(exc)
        raise
