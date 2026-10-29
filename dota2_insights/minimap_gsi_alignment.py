"""Join recorded source IDs, never nearby timestamps or inferred pixel identities."""
from collections import Counter, defaultdict
from contextlib import ExitStack
import re

from .gsi_evidence_census import GsiEvidenceCensus
from .helper_handoff import utc
from .match_journal_index import resolve_reference as resolve_gsi
from .match_journals import open_rows as open_gsi
from .minimap_index import resolve_reference as resolve_minimap
from .minimap_handoff import open_rows as open_minimap

IDENTITY = ('match_id', 'game_session_id', 'record_session_id')


def source_id(value):
    return isinstance(value, str) and re.fullmatch(r'gsi_[a-f0-9]{32}', value) is not None


def prepare(entry, native_entries, roots):
    """All readers reverify at exit, before the caller may publish an output."""
    if entry.get('state') != 'ready_with_gaps':
        raise ValueError('alignment_minimap_not_ready')
    root, run = resolve_minimap(entry, roots)
    samples, wanted, found = [], set(), {}
    source_packages = []
    selected_by_scope, verified_by_scope = defaultdict(list), defaultdict(set)
    native_entries = list(native_entries)
    for native in native_entries:
        selected_by_scope[tuple(native.get(k) for k in IDENTITY)].append(native)
    with ExitStack() as stack:
        doc, _, rows = stack.enter_context(open_minimap(root, run, expected_package_id=entry['package_id']))
        if doc['run_id'] != entry.get('run_id'):
            raise ValueError('alignment_run_index_conflict')
        for filename, line, row, origin in rows:
            if filename != 'samples.jsonl' or row.get('kind') != 'sample':
                continue
            binding = row.get('game_context_binding')
            context = binding.get('context') if binding is not None else None
            item = {key: row.get(key) for key in
                    ('sequence', 'captured_at_utc', 'captured_monotonic', 'image', 'image_sha256')}
            item.update(sample_line=line, sample_line_sha256=origin['line_sha256'],
                        source_record_status='unknown', reason='context_unconfigured',
                        pixel_identity_verified=False, calibration_verified=False)
            if binding is not None:
                item['reason'] = binding.get('reason', 'source_id_not_recorded')
            if context is not None:
                item['context'] = context
                if context['match_id'] is None:
                    item['reason'] = 'public_match_id_unknown'
                elif source_id(context.get('gsi_source_id')):
                    wanted.add(tuple(context[k] for k in IDENTITY) + (context['gsi_source_id'],))
                    item['reason'] = 'source_record_not_received_or_not_in_selected_closed_packages'
            samples.append(item)
        scopes = {key[:3] for key in wanted}
        for native in native_entries:
            scope = tuple(native.get(k) for k in IDENTITY)
            if native.get('state') != 'ready_with_gaps' or scope not in scopes:
                continue
            manifest, records = stack.enter_context(open_gsi(
                resolve_gsi(native, roots), expected_package_id=native['package_id']))
            verified_by_scope[scope].add(native['package_id'])
            if tuple(manifest[k] for k in IDENTITY) != scope:
                raise ValueError('alignment_native_index_identity_conflict')
            source_packages.append(native['package_id'])
            for line, row in enumerate(records, 1):
                key = scope + (row.get('gsi_source_id'),)
                if key not in wanted:
                    continue
                if key in found:
                    found[key]['count'] += 1
                    continue  # Even identical repeats do not establish a unique recorded row.
                census = GsiEvidenceCensus(collect_positions=True, position_limit=1)
                census.add(row, line=line)
                found[key] = {'count': 1, 'package_id': native['package_id'], 'line': line,
                    'sequence': row['sequence'], 'provider_at_utc': row.get('provider_at_utc'),
                    'clock_time_s': row.get('clock_time_s'),
                    'game_state': (row.get('map_context') or {}).get('game_state'),
                    'position': census.positions[0] if census.positions else None}
        for item in samples:
            context = item.get('context')
            if context is None or not source_id(context.get('gsi_source_id')) or context['match_id'] is None:
                continue
            match = found.get(tuple(context[k] for k in IDENTITY) + (context['gsi_source_id'],))
            if match is None:
                continue
            if match['count'] != 1:
                item.update(reason='source_id_ambiguous', matching_record_count=match['count'])
                continue
            try:
                same_provider_time = utc(context['provider_at_utc']) == utc(match['provider_at_utc'])
            except (ValueError, TypeError):
                same_provider_time = False
            if (not same_provider_time
                    or context['clock_time_s'] != match['clock_time_s']
                    or context['game_state'] != match['game_state']):
                item['reason'] = 'source_id_content_disagrees'
                continue
            position = match['position']
            item.update(source_record_status='exact_recorded_source_id', reason=None,
                native_source={k: match[k] for k in ('package_id', 'line', 'sequence')},
                position=position, position_evidence_usable=bool(position is not None
                    and position['alive'] is True and position['source_age_s'] is not None
                    and 0 <= position['source_age_s'] <= 5 and position['paused'] is False))
    counts = Counter(item['reason'] or 'exact_recorded_source_id' for item in samples)
    scope_rows = Counter(tuple(item['context'][k] for k in IDENTITY) for item in samples
                         if item.get('context') is not None and item['context']['match_id'] is not None
                         and source_id(item['context'].get('gsi_source_id')))
    scope_selection = []
    for scope, count in sorted(scope_rows.items()):
        packages = sorted(verified_by_scope[scope])
        status = ('matching_packages_verified' if packages else 'matching_index_entries_not_ready'
                  if selected_by_scope[scope] else 'scope_not_in_selected_index')
        scope_selection.append({**dict(zip(IDENTITY, scope)), 'frame_rows': count,
            'lookup_status': status, 'verified_native_package_ids': packages,
            'indexed_not_ready_entries': sum(n.get('state') != 'ready_with_gaps' for n in selected_by_scope[scope]),
            'wanted_distinct_source_ids': sum(key[:3] == scope for key in wanted)})
    return {'schema_version': 1, 'kind': 'minimap_gsi_source_alignment', 'run_id': run,
        'minimap_package_id': entry['package_id'], 'native_package_ids': sorted(set(source_packages)),
        'native_scope_selection': scope_selection,
        'sample_rows': len(samples), 'outcomes': dict(counts), 'samples': samples,
        'source_verified_before_and_after': True, 'images_verified': False,
        'pixel_identity_verified': False, 'calibration_verified': False,
        'basis': 'explicit_source_id_and_match_and_both_sessions_not_timestamp_join'}


def render_markdown(report):
    lines = ['# 小地图与本人GSI来源配对', '', f"采样run：{report['run_id']}",
        f"样本数：{report['sample_rows']}；结果：{report['outcomes']}", '',
        '只按新记录的来源ID、公开比赛号和双会话配对；时间戳相同或最邻近均不作配对依据。',
        '精确来源记录仍不证明截图中的本人位置，尚未核验PNG或地图标定；不能作为自动训练真值。', '',
        '| 样本序号 | 配对状态 | 本人坐标 | 存活/新鲜/非暂停证据可用 |',
        '| --- | --- | --- | --- |']
    for item in report['samples'][:100]:
        p = item.get('position')
        coordinates = f"{p['xpos']}, {p['ypos']}" if p is not None else '未知'
        lines.append(f"| {item['sequence']} | {item['reason'] or item['source_record_status']} | "
                     f"{coordinates} | {item.get('position_evidence_usable', False)} |")
    if len(report['samples']) > 100:
        lines.append('\n正文只列前100条；JSON保留所有实际样本。')
    lines += ['', '## GSI来源范围选择', '',
              'scope_not_in_selected_index仅表示本次索引没有选中该比赛和双会话，不证明原记录不存在。',
              '有已核验包仍缺某来源ID时，可能是采样省略；不能按时间补配对。', '',
              '| 比赛 | 范围状态 | 涉及帧 | 不同来源ID | 已核验包数 |',
              '|---|---|---:|---:|---:|']
    for scope in report.get('native_scope_selection', []):
        lines.append(f"| {scope['match_id']} | {scope['lookup_status']} | {scope['frame_rows']} | "
                     f"{scope['wanted_distinct_source_ids']} | {len(scope['verified_native_package_ids'])} |")
    return '\n'.join(lines) + '\n'


def summarize(report, *, native_index_state):
    """Compact daily evidence counts; these are coverage, never accuracy scores."""
    samples = report['samples']
    return {key: report[key] for key in ('minimap_package_id', 'native_package_ids', 'sample_rows',
        'basis', 'source_verified_before_and_after', 'images_verified', 'pixel_identity_verified',
        'calibration_verified')} | {
        'native_source_index_state': native_index_state,
        'native_scope_selection': report.get('native_scope_selection', []),
        'frame_source_id_rows': sum(source_id((item.get('context') or {}).get('gsi_source_id')) for item in samples),
        'exact_source_record_rows': sum(item['source_record_status'] == 'exact_recorded_source_id' for item in samples),
        'usable_position_rows': sum(item.get('position_evidence_usable', False) for item in samples),
        'unmatched_reasons': dict(Counter(item['reason'] for item in samples if item['reason'] is not None)),
        'meaning': 'recorded_source_coverage_not_pixel_or_position_accuracy'}
