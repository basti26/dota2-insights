"""Evaluate exported manual self-position labels, never manufacture pixel truth."""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path

from .minimap.offline_io import checked_match, check_match_metadata


def _load(path):
    path = Path(path).resolve()
    if path.stat().st_size > 8 * 1024**2:
        raise ValueError('position_review_input_budget')
    raw = path.read_bytes()
    value = json.loads(raw)
    check_match_metadata(value)
    return path, value, hashlib.sha256(raw).hexdigest()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def load_labels(samples_path, labels_path, *, maximum_latency_s=3):
    """Bind marks to SHA-checked exported PNGs, not to unverified nearest-time rows."""
    from PIL import Image
    path, samples, sample_sha = _load(samples_path)
    if labels_path is None:
        matches = {s.get('position', {}).get('match_id') for s in samples.get('selected', [])}
        if len(matches) != 1:
            raise ValueError('position_review_match_unknown')
        labels = {'schema_version': 1, 'match_id': matches.pop(), 'marks': []}
        label_sha = None
    else:
        _, labels, label_sha = _load(labels_path)
    mid = labels.get('match_id')
    checked_match(mid)
    if labels.get('schema_version') != 1 or not isinstance(labels.get('marks'), list):
        raise ValueError('position_review_labels_schema')
    selected = samples.get('selected')
    if not isinstance(selected, list) or len(selected) > 120 or len(labels['marks']) > 120:
        raise ValueError('position_review_sample_budget')
    by_sequence = {}
    for sample in selected:
        sequence = sample.get('sequence')
        if type(sequence) is not int or sequence in by_sequence:
            raise ValueError('position_review_duplicate_sample')
        if (sample.get('position') or {}).get('match_id') != mid:
            raise ValueError('position_review_match_mismatch')
        by_sequence[sequence] = sample
    accepted, rejected, seen = [], [], set()
    for mark in labels['marks']:
        sequence = mark.get('sequence')
        if type(sequence) is not int or sequence in seen:
            raise ValueError('position_review_duplicate_label')
        seen.add(sequence)
        if sequence not in by_sequence:
            raise ValueError('position_review_unknown_sample')
        sample = by_sequence[sequence]
        if mark.get('image_sha256') != sample.get('image_sha256'):
            raise ValueError('position_review_label_image_mismatch')
        # This is the exported annotation page's fixed filename, not a source path from input JSON.
        image_path = path.parent / f'sample-{sequence:06d}.png'
        if image_path.is_symlink() or image_path.stat().st_size > 8 * 1024**2:
            raise ValueError('position_review_image_budget_or_link')
        raw = image_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != mark['image_sha256']:
            raise ValueError('position_review_image_changed')
        from io import BytesIO
        with Image.open(BytesIO(raw)) as image:
            width, height = image.size
            if image.format != 'PNG' or not 32 <= width <= 1024 or not 32 <= height <= 1024:
                raise ValueError('position_review_image_dimensions')
        x, y = mark.get('x'), mark.get('y')
        if not _number(x) or not _number(y) or not (0 <= x < width and 0 <= y < height):
            raise ValueError('position_review_pixel_outside_image')
        pos = sample.get('position') or {}
        reason = None
        try:
            captured = datetime.fromisoformat(sample['captured_at_utc'].replace('Z', '+00:00'))
            received = datetime.fromisoformat(pos['received_at_utc'].replace('Z', '+00:00'))
            if captured.utcoffset() is None or received.utcoffset() is None:
                raise ValueError('naive_time')
            latency = (captured - received).total_seconds()
        except (KeyError, AttributeError, TypeError, ValueError):
            latency = None
        if sample.get('source_record_status') != 'exact_recorded_source_id':
            reason = 'source_not_exact_id'
        elif pos.get('alive') is not True or pos.get('paused') is not False:
            reason = 'not_alive_or_pause_unknown'
        elif not _number(pos.get('xpos')) or not _number(pos.get('ypos')):
            reason = 'world_position_missing'
        elif (latency is None or not 0 <= latency <= maximum_latency_s
              or not _number(pos.get('source_age_s')) or not 0 <= pos['source_age_s'] <= maximum_latency_s):
            reason = 'timing_missing_or_outside_limit'
        if reason:
            rejected.append({'sequence': sequence, 'reason': reason})
        else:
            accepted.append({'match_id': mid, 'sequence': sequence, 'world': [pos['xpos'], pos['ypos']],
                'pixel': [x, y], 'size': [width, height], 'image_sha256': mark['image_sha256'],
                'received_to_capture_s': latency, 'source_age_s': pos['source_age_s']})
    return {'match_id': mid, 'samples_sha256': sample_sha, 'labels_sha256': label_sha,
            'labels_available': labels_path is not None,
            'accepted': accepted, 'rejected': rejected, 'unlabelled': len(selected) - len(seen),
            'original_sources_revalidated': False,
            'meaning': 'manual_marks_bound_to_exported_artifacts_not_atomic_frame_truth'}


def evaluate(training, heldout):
    import numpy as np
    if training['match_id'] == heldout['match_id']:
        raise ValueError('position_review_same_match_leakage')
    train, test = training['accepted'], heldout['accepted']
    if {r['image_sha256'] for r in train} & {r['image_sha256'] for r in test}:
        raise ValueError('position_review_shared_images')
    if len(train) < 6 or len(test) < 3:
        return {'status': 'insufficient_labels', 'training_count': len(train), 'heldout_count': len(test),
                'calibration_verified': False, 'heldout_error_px': None}
    world = np.asarray([r['world'] for r in train], dtype=float)
    center, spread = world.mean(axis=0), np.ptp(world, axis=0)
    if np.any(spread <= 0):
        raise ValueError('position_review_degenerate_training')
    design = np.column_stack(((world - center) / spread, np.ones(len(train))))
    targets = np.asarray([[r['pixel'][i] / (r['size'][i] - 1) for i in (0, 1)] for r in train])
    coefficients, _, rank, singular = np.linalg.lstsq(design, targets, rcond=None)
    if rank != 3 or singular[-1] <= 0 or singular[0] / singular[-1] > 1e6:
        raise ValueError('position_review_collinear_training')
    coverage = np.ptp(targets, axis=0).tolist()
    def errors(rows):
        output = []
        for row in rows:
            normalized = ((np.asarray(row['world']) - center) / spread).tolist() + [1]
            predicted = (np.asarray(normalized) @ coefficients) * (np.asarray(row['size']) - 1)
            output.append({'sequence': row['sequence'], 'predicted_pixel': predicted.tolist(),
                           'error_px': float(np.linalg.norm(predicted - row['pixel']))})
        return output
    train_errors, test_errors = errors(train), errors(test)
    def stats(rows):
        values = [r['error_px'] for r in rows]
        return {'p50': float(np.percentile(values, 50)), 'p90': float(np.percentile(values, 90)), 'max': max(values)}
    return {'status': 'exploratory_evaluation', 'training_count': len(train), 'heldout_count': len(test),
        'model': {'world_center': center.tolist(), 'world_spread': spread.tolist(), 'coefficients': coefficients.tolist()},
        'training_normalized_pixel_span': coverage, 'training_coverage_limited': min(coverage) < .25,
        'training_error_px': stats(train_errors), 'heldout_error_px': stats(test_errors),
        'heldout_samples': test_errors, 'calibration_verified': False,
        'limitations': ['manual_identity_not_independently_verified', 'source_callback_not_atomic_pixel_sync',
                       'map_version_and_orientation_compatibility_not_certified', 'exported_artifacts_only']}
