"""Compare fixed map-window hypotheses with frozen full-crop observations.

This is an exploratory set comparison, never a calibration or identity label.
No parameters are fitted and the historical tower model is reference-only.
"""
from collections import Counter
import re

from .map_glyph_review import ICONS, compare_set
from .map_holdout import distribution
from .virtual_map import WorldBounds, number


def same_map_bytes(current_sha, historical_sha):
    for value in (current_sha, historical_sha):
        if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
            raise ValueError('map_window_sha_invalid')
    if current_sha != historical_sha:
        raise ValueError('map_window_asset_mismatch')


def project_window(xyz, bounds, size):
    """Edge-based continuous crop coordinates, an explicit unverified hypothesis."""
    bounds = WorldBounds(**bounds)
    if (not isinstance(size, list) or len(size) != 2
            or any(type(v) is not int or not 64 <= v <= 1024 for v in size)):
        raise ValueError('map_window_crop_size_invalid')
    if not isinstance(xyz, list) or len(xyz) != 3:
        raise ValueError('map_window_origin_invalid')
    x, y, _ = map(number, xyz)
    # Deliberately no clipping: an out-of-window origin must remain visible.
    return [(x-bounds.min_x)/(bounds.max_x-bounds.min_x)*size[0],
            (bounds.max_y-y)/(bounds.max_y-bounds.min_y)*size[1]]


def review(plan, observations, atlas, landmarks, historical_map_sha):
    same_map_bytes(landmarks['map_asset_sha256'], historical_map_sha)
    if str(plan['map_build']) != str(atlas['map_build']) or str(observations['map_build']) != str(plan['map_build']):
        raise ValueError('map_window_build_binding')
    if observations.get('predictions_read') is not False:
        raise ValueError('map_window_observations_not_blind')
    if observations.get('icons', list(ICONS)) != list(ICONS) or observations.get('icon_profile', 'basic') != 'basic':
        raise ValueError('map_window_basic_profile_required')
    frames = {(f['case'], f['frame_index']): f for f in plan['frames']}
    if len(frames) != len(plan['frames']) or not 1 <= len(frames) <= 100:
        raise ValueError('map_window_frame_roster')
    wanted = {(case, index, icon) for case, index in frames for icon in ICONS}
    seen = set()
    for row in observations['rows']:
        key = row['case'], row['frame_index'], row['icon']
        frame = frames.get(key[:2])
        if (key not in wanted or key in seen or row['image_sha256'] != frame['image_sha256']
                or row['crop_width'] != frame['size'][0]):
            raise ValueError('map_window_observation_roster')
        seen.add(key)
    if seen != wanted:
        raise ValueError('map_window_observation_roster')

    # Check exact authored XYZ equality, not just a nearby point or fitted offset.
    old = Counter((p['classname'], tuple(p['native_origin_xyz'])) for p in atlas['entities'])
    new = Counter((p['classname'], tuple(p['origin_xyz'])) for p in landmarks['landmarks'])
    if not new or new - old:
        raise ValueError('map_window_landmark_origins_changed')
    bounds = landmarks['declared_minimap_window']
    WorldBounds(**bounds)
    matrix = atlas['calibration']['matrix_client']
    matrix = {k: number(matrix[k]) for k in ('scale_x', 'scale_y', 'offset_x', 'offset_y')}
    if matrix['scale_x'] <= 0 or matrix['scale_y'] >= 0:
        raise ValueError('map_window_historical_axes_invalid')
    rows = []
    for observed in observations['rows']:
        frame = frames[(observed['case'], observed['frame_index'])]
        points = [p for p in atlas['entities'] if p['category'] in ICONS[observed['icon']]]
        points.sort(key=lambda p: p['id'])
        if any(p.get('parent_transform_unresolved') for p in points):
            raise ValueError('map_window_parent_transform_unresolved')
        expected = [project_window(p['native_origin_xyz'], bounds, frame['size']) for p in points]
        ox, oy = map(number, frame['crop_origin_client_xy'])
        reference = [[p['native_origin_xyz'][0]*matrix['scale_x']+matrix['offset_x']-ox,
                      p['native_origin_xyz'][1]*matrix['scale_y']+matrix['offset_y']-oy] for p in points]
        detection = observed['detection']
        if detection.get('predictions_used') is not False or type(detection.get('overflow')) is not bool:
            raise ValueError('map_window_detection_not_frozen_full_crop')
        candidates = [p['pixel_xy'] for p in detection['candidates']]
        def compare(predicted):
            if len(candidates) > 16:
                return {'status': 'unjudgeable_candidate_budget', 'errors_reference_px': None}
            return compare_set(candidates, predicted, crop_width=observed['crop_width'], overflow=detection['overflow'])
        rows.append({'case': observed['case'], 'frame_index': observed['frame_index'], 'icon': observed['icon'],
            'origin_ids': [p['id'] for p in points], 'observed_candidates': candidates,
            'asset_window_expected': expected, 'historical_tower_model_expected': reference,
            'asset_window': compare(expected), 'historical_tower_model': compare(reference),
            'identity_confirmed': False})
    summaries = {}
    for icon in ICONS:
        selected = [r for r in rows if r['icon'] == icon]
        summaries[icon] = {hypothesis: {
            'statuses': dict(Counter(r[hypothesis]['status'] for r in selected)),
            'errors_reference_360px': distribution([v for r in selected for v in r[hypothesis]['errors_reference_px'] or []])}
            for hypothesis in ('asset_window', 'historical_tower_model')}
    # The implied rectangle only describes a past tower fit; never apply it.
    rectangles = [{'case': f['case'], 'frame_index': f['frame_index'],
        'left': bounds['min_x']*matrix['scale_x']+matrix['offset_x']-f['crop_origin_client_xy'][0],
        'top': bounds['max_y']*matrix['scale_y']+matrix['offset_y']-f['crop_origin_client_xy'][1],
        'width': (bounds['max_x']-bounds['min_x'])*matrix['scale_x'],
        'height': -(bounds['max_y']-bounds['min_y'])*matrix['scale_y']}
        for f in plan['frames']]
    return {'schema_version': 1, 'kind': 'frozen_map_window_hypothesis_review',
        'historical_client_build': atlas['map_build'], 'current_client_build': landmarks['client_build'],
        'map_vpk_exact_bytes_compatible': True, 'external_dependencies_compatible': None,
        'authored_landmark_origins_exactly_matched': sum(new.values()),
        'asset_window_hypothesis': 'declared_bounds_fill_entire_saved_crop_edges',
        'parameters_fitted': False, 'historical_reference_training': 'towers_not_independent_ground_truth',
        'summaries': summaries, 'rows': rows, 'historical_reference_implied_rectangles': rectangles,
        'identity_confirmed': False, 'formal_p2_pass': False, 'map_certified': False,
        'world_physics_placement_verified': False,
        'limits': ['Repeated frames do not increase independent spatial points.',
            'Unordered glyph set compatibility does not identify named entities.',
            'A mismatch may reflect crop scale/padding, origin-to-glyph offset or detector error.',
            'Matching map VPK bytes does not certify runtime UI, external dependencies or physics placement.']}
