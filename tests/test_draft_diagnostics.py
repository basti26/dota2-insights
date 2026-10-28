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


if __name__ == '__main__':
    unittest.main()
