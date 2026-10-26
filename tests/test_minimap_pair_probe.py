"""Adjacent synthetic components test links without assigning game identity."""
import unittest
import cv2
import numpy as np

from dota2_insights.minimap_pair_probe import compare


class PairProbeTests(unittest.TestCase):
    def image(self, positions=(100,)):
        image = np.zeros((372, 372, 3), np.uint8)
        for x in positions:
            cv2.circle(image, (x, 120), 5, (0, 0, 255), -1)
        return image

    def metadata(self, sequence=1, second='01'):
        return {'sequence': sequence, 'sample_ordinal': sequence, 'captured_at_utc': f'2026-10-26T00:00:{second}Z',
                'client_size': [1920, 1080], 'roi': [0, 700, 372, 372], 'layout_segment': 1, 'time_basis': 'fixture'}

    def test_same_component_can_be_stationary_without_being_called_building(self):
        result = compare(self.image(), self.image(), self.metadata(), self.metadata(2, '02'))
        self.assertEqual(result['state'], 'compared')
        self.assertEqual(result['small_displacement_links'], 1)
        self.assertEqual(result['links'][0]['hero_or_building'], 'unresolved')
        self.assertFalse(result['hero_disappearance_inferred'])

    def test_moving_component_links_but_is_not_a_hero(self):
        result = compare(self.image(), self.image((106,)), self.metadata(), self.metadata(2, '02'))
        self.assertEqual(result['larger_displacement_links'], 1)
        self.assertEqual(result['links'][0]['distance_px'], 6)
        self.assertIsNone(result['hero_count'])

    def test_ambiguous_many_to_one_is_not_arbitrarily_linked(self):
        result = compare(self.image((90, 110)), self.image((100,)), self.metadata(), self.metadata(2, '02'))
        self.assertEqual(result['links'], [])
        self.assertEqual(result['previous_without_unique_link'], 2)
        self.assertFalse(result['hero_disappearance_inferred'])

    def test_gap_repeated_time_and_clock_regression_are_not_compared(self):
        for time in ('01', '00', '04'):
            result = compare(self.image(), self.image(), self.metadata(), self.metadata(2, time))
            self.assertEqual(result['reason'], 'capture_clock_gap_or_regression')
            self.assertEqual(result['links'], [])

    def test_nonadjacent_or_layout_change_never_bridges(self):
        for change in ({'sequence': 3}, {'sample_ordinal': 3}, {'layout_segment': 2}, {'time_basis': None}):
            result = compare(self.image(), self.image(), self.metadata(), {**self.metadata(2, '02'), **change})
            self.assertEqual(result['state'], 'unavailable')

    def test_unlinked_candidate_is_not_reported_as_hero_disappearance(self):
        result = compare(self.image(), self.image(()), self.metadata(), self.metadata(2, '02'))
        self.assertEqual(result['previous_without_unique_link'], 1)
        self.assertFalse(result['hero_disappearance_inferred'])
        self.assertFalse(result['online_warning_enabled'])

    def test_dimension_change_and_missing_clock_stay_unavailable(self):
        result = compare(self.image(), np.zeros((240, 240, 3), np.uint8), self.metadata(), self.metadata(2, '02'))
        self.assertEqual(result['reason'], 'image_dimensions_changed')
        result = compare(self.image(), self.image(), self.metadata(), {**self.metadata(2, '02'), 'captured_at_utc': None})
        self.assertEqual(result['reason'], 'capture_clock_unknown')

    def test_same_wrong_roi_and_candidate_cap_do_not_produce_links(self):
        old, new = self.metadata(), self.metadata(2, '02')
        old['roi'] = new['roi'] = [0, 700, 371, 372]
        result = compare(self.image(), self.image(), old, new)
        self.assertEqual(result['reason'], 'layout_or_image_geometry_disagrees')
        image = self.image(())
        for y in range(20, 340, 35):
            for x in range(20, 340, 35):
                cv2.circle(image, (x, y), 5, (0, 0, 255), -1)
        result = compare(image, image, self.metadata(), self.metadata(2, '02'))
        self.assertEqual(result['reason'], 'candidate_limit_prevents_pair_comparison')
