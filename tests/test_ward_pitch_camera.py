import math
import unittest

import numpy as np

from dota2_insights.ward_pitch_camera import fit, measured_world_quad


def scene(size, pitch, yaw):
    a, b = map(math.radians, (pitch, yaw))
    ground = np.array([math.cos(b), math.sin(b), 0])
    right = np.array([math.sin(b), -math.cos(b), 0])
    forward = ground * math.cos(a) + [0, 0, -math.sin(a)]
    down = -ground * math.sin(a) + [0, 0, -math.cos(a)]
    camera = np.array([13000, 14000, 1500])
    center = (np.array(size) - 1) / 2
    focal = max(size) * .9

    def ground_at(pixel):
        uv = (np.asarray(pixel) - center) / focal
        ray = forward + uv[0] * right + uv[1] * down
        return (camera - camera[2] / ray[2] * ray)[:2]

    def project(points):
        offsets = np.column_stack([points, np.zeros(len(points))]) - camera
        return np.column_stack([offsets @ right, offsets @ down]) / (offsets @ forward)[:, None] * focal + center

    w, h = np.array(size) - 1
    quad = np.array([ground_at(p) for p in [(0, 0), (w, 0), (w, h), (0, h)]])
    return quad, project


class PitchCameraTests(unittest.TestCase):
    def test_unseen_points_and_partial_side_invariance(self):
        for size in ((1920, 1080), (2560, 1440)):
            for pitch in (45, 60, 75):
                for yaw in (0, 45, 90, 147):
                    full, project = scene(size, pitch, yaw)
                    points = np.array([full.mean(axis=0), .3 * full[0] + .7 * full[2]])
                    for fraction in (.4, .6):
                        quad = full.copy()
                        quad[0] = fraction * full[0] + (1 - fraction) * full[3]
                        quad[1] = fraction * full[1] + (1 - fraction) * full[2]
                        result = fit(quad, size, pitch_degrees=pitch)
                        self.assertEqual(result['status'], 'conditional_candidate', result)
                        h = np.array(result['matrix'])
                        p = (h @ np.column_stack([points, np.ones(len(points))]).T).T
                        np.testing.assert_allclose(p[:, :2] / p[:, 2:], project(points), atol=1e-7)
                        self.assertFalse(result['coordinate_accuracy_verified'])
                        self.assertIsNone(result['absolute_camera_z'])

    def test_wrong_pitch_can_pass_edges_and_fail_unseen_ground(self):
        quad, project = scene((2560, 1440), 60, 90)
        result = fit(quad, (2560, 1440), pitch_degrees=45)
        self.assertEqual(result['status'], 'conditional_candidate')
        points = np.array([quad.mean(axis=0)])
        p = np.array(result['matrix']) @ [*points[0], 1]
        self.assertGreater(np.linalg.norm(p[:2] / p[2] - project(points)[0]), 5)
        self.assertFalse(result['pitch_in_target_frame_verified'])

    def test_invalid_inputs(self):
        quad, _ = scene((2560, 1440), 60, 90)
        for pitch in (True, '60', None, float('nan'), 0, 90):
            self.assertEqual(fit(quad, (2560, 1440), pitch_degrees=pitch)['status'], 'unavailable')
        for bad in (np.zeros((4, 2)), [[1, 2]], quad * float('nan')):
            self.assertEqual(fit(bad, (2560, 1440), pitch_degrees=60)['status'], 'unavailable')

    def test_asymmetric_sides_rejected(self):
        quad, _ = scene((2560, 1440), 60, 90)
        quad[0] += (quad[1] - quad[0]) * .25
        result = fit(quad, (2560, 1440), pitch_degrees=60)
        self.assertEqual(result['status'], 'unavailable')
        self.assertIn(result['reason'], ('side_lines_not_divergent',
                                        'observed_sides_inconsistent_with_assumptions'))

    def test_adapter_ignores_guessed_quad(self):
        terrain = dict(camera_outline_evidence=dict(observed_edge_count=3,
            measured_lower_lines=[[[0, 1], [1, 1]], [[-.5, 0], [0, 1]], [[1.5, 0], [1, 1]]]),
            minimap_roi=[10, 20, 101, 101], raw_to_minimap_screen=np.eye(3).tolist(), camera_quad='unused')
        np.testing.assert_allclose(measured_world_quad(terrain), [[-40, 20], [160, 20], [110, 120], [10, 120]])
        terrain['camera_outline_evidence']['observed_edge_count'] = 2
        with self.assertRaises(ValueError):
            measured_world_quad(terrain)
