"""Synthetic composited minimap pixels with spatial controls."""
import unittest

import numpy as np

from dota2_insights.ward_minimap_pixels import OPACITY, PALETTE, compare


class WardMinimapPixelTests(unittest.TestCase):
    def setUp(self):
        self.hidden = np.full((480, 640, 3), (38, 52, 70), dtype=np.uint8)
        self.points = [(str(i), x, y, '#ff4141' if i % 2 else '#39eb70', False)
                       for i, (x, y) in enumerate((x,y) for x in (40,70,100,130) for y in (280,310,340,370))]

    def visible(self, shift=0):
        result = self.hidden.copy()
        for _, x,y,color,_ in self.points:
            block = result[y-1:y+2,x+shift-1:x+shift+2]
            block[:] = np.round(block*(1-OPACITY)+np.asarray(PALETTE[color])*OPACITY)
        return result

    def test_matching_composite_covers_markers_but_not_shifted_controls(self):
        result = compare(self.visible(), self.hidden, self.points)
        self.assertEqual(result['status'],'distributed_marker_color_pattern_observed')
        self.assertEqual(result['markers_matched'],16)
        self.assertEqual(result['controls_matched'],0)
        self.assertFalse(result['ward_alignment_verified'])

    def test_hidden_or_shifted_contrast_cannot_masquerade_as_marker_pixels(self):
        for block in (self.hidden, self.visible(11)):
            result = compare(block, self.hidden, self.points)
            self.assertEqual(result['status'],'marker_color_pattern_unconfirmed')
            self.assertEqual(result['markers_matched'],0)
