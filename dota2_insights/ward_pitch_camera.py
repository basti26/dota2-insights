"""Offline conditional camera from three viewport edges and an explicit pitch prior.

Partial upper endpoints describe side lines, never full viewport top corners.
This is a flat-plane hypothesis, not a runtime camera or terrain calibration.
"""
import math

import numpy as np


def fit(world_quad, size, *, pitch_degrees):
    result = dict(version='three_edge_pitch_prior_v1', status='unavailable',
                  reason='invalid_input', matrix=None,
                  coordinate_accuracy_verified=False, camera_model_verified=False,
                  pitch_in_target_frame_verified=False, absolute_camera_z=None,
                  assumptions=['flat_ground_plane', 'zero_roll', 'square_pixels',
                               'centered_principal_point', 'full_native_viewport',
                               'pitch_prior_transfers_to_target_frame'])
    try:
        quad = np.asarray(world_quad, dtype=float)
        width, height = size
        if (quad.shape != (4, 2) or not np.isfinite(quad).all()
                or type(width) is not int or type(height) is not int
                or not 640 <= width <= 4096 or not 480 <= height <= 2160
                or isinstance(pitch_degrees, (bool, str))):
            return result
        pitch = float(pitch_degrees)
        if not math.isfinite(pitch) or not 30 <= pitch <= 80:
            return result
        origin = (quad[2] + quad[3]) / 2
        baseline = quad[2] - quad[3]
        breadth = np.linalg.norm(baseline)
        if breadth < 1e-6:
            return result
        right = baseline / breadth
        forward = np.array([-right[1], right[0]])
        if forward @ ((quad[0] + quad[1]) / 2 - origin) < 0:
            forward = -forward
        transform = np.array([[*right, -right @ origin],
                              [*forward, -forward @ origin], [0, 0, 1]])
        local = (quad - origin) @ np.stack([right, forward], axis=1)
        if min(local[0, 1], local[1, 1]) < 1e-6:
            return result
        left = (local[0, 0] - local[3, 0]) / local[0, 1]
        right_slope = (local[1, 0] - local[2, 0]) / local[1, 1]
        if not left < 0 < right_slope:
            result['reason'] = 'side_lines_not_divergent'
            return result
        delta = right_slope - left
        sine, cosine = math.sin(math.radians(pitch)), math.cos(math.radians(pitch))
        focal = (width - 1) * cosine / delta
        if not .2 * max(size) <= focal <= 8 * max(size):
            result['reason'] = 'focal_outside_provisional_limits'
            return result
        cx, cy = (width - 1) / 2, (height - 1) / 2
        vanishing_y = -breadth / delta
        z = -vanishing_y * cosine * (sine + cy / focal * cosine)
        camera_y = vanishing_y + math.tan(math.radians(pitch)) * z
        depth = -cosine * camera_y + sine * z
        if not 1 < z < 100000 or depth <= 1e-6:
            return result
        h = np.array([[focal, cx * cosine, cx * depth],
                      [0, -focal * sine + cy * cosine,
                       focal * (sine * camera_y + cosine * z) + cy * depth],
                      [0, cosine, depth]]) @ transform
        projected = (h @ np.column_stack([quad, np.ones(4)]).T).T
        if not np.isfinite(projected).all() or np.any(projected[:, 2] <= 1e-6):
            return result
        projected = projected[:, :2] / projected[:, 2:]
        residual = float(max(abs(projected[0, 0]), abs(projected[1, 0] - width + 1)))
        result.update(pitch_prior_degrees=pitch, focal_candidate_px=focal,
                      relative_plane_camera_height=float(z),
                      side_edge_max_residual_px=residual,
                      side_edge_limit_px=.01 * max(size))
        if residual > .01 * max(size) or np.any(projected[:2, 1] < -1) or np.any(projected[:2, 1] > height):
            result['reason'] = 'observed_sides_inconsistent_with_assumptions'
            return result
        h /= np.linalg.norm(h)
        result.update(status='conditional_candidate', reason='unverified_pitch_plane_hypothesis',
                      matrix=h.tolist())
        return result
    except (ValueError, TypeError, OverflowError, np.linalg.LinAlgError, FloatingPointError):
        return result


def measured_world_quad(terrain):
    """Reconstruct only measured bottom/side intersections; never guessed top extent."""
    evidence = terrain['camera_outline_evidence']
    if evidence['observed_edge_count'] != 3:
        raise ValueError('three_measured_edges_required')
    segments = np.asarray(evidence['measured_lower_lines'], float)
    if segments.shape != (3, 2, 2) or not np.isfinite(segments).all():
        raise ValueError('invalid_measured_lines')
    lines = [np.cross([*a, 1], [*b, 1]) for a, b in segments]
    corners = [np.cross(lines[0], lines[i]) for i in (2, 1)]
    if any(abs(p[2]) < 1e-9 for p in corners):
        raise ValueError('parallel_measured_lines')
    normalized = np.array([segments[1, 0], segments[2, 0],
                           corners[0][:2] / corners[0][2], corners[1][:2] / corners[1][2]])
    x, y, w, h = terrain['minimap_roi']
    pixels = normalized * [w - 1, h - 1] + [x, y]
    world = np.linalg.solve(np.asarray(terrain['raw_to_minimap_screen'], float),
                            np.column_stack([pixels, np.ones(4)]).T).T
    if not np.isfinite(world).all() or np.any(abs(world[:, 2]) < 1e-9):
        raise ValueError('invalid_world_transform')
    return world[:, :2] / world[:, 2:]
