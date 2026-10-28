"""Technical checks of closed recorded audio; never infer audible skill labels."""
import hashlib
import io
import json
import math
from pathlib import Path
import re
import wave
from collections import Counter

import numpy as np

from .closed_journals import ClosedJournals
from .match_journals import _read, _json
from .replay_holdout import refuse_locked_match
from .audio_source_context import validate_scope

MAX_SEGMENTS = 20000
MAX_WAV_BYTES = 4 * 1024**2


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError('audio_source_outside_history')
    if any(p.is_relative_to(root) and (p.is_symlink() or p.is_junction()) for p in (path, *path.parents)):
        raise ValueError('audio_source_link_refused')
    return path


def waveform(raw, expected):
    """Checks the bytes actually analyzed, including WAV data truncation."""
    if hashlib.sha256(raw).hexdigest() != expected['sha256']:
        raise ValueError('audio_waveform_digest_mismatch')
    with wave.open(io.BytesIO(raw), 'rb') as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getcomptype()) != (1, 2, 16000, 'NONE'):
            raise ValueError('audio_waveform_format_invalid')
        frames = stream.getnframes()
        if not 0 < frames <= 160000:
            raise ValueError('audio_waveform_duration_invalid')
        pcm = stream.readframes(frames + 1)
        if len(pcm) != frames * 2 or abs(frames / 16000 - expected['audio_seconds']) > 1 / 16000 + 1e-9:
            raise ValueError('audio_waveform_truncated_or_duration_mismatch')
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float64)
    rms = float(np.sqrt(np.mean(samples * samples)))
    peak = float(np.max(np.abs(samples)))
    return {'frames': frames, 'audio_seconds': frames / 16000,
            'rms_dbfs': 20 * math.log10(rms / 32768) if rms else None,
            'peak_dbfs': 20 * math.log10(peak / 32768) if peak else None,
            'exact_silence': peak == 0, 'samples_at_int16_limit': int(np.count_nonzero((samples == -32768) | (samples == 32767))),
            'low_level_below_minus_60_dbfs': rms < 32768 * 10**(-60 / 20)}


def review(package, roots):
    reports, unavailable = [], []
    known_matches = {m['match_id'] for m in package['matches']}
    with ClosedJournals(package, roots) as reader:
        components = [c for c in package['components'] if c['kind'] == 'audio']
        if not components:
            unavailable.append({'reason': 'audio_not_recorded'})
        for component in components:
            if component['state'] != 'closed_verified':
                unavailable.append({'reason': 'audio_not_closed_verified'})
                continue
            metadata = component['metadata']
            if metadata not in package['sources']:
                raise ValueError('audio_metadata_not_admitted')
            folder = Path(metadata['path']).parent
            raw = _read(_path(roots[metadata['root_key']], metadata['path']), 32768)
            if hashlib.sha256(raw).hexdigest() != metadata['sha256']:
                raise ValueError('audio_metadata_changed')
            summary = _json(raw)
            if (summary.get('kind') != 'match_audio_record_only' or summary.get('recognition_enabled') is not False
                    or summary.get('complete') is not True or summary.get('shutdown_complete') is not True
                    or summary.get('running') is not False or summary.get('worker_alive') is not False
                    or summary.get('finalizing') is not False or summary.get('error') is not None):
                raise ValueError('audio_capture_not_verified_record_only')
            if summary.get('run_id') != folder.name:
                raise ValueError('audio_capture_identity_conflict')
            refs = {Path(j['source']['path']).name: j['source'] for j in component['journals']}
            if len(refs) != len(component['journals']) or 'segments.jsonl' not in refs:
                raise ValueError('audio_segment_journal_missing_or_duplicate')
            for ref in refs.values():
                if ref['root_key'] != metadata['root_key'] or Path(ref['path']).parent != folder:
                    raise ValueError('audio_journal_folder_conflict')
            segments, files, previous, total_bytes = [], set(), {}, 0
            for number, row, evidence in reader.rows(refs['segments.jsonl']):
                filename, take = row.get('audio_file'), (row.get('take_label') or {}).get('take_id')
                duration, start = row.get('audio_seconds'), row.get('take_audio_start_offset_s')
                if (number > MAX_SEGMENTS or not isinstance(filename, str) or not re.fullmatch(r'audio-[0-9]{4,8}\.wav', filename)
                        or filename in files or not isinstance(take, str) or not re.fullmatch('[a-f0-9]{32}', take)
                        or not _number(duration) or not 0 < duration <= 10 or not _number(start) or start < 0
                        or (row.get('sample_rate'), row.get('channels'), row.get('sample_width_bytes')) != (16000, 1, 2)
                        or row.get('skill_label') is not None or row.get('recognition_enabled') is not False
                        or row.get('timing_basis') != 'host_delivery_and_consume_boundaries_not_exact_audio_sample_clock'):
                    raise ValueError('audio_segment_metadata_invalid')
                association = row.get('match_association') or {}
                mid = association.get('match_id')
                if mid is not None:
                    refuse_locked_match(mid)
                    if mid not in known_matches:
                        raise ValueError('audio_segment_foreign_match')
                recorded_scope = validate_scope(row.get('recorded_source_scope'), expected_match=mid)
                gap = start - previous[take] if take in previous else None
                if gap is not None and gap < -1e-6:
                    raise ValueError('audio_segment_offset_overlap_or_regression')
                previous[take] = start + duration
                path = _path(roots[metadata['root_key']], folder / filename)
                wav = _read(path, MAX_WAV_BYTES)
                quality = waveform(wav, row)
                total_bytes += len(wav)
                files.add(filename)
                segments.append({'source': {'root_key': metadata['root_key'], 'path': (folder / filename).as_posix(),
                                            'sha256': row['sha256'], 'bytes': len(wav)},
                    'journal_evidence': evidence, 'take_id': take, 'start_audio_offset_s': start,
                    'offset_gap_before_s': max(0, gap) if gap is not None else None,
                    'declared_match_id_at_segment_write': mid,
                    'recorded_source_scope': recorded_scope,
                    'association_basis': 'writer_snapshot_not_sample_exact_match_boundary', **quality})
            if (type(summary.get('segments_written')) is not int or summary['segments_written'] != len(segments)
                    or type(summary.get('audio_bytes')) is not int or summary['audio_bytes'] != total_bytes
                    or not _number(summary.get('audio_seconds'))
                    or abs(summary['audio_seconds'] - sum(s['audio_seconds'] for s in segments)) > .001):
                raise ValueError('audio_summary_totals_conflict')
            # Confirm waveform sources are still the verified bytes after the full scan.
            for segment in segments:
                ref = segment['source']
                raw = _read(_path(roots[ref['root_key']], ref['path']), MAX_WAV_BYTES)
                if len(raw) != ref['bytes'] or hashlib.sha256(raw).hexdigest() != ref['sha256']:
                    raise ValueError('audio_waveform_changed_during_review')
            reports.append({'capture_run_id': summary['run_id'], 'metadata_source': metadata,
                'segments': segments, 'segments_verified': len(segments), 'verified_audio_seconds': sum(s['audio_seconds'] for s in segments),
                'exact_silence_segments': sum(s['exact_silence'] for s in segments),
                'low_level_segments': sum(s['low_level_below_minus_60_dbfs'] for s in segments),
                'samples_at_int16_limit': sum(s['samples_at_int16_limit'] for s in segments),
                'declared_segment_match_counts': dict(Counter(s['declared_match_id_at_segment_write'] or 'unknown' for s in segments)),
                'segments_with_explicit_runtime_scope': sum(s['recorded_source_scope'] is not None for s in segments),
                'native_gsi_source_join_verified': False,
                'audio_source': summary.get('audio_source'), 'skill_audibility_verified': False,
                'process_isolated_audio_verified': False, 'sample_exact_wall_clock_verified': False})
    return {'launcher_run_id': package['launcher_run_id'], 'captures': reports, 'unavailable': unavailable,
            'source_verified_before_and_after': True}


def render_markdown(reports):
    lines = ['\n## 录音技术复核', '',
        '仅验证文件、PCM格式与幅度；系统混音不等于独立游戏音轨，非零声音不等于大招可闻。写入时比赛标签不认证片内换局边界。', '',
        '| 采集 | 已验证片段 | 音频秒 | 全零片段 | RMS低于-60dBFS片段 | int16极值样本 |',
        '|---|---:|---:|---:|---:|---:|']
    for report in reports:
        for row in report['captures']:
            lines.append(f"| {row['capture_run_id']} | {row['segments_verified']} | {row['verified_audio_seconds']:.2f} | "
                         f"{row['exact_silence_segments']} | {row['low_level_segments']} | {row['samples_at_int16_limit']} |")
        for item in report['unavailable']:
            lines.append(f"\n{report['launcher_run_id']}：{item['reason']}（未验证，不按零音频处理）。")
    return '\n'.join(lines) + '\n'
