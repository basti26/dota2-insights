"""Ground-plane height checks are independent of the tile pixel fit."""

import unittest

import numpy as np

from dota2_insights.ward_ground_tile import fit, project_footprint


class SlopedGroundTileTests(unittest.TestCase):
    def test_sloped_controls_admit_their_surface_and_reject_a_different_height(self):
        height = lambda x,y: 128+.18*x-.12*y
        coords = ((-350,-300),(350,-300),(350,300),(-350,300),(-180,120),(190,-110))
        controls = [{'world_xyz':[x,y,height(x,y)],'tile_xy':[175+x*.28,225+y*.26],
                     'role':'fit' if i<4 else 'check'} for i,(x,y) in enumerate(coords)]
        self.assertEqual(fit(controls,128)['status'],'controls_off_ground_plane')
        candidate = fit(controls)
        self.assertEqual(candidate['status'],'candidate_ground_plane')
        self.assertFalse(candidate['live_usable'])
        np.testing.assert_allclose(candidate['height_coefficients'],[.18,-.12,128],atol=1e-6)
        vertices = [[x,y,height(x,y)] for x,y in ((-36,-36),(36,-36),(36,36),(-36,36),(0,0))]
        result = project_footprint(candidate,vertices)
        np.testing.assert_allclose(result['center_xy'],[175,225],atol=1e-4)
        vertices[0][2] += 12
        self.assertIsNone(project_footprint(candidate,vertices))
        controls[-1]['world_xyz'][2] += 12
        self.assertEqual(fit(controls)['status'],'controls_off_ground_plane')
