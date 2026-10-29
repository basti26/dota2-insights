"""Verify BP log prefixes while original session logs may still append tails."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re

from .helper_handoff import utc
from .match_journals import _json, _read
from .replay_holdout import refuse_locked_match

STREAMS = ('events.jsonl', 'ui-presented.jsonl', 'manual-actions.jsonl', 'panel-visibility.jsonl',
           'portrait-visibility.jsonl', 'chat-candidates.jsonl', 'ban-window-candidates.jsonl')
UNSPECIFIED = object()


def _path(root, sid, name):
    root = Path(root).resolve(strict=True)
    path = root / 'drafts' / ('session_' + sid) / name
    if not path.resolve().is_relative_to(root):
        raise ValueError('draft_prefix_outside_root')
    for parent in (path, *path.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('draft_prefix_link_refused')
    return path


def _rows(path, spec):
    digest, rows, remaining = hashlib.sha256(), 0, spec['bytes']
    with path.open('rb') as stream:
        if path.stat().st_size < remaining:
            raise ValueError('draft_prefix_source_shrunk')
        while remaining:
            line = stream.readline(min(1024**2 + 1, remaining + 1))
            if not line or len(line) > min(1024**2, remaining) or not line.endswith(b'\n'):
                raise ValueError('draft_prefix_partial_or_large_line')
            remaining -= len(line)
            rows += 1
            if rows > spec['rows']:
                raise ValueError('draft_prefix_row_count_changed')
            digest.update(line)
            yield rows, _json(line), hashlib.sha256(line).hexdigest()
    if rows != spec['rows'] or digest.hexdigest() != spec['sha256']:
        raise ValueError('draft_prefix_digest_changed')


def _identity(row, sid, mid):
    view_input = row.get('view_input') or {}
    if not isinstance(view_input, dict):
        raise ValueError('draft_prefix_view_input_invalid')
    for value in (row, row.get('snapshot'), view_input.get('snapshot')):
        if not isinstance(value, dict):
            continue
        if value.get('session_id') is not None and value['session_id'] != sid:
            raise ValueError('draft_prefix_session_conflict')
        observed = value.get('match_id')
        if observed is not None:
            if not isinstance(observed, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', observed):
                raise ValueError('draft_prefix_match_invalid')
            refuse_locked_match(observed)
            if mid != observed:
                raise ValueError('draft_prefix_match_conflict')


def _binding(root, session, prefix_id, cutoff_match):
    path = _path(root, session, 'phase-match-binding.json')
    marker = _path(root, session, 'phase-match-binding.COMPLETE')
    if not path.exists() and not marker.exists():
        return None, None
    raw = _read(path, 4096)
    document = _json(raw)
    mid = document.get('match_id')
    if not isinstance(mid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', mid):
        raise ValueError('draft_binding_match_invalid')
    refuse_locked_match(mid)
    digest = hashlib.sha256(raw).hexdigest()
    if _read(marker, 66).strip() != digest.encode():
        raise ValueError('draft_binding_not_complete')
    if (cutoff_match is not None or type(document.get('schema_version')) is not int or document['schema_version'] != 1
            or document.get('kind') != 'foresight_draft_match_binding' or document.get('session_id') != session
            or document.get('phase_prefix_package_id') != prefix_id
            or document.get('basis') != 'same_runtime_draft_session_explicit_match_binding'):
        raise ValueError('draft_binding_contract_invalid')
    utc(document.get('bound_recorded_at_utc'))
    return document, digest


def verify(root, session, *, expected_package_id=None, expected_binding_package_id=UNSPECIFIED):
    if not isinstance(session, str) or not re.fullmatch(r'[a-f0-9]{32}', session):
        raise ValueError('draft_prefix_session_invalid')
    raw = _read(_path(root, session, 'phase-handoff.json'), 16384)
    package_id = hashlib.sha256(raw).hexdigest()
    if expected_package_id is not None and package_id != expected_package_id:
        raise ValueError('draft_prefix_receipt_changed')
    document = _json(raw)
    mid = document.get('match_id_at_cutoff')
    if mid is not None:
        if not isinstance(mid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', mid):
            raise ValueError('draft_prefix_match_invalid')
        refuse_locked_match(mid)
    if _read(_path(root, session, 'phase-handoff.COMPLETE'), 66).strip() != package_id.encode():
        raise ValueError('draft_prefix_not_complete')
    if (type(document.get('schema_version')) is not int or document['schema_version'] != 1
            or document.get('kind') != 'foresight_draft_phase_prefix' or document.get('session_id') != session
            or document.get('ending_phase') not in ('strategy', 'in_game', 'finished')
            or document.get('scope') != 'processed_logs_through_first_recorded_ending_phase'
            or document.get('later_tail') != 'may_append_not_included_in_this_prefix'
            or document.get('images') != 'not_included_or_verified'
            or document.get('meaning') != 'recorded_phase_and_log_prefix_not_complete_draft_or_display_quality_proof'
            or any(type(document.get(k)) is not bool for k in ('source_error_present', 'source_limit_reached'))):
        raise ValueError('draft_prefix_contract_invalid')
    utc(document.get('cutoff_recorded_at_utc'))
    if document.get('last_observed_at_utc') is not None:
        utc(document['last_observed_at_utc'])
    specs, missing = document.get('prefixes'), document.get('missing_at_cutoff')
    if (not isinstance(specs, list) or not isinstance(missing, list) or len(specs) + len(missing) != len(STREAMS)
            or any(not isinstance(name, str) or name not in STREAMS for name in missing)):
        raise ValueError('draft_prefix_stream_roster_invalid')
    names = list(missing)
    total = 0
    for spec in specs:
        if (not isinstance(spec, dict) or spec.get('file') not in STREAMS
                or type(spec.get('bytes')) is not int or not 0 <= spec['bytes'] <= 32 * 1024**2
                or type(spec.get('rows')) is not int or not 0 <= spec['rows'] <= 10000
                or not isinstance(spec.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', spec['sha256'])):
            raise ValueError('draft_prefix_stream_invalid')
        total += spec['bytes']
        names.append(spec['file'])
    if total > 128 * 1024**2 or sorted(names) != sorted(STREAMS):
        raise ValueError('draft_prefix_stream_roster_invalid')
    binding, binding_id = _binding(root, session, package_id, mid)
    if expected_binding_package_id is not UNSPECIFIED and expected_binding_package_id != binding_id:
        raise ValueError('draft_binding_receipt_changed')
    effective = mid if mid is not None else binding['match_id'] if binding is not None else None
    for spec in specs:
        for _, row, _ in _rows(_path(root, session, spec['file']), spec):
            _identity(row, session, effective)
    return {**document, 'effective_match_id': effective, 'binding_package_id': binding_id, 'match_binding': binding}, package_id


@contextmanager
def open_rows(root, session, *, expected_package_id=None, expected_binding_package_id=UNSPECIFIED):
    document, package_id = verify(root, session, expected_package_id=expected_package_id,
                                  expected_binding_package_id=expected_binding_package_id)
    active = True
    def rows():
        if not active:
            raise ValueError('draft_prefix_reader_closed')
        for spec in document['prefixes']:
            for number, row, sha in _rows(_path(root, session, spec['file']), spec):
                if not active:
                    raise ValueError('draft_prefix_reader_closed')
                _identity(row, session, document['effective_match_id'])
                yield spec['file'], number, row, {'line_sha256': sha}
    try:
        yield document, rows()
    finally:
        active = False
        verify(root, session, expected_package_id=package_id, expected_binding_package_id=document['binding_package_id'])


def review(root, session, *, expected_package_id=None, expected_binding_package_id=UNSPECIFIED):
    from collections import Counter
    from .draft_diagnostics import DraftDiagnostics
    diagnostics = DraftDiagnostics()
    counts, event_phases, ui_phases = Counter(), Counter(), Counter()
    candidates = ui_candidates = ui_without_snapshot = 0
    with open_rows(root, session, expected_package_id=expected_package_id,
                   expected_binding_package_id=expected_binding_package_id) as (manifest, rows):
        for name, number, row, evidence in rows:
            diagnostics.add(name, number, row, evidence)
            counts[name] += 1
            if name == 'events.jsonl':
                snapshot = row.get('snapshot') or {}
                phase = snapshot.get('phase')
                if isinstance(phase, str):
                    event_phases[phase] += 1
                if phase == 'selection' and isinstance((row.get('advice') or {}).get('candidates'), list) and row['advice']['candidates']:
                    candidates += 1
            if name == 'ui-presented.jsonl':
                view = row.get('view_input') or {}
                snapshot = view.get('snapshot')
                if not isinstance(snapshot, dict):
                    ui_without_snapshot += 1
                    continue
                phase = snapshot.get('phase')
                if isinstance(phase, str):
                    ui_phases[phase] += 1
                if phase == 'selection' and isinstance((view.get('advice') or {}).get('candidates'), list) and view['advice']['candidates']:
                    ui_candidates += 1
    present = {spec['file'] for spec in manifest['prefixes']}
    return {'session_id': session, 'diagnostics': diagnostics.result(available_streams=present), 'match_id_at_cutoff': manifest['match_id_at_cutoff'],
        'cutoff_recorded_at_utc': manifest['cutoff_recorded_at_utc'],
        'last_observed_at_utc': manifest['last_observed_at_utc'],
        'effective_match_id': manifest['effective_match_id'], 'binding_package_id': manifest['binding_package_id'],
        'match_identity_basis': ('cutoff_snapshot' if manifest['match_id_at_cutoff'] is not None else
            'same_runtime_draft_session_explicit_match_binding' if manifest['match_binding'] is not None else 'unknown'),
        'ending_phase': manifest['ending_phase'], 'stream_rows': {name: counts[name] for name in present},
        'event_phases': dict(event_phases), 'ui_snapshot_phases': dict(ui_phases),
        'selection_candidate_event_rows': candidates if 'events.jsonl' in present else None,
        'selection_candidate_ui_receipt_rows': ui_candidates if 'ui-presented.jsonl' in present else None,
        'ui_receipts_without_snapshot': ui_without_snapshot if 'ui-presented.jsonl' in present else None,
        'missing_at_cutoff': manifest['missing_at_cutoff'],
        'source_error_present': manifest['source_error_present'], 'source_limit_reached': manifest['source_limit_reached'],
        'later_tail_included': False, 'source_verified_before_and_after': True,
        'images_verified': False, 'recommendation_quality_verified': False,
        'meaning': 'prefix_records_only_not_complete_draft_or_user_perception_proof'}


def render_markdown(reports, skipped, *, index_state):
    def known(value):
        return '未知' if value is None else str(value)
    lines = ['\n## BP结束阶段前缀', '', f'索引状态：{index_state}。',
        '仅为截止时已处理日志，迟到尾部不纳入；相同来源流与完整BP日志不能相加，不评价推荐质量。', '']
    for report in reports:
        lines += [f"### 比赛 {known(report['effective_match_id'])} / BP会话 {report['session_id']}", '',
            f"来源：{report['root_key']} / {report['source']}",
            f"前缀包：{report['package_id']}；比赛绑定包：{known(report['binding_package_id'])}",
            f"截止阶段：{report['ending_phase']}；截止记录UTC：{report['cutoff_recorded_at_utc']}；最近观测UTC：{known(report['last_observed_at_utc'])}",
            f"比赛归属依据：{report['match_identity_basis']}；不使用UTC猜归属，也不宣称BP与采集会话相同。",
            f"selection候选事件行：{known(report['selection_candidate_event_rows'])}；selection候选UI应用回执行：{known(report['selection_candidate_ui_receipt_rows'])}。",
            f"缺失流：{report['missing_at_cutoff']}；与关闭启动器重合流：{report['streams_also_in_closed_launcher_scope']}",
            f"明确比赛号关联GSI包：{report['linked_native_gsi_packages']}；装备包：{report['linked_equipment_packages']}",
            'UI回执不等于用户实际看见或推荐可信，未核验图像。', '']
    for row in skipped:
        lines.append(f"- 未分析：{row.get('state', 'unknown')} / {row.get('reason', '未通过交接')}")
    return '\n'.join(lines) + '\n'
