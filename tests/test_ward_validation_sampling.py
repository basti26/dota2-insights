import unittest
from dota2_insights.ward_validation_sampling import prepare_samples


class WardValidationSamplingTests(unittest.TestCase):
    def records(self):
        return [{'team': 2 + i % 2, 'ward_type': 'observer' if i % 3 else 'sentry',
                 'placed_game_time_s': i * 65, 'duration_s': 20 if i % 4 else None,
                 'x': 100, 'y': 200, 'end_kind': 'unknown' if i % 5 else 'expired'} for i in range(40)]

    def test_repeatable_separate_and_blank(self):
        value = prepare_samples(self.records(), 'a' * 64)
        self.assertEqual(value, prepare_samples(self.records(), 'a' * 64))
        self.assertEqual((value['random_selected'], value['special_selected']), (12, 6))
        self.assertEqual(len({r['record_index'] for r in value['blind']}), 18)
        for row in value['blind']:
            self.assertIsNone(row['observed_team'])
            self.assertIsNone(row['world_x'])
            self.assertNotIn('record', row)
            self.assertNotIn('special_tags', row)
            self.assertEqual(row['status'], 'pending')
        self.assertFalse(value['independent_validation_passed'])

    def test_unknown_values_and_shortage_not_filled(self):
        value = prepare_samples([{}], 'b' * 64)
        self.assertEqual(value['random_selected'], 1)
        self.assertEqual(value['special_selected'], 0)
        self.assertIsNone(value['blind'][0]['navigation_game_s'])
        self.assertEqual(sum(s['available'] for s in value['strata']), 1)

    def test_invalid_identity_and_count_rejected(self):
        for sha, count in [('x' * 64, 12), ('a' * 64, True), ('a' * 64, 0)]:
            with self.assertRaises(ValueError):
                prepare_samples([], sha, count)

    def test_postgame_placement_is_not_match_end_censoring(self):
        records = self.records()
        for row in records:
            row['after_match_end'] = True
        value = prepare_samples(records, 'c' * 64)
        special = [a for a in value['answers'] if a['group'] == 'special']
        self.assertTrue(special)
        for row in special:
            self.assertIn('postgame_placement', row['special_tags'])
            self.assertNotIn('match_end_censored', row['special_tags'])
            self.assertNotIn('end_crosses_match_end', row['special_tags'])

    def test_extractor_unobserved_ends_are_unknown_and_crossing_needs_end_clock(self):
        records = self.records()
        for row in records:
            row.update(end_kind='unobserved', placed_game_time_s=10, ended_game_time_s=200)
        value = prepare_samples(records, 'd' * 64, match_end_game_s=100)
        for row in value['answers']:
            if row['group'] == 'special':
                self.assertIn('unknown_end', row['special_tags'])
                self.assertIn('end_crosses_match_end', row['special_tags'])

    def test_negative_pregame_navigation_is_preserved(self):
        value = prepare_samples([{'placed_game_time_s': -60}], 'e' * 64)
        self.assertEqual(value['blind'][0]['navigation_game_s'], -65)
        self.assertIsNone(value['blind'][0]['placed_game_s'])
