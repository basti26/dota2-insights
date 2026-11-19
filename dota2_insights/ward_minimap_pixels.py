"""Compare saved desktop pixels with the recorded minimap marker positions."""
import math

import numpy as np
from PIL import Image


PALETTE = {'#ff4141': (143, 185, 214), '#39eb70': (214, 194, 121)}
OPACITY = .58
CONTROL_OFFSETS = ((-11, 0), (11, 0), (0, -11), (0, 11))


def read_rgb(path, viewport):
    with Image.open(path) as source:
        if source.size != viewport or source.format not in ('PNG', 'JPEG'):
            raise ValueError('ward_minimap_pixel_image_geometry')
        image = np.asarray(source.convert('RGB'))
    return image


def _supports(visible, hidden, x, y, rgb, radius):
    draw = visible[y-radius:y+radius+1, x-radius:x+radius+1].astype(float)
    base = hidden[y-radius:y+radius+1, x-radius:x+radius+1].astype(float)
    target = base*(1-OPACITY)+np.asarray(rgb)*OPACITY
    observed = np.linalg.norm(draw-base, axis=2)
    error = np.linalg.norm(draw-target, axis=2)
    expected = np.linalg.norm(target-base, axis=2)
    pixels = (expected >= 28) & (error <= 32) & (observed-error >= 12)
    return np.count_nonzero(pixels) >= 3


def compare(visible, hidden, points):
    """Seek a distributed match and measure the same test at shifted controls."""
    if visible.shape != hidden.shape:
        raise ValueError('ward_minimap_pixel_image_size_mismatch')
    height, width, _ = visible.shape
    radius = max(3, math.ceil(height/1080*3))
    centers = np.array([(round(point[1]), round(point[2])) for point in points], dtype=int)
    if len(centers) == 0:
        return {'status':'insufficient_isolated_markers','markers_tested':0}
    tested = matched = controls = control_matches = 0
    spread = set()
    xmin, ymin = centers.min(axis=0)
    xmax, ymax = centers.max(axis=0)
    for index, ((x, y), point) in enumerate(zip(centers, points)):
        if not radius+12 <= x < width-radius-12 or not radius+12 <= y < height-radius-12:
            continue
        distance = np.linalg.norm(centers-(x,y), axis=1)
        distance[index] = float('inf')
        if distance.min() <= 2*radius:
            continue
        tested += 1
        rgb = PALETTE[point[3]]
        if _supports(visible, hidden, x, y, rgb, radius):
            matched += 1
            spread.add((x >= (xmin+xmax)/2,y >= (ymin+ymax)/2))
        for dx,dy in CONTROL_OFFSETS:
            if np.linalg.norm(centers-(x+dx,y+dy),axis=1).min() <= 2*radius:
                continue
            controls += 1
            control_matches += int(_supports(visible, hidden, x+dx, y+dy, rgb, radius))
    if tested < 8 or controls < 16:
        status = 'insufficient_isolated_markers'
    elif matched/tested >= .6 and control_matches/controls <= .25 and len(spread) >= 3:
        status = 'distributed_marker_color_pattern_observed'
    else:
        status = 'marker_color_pattern_unconfirmed'
    return {'status':status,'markers_tested':tested,'markers_matched':matched,
            'controls_tested':controls,'controls_matched':control_matches,
            'matched_quadrants':len(spread),'ward_alignment_verified':False}
