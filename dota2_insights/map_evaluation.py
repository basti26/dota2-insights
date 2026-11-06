"""Development-only map evaluation. Never releases sealed final labels or certifies P2.

Pixel window errors, direct coordinate translations and historical ward impact
are separate experiments. Repeated images never increase spatial sample size.
"""
from collections import defaultdict
import copy
import math
import statistics

from .virtual_map import MapIdentity, VirtualMap, WorldBounds, WorldPoint, digest, number


REGIONS = tuple(y + '_' + x for y in ('north', 'middle', 'south')
                for x in ('west', 'center', 'east'))
DEFAULT_POLICY = {'version': 'map-development-v1', 'reference_px': 1024,
                  'median_limit_px': 4, 'p95_limit_px': 10,
                  'minimum_unique_per_region': 3}


def summary(values):
    values = sorted(number(v) for v in values)
    return {'count': len(values), 'median': statistics.median(values) if values else None,
            'p95': values[math.ceil(.95 * len(values))-1] if values else None,
            'max': max(values) if values else None}


def vector(value, size):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError('evaluation_vector_invalid')
    return [number(v) for v in value]


def text(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError('evaluation_provenance_required')
    return value


def sha_value(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('evaluation_sha_required')
    return value


def policy_value(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_POLICY):
        raise ValueError('evaluation_policy_fields')
    text(value['version'])
    if type(value['reference_px']) is not int or value['reference_px'] != 1024:
        raise ValueError('evaluation_reference_must_be_1024')
    for key in ('median_limit_px', 'p95_limit_px'):
        if number(value[key]) <= 0:
            raise ValueError('evaluation_positive_threshold_required')
    if value['median_limit_px'] > value['p95_limit_px']:
        raise ValueError('evaluation_threshold_order')
    if type(value['minimum_unique_per_region']) is not int or not 3 <= value['minimum_unique_per_region'] <= 100:
        raise ValueError('evaluation_region_minimum')
    return dict(value)


def region(xyz, bounds):
    x, y = xyz[:2]
    u, v = (x-bounds.min_x)/(bounds.max_x-bounds.min_x), (bounds.max_y-y)/(bounds.max_y-bounds.min_y)
    if not 0 <= u <= 1 or not 0 <= v <= 1:
        raise ValueError('evaluation_landmark_outside_fixed_partition')
    return REGIONS[min(2, int(v*3))*3 + min(2, int(u*3))]


def associate_glyphs(dataset, observations, associations):
    """Attach explicit development identities to previously frozen detections.

    Pixel centers come only from the frozen full-image detector, never a map
    projection or nearest-world-point search. File callers verify receipts.
    """
    if (dataset.get('role') != 'development' or observations.get('predictions_read') is not False
            or associations.get('role') != 'development' or associations.get('prediction_guided') is not False
            or str(observations.get('map_build')) != str(dataset['image_map_identity']['client_build'])):
        raise ValueError('glyph_association_development_blind_context_required')
    reviewer = text(associations['reviewer'])
    entries = associations['rows']
    if not isinstance(entries, list) or not 1 <= len(entries) <= 128:
        raise ValueError('glyph_association_budget')
    result = copy.deepcopy(dataset)
    index = {(r['landmark_id'], r['image_sha256']): r for r in result['rows']}
    if len(index) != len(result['rows']):
        raise ValueError('glyph_association_duplicate_dataset_row')
    observed = {}
    for row in observations['rows']:
        key = row['case'], row['frame_index'], row['icon']
        if key in observed:
            raise ValueError('glyph_association_duplicate_observation')
        observed[key] = row
    used_rows, used_candidates = set(), set()
    for entry in entries:
        key = entry['landmark_id'], entry['image_sha256']
        obs_key = entry['case'], entry['frame_index'], entry['icon']
        offset = entry['candidate_index']
        candidate_key = obs_key, offset
        if (key not in index or key in used_rows or candidate_key in used_candidates
                or type(offset) is not int or offset < 0 or entry.get('visually_confirmed') is not True):
            raise ValueError('glyph_association_not_unique_or_confirmed')
        used_rows.add(key); used_candidates.add(candidate_key)
        row, obs = index[key], observed.get(obs_key)
        if row['pixel_xy'] is not None:
            raise ValueError('glyph_association_cannot_overwrite_existing_label')
        if (obs is None or obs['image_sha256'] != row['image_sha256']
                or obs.get('crop_width') != row['image_size'][0]):
            raise ValueError('glyph_association_image_context_mismatch')
        detection = obs['detection']
        if (detection.get('predictions_used') is not False or detection.get('overflow') is not False
                or offset >= len(detection['candidates'])):
            raise ValueError('glyph_association_detector_not_blind_or_complete')
        candidate = detection['candidates'][offset]
        pixel = vector(candidate['pixel_xy'], 2)
        # Frozen detector coordinates refer to the complete saved crop. Do not
        # invent a second origin, a glyph centroid or sub-pixel refinement.
        if row['content_box_xywh'] != [0, 0, *row['image_size']]:
            raise ValueError('glyph_association_full_crop_required')
        row.update(pixel_xy=pixel, confirmed_independently=True, prediction_guided=False,
                   confirmation_source=reviewer+': '+text(entry['basis']),
                   point_definition='frozen_template_canvas_center_not_glyph_centroid',
                   measurement_uncertainty_px=None,
                   pixel_evidence={'observations_sha256': digest(observations), 'case': entry['case'],
                                   'frame_index': entry['frame_index'], 'icon': entry['icon'],
                                   'candidate_index': offset, 'detector_score': candidate.get('score')})
        row.pop('missing_reason', None)
    result['association_sha256'] = digest(associations)
    return result


def evaluate(dataset, models, policy=None, *, compatibility=None):
    """Read development correspondences only; reject final/train material.

    A final inventory may carry a sealed file SHA, never its pixel labels. Its
    SHA is recorded without opening that file; this tool has no release path.
    """
    policy = policy_value(DEFAULT_POLICY if policy is None else policy)
    if type(dataset.get('schema_version')) is not int or dataset['schema_version'] != 1 or dataset.get('role') != 'development':
        raise ValueError('evaluation_development_only_no_final_release')
    identity = MapIdentity(**dataset['map_identity'])
    image_identity = MapIdentity(**dataset['image_map_identity'])
    if (image_identity.map_name != identity.map_name
            or image_identity.map_asset_sha256 != identity.map_asset_sha256):
        raise ValueError('evaluation_image_map_mismatch')
    compatibility_id = None
    if compatibility is not None:
        from .map_compatibility import validate
        compatibility_id = validate(compatibility, dataset['image_map_identity'], dataset['map_identity'])
    partition = WorldBounds(**dataset['partition_bounds'])
    if dataset.get('coordinate_frame') != 'source2_map_centered':
        raise ValueError('evaluation_asset_world_frame_required')
    final_sha = dataset.get('sealed_final_sha256')
    if final_sha is not None:
        sha_value(final_sha)
    rows = dataset['rows']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4096:
        raise ValueError('evaluation_row_budget')
    if not isinstance(models, dict) or not 1 <= len(models) <= 16:
        raise ValueError('evaluation_model_budget')
    parsed = {}
    for name, value in models.items():
        text(name)
        model = VirtualMap.from_dict(value)
        model.require_compatible(identity)
        if model.coordinate_frame != dataset['coordinate_frame']:
            raise ValueError('evaluation_model_frame_mismatch')
        parsed[name] = model
    seen, origins, locations, frames, prepared = set(), {}, {}, {}, []
    for row in rows:
        lid, image = text(row['landmark_id']), sha_value(row['image_sha256'])
        if (lid, image) in seen:
            raise ValueError('evaluation_duplicate_landmark_image')
        seen.add((lid, image))
        xyz = vector(row['world_xyz'], 3)
        original = (xyz, text(row['world_source']))
        if lid in origins and origins[lid] != original:
            raise ValueError('evaluation_landmark_origin_changed')
        origins[lid] = original
        spatial_key = tuple(xyz[:2])
        if spatial_key in locations and locations[spatial_key] != lid:
            raise ValueError('evaluation_same_xy_multiple_landmark_ids')
        locations[spatial_key] = lid
        size = vector(row['image_size'], 2)
        if any(type(v) is not int or not 32 <= v <= 16384 for v in row['image_size']):
            raise ValueError('evaluation_image_size')
        box = vector(row['content_box_xywh'], 4)
        left, top, width, height = box
        if (left < 0 or top < 0 or width <= 0 or height <= 0
                or left+width > size[0] or top+height > size[1]):
            raise ValueError('evaluation_content_box')
        if image in frames and frames[image] != (size, box):
            raise ValueError('evaluation_image_context_changed')
        frames[image] = (size, box)
        # Development observations may have been inspected before. That is
        # explicit; only independently confirmed centers enter numeric errors.
        if row.get('role') != 'development':
            raise ValueError('evaluation_role_overlap')
        if type(row.get('historical_training_point')) is not bool:
            raise ValueError('evaluation_training_history_required')
        pixel = row.get('pixel_xy')
        if pixel is not None:
            if image_identity != identity and compatibility_id is None:
                raise ValueError('evaluation_image_build_compatibility_not_approved')
            pixel = vector(pixel, 2)
            if (row.get('confirmed_independently') is not True or row.get('prediction_guided') is not False
                    or not left <= pixel[0] <= left+width or not top <= pixel[1] <= top+height):
                raise ValueError('evaluation_independent_pixel_required')
            text(row['confirmation_source'])
            text(row['point_definition'])
        else:
            text(row['missing_reason'])
        prepared.append((row, xyz, pixel, box, region(xyz, partition)))
    reports = {}
    for name, model in parsed.items():
        errors, grouped, missing = [], defaultdict(lambda: defaultdict(list)), defaultdict(set)
        for row, xyz, pixel, box, area in prepared:
            lid = row['landmark_id']
            if pixel is None:
                missing[area].add(lid)
                continue
            p = model.world_to_norm(WorldPoint(*xyz[:2], identity, model.coordinate_frame, xyz[2]),
                                    allow_unverified=True, allow_outside=True)
            left, top, width, height = box
            # Scale each axis by its own content extent: rectangular crops do
            # not silently use width as the Y scale.
            error = math.hypot(p.u-(pixel[0]-left)/width, p.v-(pixel[1]-top)/height)*1024
            grouped[area][lid].append(error)
            errors.append({'landmark_id': lid, 'image_sha256': row['image_sha256'],
                           'region': area, 'error_1024px': error,
                           'historical_training_point': row['historical_training_point']})
        regions = {}
        all_spatial = []
        for area in REGIONS:
            spatial = [statistics.median(v) for v in grouped[area].values()]
            all_spatial.extend(spatial)
            stats = summary(spatial)
            if len(spatial) < policy['minimum_unique_per_region']:
                status = 'insufficient_evidence'
            else:
                status = ('within_provisional_limits' if stats['median'] <= policy['median_limit_px']
                          and stats['p95'] <= policy['p95_limit_px'] else 'outside_provisional_limits')
            regions[area] = {'spatial_errors_1024px': stats,
                             'observed_unique_landmarks': len(grouped[area]),
                             'missing_unique_landmarks': len(missing[area]-set(grouped[area])),
                             'status': status}
        statuses = [r['status'] for r in regions.values()]
        # Report known failures even when other regions have no evidence.
        status = ('outside_provisional_limits' if 'outside_provisional_limits' in statuses else
                  'insufficient_evidence' if 'insufficient_evidence' in statuses else 'within_provisional_limits')
        reports[name] = {'model_id': model.model_id, 'status': status, 'regions': regions,
                         'spatial_errors_1024px': summary(all_spatial),
                         'frame_errors_1024px': summary([e['error_1024px'] for e in errors]), 'rows': errors}
    return {'schema_version': 1, 'kind': 'development_map_window_evaluation', 'policy': policy,
            'policy_sha256': digest(policy), 'dataset_sha256': digest(dataset),
            'models_sha256': digest(models), 'sealed_final_sha256': final_sha,
            'image_context_matches_target': image_identity == identity,
            'development_compatibility_id': compatibility_id, 'ui_equivalence_verified': False,
            'partition_bounds': dataset['partition_bounds'], 'models': reports,
            'parameters_fitted': False, 'final_labels_read': False,
            'formal_p2_pass': False, 'production_ready': False}


def translation_review(dataset, candidates):
    """Compare raw-replay and independent asset/world pairs in world units.

    No image windows are inputs. Direct same-object correspondences are
    necessary; a fitted pixel window cannot validate this translation.
    """
    if (type(dataset.get('schema_version')) is not int or dataset['schema_version'] != 1
            or dataset.get('source_frame') != 'gem_cell128_plus_vec_raw'
            or dataset.get('target_frame') != 'source2_map_centered'):
        raise ValueError('translation_pair_frames_required')
    MapIdentity(**dataset['map_identity'])
    rows = dataset['rows']
    if not isinstance(rows, list) or len(rows) > 4096:
        raise ValueError('translation_pair_budget')
    seen = set()
    pairs = []
    for row in rows:
        key = text(row['object_id'])
        if key in seen or row.get('same_object_confirmed') is not True or row.get('prediction_guided') is not False:
            raise ValueError('translation_independent_unique_object_required')
        seen.add(key)
        text(row['raw_source']); text(row['target_source']); text(row['confirmation_source'])
        pairs.append((key, vector(row['raw_xy'], 2), vector(row['world_xy'], 2)))
    if not isinstance(candidates, dict) or not 1 <= len(candidates) <= 16:
        raise ValueError('translation_candidate_budget')
    results = {}
    for name, delta in candidates.items():
        text(name)
        dx, dy = vector(delta, 2)
        errors = [{'object_id': key, 'error_world_units': math.hypot(raw[0]+dx-world[0], raw[1]+dy-world[1])}
                  for key, raw, world in pairs]
        results[name] = {'translation_xy': delta, 'errors_world_units': summary([e['error_world_units'] for e in errors]),
                         'rows': errors}
    return {'schema_version': 1, 'kind': 'direct_translation_development_review', 'results': results,
            'status': 'insufficient_evidence' if not pairs else 'development_comparison_only',
            'parameters_fitted': False, 'translation_verified': False, 'production_ready': False}


def ward_impact(layer, models, baseline):
    """Historical display displacement, never independent P3 ward validation."""
    from .map_prototype import ward_candidate_xy
    from .ward_map import status
    if baseline not in models or not 2 <= len(models) <= 16:
        raise ValueError('ward_impact_baseline_required')
    if not isinstance(layer.get('points'), list) or len(layer['points']) > 4096:
        raise ValueError('ward_impact_point_budget')
    seen = set()
    parsed = {name: VirtualMap.from_dict(value) for name, value in models.items()}
    base = parsed[baseline]
    source_identity = layer.get('source_map_identity')
    if source_identity is not None:
        source_identity = MapIdentity(**source_identity)
        if (source_identity.map_name != base.identity.map_name
                or source_identity.map_asset_sha256 != base.identity.map_asset_sha256):
            raise ValueError('ward_impact_source_map_mismatch')
    # Respect the original binding first. Derived comparisons never rewrite it.
    if not status(layer, base)['preview_allowed']:
        raise ValueError('ward_impact_source_not_admitted')
    for model in parsed.values():
        model.require_compatible(base.identity)
        if model.coordinate_frame != base.coordinate_frame:
            raise ValueError('ward_impact_frame_mismatch')
    results = {name: [] for name in models if name != baseline}
    for point in layer['points']:
        key = text(point['id'])
        if key in seen:
            raise ValueError('ward_impact_duplicate_id')
        seen.add(key)
        x, y = ward_candidate_xy(layer, point, base)
        wp = WorldPoint(x, y, base.identity, base.coordinate_frame)
        old = base.world_to_norm(wp, allow_unverified=True, allow_outside=True)
        for name, model in parsed.items():
            if name == baseline:
                continue
            new = model.world_to_norm(wp, allow_unverified=True, allow_outside=True)
            results[name].append({'id': key, 'raw_xy': point['raw_world_xy'],
                                   'baseline_norm': [old.u, old.v], 'candidate_norm': [new.u, new.v],
                                   'displacement_1024px': math.hypot(new.u-old.u, new.v-old.v)*1024})
    return {'schema_version': 1, 'kind': 'historical_ward_window_impact', 'baseline': baseline,
            'source_map_relation': status(layer, base)['source_map_relation'],
            'translation_candidate_xy': layer['translation_candidate_xy'],
            'source_layer_sha256': digest(layer), 'models_sha256': digest(models),
            'results': {name: {'rows': rows, 'displacement_1024px': summary([r['displacement_1024px'] for r in rows])}
                        for name, rows in results.items()},
            'interpretation': 'display_displacement_only_no_independent_pixel_truth',
            'conclusions_reversed': None, 'formal_p3_pass': False, 'production_ready': False}
