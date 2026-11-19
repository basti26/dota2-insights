"""Sample nonoverlapping PCM windows without reading predictions or GSI semantics."""
import json
from pathlib import Path
import random

from .audio_candidate_review import _native_review_inputs, extract
from .audio_listening_review import BLIND_PURPOSE, BLIND_TARGET, sha
from .minimap.offline_io import new_binary_output


RATE = 16000
WINDOW_FRAMES = 2 * RATE


def _windows(rows):
    by_take = {}
    for row in rows:
        take = row['take_label']['take_id']
        start = round(row['take_audio_start_offset_s'] * RATE)
        end = start + round(row['audio_seconds'] * RATE)
        spans = by_take.setdefault(take, [])
        if spans and start == spans[-1][1] and spans[-1][2] == 'five_second_segment':
            start = spans.pop()[0]
        spans.append((start, end, row['ended_by']))
    windows, tail_frames = [], 0
    for take, spans in sorted(by_take.items()):
        for start, end, _ in spans:
            windows.extend((take, frame) for frame in range(start, end - WINDOW_FRAMES + 1, WINDOW_FRAMES))
            tail_frames += (end - start) % WINDOW_FRAMES
    return windows, tail_frames


def prepare_blind_match(folder, output, *, package_id, seed, total=24):
    if type(total) is not int or not 1 <= total <= 40 or type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('audio_blind_sampling_budget_invalid')
    output = Path(output)
    clips, fingerprints = [], set()
    inspected = 0
    with _native_review_inputs(folder, package_id) as inputs:
        windows, tail_frames = _windows(inputs.segment_rows())
        if not windows:
            raise ValueError('audio_blind_no_full_windows')
        probe_limit = min(total * 4, len(windows))
        selected = random.Random(seed).sample(windows, probe_limit)
        output.mkdir(parents=True, exist_ok=False)
        for take, frame in selected:
            inspected += 1
            clip, reason = extract(inputs, {}, take, frame / RATE, (frame + WINDOW_FRAMES) / RATE)
            if clip is None:
                raise ValueError('audio_blind_selected_window_unavailable: ' + reason)
            fingerprint = sha(clip['body'])
            if fingerprint in fingerprints:
                continue
            fingerprints.add(fingerprint)
            filename = f'blind-{len(clips) + 1:03d}.wav'
            with new_binary_output(output / filename) as stream:
                stream.write(clip['body'])
            clips.append({'clip': filename, 'clip_sha256': fingerprint, 'take_id': take,
                          'from_frame': frame, 'frames': WINDOW_FRAMES, 'sources': clip['sources']})
            if len(clips) == total:
                break
    result = {'schema_version': 1, 'purpose': BLIND_PURPOSE, 'target': BLIND_TARGET,
              'source_package_id': inputs.package_id, 'source_verified_before_and_after': True,
              'sampling': {'policy': 'randomized_full_pcm_windows_skip_duplicate_wav_v2', 'seed': seed,
                           'eligible_windows': len(windows), 'requested_windows': total,
                           'inspected_windows': inspected, 'duplicate_wav_windows_skipped': inspected - len(clips),
                           'inspection_limit': probe_limit,
                           'excluded_partial_tail_frames': tail_frames, 'sample_rate': RATE},
              'clips': clips, 'training_eligible': False, 'accuracy_verified': False}
    with new_binary_output(output / 'review.json') as stream:
        stream.write((json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))
    return result
