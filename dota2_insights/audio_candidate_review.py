"""Diverse frozen-candidate clips, not new predictions, labels or accuracy data."""
from collections import defaultdict, Counter
from contextlib import contextmanager
import hashlib
import io
import json
import math
from pathlib import Path
import re
import wave

from .audio_integrity_review import _path
from .closed_audio_analysis import open_analysis_inputs, AudioMatchAnalysisInputs
from .match_journals import _read, _json
from .minimap.offline_io import new_binary_output


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def select(rows, *, per_skill=3, total=24):
    if type(per_skill) is not int or not 1 <= per_skill <= 6 or type(total) is not int or not 1 <= total <= 40:
        raise ValueError('audio_candidate_selection_budget')
    groups, seen = defaultdict(list), set()
    for line, row in enumerate(rows, 1):
        if line > 100000:
            raise ValueError('audio_candidate_row_limit')
        skill, take = row.get('skill'), row.get('take_id')
        start, end = row.get('window_start_s'), row.get('window_end_s')
        if (not isinstance(skill, str) or not re.fullmatch('[a-z0-9_]{1,120}', skill)
                or not isinstance(take, str) or not re.fullmatch('[a-f0-9]{32}', take)
                or type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end
                or end-start > 3):
            raise ValueError('audio_candidate_identity_or_window_invalid')
        key = skill, take, start, end
        if key in seen:
            continue
        seen.add(key)
        groups[skill].append({'prediction_line': line, 'prediction': row})
    queues = {}
    for skill, values in groups.items():
        values.sort(key=lambda v: (v['prediction']['take_id'], v['prediction']['window_start_s'], v['prediction_line']))
        # Endpoints first, then middle indices: temporal spread without using GSI/score.
        size = min(per_skill, len(values))
        indices = [0] if size == 1 else [0, len(values)-1]
        if size > 2:
            indices.extend(round(i*(len(values)-1)/(size-1)) for i in range(1, size-1))
        queues[skill] = [values[i] for i in indices]
    selected = []
    for turn in range(per_skill):
        for skill in sorted(queues):
            if len(selected) >= total:
                break
            if turn < len(queues[skill]):
                selected.append(queues[skill][turn])
    covered = {v['prediction']['skill'] for v in selected}
    return selected, {'candidate_skill_counts': {s: len(v) for s, v in groups.items()},
                      'omitted_skills': sorted(set(groups)-covered), 'per_skill_limit': per_skill,
                      'total_limit': total, 'policy': 'skill_round_robin_endpoints_then_middle_v1_not_GSI_or_score_ranked'}


def extract(inputs, roots, take, start, end):
    """Exact PCM sample window; any capture gap/flush boundary stays unavailable."""
    left, right = round(start*16000), round(end*16000)
    records = inputs.segment_rows()
    verified = {Path(s['source']['path']).name: s['source'] for s in inputs.capture['segments']}
    native=isinstance(inputs,AudioMatchAnalysisInputs)
    folder_ref = None if native else inputs.capture['metadata_source']
    folder = None if native else Path(folder_ref['path']).parent
    parts, sources, cursor, prior = [], [], left, None
    for row in records:
        if (row.get('take_label') or {}).get('take_id') != take:
            continue
        base = round(row['take_audio_start_offset_s']*16000)
        stop = base + round(row['audio_seconds']*16000)
        if stop <= left or base >= right:
            continue
        if base > cursor or (prior is not None and prior.get('ended_by') != 'five_second_segment'):
            return None, 'capture_gap_or_flush_boundary'
        a, b = max(cursor, base), min(right, stop)
        ref = verified[row['audio_file']]
        if native:
            from .audio_match_handoff import _safe
            raw=_read(_safe(inputs.audio_folder,row['audio_file']),4*1024**2)
            ref={'source_kind':'native_audio_match_waveform','source_package_id':inputs.package_id,
                 'capture_run_id':inputs.capture['capture_run_id'],'file':row['audio_file'],'sha256':ref['sha256']}
        else:
            raw = _read(_path(roots[folder_ref['root_key']], folder/row['audio_file']), 4*1024**2)
        if hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('audio_candidate_waveform_changed')
        with wave.open(io.BytesIO(raw), 'rb') as stream:
            if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
                raise ValueError('audio_candidate_pcm_format')
            stream.setpos(a-base)
            data = stream.readframes(b-a)
        if len(data) != (b-a)*2:
            raise ValueError('audio_candidate_pcm_truncated')
        parts.append(data)
        sources.append({'source': ref, 'from_frame': a-base, 'frames': b-a})
        cursor, prior = b, row
        if cursor == right:
            break
    if cursor != right or not parts:
        return None, 'incomplete_audio_window'
    body = io.BytesIO()
    with wave.open(body, 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
        out.writeframes(b''.join(parts))
    return {'body': body.getvalue(), 'sources': sources, 'start_s': left/16000, 'end_s': right/16000}, None


def prepare(package, roots, folder, analysis, output, *, package_id, per_skill=3, total=24):
    return _prepare(lambda _:open_analysis_inputs(package,roots,folder),roots,analysis,output,
                    package_id=package_id,per_skill=per_skill,total=total)


@contextmanager
def _native_review_inputs(folder, package_id):
    from .audio_match_handoff import open_package
    # Completed predictions already fixed the eligible source; exporting clips
    # needs reference-byte integrity, never semantic ability/CD labels.
    with open_package(folder,expected_package_id=package_id,defer_reference_review=True) as package:
        if package['manifest']['recording_integrity']!='complete':
            raise ValueError('audio_candidate_gap_free_scope_required')
        inputs=AudioMatchAnalysisInputs(folder,package)
        try:
            yield inputs
        finally:
            inputs.active=False
    inputs.after_verified=True


def prepare_match(folder, analysis, output, *, package_id=None, per_skill=3, total=24):
    return _prepare(lambda digest:_native_review_inputs(folder,digest),{},analysis,output,
                    package_id=package_id,per_skill=per_skill,total=total,native=True)


def _prepare(open_inputs, roots, analysis, output, *, package_id, per_skill, total, native=False):
    analysis, output = Path(analysis), Path(output)
    from .minimap.offline_io import check_input_paths
    check_input_paths(analysis)
    receipt_raw = _read(analysis/'analysis-receipt.json', 65536)
    receipt = _json(receipt_raw)
    if native:
        if receipt.get('source_kind')!='native_audio_match_handoff':
            raise ValueError('audio_candidate_native_analysis_required')
        declared=receipt.get('source_package_id')
        if not isinstance(declared,str) or not re.fullmatch(r'[a-f0-9]{64}',declared):
            raise ValueError('audio_candidate_source_package_invalid')
        package_id=declared if package_id is None else package_id
    elif receipt.get('source_kind')=='native_audio_match_handoff':
        raise ValueError('audio_candidate_legacy_analysis_required')
    frozen_raw = _read(analysis/'prediction-manifest.json', 4*1024**2)
    frozen = _json(frozen_raw)
    if (receipt.get('kind') != 'closed_audio_analysis_receipt' or receipt.get('complete') is not True
            or receipt.get('source_package_id') != package_id or receipt.get('gsi_used_for_inference') is not False
            or receipt.get('source_verified_before_and_after') is not True
            or receipt.get('waveforms_verified_before_and_after') is not True
            or receipt.get('prediction_manifest_sha256') != hashlib.sha256(frozen_raw).hexdigest()
            or frozen.get('stage') != 'prediction_frozen_before_reference'
            or frozen.get('gsi_read_during_inference') is not False):
        raise ValueError('completed_closed_audio_analysis_required')
    predictions_raw = _read(analysis/'predictions.jsonl', 32*1024**2)
    if hashlib.sha256(predictions_raw).hexdigest() != frozen.get('prediction_sha256'):
        raise ValueError('audio_candidate_predictions_changed')
    rows = [_json(line) for line in predictions_raw.splitlines()]
    selection, policy = select(rows, per_skill=per_skill, total=total)
    prepared, unavailable = [], []
    output.mkdir(parents=True, exist_ok=False)
    with open_inputs(package_id) as inputs:
        if native and (receipt.get('source_scope')!={k:inputs.package['manifest'][k]
                       for k in ('match_id','game_session_id','record_session_id')}
                       or receipt.get('full_capture_shutdown_required') is not False):
            raise ValueError('audio_candidate_native_scope_conflict')
        if (receipt.get('capture_run_id') != inputs.capture['capture_run_id']
                or frozen.get('source_journal_sha256') != inputs.refs['segments.jsonl']['sha256']
                or frozen.get('sources') != [{'file': Path(s['source']['path']).name, 'sha256': s['source']['sha256']}
                                             for s in inputs.capture['segments']]):
            raise ValueError('audio_candidate_capture_identity_conflict')
        for item in selection:
            row = item['prediction']
            start, end = max(0, row['window_start_s']-.6), row['window_end_s']+.6
            clip, reason = extract(inputs, roots, row['take_id'], start, end)
            info = {'prediction_line': item['prediction_line'], 'skill': row['skill'],
                    'take_id': row['take_id'], 'template_id': row.get('template_id'),
                    'window_start_s': row['window_start_s'], 'window_end_s': row['window_end_s']}
            if clip is None:
                unavailable.append({**info, 'reason': reason})
                continue
            name = f'candidate-review-{len(prepared)+1:03d}.wav'
            with new_binary_output(output/name) as stream:
                stream.write(clip['body'])
            prepared.append({**info, 'clip': name, 'clip_sha256': hashlib.sha256(clip['body']).hexdigest(),
                'start_s': clip['start_s'], 'end_s': clip['end_s'], 'sources': clip['sources'],
                'human_verdict': 'unreviewed', 'basis': 'frozen_classifier_candidate_not_verified_audible_cast'})
    if (_read(analysis/'analysis-receipt.json', 65536) != receipt_raw
            or _read(analysis/'prediction-manifest.json', 4*1024**2) != frozen_raw
            or _sha(analysis/'predictions.jsonl') != frozen['prediction_sha256']):
        raise ValueError('audio_candidate_analysis_changed_during_export')
    result = {'schema_version': 1, 'purpose': 'post_prediction_candidate_diversity_review_only',
        'prediction_sha256': frozen['prediction_sha256'], 'analysis_receipt_sha256': hashlib.sha256(receipt_raw).hexdigest(),
        'source_package_id': package_id, 'selection': policy, 'clips': prepared, 'unavailable': unavailable,
        'counts': dict(Counter(c['skill'] for c in prepared)), 'source_verified_before_and_after': True,
        'training_eligible': False, 'accuracy_verified': False}
    if native:
        result.update(source_kind='native_audio_match_handoff',source_scope=receipt['source_scope'],
                      full_capture_shutdown_required=False)
    with new_binary_output(output/'review.json') as stream:
        stream.write((json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n').encode('utf-8'))
    return result
