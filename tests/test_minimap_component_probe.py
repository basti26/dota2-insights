"""Synthetic shapes verify exploratory flags, not gameplay detection quality."""
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from dota2_insights.minimap_component_probe import probe


class ComponentProbeTests(unittest.TestCase):
    def image(self):
        return np.zeros((372, 372, 3), np.uint8)

    def test_circle_is_retained_with_unknown_identity_and_no_suppression(self):
        image = self.image()
        cv2.circle(image, (120, 120), 7, (0, 0, 255), -1)
        result = probe(image)
        self.assertEqual(len(result['candidates']), 1)
        candidate = result['candidates'][0]
        self.assertIsNone(candidate['hero_id'])
        self.assertEqual(candidate['team_relation'], 'unknown')
        self.assertFalse(candidate['suppressed'])
        self.assertIsNone(result['hero_count'])
        self.assertFalse(result['online_changes_applied'])

    def test_horizontal_glyph_like_marks_are_bands_but_not_classified_text(self):
        image = self.image()
        for x in range(50, 90, 5):
            cv2.rectangle(image, (x, 80), (x + 2, 88), (0, 255, 0), -1)
        result = probe(image)
        self.assertGreater(len(result['text_bands']), 0)
        self.assertFalse(result['text_classification_verified'])

    def test_solid_square_overlap_does_not_delete_candidate_or_call_it_building(self):
        image = self.image()
        cv2.rectangle(image, (100, 100), (110, 110), (0, 0, 255), -1)
        result = probe(image)
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates_with_square_overlap'], 1)
        self.assertFalse(result['building_classification_verified'])
        self.assertFalse(result['candidates'][0]['suppressed'])

    def test_circle_next_to_glyphs_remains_even_if_a_band_overlaps(self):
        image = self.image()
        cv2.circle(image, (120, 120), 6, (0, 255, 0), -1)
        for x in range(123, 160, 5):
            cv2.rectangle(image, (x, 116), (x + 2, 124), (0, 255, 0), -1)
        result = probe(image)
        self.assertTrue(result['text_bands'])
        self.assertTrue(all(not row['suppressed'] for row in result['candidates']))

    def test_building_probe_ambiguity_does_not_turn_into_zero_buildings_proof(self):
        with patch('dota2_insights.minimap_component_probe.square_candidates', side_effect=ValueError('fixture')):
            result = probe(self.image())
        self.assertEqual(result['square_status'], 'unsupported_or_ambiguous')
        self.assertFalse(result['building_classification_verified'])

    def test_dense_components_report_cap_and_do_not_claim_48_heroes(self):
        image = self.image()
        for y in range(20, 340, 35):
            for x in range(20, 340, 35):
                cv2.circle(image, (x, y), 5, (0, 0, 255), -1)
        result = probe(image)
        self.assertEqual(len(result['candidates']), 48)
        self.assertGreater(result['candidate_count_before_limit'], 48)
        self.assertTrue(result['candidate_limit_reached'])
        self.assertIsNone(result['hero_count'])

    def test_source_pixels_unchanged_and_parameters_deterministic(self):
        image = self.image()
        before = image.copy()
        first, second = probe(image), probe(image)
        np.testing.assert_array_equal(image, before)
        self.assertEqual(first, second)

    def test_unsupported_dtype_shape_and_dimensions_refused(self):
        for image in (np.zeros((10, 10, 3), np.uint8), np.zeros((372, 372), np.uint8),
                      np.zeros((372, 372, 3), np.float32)):
            with self.subTest(shape=image.shape), self.assertRaises(ValueError):
                probe(image)
