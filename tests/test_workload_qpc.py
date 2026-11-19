"""Synthetic work/frame overlaps do not establish assistant-caused stalls."""
from copy import deepcopy
from unittest import TestCase

from dota2_insights.workload_qpc import review_overlap

SCOPE = {'match_id': '123', 'game_session_id': 'game_'+'a'*32, 'record_session_id': 'run_'+'b'*32}


def context():
    return {**SCOPE, 'game_state': 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS',
            'foreground_dota': True, 'game_provider_age_s': 0, 'qpc_ticks': 3000,
            'workload_qpc': {'schema_version': 1, 'clock': 'windows_QueryPerformanceCounter',
                'clock_id': 'c'*32, 'frame_id': 'd'*32, 'frequency_hz': 1000, 'process_id': 123,
                'unmeasured_spans': 0, 'dropped_spans': 0,
                'spans': [{'stage': 'items', 'start_qpc': 2700, 'end_qpc': 2800, 'failed': False},
                          {'stage': 'frame_total', 'start_qpc': 2600, 'end_qpc': 2900, 'failed': False}]}}


def review(rows):
    return review_overlap(rows, scope=SCOPE, frequency=1000, helper_process_id=123, windows=[[2000, 4000]], examples=[
        {'csv_data_row': 3, 'intervals': [{'qpc_start': 2650, 'qpc_end': 2850},
                                         {'qpc_start': 2750, 'qpc_end': 2950}]}])


class WorkloadOverlapTests(TestCase):
    def test_bound_audio_and_ocr_share_native_clock_without_conflating_audio_sample_time(self):
        row = context()
        audio = deepcopy(row['workload_qpc'])
        audio.update(clock_id='e'*32, frame_id='f'*32)
        audio['spans'] = [{'stage':'audio_save', 'start_qpc':2800, 'end_qpc':2820, 'failed':False}]
        row['audio_workload_qpc'] = [{'scope':SCOPE, 'workload_qpc':audio}]
        result = review([row, deepcopy(row)])
        self.assertEqual(result['unique_frame_traces'], 2)
        self.assertEqual(result['eligible_spans'], 3)
        stages = result['long_interval_examples'][0]['stages']
        self.assertEqual(next(s for s in stages if s['stage']=='audio_save')['overlap_ms'], 20)
        self.assertIn('not_game_frame_or_PCM_sample', result['trace_unit'])
        self.assertFalse(result['assistant_causality_verified'])

    def test_audio_needs_its_own_exact_source_scope_and_bounded_carrier(self):
        for change in ({'match_id':'124'}, {'game_session_id':'game_'+'f'*32},
                       {'record_session_id':'run_'+'f'*32}):
            row = context()
            audio = row.pop('workload_qpc')
            audio['spans'] = [{'stage':'audio_save','start_qpc':2700,'end_qpc':2800,'failed':False}]
            row['audio_workload_qpc'] = [{'scope':{**SCOPE, **change}, 'workload_qpc':audio}]
            self.assertEqual(review([row])['eligible_spans'], 0)
        row = context()
        row['workload_qpc']['spans'] = [{'stage':'audio_save','start_qpc':2700,'end_qpc':2800,'failed':False}]
        self.assertEqual(review([row])['eligible_spans'], 0)  # Cannot bypass the separate audio binding.
        value = row.pop('workload_qpc')
        row['audio_workload_qpc'] = [{'scope':SCOPE,'workload_qpc':value}]*3
        self.assertEqual(review([row])['eligible_spans'], 0)

    def test_same_frame_repeats_and_submitted_display_intersections_are_not_added_twice(self):
        row = context()
        result = review([row, deepcopy(row)])
        self.assertEqual(result['unique_frame_traces'], 1)
        self.assertEqual(result['eligible_spans'], 2)
        stages = result['long_interval_examples'][0]['stages']
        self.assertEqual(stages, [{'stage': 'items', 'overlap_ms': 100.0, 'recorded_spans': 1, 'failed_spans': 0},
                                 {'stage': 'frame_total', 'overlap_ms': 250.0, 'recorded_spans': 1, 'failed_spans': 0}])
        self.assertFalse(result['assistant_causality_verified'])
        self.assertFalse(result['sampling_complete'])
        self.assertFalse(result['stage_durations_additive'])

    def test_unknown_legacy_clock_or_wrong_scope_cannot_be_joined_by_time(self):
        for change in ({'match_id': '124'}, {'game_session_id': 'game_'+'e'*32},
                       {'record_session_id': 'run_'+'f'*32}, {'foreground_dota': False},
                       {'game_provider_age_s': 6}, {'qpc_ticks': True}, {'workload_qpc': None}):
            self.assertEqual(review([{**context(), **change}])['eligible_spans'], 0)
        for change in ({'frequency_hz': 999}, {'clock': 'UTC'}, {'frequency_hz': True},
                       {'schema_version': True}, {'spans': [{'stage': []}]}):
            row = context()
            row['workload_qpc'].update(change)
            self.assertEqual(review([row])['eligible_spans'], 0)

    def test_conflicting_frame_or_clock_process_excludes_all_versions(self):
        a, b = context(), context()
        b['workload_qpc']['spans'][0]['end_qpc'] = 2801
        self.assertEqual(review([a,b])['eligible_spans'], 0)
        b = context()
        b['workload_qpc'].update(frame_id='e'*32, process_id=124)
        self.assertEqual(review([a,b])['eligible_spans'], 0)

    def test_equal_frequency_and_ticks_do_not_bind_another_helper_process(self):
        row = context()
        row['workload_qpc']['process_id'] = 125
        result = review([row])
        self.assertEqual(result['eligible_spans'], 0)
        self.assertEqual(result['excluded'], {'helper_process_not_bound_to_capture': 1})

    def test_future_or_boundary_crossing_work_is_not_extrapolated(self):
        row = context()
        row['qpc_ticks'] = 2799
        self.assertEqual(review([row])['eligible_spans'], 0)
        row = context()
        row['workload_qpc']['spans'][0]['start_qpc'] = 1999
        result = review([row])
        self.assertEqual(result['eligible_spans'], 1)
        self.assertEqual(result['excluded']['span_not_fully_in_game_window'], 1)

    def test_no_overlap_and_recording_loss_stay_distinct_from_no_work(self):
        row = context()
        row['workload_qpc'].update(unmeasured_spans=2, dropped_spans=1)
        row['workload_qpc']['spans'] = [{'stage': 'items', 'start_qpc': 2100, 'end_qpc': 2200, 'failed': True}]
        result = review([row])
        self.assertEqual(result['eligible_spans'], 1)
        self.assertEqual(result['long_interval_examples'][0]['stages'], [])
        self.assertEqual(result['recording_losses'], {'unmeasured_spans': 2, 'dropped_spans': 1})
        self.assertFalse(result['absence_of_overlap_proves_absence_of_work'])
