import unittest
from dota2_insights.ward_proximity import analyze


def packet(xs, *, match='1', team=2, kind='observer', times=None, ends=None, map_version=None):
    times = times or list(range(len(xs)))
    ends = ends or [t+10 for t in times]
    points = [{'id': f'{match}:{i}', 'x': x, 'y': 0, 'team': team,
               'ward_type': kind, 'placed_game_time_s': times[i]} for i, x in enumerate(xs)]
    wards = [{'end_kind': 'killed' if ends[i] is not None else 'unknown',
              'ended_game_time_s': ends[i], 'duration_s': None} for i in range(len(xs))]
    return ({'match_id': match, 'points': points, 'map_version': map_version}, wards)


class ProximityTests(unittest.TestCase):
    def test_overlap_and_replacement(self):
        g = analyze([packet([0, 20, 25], times=[0, 5, 30], ends=[10, 20, 40])], (128,))['radii'][0]
        self.assertEqual(g['same_team_positive_overlap_pairs'], 1)
        self.assertEqual(g['pairs'][0]['observed_overlap_s'], 5)
        self.assertEqual(g['pairs'][1]['later_minus_first_end_s'], 20)

    def test_cross_match_and_opponents_not_overlap(self):
        d = analyze([packet([0], map_version='m1'), packet([1], match='2', map_version='m1'),
                     packet([2], team=3, match='3', map_version='m1')], (128,))
        self.assertEqual(d['radii'][0]['pair_counts'], {'cross_match': 3})
        self.assertFalse(d['map_compatibility_verified'])
        self.assertTrue(all(p['observed_overlap_s'] is None for p in d['radii'][0]['pairs']))
        a = packet([0, 1]); a[0]['points'][1]['team'] = 3
        self.assertEqual(analyze([a], (128,))['radii'][0]['pair_counts'], {'opponents': 1})

    def test_unknown_end_remains_unknown(self):
        d = analyze([packet([0, 1], ends=[None, 10])], (128,))
        self.assertIsNone(d['radii'][0]['pairs'][0]['observed_overlap_s'])

    def test_same_type_only_and_unique_matches(self):
        d = analyze([packet([0, 1, 2], map_version='m1'),
                     packet([0], kind='sentry', match='2', map_version='m1')], (128,))
        self.assertEqual(len(d['radii'][0]['pairs']), 3)
        self.assertEqual(d['radii'][0]['cross_match_pair_count'], 0)
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

    def test_identical_exports_removed_but_same_coordinate_placements_retained(self):
        export = packet([0, 0], times=[5, 20])
        export[0].update(source_replay_sha256='a'*64, source_wards_sha256='b'*64)
        d = analyze([export, export], (128,))
        self.assertEqual(d['duplicate_exports_omitted'], 1)
        self.assertEqual(d['ward_count'], 2)
        self.assertEqual(d['radii'][0]['pairs'][0]['distance'], 0)
        self.assertEqual(d['radii'][0]['hotspots'][0]['placement_count'], 2)
        conflict = ({**export[0], 'source_wards_sha256': 'c'*64}, export[1])
        with self.assertRaisesRegex(ValueError, 'conflicting_or_unverified_export'):
            analyze([export, conflict])

    def test_unknown_or_different_map_does_not_produce_cross_match_pairs_or_hotspots(self):
        d = analyze([packet([0, 0], map_version='m1'), packet([0, 0], match='2', map_version='m2'),
                     packet([0, 0], match='3'), packet([0, 0], match='4', map_version='unknown')], (128,))
        self.assertEqual(d['match_map_versions'], {'1': 'm1', '2': 'm2', '3': None, '4': None})
        self.assertEqual(d['cross_match_inputs'], {'same_declared_version': 0, 'different_version': 1,
                                                  'unknown_version': 5})
        radius = d['radii'][0]
        self.assertIsNone(radius['cross_match_pair_count'])
        self.assertEqual(radius['pair_counts'], {'same_team': 4})
        self.assertEqual([h['match_count'] for h in radius['hotspots']], [1, 1, 1, 1])
