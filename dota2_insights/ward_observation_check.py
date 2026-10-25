"""Blind observation preflight. File checks never certify screenshot semantics."""
from datetime import datetime, timezone
from io import BytesIO
import json
import math
from pathlib import Path

from PIL import Image

from dota2_insights.ward_validation_package import sha

TIME_WINDOW_MAX = 2.0
WORLD_DISTANCE_MAX = 64.0
MAP_DISTANCE_MAX = 0.01
FIELDS = ('ward_type', 'team', 'placed_time', 'ended_time', 'lifetime',
          'world_position', 'map_position', 'end_reason')


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def interval(value):
    return (isinstance(value, list) and len(value) == 2 and all(number(v) for v in value)
            and 0 <= value[1] - value[0] <= TIME_WINDOW_MAX)


def compare_interval(value, observed):
    if not interval(observed):
        raise ValueError('invalid_observed_time_bracket')
    if not number(value):
        return 'unjudgeable'
    return 'pass' if observed[0] <= value <= observed[1] else 'mismatch'


def compare_distance(value, observed, *, tolerance):
    if (not isinstance(observed, list) or len(observed) != 2 or not all(number(v) for v in observed)
            or not number(tolerance) or tolerance <= 0):
        raise ValueError('invalid_observed_position_or_tolerance')
    if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(number(v) for v in value):
        return 'unjudgeable'
    distance = math.hypot(value[0] - observed[0], value[1] - observed[1])
    return 'pass' if distance <= tolerance or math.isclose(distance, tolerance, rel_tol=0, abs_tol=1e-12) else 'mismatch'


def compare_world_position(value, observed):
    return compare_distance(value, observed, tolerance=WORLD_DISTANCE_MAX)


def compare_map_position(value, observed):
    if not isinstance(observed, list) or len(observed) != 2 or not all(number(v) and 0 <= v <= 1 for v in observed):
        raise ValueError('normalized_observed_map_position_required')
    return compare_distance(value, observed, tolerance=MAP_DISTANCE_MAX)


def compare_lifetime(value, placed, ended):
    if not interval(placed) or not interval(ended) or ended[1] < placed[0]:
        raise ValueError('invalid_independent_lifetime_brackets')
    if not number(value):
        return 'unjudgeable'
    low, high = max(0, ended[0] - placed[1]), ended[1] - placed[0]
    return 'pass' if low <= value <= high else 'mismatch'


def load_blind(package):
    package = Path(package)
    body = (package / 'package.json').read_bytes()
    if sha(body) != (package / 'package.sha256').read_text(encoding='ascii').strip():
        raise ValueError('package_fingerprint_mismatch')
    manifest = json.loads(body)
    if manifest.get('status') != 'samples_frozen_pending_visual_review' or manifest.get('role') != 'development':
        raise ValueError('admitted_development_package_required')
    blind_bytes = (package / 'review/blind.json').read_bytes()
    if sha(blind_bytes) != manifest['files']['review/blind.json']:
        raise ValueError('blind_fingerprint_mismatch')
    if sha((package / 'evidence/freeze.json').read_bytes()) != manifest['source_freeze_sha256']:
        raise ValueError('freeze_binding_mismatch')
    return manifest, sha(body), json.loads(blind_bytes)


def template(package):
    manifest, fingerprint, blind = load_blind(package)
    return {'schema_version': 1, 'kind': 'independent_ward_observations',
            'package_sha256': fingerprint, 'source_freeze_sha256': manifest['source_freeze_sha256'],
            'match_id': manifest['match_id'], 'observer': None, 'reviewed_at_utc': None,
            'evidence': [], 'samples': [{
                'sample_id': row['sample_id'], 'record_index': row['record_index'],
                'observed_type': None, 'observed_team': None, 'placed_window_s': None,
                'ended_window_s': None, 'world_position': None, 'map_position': None,
                'end_reason': None, 'field_evidence': {}, 'unjudgeable_fields': {}}
                for row in blind]}


def utc(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.utcoffset() == timezone.utc.utcoffset(stamp)
    except (ValueError, AttributeError):
        return False


def verified_frame(row, root, match_id):
    if (row.get('match_id') != match_id or row.get('clock_basis') != 'client_game_clock'
            or not number(row.get('game_time_s')) or not utc(row.get('captured_at_utc'))
            or not number(row.get('clock_precision_s')) or not 0 < row['clock_precision_s'] <= 1):
        raise ValueError('frame_match_or_clock_metadata_required')
    path = (root / row['file']).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
        raise ValueError('frame_must_be_local_image')
    if path.stat().st_size > 10 * 1024 * 1024:
        raise ValueError('frame_byte_budget')
    body = path.read_bytes()
    if sha(body) != row.get('sha256'):
        raise ValueError('frame_fingerprint_mismatch')
    with Image.open(BytesIO(body)) as picture:
        width, height = picture.size
        if picture.format not in ('PNG', 'JPEG') or width < 640 or height < 360 or width * height > 16 * 1024 * 1024:
            raise ValueError('frame_dimension_budget')
        picture.load()
    return row


def preflight(package, observations_file):
    manifest, fingerprint, blind = load_blind(package)
    observations_file = Path(observations_file)
    if observations_file.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('observation_byte_budget')
    body = observations_file.read_bytes()
    observed = json.loads(body)
    if (type(observed.get('schema_version')) is not int or observed['schema_version'] != 1
            or observed.get('kind') != 'independent_ward_observations'
            or observed.get('package_sha256') != fingerprint
            or observed.get('source_freeze_sha256') != manifest['source_freeze_sha256']
            or observed.get('match_id') != manifest['match_id']):
        raise ValueError('observation_package_binding_required')
    expected = {row['sample_id']: row for row in blind}
    rows = observed.get('samples', [])
    if (len(rows) != len(expected) or len({r['sample_id'] for r in rows}) != len(rows)
            or {r['sample_id'] for r in rows} != set(expected)):
        raise ValueError('complete_unique_sample_identity_required')
    frames = {}
    for row in observed.get('evidence', []):
        key = row['evidence_id']
        if key in frames:
            raise ValueError('duplicate_frame_id')
        frames[key] = verified_frame(row, observations_file.parent, manifest['match_id'])
    reviewed = bool(isinstance(observed.get('observer'), str) and observed['observer'].strip()
                    and utc(observed.get('reviewed_at_utc')))
    results = []
    for row in rows:
        if row.get('record_index') != expected[row['sample_id']]['record_index']:
            raise ValueError('record_index_changed')
        evidence = row.get('field_evidence', {})
        unknown = row.get('unjudgeable_fields', {})
        states = {}
        values = {'ward_type': row.get('observed_type') in ('observer', 'sentry'),
                  'team': type(row.get('observed_team')) is int and row['observed_team'] in (2, 3),
                  'placed_time': interval(row.get('placed_window_s')), 'ended_time': interval(row.get('ended_window_s')),
                  'world_position': isinstance(row.get('world_position'), list) and len(row['world_position']) == 2 and all(number(v) for v in row['world_position']),
                  'map_position': isinstance(row.get('map_position'), list) and len(row['map_position']) == 2 and all(number(v) and 0 <= v <= 1 for v in row['map_position']),
                  'end_reason': row.get('end_reason') in ('enemy_deward', 'deny', 'expired', 'match_end_censored')}
        values['lifetime'] = (values['placed_time'] and values['ended_time']
                              and row['placed_window_s'][0] <= row['ended_window_s'][1]
                              and row.get('end_reason') != 'match_end_censored')
        for field in FIELDS:
            refs = evidence.get(field, [])
            valid_refs = bool(isinstance(refs, list) and refs and all(key in frames for key in refs))
            states[field] = 'pending'
            if not reviewed or not valid_refs:
                continue
            if isinstance(unknown.get(field), str) and unknown[field].strip():
                states[field] = 'unjudgeable'
                continue
            if not values[field]:
                continue
            if field in ('placed_time', 'ended_time'):
                window = row['placed_window_s' if field == 'placed_time' else 'ended_window_s']
                roles = {frames[key].get('event_role'): frames[key] for key in refs}
                if (len(set(refs)) < 2 or set(roles) != {'before', 'after'}
                        or len({frames[key]['sha256'] for key in refs}) < 2
                        or roles['before']['game_time_s'] != window[0]
                        or roles['after']['game_time_s'] < roles['before']['game_time_s']
                        or roles['after']['game_time_s'] + roles['after']['clock_precision_s'] != window[1]):
                    continue
            if field == 'lifetime':
                # Two independently bracketed endpoints are mandatory.
                if states.get('placed_time') != 'ready_for_comparison' or states.get('ended_time') != 'ready_for_comparison':
                    continue
            if field == 'world_position' and row.get('world_coordinate_basis') != 'client_display':
                continue
            if field == 'map_position' and row.get('map_alignment_basis') != 'independent_client_landmarks':
                continue
            states[field] = 'ready_for_comparison'
        results.append({'sample_id': row['sample_id'], 'group': expected[row['sample_id']].get('group'), 'fields': states})
    if observations_file.read_bytes() != body:
        raise ValueError('observations_changed')
    return {'status': 'observation_preflight_only', 'match_id': manifest['match_id'],
            'package_sha256': fingerprint, 'observations_sha256': sha(body),
            'source_freeze_sha256': manifest['source_freeze_sha256'], 'cohort_key': manifest.get('cohort_key'),
            'role': manifest['role'],
            'comparison_rules': {'time_window_max_s': TIME_WINDOW_MAX, 'world_distance_max': WORLD_DISTANCE_MAX,
                                 'map_distance_max': MAP_DISTANCE_MAX},
            'independent_validation_passed': False, 'frame_count': len(frames), 'samples': results,
            'scope': 'checks file integrity and declared metadata; does not confirm pixels, game identity or visual truth'}
