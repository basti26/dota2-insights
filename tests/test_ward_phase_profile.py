import hashlib
import json
import unittest

from dota2_insights.ward_phase_profile import analyze, neighborhoods
from dota2_insights.ward_map_reference import project


def source(mid='9876543210', duration=1200, entries=None, **meta):
    entries = entries if entries is not None else [{'placed_game_time_s': 60}]
    wards = [{'match_id': mid, 'x': 15000+i, 'y': 16000, 'ward_type': 'observer',
              'team': 2, 'placed_game_time_s': 60, 'after_match_end': False,
              'coordinate_basis': 'gem_cell128_plus_vec_raw_coordinates', 'end_kind': 'expired', **e}
             for i, e in enumerate(entries)]
    raw = json.dumps(wards).encode()
    manifest = {'match_id': mid, 'parsed_match_id': mid, 'complete': True,
                'independent_footer_matches_parser': True, 'parse_error': None,
                'source_unchanged_stat': True, 'stop_reason': 'replay_eof', 'ward_count': len(wards),
                'match_end_game_time_s': duration, 'parser_version': '0.13.0', 'parser_commit': 'a'*40,
                'parser_sha256': 'b'*64, 'ward_extractor_sha256': 'c'*64, 'adapter_sha256': 'd'*64,
                'source_sha256': 'e'*64,
                'files': {'wards.json': {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}}, **meta}
    return manifest, raw


def stratum(d, phase=0, team=2, kind='observer'):
    return next(s for s in d['strata'] if s['phase'] == phase and s['team'] == team and s['ward_type'] == kind)


class WardPhaseProfileTests(unittest.TestCase):
    def test_partial_success_excluded_from_both_numerator_and_denominator(self):
        d = analyze([source(entries=[{'placed_game_time_s': 700}, {'placed_game_time_s': 710}]),
                     source('9876543211', 900, [{'placed_game_time_s': 720}])])
        s = stratum(d, 1)
        self.assertEqual(s['full_match_ids'], ['9876543210'])
        self.assertEqual(s['partial_match_ids_reference_only'], ['9876543211'])
        self.assertEqual(s['placements_full'], 2)
        self.assertEqual(s['placements_partial_reference_only'], 1)
        h = s['hotspots'][0]
        self.assertEqual((h['support_matches'], h['full_matches'], h['placements']), (1, 1, 2))
        self.assertEqual(h['support_match_ids'], ['9876543210'])

    def test_matches_without_wards_still_in_full_denominator(self):
        d = analyze([source(entries=[{}, {}]), source('9876543211', entries=[])])
        h = stratum(d)['hotspots'][0]
        self.assertEqual(h['match_fraction'], .5)
        self.assertEqual(h['full_matches'], 2)

    def test_no_full_interval_keeps_partial_but_has_no_hotspot_rate(self):
        d = analyze([source(duration=300, entries=[{}, {}])])
        s = stratum(d)
        self.assertEqual(s['full_match_ids'], [])
        self.assertEqual(s['hotspots'], [])
        self.assertEqual(s['placements_partial_reference_only'], 2)

    def test_phase_boundaries_and_after_end(self):
        d = analyze([source(duration=1200, entries=[{'placed_game_time_s': -1},
            {'placed_game_time_s': 0}, {'placed_game_time_s': 599.99},
            {'placed_game_time_s': 600}, {'placed_game_time_s': 1200}])])
        self.assertEqual(stratum(d)['placements_full'], 2)
        self.assertEqual(stratum(d, 1)['placements_full'], 1)
        self.assertEqual(stratum(d, -1)['placements_partial_reference_only'], 1)
        self.assertEqual(stratum(d, -1)['full_match_ids'], [])
        self.assertEqual(d['excluded']['at_or_after_match_end'], 1)

    def test_team_type_and_producer_cohorts_do_not_mix(self):
        d = analyze([source(entries=[{}, {}, {'team': 3}, {'ward_type': 'sentry'}]),
                     source('9876543211', parser_version='different')])
        self.assertEqual(len(d['cohorts']), 2)
        hotspots = [h for s in d['strata'] for h in s['hotspots']]
        self.assertEqual(len(hotspots), 1)
        self.assertEqual(hotspots[0]['support_matches'], 1)
        self.assertEqual(hotspots[0]['placements'], 2)

    def test_bad_time_team_and_coordinates_not_counted(self):
        d = analyze([source(entries=[{'team': None}, {'team': True},
            {'placed_game_time_s': None}, {'coordinate_basis': 'signed_world'}, {'x': None}])])
        self.assertEqual(d['rows'], [])
        self.assertEqual(d['excluded']['unknown_team'], 2)
        self.assertEqual(d['excluded']['unsupported_coordinate_basis'], 1)

    def test_duplicate_incomplete_or_bad_duration_refused(self):
        for sources in ([source(), source()], [source(duration=None)], [source(source_unchanged_stat=False)],
                        [source(stop_reason='timeout')], []):
            with self.assertRaises(ValueError):
                analyze(sources)
        m, raw = source()
        with self.assertRaises(ValueError):
            analyze([(m, raw+b' ')])

    def test_grid_neighborhood_matches_distance_not_transitive_chain(self):
        rows = [{'id': str(i), 'match_id': str(i), 'x': x, 'y': 0, 'end_kind': None}
                for i, x in enumerate([-1, 99, 199, 299, 399])]
        out = neighborhoods(rows, 100)
        self.assertTrue(out)
        self.assertLessEqual(max(h['placements'] for h in out), 3)
        for h in out:
            for mid in h['members']:
                self.assertLessEqual(abs(rows[int(mid)]['x']-h['x']), 100)

    def test_map_projection_matches_existing_report_geometry(self):
        x, y = project(7487, 7693)
        self.assertAlmostEqual(x, (8878-8356)/2/8878*1600)
        self.assertAlmostEqual(y, 8356/8878*1600)
        self.assertGreater(project(18000, 20000)[0], project(17000, 20000)[0])
        self.assertLess(project(18000, 20000)[1], project(18000, 19000)[1])

    def test_pregame_repeats_are_reference_without_coverage_denominator(self):
        d = analyze([source(entries=[{'placed_game_time_s': -50}, {'placed_game_time_s': -5}])])
        s = stratum(d, -1)
        self.assertEqual(s['exposure_basis'], 'pregame_coverage_not_verified')
        self.assertIsNone(s['hotspots'][0]['match_fraction'])
        self.assertEqual(s['hotspots'][0]['support_matches'], 1)
        self.assertEqual(s['hotspots'][0]['full_matches'], 0)
