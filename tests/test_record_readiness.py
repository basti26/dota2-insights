"""Readiness is not accuracy, and shared files are not per-match observations."""
import copy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dota2_insights import record_readiness as review
from dota2_insights import helper_handoff as handoff


def package():
    source = {'path': 'samples.jsonl', 'sha256': 'a' * 64}
    return {'launcher_run_id': 'run_test', 'state': 'ready_with_gaps',
        'matches': [{'match_id': '123', 'game_session_id': 'game_one',
                     'record_session_id': 'run_one', 'match_end_evidence': 'not_observed'},
                    {'match_id': '456', 'game_session_id': 'game_two',
                     'record_session_id': 'run_two', 'match_end_evidence': 'own_recorded_gsi_post_game'}],
        'sources': [source], 'components': [{'kind': 'own_gsi', 'state': 'closed_verified',
            'journals': [{'source': source, 'summary': {'rows': 100, 'row_meaning': 'json_objects',
                'reported_match_counts': {'123': 20}, 'unbound_rows': 80,
                'time_fields': {'received_at_utc': {'first': '2026-10-26T00:00:00Z'}}}}],
            'warnings': ['limit_reached'], 'issues': []}]}


class ReadinessTests(unittest.TestCase):
    def evaluate(self, value=None):
        with patch.object(review, 'verify_package', return_value=True) as verify:
            result = review.review_package(value or package(), {})
            verify.assert_called_once()
        return result

    def test_reported_match_counts_do_not_spread_to_other_matches(self):
        r = self.evaluate()
        self.assertEqual(r['matches'][0]['channels'][0]['match_bound_rows'], 20)
        self.assertIsNone(r['matches'][1]['channels'][0]['match_bound_rows'])
        self.assertFalse(r['full_match_coverage_verified'])
        self.assertIsNone(r['recognition_accuracy'])

    def test_missing_channels_are_unknown_not_zero(self):
        r = self.evaluate()['matches'][0]
        missing = next(c for c in r['channels'] if c['kind'] == 'minimap')
        self.assertEqual(missing['state'], 'not_recorded')
        self.assertIsNone(missing['match_bound_rows'])

    def test_explicit_match_folder_can_bind_rows_but_not_other_match(self):
        p = package()
        p['components'][0]['declared_match_id'] = '456'
        r = self.evaluate(p)['matches']
        self.assertEqual(r[0]['channels'][0]['state'], 'not_recorded')
        self.assertEqual(r[1]['channels'][0]['match_bound_rows'], 100)

    def test_csv_physical_lines_are_not_observations(self):
        p = package()
        p['components'][0]['journals'][0]['summary']['row_meaning'] = 'physical_lines'
        self.assertIsNone(self.evaluate(p)['matches'][0]['channels'][0]['match_bound_rows'])

    def test_blocked_audio_has_no_count_and_requests_closure(self):
        p = package()
        c = copy.deepcopy(p['components'][0])
        c.update(kind='audio', state='blocked', issues=['handoff_audio_not_closed'])
        p['components'].append(c)
        r = self.evaluate(p)['matches'][0]
        audio = next(c for c in r['channels'] if c['kind'] == 'audio')
        self.assertEqual(audio['journals'], [])
        self.assertIsNone(audio['match_bound_rows'])
        self.assertIn({'kind': 'audio', 'reason': 'resolve_closure_before_analysis'}, r['next_actions'])

    def test_unverified_journal_reference_is_refused(self):
        p = package()
        p['sources'] = []
        with self.assertRaisesRegex(ValueError, 'journal_not_in_verified_sources'):
            self.evaluate(p)

    def test_multiple_recorders_and_time_field_names_are_preserved(self):
        p = package()
        p['components'].append(copy.deepcopy(p['components'][0]))
        r = self.evaluate(p)
        self.assertEqual(len([c for c in r['matches'][0]['channels'] if c['kind'] == 'own_gsi']), 2)
        self.assertIn('received_at_utc', r['matches'][0]['channels'][0]['journals'][0]['source_time_fields'])
        self.assertNotIn('captured_at_utc', r['matches'][0]['channels'][0]['journals'][0]['source_time_fields'])

    def write_index(self, folder, p=None):
        p = p or package()
        raw = json.dumps(p).encode()
        pid = hashlib.sha256(raw).hexdigest()
        (folder / 'packages').mkdir()
        path = folder / 'packages' / (pid + '.json')
        path.write_bytes(raw)
        index = folder / 'index.json'
        index.write_text(json.dumps({'kind': 'helper_handoff_index', 'schema_version': 1,
            'runs': [{'package_id': pid, 'launcher_run_id': p['launcher_run_id']}]}))
        return index, path

    def test_stale_package_never_publishes_old_counts(self):
        with TemporaryDirectory() as temp:
            index, _ = self.write_index(Path(temp))
            with patch.object(review, 'verify_package', side_effect=ValueError('handoff_index_stale')):
                r = review.build_report(index, {})['runs'][0]
            self.assertEqual(r['state'], 'blocked')
            self.assertEqual(r['matches'], [])

    def test_tampered_package_refused_before_source_reads(self):
        with TemporaryDirectory() as temp:
            index, source = self.write_index(Path(temp))
            source.write_bytes(source.read_bytes() + b' ')
            with patch.object(review, 'verify_package') as verify:
                with self.assertRaisesRegex(ValueError, 'digest_mismatch'):
                    review.build_report(index, {})
                verify.assert_not_called()

    def test_waiting_discovery_without_package_remains_waiting(self):
        with TemporaryDirectory() as temp:
            index = Path(temp) / 'index.json'
            index.write_text(json.dumps({'kind': 'helper_handoff_index', 'schema_version': 1,
                'runs': [{'state': 'waiting', 'reason': 'handoff_source_not_quiet'}]}))
            r = review.build_report(index, {})['runs'][0]
            self.assertEqual(r['state'], 'waiting')
            self.assertFalse(r['source_verified'])

    def test_markdown_uses_unknown_and_keeps_limit_warning(self):
        text = review.render_markdown({'runs': [self.evaluate()]})
        self.assertIn('未确定', text)
        self.assertIn('limit_reached', text)
        self.assertIn('不代表全程采集', text)

    def test_actual_handoff_package_revalidated_against_synthetic_sources(self):
        # The existing synthetic fixture produces a real closed, drained package.
        from test_helper_handoff import HandoffTests
        fixture = HandoffTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        p = fixture.inspect()
        self.assertTrue(review.review_package(p, fixture.roots)['source_verified'])
        fixture.journal.write_text('{}\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'stale'):
            review.review_package(p, fixture.roots)
