import ast
import copy
import hashlib
from pathlib import Path
import unittest

from dota2_insights.replay_phase_evidence import (
    DISCONNECT, ENUM_BASIS, IN_PROGRESS, POST_GAME, PhaseTracker,
)


class PhaseTrackerTests(unittest.TestCase):
    def test_default_enums_match_the_actual_pinned_gem_proto_descriptor(self):
        # Read the pinned generated proto, never the game or a DEM. This guards
        # against tests and implementation agreeing on the same wrong numbers.
        source = (Path(__file__).resolve().parents[1]
                  / '.replay-deps/gem/proto/dota_shared_enums_pb2.py')
        if not source.is_file():
            self.skipTest('optional pinned Gem proto source is not installed')
        from google.protobuf.descriptor_pb2 import FileDescriptorProto
        raw = source.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '40003e9ed1f3e9ee0c77a83ad6cd9b332335be84a7c8f3bfd9e44ac36cdc842f')
        blobs = [ast.literal_eval(node.args[0]) for node in ast.walk(ast.parse(raw.decode('utf-8')))
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == 'AddSerializedFile' and len(node.args) == 1]
        self.assertEqual(len(blobs), 1)
        descriptor = FileDescriptorProto.FromString(blobs[0])
        values = {value.name: value.number for enum in descriptor.enum_type
                  if enum.name == 'DOTA_GameState' for value in enum.value}
        self.assertEqual((IN_PROGRESS, POST_GAME, DISCONNECT), (
            values['DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'],
            values['DOTA_GAMERULES_STATE_POST_GAME'],
            values['DOTA_GAMERULES_STATE_DISCONNECT']))
        self.assertEqual((IN_PROGRESS, POST_GAME, DISCONNECT), (5, 6, 7))
        self.assertEqual(ENUM_BASIS, 'pinned_gem_dota_shared_enums_proto')

    def report(self, observations, **options):
        tracker = PhaseTracker(**options)
        for observation in observations:
            tracker.observe(*observation)
        return tracker.manifest(observation_complete=True)

    def test_last_active_and_first_post_bracket_without_integer_duration(self):
        report = self.report([(1, -1, 4), (2, 0, 5), (3, 10.25, 5),
                              (4, 10.5, 6), (5, 145, 6)])
        self.assertEqual(report['status'], 'observed_interval')
        self.assertEqual(report['last_in_progress']['completed_packet'], 3)
        self.assertEqual(report['first_post_game']['completed_packet'], 4)
        self.assertEqual(report['end_window']['game_time_s'], [10.25, 10.5])
        self.assertEqual(report['end_window']['tick'], [3, 4])
        self.assertEqual(len(report['changes']), 3)
        self.assertFalse(report['end_window']['exact_end_verified'])
        self.assertNotIn('duration_s', report)

    def test_pause_can_produce_equal_clocks_without_claiming_exact_end(self):
        report = self.report([(1, 100, 5), (2, 100, 6)])
        self.assertEqual(report['status'], 'observed_interval')
        self.assertEqual(report['end_window']['game_time_s'], [100, 100])
        self.assertFalse(report['end_window']['exact_end_verified'])

    def test_disconnect_preserves_real_post_window_and_never_moves_it_120_seconds(self):
        report = self.report([(6512, 0.0, 5), (80155, 2454.7666, 5),
                              (80156, 2454.7999817, 6), (83756, 2574.79998, 7),
                              (83757, None, None)])
        self.assertEqual(report['state_enums'], {'in_progress': 5, 'post_game': 6})
        self.assertEqual(report['status'], 'observed_interval')
        self.assertEqual(report['first_post_game']['tick'], 80156)
        self.assertEqual(report['end_window']['game_time_s'], [2454.7666, 2454.7999817])
        self.assertEqual(report['state_reversals'], 0)
        for state in (5, 6):
            backwards = self.report([(1, 0, 5), (2, 1, 6), (3, 2, 7), (4, 3, state)])
            self.assertEqual(backwards['status'], 'invalid')

    def test_signon_negative_ticks_and_later_rules_destruction_do_not_erase_bracket(self):
        report = self.report([(0, None, None), (-1, None, None), (-1, None, 4),
                              (1, 0, 5), (2, 10, 6), (3, None, None), (4, None, None)])
        self.assertEqual(report['status'], 'observed_interval')
        self.assertEqual(report['tick_regressions'], 0)
        self.assertEqual(report['changes'][0]['tick'], 0)
        self.assertEqual(report['changes'][1]['tick'], -1)
        self.assertEqual(report['first_post_game']['completed_packet'], 5)
        self.assertEqual(report['end_window']['game_time_s'], [0, 10])

    def test_no_post_or_no_prior_active_cannot_infer_end_from_last_tick(self):
        for rows in ([], [(1, 5, 5), (2, 999, 5)], [(1, 999, 6)]):
            with self.subTest(rows=rows):
                report = self.report(rows)
                self.assertEqual(report['status'], 'unknown')
                self.assertIsNone(report['end_window'])

    def test_missing_first_post_clock_is_not_filled_from_later_post(self):
        report = self.report([(1, 10, 5), (2, None, 6), (3, 11, 6)])
        self.assertEqual(report['status'], 'unknown')
        self.assertIsNone(report['first_post_game']['game_time_s'])
        self.assertIsNone(report['end_window'])

    def test_missing_boundary_clock_or_gap_is_unknown(self):
        for rows in ([(1, None, 5), (2, 10, 6)],
                     [(1, 9, 5), (2, None, None), (3, 10, 6)]):
            report = self.report(rows)
            self.assertEqual(report['status'], 'unknown')
            self.assertFalse(report['boundary_clock_complete'])
            self.assertIsNone(report['end_window'])

    def test_later_active_can_supply_new_lower_endpoint_without_backfill(self):
        report = self.report([(1, None, 5), (2, 9, 5), (3, 10, 6)])
        self.assertIsNone(report['changes'][0]['game_time_s'])
        self.assertEqual(report['last_in_progress']['game_time_s'], 9)
        self.assertEqual(report['status'], 'observed_interval')

    def test_clock_or_tick_regression_invalidates_even_an_existing_bracket(self):
        for rows in ([(2, 9, 5), (1, 10, 6)],
                     [(1, 10, 5), (2, 9, 6)],
                     [(1, 9, 5), (2, 10, 6), (3, 8, 6)]):
            report = self.report(rows)
            self.assertEqual(report['status'], 'invalid')
            self.assertIsNone(report['end_window'])

    def test_state_reversal_or_second_round_is_invalid(self):
        for rows in ([(1, 0, 5), (2, 1, 4), (3, 2, 6)],
                     [(1, 0, 5), (2, 1, 6), (3, 2, 5), (4, 3, 6)]):
            report = self.report(rows)
            self.assertEqual(report['status'], 'invalid')
            self.assertGreater(report['state_reversals'], 0)
            self.assertIsNone(report['end_window'])

    def test_consecutive_unknowns_and_long_active_run_use_bounded_history(self):
        tracker = PhaseTracker(max_changes=4)
        for tick in range(10000):
            tracker.observe(tick, None, None)
        for tick in range(10000, 20000):
            tracker.observe(tick, tick, 5)
        tracker.observe(20000, 20000, 6)
        report = tracker.manifest(observation_complete=True)
        self.assertEqual(report['completed_packets'], 20001)
        self.assertEqual(len(report['changes']), 3)
        self.assertFalse(report['changes_truncated'])
        self.assertEqual(report['status'], 'observed_interval')

    def test_change_budget_or_resource_stop_prevents_native_end_binding(self):
        report = self.report([(1, -1, 4), (2, 0, 5), (3, 1, 6)], max_changes=2)
        self.assertEqual(len(report['changes']), 2)
        self.assertTrue(report['changes_truncated'])
        self.assertEqual(report['status'], 'invalid')
        self.assertIsNone(report['end_window'])
        tracker = PhaseTracker()
        tracker.observe(1, 0, 5)
        tracker.observe(2, 1, 6)
        report = tracker.manifest(observation_complete=False, stop_reason='wall_time_limit')
        self.assertEqual(report['status'], 'invalid')
        self.assertIn('observation_incomplete', report['reasons'])

    def test_custom_enums_and_snapshot_copies(self):
        tracker = PhaseTracker(in_progress=60, post_game=70)
        tracker.observe(1, 2, 60)
        tracker.observe(2, 3, 70)
        first = tracker.manifest(observation_complete=True)
        original = copy.deepcopy(first)
        first['changes'][0]['state_raw'] = 99
        first['last_in_progress']['tick'] = 99
        self.assertEqual(tracker.manifest(observation_complete=True), original)

    def test_invalid_inputs_do_not_mutate_tracker(self):
        tracker = PhaseTracker()
        original = tracker.manifest(observation_complete=True)
        for args in ((True, 0, 5), (1.5, 0, 5), (10 ** 100, 0, 5),
                     (1, True, 5), (1, float('nan'), 5), (1, float('inf'), 5),
                     (1, 10 ** 100, 5), (1, 0, True), (1, 0, '6')):
            with self.subTest(args=args), self.assertRaises(ValueError):
                tracker.observe(*args)
            self.assertEqual(tracker.manifest(observation_complete=True), original)
        for kwargs in ({'max_changes': True}, {'max_changes': 0}, {'max_changes': 10 ** 9},
                       {'in_progress': True}, {'in_progress': 6}, {'post_game': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PhaseTracker(**kwargs)


if __name__ == '__main__':
    unittest.main()
