"""Offline color/shape ambiguity diagnostics; no identities or live hooks."""
import hashlib
import json
import math

import cv2
import numpy as np

from .minimap.static_map_calibration import square_candidates

VERSION = 'minimap_color_text_overlap_probe_v1'
# Baseline adapts Foresight's minimap_color_candidates_v1. Origin SHA is
# recorded separately; this module never imports the other repository.
BANDS = {'red_low': ((0, 140, 130), (10, 255, 255)),
         'red_high': ((170, 140, 130), (179, 255, 255)),
         'green': ((35, 140, 150), (85, 255, 255)),
         'blue': ((90, 140, 150), (135, 255, 255))}
PARAMETERS = {'baseline_reference_size': 240, 'baseline_close_kernel': [3, 3],
    'baseline_area_range': [8, 700], 'baseline_max_aspect': 3, 'baseline_min_circularity': .12,
    'candidate_limit': 48, 'text_reference_size': 372, 'text_join_width': 5,
    'text_min_width': 18, 'text_height_range': [3, 16], 'text_min_aspect': 2.4,
    'text_min_original_pixels': 16, 'text_limit': 128}


def _image(image):
    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim != 3
            or image.shape[2] != 3 or not 64 <= image.shape[0] <= 1024
            or not 64 <= image.shape[1] <= 1024):
        raise ValueError('component_probe_native_uint8_bgr_required')


def _baseline(image, masks):
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    for value in masks.values():
        mask |= value
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    scale = min(image.shape[:2]) / 240.0
    result = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if not 8 * scale**2 <= area <= 700 * scale**2:
            continue
        x, y, width, height = cv2.boundingRect(contour)
        if width < 2 or height < 2 or max(width / height, height / width) > 3:
            continue
        perimeter = cv2.arcLength(contour, True)
        circularity = 4 * math.pi * area / perimeter**2 if perimeter else 0
        if circularity < .12:
            continue
        padding = max(2, round(3 * scale))
        left, top = max(0, x - padding), max(0, y - padding)
        right, bottom = min(image.shape[1], x + width + padding), min(image.shape[0], y + height + padding)
        color = max(masks, key=lambda name: cv2.countNonZero(masks[name][y:y + height, x:x + width]))
        result.append({'box': [left, top, right - left, bottom - top],
            'center': [round(x + width / 2, 2), round(y + height / 2, 2)], 'color_band': color,
            'area': round(area, 2), 'circularity': round(circularity, 3)})
    result.sort(key=lambda entry: (-entry['area'], entry['center'][1], entry['center'][0]))
    return result[:48], len(result)


def _text_bands(image, masks):
    scale = min(image.shape[:2]) / PARAMETERS['text_reference_size']
    join = max(2, round(PARAMETERS['text_join_width'] * scale))
    found = []
    for color, mask in (('red', masks['red_low'] | masks['red_high']), ('green', masks['green'])):
        joined = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((1, join), np.uint8))
        _, _, stats, _ = cv2.connectedComponentsWithStats(joined, 8)
        for x, y, width, height, _ in stats[1:]:
            x, y, width, height = map(int, (x, y, width, height))
            original_pixels = int(cv2.countNonZero(mask[y:y + height, x:x + width]))
            if (width >= PARAMETERS['text_min_width'] * scale and width / height >= PARAMETERS['text_min_aspect']
                    and PARAMETERS['text_height_range'][0] * scale <= height <= PARAMETERS['text_height_range'][1] * scale
                    and original_pixels >= PARAMETERS['text_min_original_pixels'] * scale**2):
                found.append({'box': [x, y, width, height], 'display_color': color,
                    'original_color_pixels': original_pixels,
                    'meaning': 'horizontal_color_band_may_be_text_or_other_marks'})
    found.sort(key=lambda item: (item['box'][1], item['box'][0], item['display_color']))
    return found[:128], len(found)


def _intersection(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    return max(0, min(a[0] + a[2], b[0] + b[2]) - x) * max(0, min(a[1] + a[3], b[1] + b[3]) - y)


def probe(image):
    _image(image)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    masks = {name: cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8))
             for name, (low, high) in BANDS.items()}
    candidates, total = _baseline(image, masks)
    text, text_total = _text_bands(image, masks)
    try:
        squares = square_candidates(image)
        square_state = 'shape_candidates_only'
    except ValueError:
        squares, square_state = [], 'unsupported_or_ambiguous'
    for entry in candidates:
        color = {'red_low': 'red', 'red_high': 'red', 'green': 'green'}.get(entry['color_band'])
        entry['text_band_overlaps'] = [i for i, band in enumerate(text)
            if band['display_color'] == color and _intersection(entry['box'], band['box']) > 0]
        entry['solid_square_overlaps'] = [i for i, square in enumerate(squares)
            if square['color'] == color and entry['box'][0] <= square['pixel_x'] < entry['box'][0] + entry['box'][2]
            and entry['box'][1] <= square['pixel_y'] < entry['box'][1] + entry['box'][3]]
        entry.update(hero_id=None, team_relation='unknown', suppressed=False)
    settings = {'bands': BANDS, 'parameters': PARAMETERS}
    return {'version': VERSION, 'image_size': [image.shape[1], image.shape[0]],
        'settings_sha256': hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest(),
        'settings': settings, 'candidate_count_before_limit': total, 'candidates': candidates,
        'candidate_limit_reached': total > len(candidates), 'text_bands': text,
        'text_band_count_before_limit': text_total, 'text_limit_reached': text_total > len(text),
        'square_status': square_state, 'square_shapes': squares,
        'candidates_with_text_overlap': sum(bool(c['text_band_overlaps']) for c in candidates),
        'candidates_with_square_overlap': sum(bool(c['solid_square_overlaps']) for c in candidates),
        'hero_count': None, 'identity_verified': False, 'text_classification_verified': False,
        'building_classification_verified': False, 'online_changes_applied': False,
        'meaning': 'overlap_flags_not_hero_counts_no_candidate_suppression'}
