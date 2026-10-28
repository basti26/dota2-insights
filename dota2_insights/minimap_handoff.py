"""Read immutable standalone minimap runs; clocks never imply match identity."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re

from .helper_handoff import utc
from .match_journals import _json, _read
from .replay_holdout import refuse_locked_match

STREAM_LIMITS = {'samples.jsonl': 128 * 1024**2, 'capture_attempts.jsonl': 64 * 1024**2,
                 'context.jsonl': 8 * 1024**2, 'rejected.jsonl': 8 * 1024**2,
                 'markers.jsonl': 512 * 1024**2}


def _path(root, run, name):
    root = Path(root).resolve(strict=True)
    base = root / 'minimap-learning'
    if name in ('record-handoff.json', 'record-handoff.COMPLETE'):
        base = base / 'closed-runs'
    path = base / ('run_' + run) / name
    if not path.resolve().is_relative_to(root):
        raise ValueError('minimap_source_outside_root')
    for parent in (path, *path.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('minimap_source_link_refused')
    return path


def _spec(spec, name, limit, *, rows=False):
    if (not isinstance(spec, dict) or spec.get('file') != name
            or type(spec.get('bytes')) is not int or not 0 <= spec['bytes'] <= limit
            or not isinstance(spec.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', spec['sha256'])
            or rows and (type(spec.get('rows')) is not int or not 0 <= spec['rows'] <= 72000)):
        raise ValueError('minimap_source_spec_invalid')


def _rows(path, spec, run, frame_summary=None):
    from .minimap_game_context import binding
    last_context_sequence, last_context_signature = 0, None
    digest, count, size = hashlib.sha256(), 0, 0
    with path.open('rb') as stream:
        before = path.stat()
        if before.st_size != spec['bytes']:
            raise ValueError('minimap_source_size_changed')
        while line := stream.readline(1024**2 + 1):
            size += len(line)
            count += 1
            if len(line) > 1024**2 or not line.endswith(b'\n') or count > spec['rows'] or size > spec['bytes']:
                raise ValueError('minimap_source_journal_invalid')
            digest.update(line)
            row = _json(line)
            if row.get('match_id') is not None:
                refuse_locked_match(row['match_id'])
                raise ValueError('minimap_unbound_contract_has_match_identity')
            if row.get('run_id') is not None and row['run_id'] != run:
                raise ValueError('minimap_source_run_conflict')
            context = binding(row, frame_summary)
            if context is not None and context['status'] == 'explicit_last_context':
                import json
                signature = (context['sent_monotonic_ns'], json.dumps(context['context'], sort_keys=True))
                if (last_context_signature is not None and signature[0] < last_context_signature[0]
                        or context['sequence'] < last_context_sequence or context['sequence'] == last_context_sequence
                        and signature != last_context_signature):
                    raise ValueError('minimap_frame_context_reordered_or_redefined')
                last_context_sequence, last_context_signature = context['sequence'], signature
            yield count, row, hashlib.sha256(line).hexdigest()
        after = path.stat()
    if ((before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino)
            or size != spec['bytes'] or count != spec['rows'] or digest.hexdigest() != spec['sha256']):
        raise ValueError('minimap_source_changed')


def verify(root, run, *, expected_package_id=None):
    if not isinstance(run, str) or not re.fullmatch(r'[a-f0-9]{32}', run):
        raise ValueError('minimap_run_invalid')
    if not (Path(__file__).resolve().parents[1] / 'data/validation-locks.json').is_file():
        raise ValueError('minimap_public_locks_missing')
    raw = _read(_path(root, run, 'record-handoff.json'), 16384)
    package_id = hashlib.sha256(raw).hexdigest()
    if expected_package_id is not None and package_id != expected_package_id:
        raise ValueError('minimap_handoff_receipt_changed')
    doc = _json(raw)
    if doc.get('match_id') is not None:
        refuse_locked_match(doc['match_id'])
        raise ValueError('minimap_handoff_cannot_claim_match')
    from .minimap_game_context import summary as context_summary
    frame_summary = context_summary(doc.get('frame_context_relay'))
    if _read(_path(root, run, 'record-handoff.COMPLETE'), 66).strip() != package_id.encode():
        raise ValueError('minimap_handoff_not_complete')
    if (type(doc.get('schema_version')) is not int or doc['schema_version'] != 1
            or doc.get('kind') != 'foresight_closed_minimap_run' or doc.get('run_id') != run
            or doc.get('scope') != 'closed_standalone_run_not_whole_match'
            or doc.get('match_association') != 'unknown_no_time_inference'
            or doc.get('images') != 'declared_references_require_separate_verification'
            or doc.get('close_reason') not in ('user_stop', 'source_exhausted', 'layout_changed')
            or doc.get('chain_id') is not None and not re.fullmatch(r'[a-f0-9]{32}', str(doc['chain_id']))):
        raise ValueError('minimap_handoff_contract_invalid')
    if utc(doc.get('ended_at_utc')) < utc(doc.get('started_at_utc')):
        raise ValueError('minimap_handoff_time_order_invalid')
    _spec(doc.get('session'), 'session.json', 65536)
    session_raw = _read(_path(root, run, 'session.json'), 65536)
    if len(session_raw) != doc['session']['bytes'] or hashlib.sha256(session_raw).hexdigest() != doc['session']['sha256']:
        raise ValueError('minimap_session_source_changed')
    session = _json(session_raw)
    if session.get('frame_context_relay') != frame_summary:
        raise ValueError('minimap_frame_context_summary_changed')
    if session.get('match_id') is not None:
        refuse_locked_match(session['match_id'])
        raise ValueError('minimap_session_cannot_claim_match')
    evidence = session.get('recording_evidence') or {}
    channels = evidence.get('channels')
    if (session.get('run_id') != run or session.get('closed') is not True or session.get('complete') is not True
            or session.get('error') is not None or session.get('limited') is not False
            or session.get('ended_reason') != doc['close_reason'] or session.get('chain_id') != doc['chain_id']
            or any(session.get(key) != doc[key] for key in ('started_at_utc', 'ended_at_utc'))
            or evidence.get('pending_images') != 0 or type(evidence.get('pending_images')) is not int
            or evidence.get('queue_policy') != 'synchronous_no_pending_image_queue'
            or not isinstance(channels, dict) or set(channels) != {'attempts', 'context', 'rejected', 'markers'}
            or any(not isinstance(value, dict) or value.get('closed') is not True for value in channels.values())):
        raise ValueError('minimap_source_not_cleanly_closed')
    for key in ('frames_saved', 'images_saved', 'capture_attempts'):
        if type(session.get(key)) is not int or not 0 <= session[key] <= 72000:
            raise ValueError('minimap_session_counter_invalid')
    specs, missing = doc.get('streams'), doc.get('missing_at_close')
    if not isinstance(specs, list) or not isinstance(missing, list) or len(specs) + len(missing) != len(STREAM_LIMITS):
        raise ValueError('minimap_stream_roster_invalid')
    names = list(missing)
    for spec in specs:
        name = spec.get('file') if isinstance(spec, dict) else None
        if name not in STREAM_LIMITS:
            raise ValueError('minimap_stream_name_invalid')
        _spec(spec, name, STREAM_LIMITS[name], rows=True)
        names.append(name)
    if any(not isinstance(name, str) for name in names) or sorted(names) != sorted(STREAM_LIMITS) or 'samples.jsonl' in missing:
        raise ValueError('minimap_stream_roster_invalid')
    for name in missing:
        if _path(root, run, name).exists():
            raise ValueError('minimap_missing_stream_appeared')
    samples = 0
    for spec in specs:
        for _, row, _ in _rows(_path(root, run, spec['file']), spec, run, frame_summary):
            if spec['file'] == 'samples.jsonl' and row.get('kind') == 'sample':
                samples += 1
                if type(row.get('sequence')) is not int or row['sequence'] != samples:
                    raise ValueError('minimap_sample_sequence_invalid')
                utc(row.get('captured_at_utc'))
    if samples != session['frames_saved']:
        raise ValueError('minimap_sample_count_disagrees')
    return doc, session, package_id


@contextmanager
def open_rows(root, run, *, expected_package_id=None):
    doc, session, package_id = verify(root, run, expected_package_id=expected_package_id)
    active = True
    cursors = []
    def rows():
        for spec in doc['streams']:
            if not active:
                raise ValueError('minimap_reader_closed')
            cursor = _rows(_path(root, run, spec['file']), spec, run, doc.get('frame_context_relay'))
            cursors.append(cursor)
            try:
                while True:
                    if not active:
                        raise ValueError('minimap_reader_closed')
                    try:
                        number, row, digest = next(cursor)
                    except StopIteration:
                        break
                    yield spec['file'], number, row, {'line_sha256': digest}
            finally:
                cursor.close()
    try:
        yield doc, session, rows()
    finally:
        active = False
        for cursor in cursors:
            cursor.close()
        verify(root, run, expected_package_id=package_id)


def review(root, run, *, expected_package_id=None):
    from collections import Counter
    counts, outcomes, bindings, scopes = Counter(), Counter(), Counter(), Counter()
    first = last = None
    samples = 0
    with open_rows(root, run, expected_package_id=expected_package_id) as (doc, session, rows):
        for name, _, row, _ in rows:
            counts[name] += 1
            if name == 'samples.jsonl' and row.get('kind') == 'sample':
                observed = row.get('captured_at_utc')
                utc(observed)
                first = observed if first is None else first
                last = observed
                samples += 1
                binding = row.get('game_context_binding')
                bindings[binding['status'] if binding is not None else 'unconfigured'] += 1
                if binding is not None and binding['status'] == 'explicit_last_context':
                    c = binding['context']
                    scopes[(c['match_id'], c['game_session_id'], c['record_session_id'])] += 1
            if name == 'capture_attempts.jsonl' and isinstance(row.get('outcome'), str):
                outcomes[row['outcome']] += 1
    return {'run_id': run, 'chain_id': doc['chain_id'], 'match_id': None,
        'started_at_utc': doc['started_at_utc'], 'ended_at_utc': doc['ended_at_utc'],
        'close_reason': doc['close_reason'], 'stream_rows': dict(counts), 'missing_at_close': doc['missing_at_close'],
        'sample_rows': samples, 'first_sample_at_utc': first, 'last_sample_at_utc': last,
        'attempt_outcomes': dict(outcomes), 'source_evidence_complete': session.get('evidence_complete'),
        'images_verified': False, 'identity_verified': False, 'whole_match_coverage_verified': False,
        'match_association': 'unknown_no_time_inference', 'source_verified_before_and_after': True,
        'frame_context_counts': dict(bindings),
        'explicit_context_scopes': [{'match_id': mid, 'game_session_id': gid, 'record_session_id': sid,
                                     'sample_rows': n, 'pixel_identity_verified': False}
                                    for (mid,gid,sid),n in sorted(scopes.items(), key=lambda item: (item[0][0] or '', *item[0][1:]))]}


def render_markdown(reports, skipped, *, index_state):
    lines = ['\n## 小地图独立采样run', '', f'索引状态：{index_state}。',
        '比赛归属未知；不按UTC关联GSI/装备，不累计重复流，不代表整局覆盖或消失预警已验证。', '']
    for report in reports:
        first = report['first_sample_at_utc'] or '未知'
        last = report['last_sample_at_utc'] or '未知'
        evidence = report['source_evidence_complete']
        lines += [f"### 采样run {report['run_id']}", '',
            f"来源：{report['root_key']} / {report['source']}",
            f"包SHA：{report['package_id']}；比赛：未知；链：{report['chain_id'] or '未知'}",
            f"原记录开始/结束UTC：{report['started_at_utc']} / {report['ended_at_utc']}；关闭原因：{report['close_reason']}",
            f"样本行：{report['sample_rows']}；原采集UTC首/末：{first} / {last}",
            f"各流行数：{report['stream_rows']}；采集尝试结果：{report['attempt_outcomes']}",
            f"帧来源上下文：{report.get('frame_context_counts', {})}；只描述采样前最近明确GSI来源，不认证像素身份。",
            f"缺失流：{report['missing_at_close']}；源附加记录完整性：{evidence if evidence is not None else '未知'}",
            f"与闭合启动器重合流（不可重复累计）：{report['streams_also_in_closed_launcher_scope']}",
            '只核验记录文件，未核验PNG、英雄身份、地图坐标或预警准确性。', '']
        for scope in report.get('explicit_context_scopes', []):
            lines.append(f"- 明确来源会话：比赛{scope['match_id'] or '未知（仅本地会话）'} / {scope['game_session_id']} / {scope['record_session_id']}；"
                         f"样本{scope['sample_rows']}；已封存本人GSI包：{scope.get('linked_native_gsi_packages', [])}。")
        alignment = report.get('gsi_source_alignment')
        if alignment is not None:
            lines += ['', f"精确来源配对：{alignment['exact_source_record_rows']}/{alignment['sample_rows']}条；"
                f"其中存活、新鲜且非暂停的位置证据{alignment['usable_position_rows']}条。",
                f"来源ID已记录：{alignment['frame_source_id_rows']}条；未配对原因：{alignment['unmatched_reasons']}；"
                f"原生GSI索引：{alignment['native_source_index_state']}。",
                '以上为来源覆盖，不是识别准确率；像素身份和坐标映射仍未认证。', '']
    for row in skipped:
        lines.append(f"- 未分析：{row.get('state', 'unknown')} / {row.get('reason', '未通过交接')}")
    return '\n'.join(lines) + '\n'
