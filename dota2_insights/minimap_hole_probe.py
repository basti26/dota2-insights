"""Measure enclosed holes in display-color components; glyphs can qualify too."""
import hashlib
import json
import math
import cv2
import numpy as np

from .minimap_component_probe import BANDS, probe as component_probe, _image, _intersection

VERSION = 'minimap_enclosed_color_hole_probe_v1'
PARAMETERS = {'reference_size': 372, 'max_dimension_range': [8, 28], 'min_dimension': 4,
    'max_aspect': 3, 'minimum_contour_area': 12, 'minimum_circularity': .2,
    'hole_pixel_fraction_range': [.05, .85], 'feature_limit': 128}


def describe(image):
    _image(image)
    evidence = component_probe(image)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    masks = {name: cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8))
             for name, (low, high) in BANDS.items()}
    scale = min(image.shape[:2]) / PARAMETERS['reference_size']
    result = []
    for color, mask in (('red', masks['red_low'] | masks['red_high']), ('green', masks['green']), ('blue', masks['blue'])):
        contours, hierarchy = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
        if hierarchy is None:
            continue
        for i, contour in enumerate(contours):
            if hierarchy[0][i][3] != -1 or hierarchy[0][i][2] == -1:
                continue
            x, y, width, height = cv2.boundingRect(contour)
            area = float(cv2.contourArea(contour))
            perimeter = float(cv2.arcLength(contour, True))
            circularity = 4 * math.pi * area / perimeter**2 if perimeter else 0
            if (not PARAMETERS['max_dimension_range'][0] * scale <= max(width, height) <= PARAMETERS['max_dimension_range'][1] * scale
                    or min(width, height) < PARAMETERS['min_dimension'] * scale
                    or max(width / height, height / width) > PARAMETERS['max_aspect']
                    or area < PARAMETERS['minimum_contour_area'] * scale**2
                    or circularity < PARAMETERS['minimum_circularity']):
                continue
            hole_count, child = 0, int(hierarchy[0][i][2])
            while child >= 0:
                hole_count += 1
                child = int(hierarchy[0][child][0])
            # Contour-area ratios are biased for a one-pixel border: the
            # child contour includes boundary pixels. Measure actual vacant
            # pixels inside the filled outer contour instead.
            outer = np.zeros((height, width), np.uint8)
            cv2.drawContours(outer, [contour - np.array([[[x, y]]], np.int32)], -1, 255, -1)
            enclosed_pixels = int(cv2.countNonZero(outer))
            hole_pixels = int(np.count_nonzero((outer > 0) & (mask[y:y + height, x:x + width] == 0)))
            hole_ratio = hole_pixels / enclosed_pixels if enclosed_pixels else 0
            if not PARAMETERS['hole_pixel_fraction_range'][0] <= hole_ratio <= PARAMETERS['hole_pixel_fraction_range'][1]:
                continue
            box = [x, y, width, height]
            result.append({'box': box, 'center': [round(x + width / 2, 3), round(y + height / 2, 3)],
                'display_color': color, 'outer_area': round(area, 4), 'hole_pixel_fraction': round(hole_ratio, 4),
                'enclosed_pixels': enclosed_pixels, 'hole_pixels': hole_pixels,
                'hole_count': hole_count, 'circularity': round(circularity, 4),
                'normalized_width': round(width / scale, 3), 'normalized_height': round(height / scale, 3),
                'text_band_overlaps': [j for j, band in enumerate(evidence['text_bands'])
                    if band['display_color'] == color and _intersection(box, band['box']) > 0],
                'square_shape_overlaps': [j for j, square in enumerate(evidence['square_shapes'])
                    if square['color'] == color and _intersection(box, square['rect']) > 0],
                'shape_hint': 'enclosed_color_hole', 'hero_id': None, 'team_relation': 'unknown',
                'hero_or_glyph_or_other': 'unresolved'})
    result.sort(key=lambda entry: (entry['box'][1], entry['box'][0], entry['display_color']))
    return {'version': VERSION, 'parameters': PARAMETERS,
        'settings_sha256': hashlib.sha256(json.dumps(PARAMETERS, sort_keys=True).encode()).hexdigest(),
        'feature_count_before_limit': len(result), 'features': result[:PARAMETERS['feature_limit']],
        'limit_reached': len(result) > PARAMETERS['feature_limit'], 'hero_count': None,
        'shape_semantics_verified': False, 'online_changes_applied': False,
        'meaning': 'enclosed_color_holes_not_confirmed_hero_rings'}
