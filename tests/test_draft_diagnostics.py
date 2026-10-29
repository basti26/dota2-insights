"""Recorded BP state and widget evidence only; no pixels or live UI."""
import copy
import unittest

from dota2_insights.draft_diagnostics import DraftDiagnostics, render_markdown


class DraftDiagnosticsTests(unittest.TestCase):
    def event(self):
        return {'written_at_utc': '2026-10-26T12:00:01Z',
            'snapshot': {'session_id': 'a'*32, 'revision': 3, 'phase': 'selection', 'own_side': 'left',
                'slots': [{'slot': 'left1', 'hero_id': 'axe', 'public': True, 'conflict': False},
                          {'slot': 'left2', 'hero_id': 'wrong', 'public': True, 'conflict': True},
                          {'slot': 'right1', 'hero_id': 'lion', 'public': True, 'conflict': False},
                          {'slot': 'right2', 'hero_id': 'pudge', 'public': True, 'conflict': False}]},
            'advice': {'stage': 'waiting', 'candidates': [], 'message': 'waiting_for_public_roster'},
            'timing_ms': {'processing_last': 1500, 'vision_last': {'total_ms': 1400}, 'advice_last': None}}

    def receipt(self, event):
        return {'presented_at_utc': '2026-10-26T12:00:01.125Z', 'surface': 'game_panel',
                'view_input': {'snapshot': copy.deepcopy(event['snapshot']), 'advice': copy.deepcopy(event['advice'])}}

    def test_conflicting_fourth_hero_is_not_confirmed_two_plus_two(self):
        diag = DraftDiagnostics()
        diag.add('events.jsonl', 1, self.event(), {'line_sha256': 'a'*64})
        result = diag.result()
        row = result['events'][0]
        self.assertEqual(row['raw_hero_slots'], 4)
        self.assertEqual(row['accepted_own_heroes'], 1)
        self.assertEqual(row['accepted_enemy_heroes'], 2)
        self.assertEqual(row['conflicted_slots'], ['left2'])
        self.assertEqual(result['selection_recommendation_event_lines'], [])

    def test_exact_unique_view_links_widget_application_not_display(self):
        event = self.event()
        event['advice'].update(stage='round_one', candidates=[{'hero_id': 'lina'}])
        diag = DraftDiagnostics()
        diag.add('events.jsonl', 2, event, {'line_sha256': 'a'*64})
        diag.add('ui-presented.jsonl', 1, self.receipt(event), {'line_sha256': 'b'*64})
        result = diag.result()
        self.assertEqual(result['ui_application_evidence'][0]['recorded_generation_to_widget_ms'], 125)
        self.assertEqual(result['ui_application_evidence'][0]['event_line'], 2)
        self.assertFalse(result['reveal_latency_verified'])
        self.assertFalse(result['os_display_verified'])

    def test_same_revision_different_advice_never_matches(self):
        event = self.event()
        receipt = self.receipt(event)
        receipt['view_input']['advice']['candidates'] = [{'hero_id': 'lina'}]
        diag = DraftDiagnostics()
        diag.add('events.jsonl', 1, event, {'line_sha256': 'a'*64})
        diag.add('ui-presented.jsonl', 1, receipt, {'line_sha256': 'b'*64})
        self.assertIsNone(diag.result()['ui_application_evidence'][0]['event_line'])

    def test_duplicate_prior_events_do_not_invent_latency(self):
        event = self.event()
        diag = DraftDiagnostics()
        for line in (1, 2):
            diag.add('events.jsonl', line, event, {'line_sha256': 'a'*64})
        diag.add('ui-presented.jsonl', 1, self.receipt(event), {'line_sha256': 'b'*64})
        self.assertIsNone(diag.result()['ui_application_evidence'][0]['recorded_generation_to_widget_ms'])

    def test_regressed_or_naive_ui_time_stays_unknown(self):
        for stamp in ('2026-10-26T12:00:00Z', '2026-10-26T12:00:01.125'):
            diag = DraftDiagnostics()
            event = self.event()
            receipt = self.receipt(event)
            receipt['presented_at_utc'] = stamp
            diag.add('events.jsonl', 1, event, {'line_sha256': 'a'*64})
            diag.add('ui-presented.jsonl', 1, receipt, {'line_sha256': 'b'*64})
            self.assertIsNone(diag.result()['ui_application_evidence'][0]['event_line'])

    def test_missing_ui_stream_renders_unknown_not_zero(self):
        text = render_markdown([{'session_id': 'a'*32, 'stream_rows': {'events.jsonl': 1},
                                 'diagnostics': DraftDiagnostics().result()}])
        self.assertIn('未知（无回执流）', text)

    def test_duplicate_slot_is_refused(self):
        event = self.event()
        event['snapshot']['slots'].append(event['snapshot']['slots'][0])
        with self.assertRaisesRegex(ValueError, 'slot_identity'):
            DraftDiagnostics().add('events.jsonl', 1, event, {'line_sha256': 'a'*64})

    def ready(self, stamp, candidates=False):
        event = self.event()
        event['written_at_utc'] = stamp
        event['snapshot']['slots'][1]['conflict'] = False
        if candidates:
            event['advice'].update(stage='round_one', candidates=[{'hero_id': 'lina'}])
        return event

    def test_stage_wait_generation_and_later_widget_are_separate(self):
        diag = DraftDiagnostics()
        wait = self.ready('2026-10-26T12:00:00Z')
        first = self.ready('2026-10-26T12:00:02Z', True)
        later = self.ready('2026-10-26T12:00:03Z', True)
        later['snapshot']['revision'] += 1
        for i, event in enumerate((wait, first, later), 1):
            diag.add('events.jsonl', i, event, {'line_sha256': 'a'*64})
        receipt = self.receipt(later)
        receipt['presented_at_utc'] = '2026-10-26T12:00:03.125Z'
        diag.add('ui-presented.jsonl', 1, receipt, {'line_sha256': 'b'*64})
        stage = diag.result()['stage_timing']['stages'][0]
        self.assertEqual(stage['recorded_eligibility_to_generation_ms'], 2000)
        self.assertEqual(stage['recorded_eligibility_to_widget_ms'], 3125)
        self.assertEqual(stage['widget_source_event_line'], 3)
        self.assertEqual(stage['widget_relative_to_recorded_end'], 'unknown')

    def test_end_event_cannot_prove_hide_and_late_receipt_is_exposed(self):
        diag = DraftDiagnostics()
        first = self.ready('2026-10-26T12:00:00Z', True)
        end = copy.deepcopy(first)
        end['snapshot']['phase'] = 'strategy'
        end['written_at_utc'] = '2026-10-26T12:00:01Z'
        diag.add('events.jsonl', 1, first, {'line_sha256': 'a'*64})
        diag.add('events.jsonl', 2, end, {'line_sha256': 'a'*64})
        receipt = self.receipt(first)
        diag.add('ui-presented.jsonl', 1, receipt, {'line_sha256': 'b'*64})
        report = diag.result()['stage_timing']
        self.assertEqual(report['stages'][0]['widget_relative_to_recorded_end'], 'at_or_after')
        self.assertTrue(report['stages'][0]['eligibility_and_generation_same_event'])
        self.assertIsNone(report['stages'][0]['recorded_eligibility_to_generation_ms'])
        self.assertFalse(report['end_widget_hidden_verified'])

    def test_conflicts_unknown_side_and_missing_widget_do_not_become_zero(self):
        for own_side in (None, 'left'):
            event = self.event()
            event['snapshot']['own_side'] = own_side
            diag = DraftDiagnostics()
            diag.add('events.jsonl', 1, event, {'line_sha256': 'a'*64})
            stage = diag.result()['stage_timing']['stages'][0]
            self.assertEqual(stage['status'], 'eligibility_not_observed')
            self.assertIsNone(stage['recorded_eligibility_to_widget_ms'])

    def test_stage_clock_regression_is_not_treated_as_latency(self):
        diag = DraftDiagnostics()
        for line, event in enumerate((self.ready('2026-10-26T12:00:03Z'),
                                      self.ready('2026-10-26T12:00:02Z'),
                                      self.ready('2026-10-26T12:00:04Z', True)), 1):
            diag.add('events.jsonl', line, event, {'line_sha256': 'a'*64})
        stage = diag.result()['stage_timing']['stages'][0]
        self.assertIsNone(stage['recorded_eligibility_to_generation_ms'])
        self.assertEqual(stage['status'], 'generated_without_linked_widget_receipt')

    def visibility(self, **values):
        row = {'record_version': 1, 'session_id': 'a'*32, 'revision': 0, 'phase': 'selection',
               'advice_stage': 'round_one', 'visible': True, 'expanded': False, 'reason': 'shown',
               'display_mode': 'bp', 'observed_at_utc': '2026-10-26T12:00:01Z',
               'written_at_utc': '2026-10-26T12:00:01.1Z'}
        return {**row, **values}

    def test_visibility_flags_are_separate_from_application_and_OS_truth(self):
        diag = DraftDiagnostics()
        for line, row in enumerate((self.visibility(), self.visibility(visible=False, reason='outside_draft',
                                                                       phase='strategy', display_mode='laning')), 1):
            diag.add('panel-visibility.jsonl', line, row, {'line_sha256': str(line)*64})
        result = diag.result()
        flags = result['panel_visibility_evidence']
        self.assertEqual(flags['valid_rows'], 2)
        self.assertFalse(flags['rows'][1]['visible'])
        self.assertEqual(flags['rows'][0]['revision'], 0)
        self.assertEqual(flags['recorded_reason_counts'], {'shown': 1, 'outside_draft': 1})
        self.assertEqual(result['ui_application_evidence'], [])
        self.assertFalse(result['stage_timing']['end_widget_hidden_verified'])
        self.assertFalse(flags['os_visibility_verified'])

    def test_legacy_mode_is_unknown_content_and_laning_is_not_BP(self):
        diag = DraftDiagnostics()
        legacy = self.visibility(phase='strategy'); legacy.pop('display_mode')
        for line, row in enumerate((legacy, self.visibility(phase='strategy', display_mode='laning'),
                                    self.visibility(phase='strategy')), 1):
            diag.add('panel-visibility.jsonl', line, row, {'line_sha256': 'a'*64})
        flags = diag.result()['panel_visibility_evidence']
        self.assertEqual(flags['legacy_missing_mode_rows'], 1)
        self.assertEqual(flags['end_phase_explicit_bp_shown_lines'], [3])
        self.assertEqual(flags['explicit_mode_counts'], {'laning': 1, 'bp': 1})

    def test_bad_visibility_rows_do_not_become_hidden_or_healthy(self):
        diag = DraftDiagnostics()
        bad = ({'visible': 1}, {'visible': False}, {'record_version': True},
               {'observed_at_utc': '2026-10-26T12:00:02Z'}, {'written_at_utc': '2026-10-26T12:00:01'},
               {'display_mode': 'unknown'}, {'revision': True})
        for line, values in enumerate(bad, 1):
            diag.add('panel-visibility.jsonl', line, self.visibility(**values), {'line_sha256': 'a'*64})
        flags = diag.result()['panel_visibility_evidence']
        self.assertEqual(flags['invalid_rows'], len(bad))
        self.assertEqual(flags['valid_rows'], 0)
        self.assertEqual(flags['recorded_reason_counts'], {})

    def test_absent_empty_and_not_declared_streams_are_distinct(self):
        diag = DraftDiagnostics()
        self.assertIsNone(diag.result()['panel_visibility_evidence']['stream_present'])
        self.assertFalse(diag.result(available_streams=[])['panel_visibility_evidence']['stream_present'])
        flags = diag.result(available_streams=['panel-visibility.jsonl'])['panel_visibility_evidence']
        self.assertTrue(flags['stream_present'])
        self.assertEqual(flags['valid_rows'], 0)

    def test_finished_is_a_recorded_terminal_phase(self):
        diag = DraftDiagnostics()
        event = self.event(); event['snapshot']['phase'] = 'finished'
        diag.add('events.jsonl', 1, event, {'line_sha256': 'a'*64})
        self.assertEqual(diag.result()['stage_timing']['first_recorded_end_phase'], 'finished')


if __name__ == '__main__':
    unittest.main()
