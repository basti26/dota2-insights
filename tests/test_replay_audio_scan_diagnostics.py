"""Generated score windows only; no playback, real audio or hearing labels."""
from copy import deepcopy
import unittest

from tools.review_replay_audio import scan_window_diagnostics


class ReplayAudioScanDiagnosticsTests(unittest.TestCase):
    def scan(self, offset=10., skill='ultimate', score=.8, runner=.75, **extra):
        return {'take_id': 'take', 'audio_offset_s': offset, 'skill': skill,
                'similarity': score, 'runner_up_similarity': runner,
                'reason': 'background_competitor', **extra}

    def review(self, rows, **extra):
        args = dict(take_id='take', offset_s=10., recorded_skill='ultimate', targets={'ultimate', 'other_ultimate'})
        args.update(extra)
        return scan_window_diagnostics(rows, **args)

    def test_ordinary_control_empty_same_skill_still_exposes_target_competitor(self):
        row = self.scan()
        result = self.review([row], recorded_skill='ordinary_spell')
        self.assertEqual(result['near_scan_reasons'], {})
        self.assertIsNone(result['strongest_near_scan'])
        diag = result['scan_window_diagnostics']
        self.assertEqual(diag['all_scan_count'], 1)
        self.assertEqual(diag['same_skill_scan_count'], 0)
        self.assertEqual(diag['strongest_target_scan'], row)
        self.assertFalse(diag['audibility_verified'])

    def test_highest_score_and_widest_margin_are_not_conflated(self):
        high = self.scan(score=.9, runner=.89)
        wider = self.scan(offset=10.2, score=.8, runner=.70, reason='candidate')
        result = self.review([high, wider])
        self.assertEqual(result['strongest_near_scan'], high)
        self.assertEqual(result['scan_window_diagnostics']['widest_margin_same_skill_scan'], wider)

    def test_other_target_winner_does_not_become_same_skill_support(self):
        same = self.scan(score=.8, runner=.78)
        other = self.scan(skill='other_ultimate', score=.9, runner=.7)
        result = self.review([same, other])
        self.assertEqual(result['strongest_near_scan'], same)
        self.assertEqual(result['scan_window_diagnostics']['widest_margin_target_scan'], other)
        self.assertEqual(result['scan_window_diagnostics']['widest_margin_same_skill_scan'], same)

    def test_low_level_unscored_windows_are_retained_without_fabricated_scores(self):
        rows = [{'take_id': 'take', 'audio_offset_s': 10., 'reason': 'low_level'}]
        result = self.review(rows)
        diag = result['scan_window_diagnostics']
        self.assertEqual(diag['low_level_scan_count'], 1)
        self.assertEqual(diag['unscored_scan_count'], 1)
        self.assertEqual(diag['target_scan_count'], 0)
        self.assertIsNone(diag['strongest_target_scan'])
        self.assertIsNone(diag['widest_margin_target_scan'])

    def test_take_time_and_target_scope_are_separate_and_boundaries_inclusive(self):
        rows = [self.scan(offset=8.), self.scan(offset=12.), self.scan(offset=12.001),
                self.scan(take_id='other'), self.scan(skill='ordinary_spell')]
        result = self.review(rows)
        self.assertEqual(result['scan_window_diagnostics']['all_scan_count'], 3)
        self.assertEqual(result['scan_window_diagnostics']['target_scan_count'], 2)

    def test_missing_nonfinite_and_boolean_scores_not_ranked_as_numbers(self):
        rows = [self.scan(score=None), self.scan(score=float('nan')),
                self.scan(score=True), self.scan(score=.1, runner=None),
                self.scan(score=0., runner=0.)]
        result = self.review(rows)
        diag = result['scan_window_diagnostics']
        self.assertEqual(diag['strongest_target_scan']['similarity'], .1)
        self.assertEqual(diag['widest_margin_target_scan']['similarity'], 0.)
        self.assertEqual(diag['unscored_scan_count'], 3)

    def test_invalid_time_or_missing_take_cannot_match_zero_offset(self):
        rows = [self.scan(offset=None), self.scan(offset=True), self.scan(offset=float('inf')),
                self.scan(offset=0., take_id=None), self.scan(offset=0.)]
        result = self.review(rows, offset_s=0.)
        self.assertEqual(result['scan_window_diagnostics']['all_scan_count'], 1)

    def test_result_is_detached_and_source_rows_unchanged(self):
        rows = [self.scan(details={'source': 'frozen'})]
        before = deepcopy(rows)
        result = self.review(rows)
        result['strongest_near_scan']['details']['source'] = 'changed'
        self.assertEqual(rows, before)

    def test_invalid_window_context_is_rejected(self):
        for change in ({'offset_s': True}, {'offset_s': float('nan')}, {'offset_s': -1.},
                       {'radius_s': 0}, {'radius_s': float('inf')}, {'targets': 'ultimate'},
                       {'targets': set()}, {'targets': {''}}, {'take_id': ''}, {'recorded_skill': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.review([], **change)
