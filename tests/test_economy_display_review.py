"""Synthetic optional registry metadata; no live source or cross-repo import."""
import unittest
from unittest.mock import patch

from lock_fixtures import install_local_lock
from dota2_insights.economy_display_review import summarize_registry, SCOPE


def row(reason='placement_requested', mid='12345', second=1):
    return {'reason': reason, 'window_requested': reason in {'placement_requested', 'preview'},
            'match_id': mid, 'observed_at_utc': f'2026-10-25T09:10:{second:02d}Z'}


class DisplayReviewTests(unittest.TestCase):
    def setUp(self):
        install_local_lock(self)

    def summarize(self, value):
        return summarize_registry(value, {'12345'}, started_at_utc='2026-10-25T09:00:00Z',
                                  closed_at_utc='2026-10-25T10:00:00Z')

    def test_missing_is_unknown_and_bound_transitions_do_not_imply_visibility(self):
        self.assertIsNone(self.summarize({})['transition_count'])
        rows = [row('gsi_waiting', None), row(second=2), row('game_not_foreground', second=3)]
        result = self.summarize({'economy_display_transitions': rows,
                                'economy_display': {**rows[-1], 'scope': SCOPE}})
        self.assertFalse(result['pixel_visibility_verified'])
        self.assertEqual(result['by_explicit_match']['12345'],
                         {'placement_requested': 1, 'game_not_foreground': 1})
        self.assertEqual(result['unbound_reason_transition_counts'], {'gsi_waiting': 1})

    def test_rejects_extra_private_fields_bad_boolean_time_and_foreign_identity(self):
        for change in ({'token': 'secret'}, {'reason': []}, {'window_requested': 1},
                       {'window_requested': False}, {'observed_at_utc': '2026-10-26T09:10:01Z'},
                       {'match_id': '99999'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.summarize({'economy_display_transitions': [{**row(), **change}]})
        with patch('dota2_insights.economy_display_review.refuse_locked_match',
                   side_effect=ValueError('locked')) as guard:
            with self.assertRaisesRegex(ValueError, 'locked'):
                self.summarize({'economy_display_transitions': [row()]})
        guard.assert_called_once_with('12345')

    def test_budget_order_duplicate_and_truncation_latest_are_checked(self):
        for rows in ([], [row()] * 129, [row(), row(second=2)],
                     [row(second=2), row('gsi_stale', second=1)]):
            with self.assertRaises(ValueError):
                self.summarize({'economy_display_transitions': rows})
        history = [row('gsi_stale' if i % 2 else 'placement_requested', second=1) for i in range(128)]
        doc = {'economy_display_transitions': history, 'economy_display_history_limited': True,
               'economy_display': {**row('user_hidden', second=5), 'scope': SCOPE}}
        result = self.summarize(doc)
        self.assertTrue(result['history_limited'])
        self.assertEqual(result['latest']['reason'], 'user_hidden')
        doc['economy_display_history_limited'] = False
        with self.assertRaisesRegex(ValueError, 'latest_inconsistent'):
            self.summarize(doc)

    def test_closed_source_integration_and_exit_reverification(self):
        from test_helper_handoff import HandoffTests
        from dota2_insights.record_content_review import summarize, render_markdown
        f = HandoffTests(); f.setUp(); self.addCleanup(f.doCleanups)
        f.document['economy_display_transitions'] = [row()]
        f.document['economy_display'] = {**row(), 'scope': SCOPE}
        f.write(f.registry, f.document)
        package = f.inspect()
        result = summarize(package, f.roots)
        self.assertEqual(result['economy_window_requests']['transition_count'], 1)
        self.assertIn('不证明屏幕可见', render_markdown([result]))
        from dota2_insights.closed_journals import ClosedJournals
        original = ClosedJournals.registry
        def mutate(reader):
            value = original(reader)
            f.registry.write_bytes(f.registry.read_bytes() + b' ')
            return value
        with patch.object(ClosedJournals, 'registry', mutate):
            with self.assertRaises(ValueError):
                summarize(package, f.roots)
