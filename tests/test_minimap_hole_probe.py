"""Synthetic topology tests include a glyph false positive intentionally."""
import unittest
import cv2
import numpy as np

from dota2_insights.minimap_hole_probe import describe


class HoleProbeTests(unittest.TestCase):
    def image(self, size=372):
        return np.zeros((size, size, 3), np.uint8)

    def test_hollow_ring_has_hole_but_no_hero_identity(self):
        image = self.image()
        cv2.ellipse(image, (100, 100), (7, 5), 0, 0, 360, (0, 255, 0), 2)
        result = describe(image)
        self.assertEqual(len(result['features']), 1)
        feature = result['features'][0]
        self.assertGreater(feature['hole_pixel_fraction'], 0)
        self.assertIsNone(feature['hero_id'])
        self.assertEqual(feature['hero_or_glyph_or_other'], 'unresolved')
        self.assertFalse(result['shape_semantics_verified'])

    def test_filled_creep_sized_dot_and_square_have_no_enclosed_hole(self):
        image = self.image()
        cv2.circle(image, (50, 50), 3, (0, 255, 0), -1)
        cv2.rectangle(image, (90, 90), (100, 100), (0, 0, 255), -1)
        self.assertEqual(describe(image)['features'], [])

    def test_closed_glyph_can_qualify_and_must_stay_unresolved(self):
        image = self.image()
        for x in (80, 92, 104):
            cv2.rectangle(image, (x, 100), (x + 8, 110), (0, 255, 0), 1)
        result = describe(image)
        self.assertTrue(result['features'])
        self.assertTrue(any(feature['text_band_overlaps'] for feature in result['features']))
        self.assertTrue(all(feature['hero_or_glyph_or_other'] == 'unresolved' for feature in result['features']))
        self.assertEqual(result['features'][0]['enclosed_pixels'], 99)
        self.assertEqual(result['features'][0]['hole_pixels'], 63)

    def test_scaled_ring_is_detected_without_resizing_source(self):
        for size in (240, 372, 744):
            image = self.image(size)
            scale = size / 372
            cv2.ellipse(image, (size // 2, size // 2), (round(7 * scale), round(5 * scale)),
                        0, 0, 360, (0, 255, 0), max(1, round(2 * scale)))
            self.assertGreater(len(describe(image)['features']), 0)

    def test_unflagged_isolated_glyph_does_not_become_a_confirmed_hero(self):
        image = self.image()
        cv2.rectangle(image, (80, 100), (88, 110), (0, 255, 0), 1)
        result = describe(image)
        self.assertEqual(len(result['features']), 1)
        self.assertEqual(result['features'][0]['text_band_overlaps'], [])
        self.assertEqual(result['features'][0]['hero_or_glyph_or_other'], 'unresolved')
        self.assertIsNone(result['hero_count'])

    def test_original_pixels_unchanged_and_empty_image_does_not_mean_zero_heroes(self):
        image = self.image()
        original = image.copy()
        result = describe(image)
        np.testing.assert_array_equal(image, original)
        self.assertEqual(result['features'], [])
        self.assertIsNone(result['hero_count'])
        self.assertFalse(result['online_changes_applied'])


class HoleSelectionTests(unittest.TestCase):
    def test_different_length_groups_do_not_share_a_late_bound_queue(self):
        from tools.probe_minimap_holes import select_features
        features = [{'frame_index': i, 'feature': {'display_color': color, 'text_band_overlaps': [],
                     'normalized_width': 10, 'normalized_height': 8}}
                    for color, size in (('blue', 5), ('green', 3), ('red', 1)) for i in range(size)]
        selected = select_features(features, 8)
        self.assertEqual(len(selected), 8)
        self.assertEqual({r['feature']['display_color'] for r in selected[:3]}, {'blue', 'green', 'red'})
        self.assertEqual(len({id(item) for item in selected}), 8)
        self.assertTrue(all(item in features for item in selected))
        self.assertEqual(selected, select_features(features, 8))

    def test_empty_selection_and_invalid_limit(self):
        from tools.probe_minimap_holes import select_features
        self.assertEqual(select_features([], 8), [])
        for limit in (0, True, 97):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                select_features([], limit)
