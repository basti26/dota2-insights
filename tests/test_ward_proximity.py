import unittest
from dota2_insights.ward_proximity import analyze


def packet(xs, *, match='1', team=2, kind='observer', times=None, ends=None):
    times = times or list(range(len(xs)))
    ends = ends or [t+10 for t in times]
    points = [{'id': f'{match}:{i}', 'x': x, 'y': 0, 'team': team,
               'ward_type': kind, 'placed_game_time_s': times[i]} for i, x in enumerate(xs)]
    wards = [{'end_kind': 'killed' if ends[i] is not None else 'unknown',
              'ended_game_time_s': ends[i], 'duration_s': None} for i in range(len(xs))]
    return ({'match_id': match, 'points': points}, wards)


class ProximityTests(unittest.TestCase):
    def test_overlap_and_replacement(self):
        g = analyze([packet([0, 20, 25], times=[0, 5, 30], ends=[10, 20, 40])], (128,))['radii'][0]
        self.assertEqual(g['same_team_positive_overlap_pairs'], 1)
        self.assertEqual(g['pairs'][0]['observed_overlap_s'], 5)
        self.assertEqual(g['pairs'][1]['later_minus_first_end_s'], 20)

    def test_cross_match_and_opponents_not_overlap(self):
        d = analyze([packet([0]), packet([1], match='2'), packet([2], team=3, match='3')], (128,))
        self.assertEqual(d['radii'][0]['pair_counts'], {'cross_match': 3})
        self.assertTrue(all(p['observed_overlap_s'] is None for p in d['radii'][0]['pairs']))
        a = packet([0, 1]); a[0]['points'][1]['team'] = 3
        self.assertEqual(analyze([a], (128,))['radii'][0]['pair_counts'], {'opponents': 1})

    def test_unknown_end_remains_unknown(self):
        d = analyze([packet([0, 1], ends=[None, 10])], (128,))
        self.assertIsNone(d['radii'][0]['pairs'][0]['observed_overlap_s'])

    def test_same_type_only_and_unique_matches(self):
        d = analyze([packet([0, 1, 2]), packet([0], kind='sentry', match='2')], (128,))
        self.assertEqual(len(d['radii'][0]['pairs']), 3)
        self.assertEqual(d['radii'][0]['hotspots'][0]['match_count'], 1)

    def test_radius_neighborhood_not_transitive(self):
        d = analyze([packet([0, 100, 200, 300, 400])], (100,))
        self.assertTrue(all(h['placement_count'] <= 3 for h in d['radii'][0]['hotspots']))
        self.assertEqual(len(d['radii'][0]['pairs']), 4)

    def test_duplicates_and_invalid_radius(self):
        with self.assertRaises(ValueError):
            analyze([packet([0]), packet([0])])
        for radius in (0, -1, float('nan')):
            with self.assertRaises(ValueError):
                analyze([], (radius,))
