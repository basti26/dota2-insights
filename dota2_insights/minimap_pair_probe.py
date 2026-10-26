"""Adjacent-image component links are pixel evidence, never hero tracks."""
import math
import numpy as np

from .helper_handoff import utc
from .minimap_component_probe import probe

VERSION = 'minimap_mutual_component_pair_probe_v1'
PARAMETERS = {'max_capture_gap_s': 1.5, 'max_link_normalized_distance': .035,
              'stationary_reference_size': 372, 'stationary_distance_px': 1.5,
              'changed_channel_threshold': 30}


def compare(previous, current, previous_metadata, current_metadata):
    result = {'version': VERSION, 'parameters': PARAMETERS, 'state': 'unavailable', 'reason': None,
        'links': [], 'hero_count': None, 'identity_verified': False, 'hero_disappearance_inferred': False,
        'online_warning_enabled': False, 'meaning': 'mutual_unique_image_component_links_not_hero_tracks'}
    if (not isinstance(previous, np.ndarray) or not isinstance(current, np.ndarray)
            or previous.dtype != np.uint8 or current.dtype != np.uint8
            or previous.ndim != 3 or current.ndim != 3 or previous.shape[2] != 3 or current.shape[2] != 3):
        raise ValueError('pair_probe_uint8_bgr_required')
    if previous.shape != current.shape:
        return {**result, 'reason': 'image_dimensions_changed'}
    for key in ('client_size', 'roi', 'layout_segment', 'time_basis'):
        if previous_metadata.get(key) is None or current_metadata.get(key) != previous_metadata[key]:
            return {**result, 'reason': 'layout_or_time_basis_unknown_or_changed'}
    roi = current_metadata.get('roi')
    if (not isinstance(roi, list) or len(roi) != 4 or any(type(value) is not int for value in roi)
            or roi[2:] != [current.shape[1], current.shape[0]]
            or type(current_metadata.get('layout_segment')) is not int
            or not isinstance(current_metadata.get('time_basis'), str) or not current_metadata['time_basis'].strip()):
        return {**result, 'reason': 'layout_or_image_geometry_disagrees'}
    for key in ('sequence', 'sample_ordinal'):
        before, after = previous_metadata.get(key), current_metadata.get(key)
        if type(before) is not int or type(after) is not int or after != before + 1:
            return {**result, 'reason': 'nonadjacent_samples'}
    try:
        dt = (utc(current_metadata.get('captured_at_utc')) - utc(previous_metadata.get('captured_at_utc'))).total_seconds()
    except (ValueError, TypeError):
        return {**result, 'reason': 'capture_clock_unknown'}
    result['capture_gap_s'] = dt
    if not 0 < dt <= PARAMETERS['max_capture_gap_s']:
        return {**result, 'reason': 'capture_clock_gap_or_regression'}
    old, new = probe(previous), probe(current)
    if old['candidate_limit_reached'] or new['candidate_limit_reached']:
        return {**result, 'reason': 'candidate_limit_prevents_pair_comparison'}
    height, width = current.shape[:2]
    edges = []
    for new_item in new['candidates']:
        x, y = new_item['center']
        edges.append([i for i, old_item in enumerate(old['candidates'])
            if old_item['color_band'] == new_item['color_band']
            and math.hypot((x - old_item['center'][0]) / width, (y - old_item['center'][1]) / height)
                <= PARAMETERS['max_link_normalized_distance']])
    used_previous, links = set(), []
    for new_index, matches in enumerate(edges):
        if len(matches) != 1 or sum(matches[0] in values for values in edges) != 1:
            continue
        old_index = matches[0]
        before, after = old['candidates'][old_index], new['candidates'][new_index]
        distance = math.dist(before['center'], after['center'])
        stationary = distance <= PARAMETERS['stationary_distance_px'] * min(height, width) / PARAMETERS['stationary_reference_size']
        used_previous.add(old_index)
        links.append({'previous_index': old_index, 'current_index': new_index,
            'previous_center': before['center'], 'current_center': after['center'],
            'color_band': after['color_band'], 'distance_px': round(distance, 4),
            'pixel_motion': 'small_displacement' if stationary else 'larger_displacement',
            'square_overlap_either_frame': bool(before['solid_square_overlaps'] or after['solid_square_overlaps']),
            'text_overlap_either_frame': bool(before['text_band_overlaps'] or after['text_band_overlaps']),
            'hero_or_building': 'unresolved'})
    changed = np.max(np.abs(current.astype(np.int16) - previous.astype(np.int16)), axis=2) > PARAMETERS['changed_channel_threshold']
    result.update({'state': 'compared', 'reason': 'adjacent_compatible_capture_pair', 'links': links,
        'previous_candidates': len(old['candidates']), 'current_candidates': len(new['candidates']),
        'current_without_unique_link': len(new['candidates']) - len(links),
        'previous_without_unique_link': len(old['candidates']) - len(used_previous),
        'small_displacement_links': sum(link['pixel_motion'] == 'small_displacement' for link in links),
        'larger_displacement_links': sum(link['pixel_motion'] == 'larger_displacement' for link in links),
        'changed_pixel_fraction': round(float(changed.mean()), 6)})
    return result
