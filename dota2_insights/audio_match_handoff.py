"""Independent, bounded reader of writer-closed per-game audio metadata/WAVs."""
from contextlib import contextmanager
import hashlib
import math
from pathlib import Path
import re

from .audio_integrity_review import waveform
from .audio_source_context import validate_scope, validate_reference_context
from .helper_handoff import utc
from .match_journals import _json, _read

SCOPE_FIELDS = ('match_id', 'game_session_id', 'record_session_id')
MAX_METADATA = 64*1024**2


def _scope_name(scope):
    import json
    return 'scope_'+hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _safe(root, name):
    root = Path(root)
    path = root/name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('audio_match_source_outside_root')
    for part in (path, *path.parents):
        if part.is_relative_to(root) and (part.is_symlink() or part.is_junction()):
            raise ValueError('audio_match_source_link_refused')
    return path


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _rows(folder, spec, limit, *, decode=True):
    if (not isinstance(spec, dict) or set(spec) != {'file','bytes','rows','sha256'}
            or type(spec['bytes']) is not int or not 0 <= spec['bytes'] <= MAX_METADATA
            or type(spec['rows']) is not int or not 0 <= spec['rows'] <= limit
            or not re.fullmatch(r'[a-f0-9]{64}', str(spec['sha256']))):
        raise ValueError('audio_match_journal_spec_invalid')
    path = _safe(folder, spec['file'])
    digest, total, count, rows = hashlib.sha256(), 0, 0, []
    with path.open('rb') as stream:
        before = path.stat()
        if before.st_size != spec['bytes']:
            raise ValueError('audio_match_journal_size_changed')
        while line := stream.readline(65537):
            total += len(line)
            if total > spec['bytes'] or count >= spec['rows'] or len(line) > 65536 or not line.endswith(b'\n'):
                raise ValueError('audio_match_journal_budget_or_tail')
            count += 1
            if decode:
                rows.append(_json(line))
            digest.update(line)
        after = path.stat()
    if ((before.st_size,before.st_mtime_ns,before.st_ino) != (after.st_size,after.st_mtime_ns,after.st_ino)
            or total != spec['bytes'] or count != spec['rows'] or digest.hexdigest() != spec['sha256']):
        raise ValueError('audio_match_journal_changed')
    return rows if decode else None


def verify(folder, *, expected_package_id=None, defer_reference_review=False):
    if type(defer_reference_review) is not bool:
        raise ValueError('audio_match_reference_mode_invalid')
    folder = Path(folder).absolute()
    capture = folder.parent.parent
    # Do not follow a renamed/junctioned package before checking its layout.
    if (folder.parent.name != 'closed-matches'
            or not re.fullmatch(r'scope_[a-f0-9]{64}', folder.name)
            or not re.fullmatch(r'run_[a-f0-9]{32}', capture.name)):
        raise ValueError('audio_match_layout_invalid')
    raw = _read(_safe(capture, f'closed-matches/{folder.name}/manifest.json'), 16384)
    package_id = hashlib.sha256(raw).hexdigest()
    if expected_package_id is not None and package_id != expected_package_id:
        raise ValueError('audio_match_index_source_changed')
    document = _json(raw)
    scope = {k: document.get(k) for k in SCOPE_FIELDS}
    validate_scope(scope, expected_match=scope['match_id'])  # Lock BEFORE journals or WAVs.
    if folder.name != _scope_name(scope):
        raise ValueError('audio_match_scope_directory_conflict')
    if _read(_safe(folder, 'COMPLETE'), 66) != package_id.encode()+b'\n':
        raise ValueError('audio_match_not_complete')
    if (type(document.get('schema_version')) is not int or document['schema_version'] != 1
            or document.get('kind') != 'foresight_audio_match_handoff'
            or document.get('audio_capture_run_id') != capture.name
            or document.get('end_request_consumed_after_tail_flush') is not True
            or document.get('scope') != 'explicit_runtime_scope_audio_until_writer_consumed_post_game_not_whole_match_proof'
            or document.get('waveforms') != 'immutable_capture_root_files_referenced_by_segments_sha_requires_reader_verification'
            or document.get('source_audio_run') != 'may_continue_recording_other_scopes'
            or document.get('health_scope') != 'cumulative_audio_worker_at_publication_not_per_match'
            or document.get('other_channels') != 'not_closed_by_this_manifest'):
        raise ValueError('audio_match_contract_invalid')
    utc(document.get('closed_at_utc'))
    end = document.get('end_context')
    if (not isinstance(end, dict) or set(end) != {'match_id','provider_timestamp','game_state','clock_time_s','recorded_source_context'}
            or end.get('game_state') != 'DOTA_GAMERULES_STATE_POST_GAME'
            or type(end.get('clock_time_s')) is not int or not -86400 <= end['clock_time_s'] <= 86400
            or end.get('match_id') != scope['match_id']):
        raise ValueError('audio_match_end_context_invalid')
    end_source = validate_reference_context(end)
    if end_source is None or any(end_source[k] != scope[k] for k in SCOPE_FIELDS):
        raise ValueError('audio_match_end_scope_conflict')
    health = document.get('health')
    if (not isinstance(health, dict) or set(health) != {'audio_error_present','reference_error_present','limit_reached','end_request_error_present'}
            or any(type(v) is not bool for v in health.values())
            or document.get('recording_integrity') != ('gaps' if any(health.values()) else 'complete')):
        raise ValueError('audio_match_health_invalid')
    specs = document.get('files')
    if not isinstance(specs, list) or len(specs) != 2 or any(not isinstance(s,dict) for s in specs):
        raise ValueError('audio_match_journals_required')
    names = [s.get('file') for s in specs]
    if names.count('segments.jsonl') != 1 or names.count('reference-gsi.jsonl') != 1:
        raise ValueError('audio_match_journal_names_invalid')
    if (any(type(s.get('bytes')) is not int or s['bytes'] < 0 for s in specs)
            or sum(s['bytes'] for s in specs) > MAX_METADATA):
        raise ValueError('audio_match_metadata_limit')
    journals = {s['file']: _rows(folder, s, 5000 if s['file']=='segments.jsonl' else 100000,
                decode=not (defer_reference_review and s['file']=='reference-gsi.jsonl')) for s in specs}
    segments, references = journals['segments.jsonl'], journals['reference-gsi.jsonl']
    if not segments:
        raise ValueError('audio_match_empty_audio_not_delivery')
    files, previous, qualities, total_wave_bytes = set(), {}, [], 0
    for row in segments:
        rscope = validate_scope(row.get('recorded_source_scope'), expected_match=scope['match_id'])
        association, label = row.get('match_association'), row.get('take_label')
        if (rscope != scope or not isinstance(association,dict) or association.get('match_id') != scope['match_id']
                or not isinstance(label,dict)):
            raise ValueError('audio_match_segment_scope_conflict')
        name, take = row.get('audio_file'), label.get('take_id')
        start, duration = row.get('take_audio_start_offset_s'), row.get('audio_seconds')
        if (type(row.get('schema_version')) is not int or row['schema_version'] != 1
                or not isinstance(name,str) or not re.fullmatch(r'audio-[0-9]{4,8}\.wav',name) or name in files
                or not isinstance(take,str) or not re.fullmatch(r'[a-f0-9]{32}',take)
                or not _number(start) or not 0 <= start <= 14400 or not _number(duration) or not 0 < duration <= 10
                or (take in previous and start < previous[take]-1e-6)
                or (row.get('sample_rate'),row.get('channels'),row.get('sample_width_bytes')) != (16000,1,2)
                or row.get('recognition_enabled') is not False or row.get('skill_label') is not None
                or not re.fullmatch(r'[a-f0-9]{64}', str(row.get('sha256')))
                or row.get('timing_basis') != 'host_delivery_and_consume_boundaries_not_exact_audio_sample_clock'):
            raise ValueError('audio_match_segment_invalid')
        path = _safe(capture, name)
        before = path.stat()
        wav = _read(path, 4*1024**2)
        qualities.append(waveform(wav, row))
        after = path.stat()
        if (before.st_size,before.st_mtime_ns,before.st_ino) != (after.st_size,after.st_mtime_ns,after.st_ino):
            raise ValueError('audio_match_wave_changed_during_read')
        total_wave_bytes += len(wav)
        if total_wave_bytes > 512*1024**2:
            raise ValueError('audio_match_wave_budget')
        files.add(name); previous[take] = start+duration
    for row in references or []:
        context = row.get('context')
        if not isinstance(context,dict):
            raise ValueError('audio_match_reference_invalid')
        source = validate_reference_context(context)
        if (source is None or any(source[k] != scope[k] for k in SCOPE_FIELDS)
                or row.get('role') != 'post_test_reference_only'
                or not isinstance(row.get('take_id'),str) or not re.fullmatch(r'[a-f0-9]{32}',row['take_id'])
                or not _number(row.get('audio_offset_s')) or not 0 <= row['audio_offset_s'] <= 14400
                or context.get('game_state') not in {'DOTA_GAMERULES_STATE_PRE_GAME','DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'}):
            raise ValueError('audio_match_reference_scope_conflict')
        utc(row.get('recorded_at_utc'))
    return {'manifest': document, 'package_id': package_id, 'segments': segments,
            'references': references, 'waveform_quality': qualities,
            'reference_semantics_verified': not defer_reference_review}


@contextmanager
def open_package(folder, *, expected_package_id=None, defer_reference_review=False):
    before = verify(folder, expected_package_id=expected_package_id,defer_reference_review=defer_reference_review)
    yield before
    if verify(folder, expected_package_id=before['package_id'],defer_reference_review=defer_reference_review) != before:
        raise ValueError('audio_match_sources_changed_during_analysis')


def review(folder, *, expected_package_id=None, native_entries=None, roots=None,
           native_index_state='missing_not_treated_as_zero'):
    with open_package(folder, expected_package_id=expected_package_id) as package:
        doc, qualities = package['manifest'], package['waveform_quality']
        result = {'schema_version': 1, 'kind': 'audio_match_technical_review',
            **{k:doc[k] for k in SCOPE_FIELDS}, 'package_id': package['package_id'],
            'audio_capture_run_id': doc['audio_capture_run_id'], 'recording_integrity': doc['recording_integrity'],
            'segments_verified': len(qualities), 'references_verified': len(package['references']),
            'verified_audio_seconds': sum(q['audio_seconds'] for q in qualities),
            'waveform_sha256_by_file': {r['audio_file']:r['sha256'] for r in package['segments']},
            'exact_silence_segments': sum(q['exact_silence'] for q in qualities),
            'whole_match_coverage_verified': False, 'sample_exact_audio_GSI_timing_verified': False,
            'native_GSI_journal_join_verified': False, 'skill_audibility_verified': False,
            'source_audio_run_must_be_stopped': False}
        if native_entries is not None:
            from .audio_gsi_alignment import align_package, summarize
            alignment=align_package(package,native_entries,roots,native_index_state=native_index_state)
            result['gsi_source_alignment']=summarize(alignment)
            result['native_GSI_journal_join_verified']=result['gsi_source_alignment']['all_reference_sources_matched']
    result['source_verified_before_and_after'] = True
    return result


def render_markdown(reports, skipped=(), *, index_state):
    lines=['\n## 逐局录音技术复核','',
        '已封存范围可在观察继续时读取；不证明整局未漏录、技能可闻或音频/GSI精确同步。与旧整次录音重合的文件不可重复相加。',
        '',f'独立索引：{index_state}。','',
        '| 比赛 | 片段 | 音频秒 | GSI旁证行 | 精确来源配对 | 交付完整性 | 与旧流重合WAV |',
        '|---|---:|---:|---:|---:|---|---:|']
    for row in reports:
        lines.append(f"| {row['match_id'] or '未知（保留本地会话）'} | {row['segments_verified']} | "
            f"{row['verified_audio_seconds']:.2f} | {row['references_verified']} | "
            f"{row.get('gsi_source_alignment',{}).get('exact_source_record_rows','待核对')} | {row['recording_integrity']} | "
            f"{row['waveforms_also_in_closed_launcher_scope']} |")
    lines.append('\n来源配对是日志覆盖，不是音效识别准确率；缺ID/重复ID/未落盘记录不按时间补齐。')
    for item in skipped:
        lines.append(f"\n未读取：{item['reason']}（不作为零音频）。")
    return '\n'.join(lines)+'\n'
