"""Overlap native work intervals from admitted performance contexts, not UTC."""
from collections import Counter
import json
import math
import re

AUDIO_STAGES = ('audio_resample', 'audio_save')
STAGES = ('anchor', 'hud', 'hero_ocr', 'items', 'upgrades', 'wallet', 'frame_total', *AUDIO_STAGES)


def trace(value, frequency):
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int
            or value['schema_version'] != 1 or value.get('clock') != 'windows_QueryPerformanceCounter'
            or type(value.get('frequency_hz')) is not int or value['frequency_hz'] != frequency
            or type(value.get('process_id')) is not int or not 0 < value['process_id'] < 2**63):
        return None
    for name in ('clock_id', 'frame_id'):
        if not isinstance(value.get(name), str) or not re.fullmatch('[a-f0-9]{32}', value[name]):
            return None
    if any(type(value.get(k)) is not int or not 0 <= value[k] <= 2**31-1
           for k in ('unmeasured_spans', 'dropped_spans')):
        return None
    spans = value.get('spans')
    if not isinstance(spans, list) or len(spans) > 16:
        return None
    result = {k: value[k] for k in ('clock_id', 'frame_id', 'frequency_hz', 'process_id',
                                  'unmeasured_spans', 'dropped_spans')}
    result['spans'] = []
    for span in spans:
        if (not isinstance(span, dict) or span.get('stage') not in STAGES or type(span.get('failed')) is not bool
                or any(type(span.get(k)) is not int for k in ('start_qpc', 'end_qpc'))
                or not 0 <= span['start_qpc'] <= span['end_qpc'] < 2**63):
            return None
        result['spans'].append({k: span[k] for k in ('stage', 'start_qpc', 'end_qpc', 'failed')})
    return result


def merged_length(intervals):
    end, total = None, 0
    for first, last in sorted(intervals):
        total += max(0, last - max(first, end if end is not None else first))
        end = max(last, end if end is not None else last)
    return total


def row_traces(row, scope, reasons):
    if row.get('workload_qpc') is not None:
        yield row['workload_qpc'], False
    audio = row.get('audio_workload_qpc', [])
    if not isinstance(audio, list) or len(audio) > 2:
        reasons['audio_workload_budget_or_shape'] += 1
        return
    for entry in audio:
        if (not isinstance(entry, dict) or not isinstance(entry.get('scope'), dict)
                or set(entry['scope']) != {'match_id', 'game_session_id', 'record_session_id'}
                or entry['scope'] != scope):
            reasons['audio_source_scope_mismatch'] += 1
            continue
        yield entry.get('workload_qpc'), True


def review_overlap(contexts, *, scope, frequency, windows, examples, helper_process_id):
    """Caller must keep source handoff verification open around this review.

    Context sampling is incomplete. Same-frame repeats are deduplicated, and
    conflicting repeats are all excluded. Only complete intervals inside an
    admitted foreground game window participate; no wall-clock extrapolation.
    """
    if (len(contexts) > 16384 or len(examples) > 5
            or type(frequency) is not int or not 0 < frequency <= 10**12):
        raise ValueError('workload_overlap_budget')
    frames, signatures, blocked, reasons = {}, {}, set(), Counter()
    clock_processes, blocked_clocks = {}, set()
    for row in contexts:
        if row.get('workload_qpc') is None and not row.get('audio_workload_qpc'):
            continue
        age, ticks = row.get('game_provider_age_s'), row.get('qpc_ticks')
        if (any(row.get(k) != scope[k] for k in ('match_id', 'game_session_id', 'record_session_id'))
                or row.get('foreground_dota') is not True
                or row.get('game_state') != 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'
                or type(age) not in (int, float) or not math.isfinite(age) or not 0 <= age <= 5
                or type(ticks) is not int or not 0 < ticks < 2**63):
            reasons['context_outside_scope_or_stale'] += 1
            continue
        for candidate, audio in row_traces(row, scope, reasons):
            value = trace(candidate, frequency)
            if value is None or any((s['stage'] in AUDIO_STAGES) != audio for s in value['spans']):
                reasons['invalid_trace_or_frequency'] += 1
                continue
            if any(s['end_qpc'] > ticks for s in value['spans']):
                reasons['work_after_context_sample'] += 1
                continue
            key = value['clock_id'], value['frame_id']
            if value['clock_id'] in clock_processes and clock_processes[value['clock_id']] != value['process_id']:
                blocked_clocks.add(value['clock_id'])
                reasons['clock_process_conflict'] += 1
            clock_processes[value['clock_id']] = value['process_id']
            if type(helper_process_id) is not int or helper_process_id != value['process_id']:
                reasons['helper_process_not_bound_to_capture'] += 1
                continue
            signature = json.dumps(value, sort_keys=True)
            if key in signatures and signatures[key] != signature:
                blocked.add(key)
                reasons['conflicting_same_frame'] += 1
            else:
                signatures[key], frames[key] = signature, value
    blocked.update(k for k in frames if k[0] in blocked_clocks)
    spans, losses = [], Counter()
    for key, value in frames.items():
        if key in blocked:
            continue
        losses['unmeasured_spans'] += value['unmeasured_spans']
        losses['dropped_spans'] += value['dropped_spans']
        for span in value['spans']:
            if not any(a <= span['start_qpc'] <= span['end_qpc'] <= b for a, b in windows):
                reasons['span_not_fully_in_game_window'] += 1
                continue
            spans.append(span)
    output = []
    for event in examples:
        stages = []
        for stage in STAGES:
            overlaps, count, failed = [], 0, 0
            for span in spans:
                if span['stage'] != stage:
                    continue
                parts = [(max(span['start_qpc'], i['qpc_start']), min(span['end_qpc'], i['qpc_end']))
                         for i in event['intervals']]
                parts = [(a,b) for a,b in parts if b > a]
                if parts:
                    overlaps.extend(parts)
                    count += 1
                    failed += int(span['failed'])
            if count:
                stages.append({'stage': stage, 'overlap_ms': round(merged_length(overlaps)*1000/frequency, 3),
                               'recorded_spans': count, 'failed_spans': failed})
        output.append({'csv_data_row': event['csv_data_row'],
                       'game_process_id': event.get('process_id'), 'stages': stages})
    status = 'no_eligible_recorded_work'
    if spans:
        status = 'recorded_overlap_review' if examples else 'no_long_frame_examples'
    return {'schema_version': 1, 'status': status,
            'clock': 'native_QPC_bound_helper_process_same_frequency_no_UTC_conversion',
            'unique_frame_traces': len(frames.keys()-blocked), 'eligible_spans': len(spans),
            'trace_unit': 'image_processing_frame_or_audio_operation_not_game_frame_or_PCM_sample',
            'excluded': dict(reasons), 'recording_losses': dict(losses), 'long_interval_examples': output,
            'stage_durations_additive': False, 'sampling_complete': False,
            'absence_of_overlap_proves_absence_of_work': False, 'assistant_causality_verified': False}
