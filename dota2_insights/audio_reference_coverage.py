"""PCM coverage of sampled reference times, never cast/audibility labels."""
from collections import Counter, defaultdict
import math


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('audio_reference_coverage_time')
    return value


def annotate(edges, segments, *, radius_s=2):
    """Use admitted segment metadata after prediction freeze; 16kHz half-open PCM.

    A flush/pause boundary is not bridged even if its offsets are contiguous.
    The caller owns source admission and before/after integrity verification.
    """
    radius = round(_number(radius_s) * 16000)
    if not 0 < radius <= 160000:
        raise ValueError('audio_reference_coverage_radius')
    by_take = defaultdict(list)
    for count, row in enumerate(segments, 1):
        if count > 100000:
            raise ValueError('audio_reference_coverage_segment_limit')
        take = (row.get('take_label') or {}).get('take_id')
        if not isinstance(take, str) or not take:
            raise ValueError('audio_reference_coverage_take')
        start = round(_number(row['take_audio_start_offset_s']) * 16000)
        frames = round(_number(row['audio_seconds']) * 16000)
        if frames <= 0:
            raise ValueError('audio_reference_coverage_empty_segment')
        by_take[take].append((start, start + frames, row.get('ended_by')))
    spans = {}
    for take, rows in by_take.items():
        merged = []
        prior_end, prior_reason = None, None
        for start, end, reason in rows:
            if prior_end is not None and start < prior_end:
                raise ValueError('audio_reference_coverage_overlapping_or_reordered')
            if merged and start == prior_end and prior_reason == 'five_second_segment':
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
            prior_end, prior_reason = end, reason
        spans[take] = merged
    result = []
    for count, edge in enumerate(edges, 1):
        if count > 100000:
            raise ValueError('audio_reference_coverage_edge_limit')
        point = round(_number(edge['audio_offset_s']) * 16000)
        take = edge.get('take_id')
        candidates = spans.get(take, [])
        present = any(left <= point < right for left, right in candidates)
        complete = any(left <= point-radius and point+radius <= right for left, right in candidates)
        status = ('continuous_window_recorded' if complete else
                  'point_recorded_window_incomplete' if present else 'point_not_recorded')
        result.append({**edge, 'pcm_point_recorded': present,
                       'pcm_reference_window_recorded': complete, 'pcm_coverage_status': status})
    return {'policy': 'same_take_continuous_16000hz_pcm_half_open_v1',
            'window_radius_s': radius / 16000, 'edges': result,
            'counts': dict(Counter(e['pcm_coverage_status'] for e in result)),
            'bank_covered_counts': dict(Counter(e['pcm_coverage_status'] for e in result
                                               if e.get('covered_by_bank') is True)),
            'limits': ['PCM coverage is not audibility or a precise cast timestamp',
                       'missing coverage is not a classifier failure',
                       'source admission and exit verification are required by caller']}
