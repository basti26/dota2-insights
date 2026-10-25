"""Frozen non-tower minimap checks; never a world/map/ward certificate."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import statistics

PROTOCOL = {
    'version': 'non_tower_holdout_v1', 'reference_minimap_width_px': 360,
    'maximum_error_reference_px': 1.5, 'maximum_bias_reference_px': .75,
    'bias_scope': 'unique_spatial_points_and_each_capture_case',
    'maximum_uncertainty_reference_px': .5, 'minimum_unique_measured_points': 8,
    'required_quadrants': ['NE', 'NW', 'SE', 'SW'],
    'required_power_rune_points': 2, 'frame_indices': [31, 217, 403],
    'point_definition': 'visually_identified_minimap_glyph_geometric_center',
    'unmeasurable_origins': 'No glyph/ambiguous center/occlusion remains unjudgeable; never use camera targets or prediction-guided pixel search.',
    'claim': 'held_out_landmark_pixel_consistency_only_not_world_coordinate_or_ward_certification',
}
CATEGORIES = {'barracks', 'ancient', 'fountain', 'power_rune_spawn_origin',
              'bounty_spawn_origin', 'lotus_pool', 'watch_tower'}


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load(path, limit=16 * 1024 ** 2):
    path = Path(path)
    if path.stat().st_size > limit:
        raise ValueError('holdout_json_budget')
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)


def local(root, name):
    p = (Path(root) / name).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise ValueError('holdout_path_escape')
    return p


def finite(v):
    return type(v) in (float, int) and math.isfinite(v)


def quadrant(x, y):
    return ('N' if y >= 0 else 'S') + ('E' if x >= 0 else 'W')


def select_points(atlas):
    points = [p for p in atlas['entities'] if p['category'] in CATEGORIES
              and not p.get('parent_transform_unresolved')]
    # One authored spawn origin per quadrant, selected before any image review.
    for q in PROTOCOL['required_quadrants']:
        camps = [p for p in atlas['entities'] if p['category'] == 'neutral_spawn_origin'
                 and not p.get('parent_transform_unresolved')
                 and quadrant(*p['native_origin_xyz'][:2]) == q]
        if camps:
            target = (4000 if q[1] == 'E' else -4000, 4000 if q[0] == 'N' else -4000)
            points.append(min(camps, key=lambda p: (sum((p['native_origin_xyz'][i] - target[i]) ** 2
                                                      for i in (0, 1)), p['id'])))
    if not points or len({p['id'] for p in points}) != len(points) or len(points) > 64:
        raise ValueError('holdout_unique_non_tower_points_required')
    return sorted(points, key=lambda p: (p['category'], p['id']))


def prepare(atlas_path, request_path, output):
    from PIL import Image
    from dota2_insights.minimap.offline_io import check_input_paths
    atlas_path, request_path, output = Path(atlas_path), Path(request_path), Path(output)
    check_input_paths(atlas_path, request_path)
    source_hashes = {str(p.resolve()): sha(p) for p in (atlas_path, request_path, Path(__file__),
        Path(__file__).with_name('map_holdout_view.py'))}
    atlas, request = load(atlas_path), load(request_path)
    build, calibration = atlas['map_build'], atlas['calibration']
    matrix = calibration['matrix_client']
    if any(not finite(matrix.get(k)) for k in ('scale_x', 'scale_y', 'offset_x', 'offset_y')) or any(
            matrix[k] == 0 for k in ('scale_x', 'scale_y')):
        raise ValueError('holdout_frozen_matrix_required')
    points = select_points(atlas)
    training_ids = {r.get('atlas_id') for r in calibration.get('training', {}).get('matches', [])}
    if any(p['category'] == 'tower' or p['id'] in training_ids for p in points):
        raise ValueError('holdout_training_overlap')
    if not 2 <= len(request['cases']) <= 12 or len({c['id'] for c in request['cases']}) != len(request['cases']):
        raise ValueError('holdout_cases_invalid')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'images').mkdir()
    (output / 'reference').mkdir()
    frames, predictions = [], []
    for case in request['cases']:
        cid = case['id']
        if not isinstance(cid, str) or not cid or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_-' for c in cid):
            raise ValueError('holdout_case_id')
        folder = Path(case['folder'])
        check_input_paths(folder)
        index_path = folder / 'frames.jsonl'
        for p in (folder / 'manifest.json', folder / 'tour-confirmed.json', index_path):
            source_hashes[str(p.resolve())] = sha(p)
        manifest = load(folder / 'manifest.json', 128 * 1024)
        confirmation = load(folder / 'tour-confirmed.json', 16384)
        if (manifest['config'].get('purpose') != 'map_survey'
                or manifest['config'].get('match_id') not in ('', None)):
            raise ValueError('holdout_only_map_surveys_allowed')
        if manifest['config'].get('map_build') != build:
            raise ValueError('holdout_cross_build_refused')
        if source_hashes[str(index_path.resolve())] != manifest['frames.jsonl_sha256']:
            raise ValueError('holdout_index_changed')
        if index_path.stat().st_size > 32 * 1024 ** 2:
            raise ValueError('holdout_index_budget')
        rows = [json.loads(l) for l in index_path.read_text(encoding='utf-8').splitlines()]
        if len(rows) > 14400 or len({r['frame_index'] for r in rows}) != len(rows):
            raise ValueError('holdout_index_count_or_duplicates')
        index = {r['frame_index']: r for r in rows}
        for number in PROTOCOL['frame_indices']:
            row = index[number]
            p = local(folder, row['file'])
            if p.parent != (folder / 'frames').resolve() or p.stat().st_size > 64 * 1024 ** 2:
                raise ValueError('holdout_image_scope')
            source_hashes[str(p)] = sha(p)
            if source_hashes[str(p)] != row['sha256']:
                raise ValueError('holdout_image_changed')
            with Image.open(p) as raw:
                if list(raw.size) != [row['width'], row['height']] or list(raw.size) != calibration['client_size']:
                    raise ValueError('holdout_client_size')
                roi = confirmation['roi']
                if (len(roi) != 4 or any(not finite(v) for v in roi)
                        or not 0 <= roi[0] < roi[2] <= 1 or not 0 <= roi[1] < roi[3] <= 1):
                    raise ValueError('holdout_roi')
                bounds = [round(v * size) for v, size in zip(roi, [raw.width, raw.height] * 2)]
                crop = raw.crop(bounds).convert('RGB')
                image_name = f'images/{cid}-{number}.png'
                crop.save(output / image_name)
            frame = {'case': cid, 'frame_index': number, 'image': image_name,
                'image_sha256': sha(output / image_name), 'source_image_sha256': row['sha256'],
                'crop_origin_client_xy': bounds[:2], 'size': list(crop.size),
                'source_file': str(p), 'environment_label': case['environment']}
            frames.append(frame)
        origin = frames[-1]['crop_origin_client_xy']
        for point in points:
            x, y = point['native_origin_xyz'][:2]
            predictions.append({'case': cid, 'point_id': point['id'],
                'pixel_xy': [matrix['scale_x'] * x + matrix['offset_x'] - origin[0],
                             matrix['scale_y'] * y + matrix['offset_y'] - origin[1]],
                'scale_xy': [matrix['scale_x'], matrix['scale_y']]})
    # Existing semantic world data/tower fit have been seen; do not call this
    # wholly blind independent validation. Non-tower points were never fitted.
    plan = {'schema_version': 1, 'kind': 'non_tower_spatial_holdout', 'map_build': build,
        'protocol': PROTOCOL, 'source_hashes': source_hashes,
        'observer_exposure': 'prior_atlas_world_data_and_tower_calibration_seen_not_clean_blind',
        'points': [{'id': p['id'], 'name': p['name'], 'category': p['category'],
                    'quadrant': quadrant(*p['native_origin_xyz'][:2])} for p in points],
        'frames': frames, 'cases': [c['id'] for c in request['cases']],
        'observations_must_precede_prediction_comparison': True,
        'map_certified': False}
    save(output / 'plan.json', plan)
    save(output / 'reference/predictions.json', {'predictions': predictions})
    observations = {'schema_version': 1, 'map_build': build,
        'plan_sha256': sha(output / 'plan.json'), 'reviewer': '',
        'observer_exposure': plan['observer_exposure'],
        'rows': [{'case': c, 'point_id': p['id'], 'status': 'pending', 'frame_index': None,
                  'pixel_xy': None, 'uncertainty_px': None, 'identity_confirmed': False, 'reason': ''}
                 for c in plan['cases'] for p in plan['points']]}
    save(output / 'observations-template.json', observations)
    from .map_holdout_view import write_view
    write_view(output / 'annotate.html', plan, observations)
    files = {name: sha(output / name) for name in ('plan.json', 'reference/predictions.json',
                                                  'observations-template.json', 'annotate.html')}
    files.update({f['image']: f['image_sha256'] for f in frames})
    save(output / 'package.json', {'schema_version': 1, 'files': files})
    (output / 'package.sha256').write_text(sha(output / 'package.json'), encoding='ascii')
    # Plain native-pixel gallery: deliberately no predicted dots/coordinates.
    cards = ''.join(f'<h2>{html.escape(f["case"])} · {f["frame_index"]}</h2>'
        f'<img src="{f["image"]}" width="{f["size"][0]*2}" style="image-rendering:pixelated">'
        for f in frames)
    (output / 'review.html').write_text('<!doctype html><meta charset="utf-8"><title>非塔地标核对</title>'
        '<style>body{background:#19212b;color:#eee;font:16px sans-serif;margin:24px}</style>'
        '<h1>非塔地标原图 · 不显示预测点</h1><p>只量可独立辨认的图标中心；无图标/遮挡记无法判断。'
        '图片显示为2倍，观察坐标仍须填原始图片像素。先冻结观察，再比较预测。</p>' + cards, encoding='utf-8')
    if any(sha(p) != h for p, h in source_hashes.items()):
        raise ValueError('holdout_sources_changed_during_prepare')
    return plan


def read_plan(package):
    package = Path(package)
    if sha(package / 'package.json') != (package / 'package.sha256').read_text(encoding='ascii').strip():
        raise ValueError('holdout_package_changed')
    manifest = load(package / 'package.json')
    # Do not read predictions during observation validation/freezing.
    if sha(package / 'plan.json') != manifest['files']['plan.json']:
        raise ValueError('holdout_plan_changed')
    plan = load(package / 'plan.json')
    for f in plan['frames']:
        p = local(package, f['image'])
        if sha(p) != f['image_sha256'] or manifest['files'][f['image']] != f['image_sha256']:
            raise ValueError('holdout_evidence_changed')
    return plan, manifest


def validate_observations(plan, plan_hash, observations):
    if (observations.get('schema_version') != 1 or observations.get('map_build') != plan['map_build']
            or observations.get('plan_sha256') != plan_hash
            or observations.get('observer_exposure') != plan['observer_exposure']):
        raise ValueError('holdout_observation_binding')
    expected = {(case, p['id']) for case in plan['cases'] for p in plan['points']}
    rows = observations.get('rows')
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError('holdout_full_roster_required')
    seen = set()
    frames = {(f['case'], f['frame_index']): f for f in plan['frames']}
    for r in rows:
        key = r.get('case'), r.get('point_id')
        if key not in expected or key in seen or r.get('status') not in {'pending', 'measured', 'unjudgeable'}:
            raise ValueError('holdout_row_identity_or_status')
        seen.add(key)
        if r['status'] == 'pending':
            continue
        frame = frames.get((r['case'], r.get('frame_index')))
        if frame is None or not isinstance(r.get('reason'), str) or not r['reason'].strip():
            raise ValueError('holdout_review_evidence_and_reason_required')
        if r['status'] == 'measured':
            xy = r.get('pixel_xy')
            if (not isinstance(xy, list) or len(xy) != 2 or any(not finite(v) for v in xy)
                    or any(not 0 <= v < size for v, size in zip(xy, frame['size']))
                    or not finite(r.get('uncertainty_px')) or r['uncertainty_px'] < 0
                    or r.get('identity_confirmed') is not True):
                raise ValueError('holdout_measurement_invalid')


def freeze(package, observations_path, output):
    package, output = Path(package), Path(output)
    plan, _ = read_plan(package)
    obs = load(observations_path, 1024 ** 2)
    validate_observations(plan, sha(package / 'plan.json'), obs)
    if any(r['status'] != 'pending' for r in obs['rows']) and (
            not isinstance(obs.get('reviewer'), str) or not obs['reviewer'].strip()):
        raise ValueError('holdout_reviewer_required')
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'observations.json', obs)
    save(output / 'freeze.json', {'schema_version': 1,
        'observations_sha256': sha(output / 'observations.json'),
        'package_sha256': sha(package / 'package.json'),
        'frozen_at_utc': datetime.now(timezone.utc).isoformat(), 'predictions_read': False})
    return obs


def distribution(values):
    if not values:
        return None
    a = sorted(values)
    return {'count': len(a), 'min': a[0], 'median': statistics.median(a),
            'mean': statistics.fmean(a), 'p90': a[math.ceil(.90*len(a))-1],
            'p95': a[math.ceil(.95*len(a))-1], 'max': a[-1], 'percentile_method': 'nearest_rank'}


def compare(package, batch, output):
    package, batch, output = Path(package), Path(batch), Path(output)
    plan, manifest = read_plan(package)
    frozen = load(batch / 'freeze.json', 16384)
    if (frozen.get('package_sha256') != sha(package / 'package.json')
            or frozen.get('observations_sha256') != sha(batch / 'observations.json')
            or frozen.get('predictions_read') is not False):
        raise ValueError('holdout_freeze_changed')
    observations = load(batch / 'observations.json', 1024 ** 2)
    validate_observations(plan, sha(package / 'plan.json'), observations)
    reference_path = package / 'reference/predictions.json'
    if sha(reference_path) != manifest['files']['reference/predictions.json']:
        raise ValueError('holdout_predictions_changed')
    ref = {(r['case'], r['point_id']): r for r in load(reference_path)['predictions']}
    frames = {(r['case'], r['frame_index']): r for r in plan['frames']}
    details, counts, by_point, by_case = [], Counter(), defaultdict(list), defaultdict(list)
    for obs in observations['rows']:
        key = obs['case'], obs['point_id']
        status = obs['status']
        result = {'case': key[0], 'point_id': key[1], 'status': status, 'reason': obs.get('reason')}
        if status == 'measured':
            f, predicted = frames[(key[0], obs['frame_index'])], ref[key]
            factor = plan['protocol']['reference_minimap_width_px'] / f['size'][0]
            if obs['uncertainty_px'] * factor > plan['protocol']['maximum_uncertainty_reference_px']:
                result.update(status='unjudgeable', reason='measurement_uncertainty_exceeds_frozen_limit')
            else:
                dx, dy = [obs['pixel_xy'][i] - predicted['pixel_xy'][i] for i in (0, 1)]
                error = math.hypot(dx, dy) * factor
                result.update(status='pass' if error <= plan['protocol']['maximum_error_reference_px'] else 'mismatch',
                    observed_pixel_xy=obs['pixel_xy'], predicted_pixel_xy=predicted['pixel_xy'],
                    dx_reference_px=dx * factor, dy_reference_px=dy * factor,
                    error_reference_px=error,
                    residual_authored_units=math.hypot(dx / predicted['scale_xy'][0], dy / predicted['scale_xy'][1]),
                    units_meaning='inverse_frozen_pixel_fit_not_independent_world_position',
                    frame_index=obs['frame_index'])
                by_point[key[1]].append(result)
                by_case[key[0]].append(result)
        counts[result['status']] += 1
        details.append(result)
    # Unique spatial points carry the bias statistic; repeated frames/cases
    # cannot inflate the number of independent landmark identities.
    point_means = [{'point_id': k, 'dx': statistics.fmean(r['dx_reference_px'] for r in v),
                    'dy': statistics.fmean(r['dy_reference_px'] for r in v)} for k, v in by_point.items()]
    bias = [statistics.fmean(p[axis] for p in point_means) for axis in ('dx', 'dy')] if point_means else None
    measured = [p for p in plan['points'] if p['id'] in by_point]
    case_bias = {k: [statistics.fmean(r[axis] for r in values)
                    for axis in ('dx_reference_px', 'dy_reference_px')]
                 for k, values in by_case.items()}
    coverage = {'unique_points': len(measured), 'quadrants': sorted({p['quadrant'] for p in measured}),
                'power_rune_points': sum(p['category'] == 'power_rune_spawn_origin' for p in measured)}
    ready = (counts['pending'] == 0 and counts['mismatch'] == 0
        and coverage['unique_points'] >= plan['protocol']['minimum_unique_measured_points']
        and set(coverage['quadrants']) == set(plan['protocol']['required_quadrants'])
        and coverage['power_rune_points'] >= plan['protocol']['required_power_rune_points']
        and bias is not None and all(abs(v) <= plan['protocol']['maximum_bias_reference_px'] for v in bias)
        and all(abs(v) <= plan['protocol']['maximum_bias_reference_px'] for xy in case_bias.values() for v in xy)
        and all(case in by_case for case in plan['cases']))
    report = {'schema_version': 1, 'kind': 'non_tower_holdout_comparison', 'map_build': plan['map_build'],
        'protocol': plan['protocol'],
        'counts': {s: counts[s] for s in ('pass', 'mismatch', 'pending', 'unjudgeable')}, 'coverage': coverage,
        'bias_reference_xy': bias, 'case_bias_reference_xy': case_bias, 'unique_point_means': point_means,
        'error_distribution_reference_px': distribution([r['error_reference_px'] for v in by_case.values() for r in v]),
        'case_distributions': {k: distribution([r['error_reference_px'] for r in v]) for k, v in by_case.items()},
        'p2_supported': ready, 'map_certified': False, 'ward_validation_unlocked': False,
        'observer_exposure': plan['observer_exposure'], 'details': details,
        'bindings': {'package_sha256': sha(package / 'package.json'), 'freeze_sha256': sha(batch / 'freeze.json'),
                     'observations_sha256': frozen['observations_sha256'], 'review_code_sha256': sha(Path(__file__))}}
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'comparison.json', report)
    (output / 'report.html').write_text('<!doctype html><meta charset="utf-8"><title>非塔地标留出核对</title>'
        '<style>body{font:16px sans-serif;margin:24px}pre{white-space:pre-wrap}</style>'
        '<h1>非塔地标留出核对</h1><p>仅当前构建和已观察点；不认证世界坐标，不解锁眼位封存。</p><pre>'
        + html.escape(json.dumps(report, ensure_ascii=False, indent=2)) + '</pre>', encoding='utf-8')
    return report
