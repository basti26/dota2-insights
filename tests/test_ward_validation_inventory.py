import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from dota2_insights.ward_validation_inventory import inventory


class WardValidationInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = self.root / 'history'
        self.replays = self.root / 'replays'
        self.history.mkdir()
        self.replays.mkdir()
        self.catalog = self.root / 'catalog.json'
        self.catalog.write_text(json.dumps({'matches': {
            '9050000001': {'analysis_status': 'complete'},
            '9050000002': {'analysis_status': 'partial_or_failed'},
            '9050000003': {'analysis_status': 'waiting_for_replay'}}}), encoding='utf-8')

    def scan(self):
        return inventory([self.history], [self.replays], self.catalog)

    def test_completed_and_failed_are_exposed_waiting_is_not_certified(self):
        result = self.scan()
        self.assertIn('9050000001', result['exposure'])
        self.assertIn('9050000002', result['exposure'])
        self.assertNotIn('9050000003', result['exposure'])
        self.assertFalse(result['eligibility_certified'])
        self.assertFalse(result['independent_validation_passed'])
        self.assertEqual(result['frozen_matches'], [])

    def test_read_only_database_and_text_references_remain_distinct(self):
        dbpath = self.history / 'facts.sqlite'
        with closing(sqlite3.connect(dbpath)) as db:
            db.execute('CREATE TABLE matches (match_id TEXT)')
            db.execute("INSERT INTO matches VALUES ('9050000004')")
            db.commit()
        before = dbpath.read_bytes()
        (self.history / 'old.md').write_text('开发记录9050000005', encoding='utf-8')
        result = self.scan()
        self.assertEqual(result['exposure']['9050000004'], ['analysis_database'])
        self.assertEqual(result['exposure']['9050000005'], ['historical_reference'])
        self.assertEqual(dbpath.read_bytes(), before)

    def test_unseen_filename_is_only_a_candidate_not_footer_verified(self):
        (self.replays / '9050000006.dem').write_bytes(b'not a replay')
        result = self.scan()
        self.assertEqual(result['local_replays'][0]['eligibility'], 'requires_footer_and_history_review')
        self.assertFalse(result['eligibility_certified'])

    def test_broken_database_is_a_recorded_gap(self):
        (self.history / 'bad.sqlite').write_bytes(b'broken')
        result = self.scan()
        self.assertFalse(result['history_scan_complete'])
        self.assertEqual(len(result['scan_issues']), 1)

    def test_missing_history_and_replay_roots_are_gaps(self):
        result = inventory([self.root / 'absent'], [self.root / 'absent2'], self.catalog)
        self.assertFalse(result['history_scan_complete'])
        self.assertEqual(len(result['scan_issues']), 2)

    def test_duplicate_history_root_does_not_duplicate_sources(self):
        (self.history / 'old.md').write_text('9050000007', encoding='utf-8')
        result = inventory([self.history, self.history], [self.replays], self.catalog)
        self.assertEqual(len(result['history_sources']), 1)


if __name__ == '__main__':
    unittest.main()
