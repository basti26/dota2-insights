import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from dota2_insights.ward_validation_freeze import freeze_sources


class WardValidationFreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inventory = self.root / 'inventory.json'
        self.history = {'history_scan_complete': True, 'scan_issues': [], 'exposure': {}}
        self.save_history()
        self.protocol = self.root / 'protocol.md'
        self.protocol.write_text('synthetic test protocol', encoding='utf-8')
        self.map = self.root / 'map'
        self.map.write_bytes(b'synthetic map')
        self.request = {'inventory_sha256': self.sha(self.inventory), 'protocol_sha256': self.sha(self.protocol),
                        'history_review': {'reviewer': 'fixture', 'reviewed_at_utc': '2026-10-23T00:00:00Z',
                                           'scope_notes': 'synthetic fixtures only'}, 'matches': []}
        for i in range(2):
            source = self.root / f'{i}.dem'
            source.write_bytes(bytes([i]) * 32)
            self.request['matches'].append({'match_id': str(9050000000 + i),
                'role': 'development' if i == 0 else 'heldout', 'source': str(source),
                'source_origin': 'synthetic', 'acquired_at_utc': '2026-10-23T00:00:00Z',
                'replay_sha256': self.sha(source), 'map_image': str(self.map), 'map_mapping': str(self.map)})
        self.probe = Mock(side_effect=lambda source, mid, sha: {
            'status': 'verified_bounded_metadata', 'match_id': mid, 'footer': {'match_id': mid},
            'source_sha256': sha, 'source_sha256_after_read': sha, 'source_stat_unchanged': True,
            'game_build': 6964, 'game_mode': 22})

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def save_history(self):
        self.inventory.write_text(json.dumps(self.history), encoding='utf-8')

    def freeze(self):
        return freeze_sources(self.request, self.inventory, self.protocol, self.probe)

    def test_source_admission_is_not_visual_verification(self):
        result = self.freeze()
        self.assertEqual(result['status'], 'sources_frozen_pending_samples')
        self.assertFalse(result['independent_validation_passed'])
        self.assertEqual([m['role'] for m in result['matches']], ['development', 'heldout'])
        self.assertTrue(all(not m['map_calibration_verified'] for m in result['matches']))
        self.assertNotIn('wards', result)
        self.assertEqual((result['random_per_match'], result['special_per_match']), (16, 8))
        self.assertEqual(len(result['cohort_keys']), 1)

    def test_history_gaps_reject_before_opening_replays(self):
        self.history['scan_issues'] = [{'path': 'unknown', 'reason': 'oversize'}]
        self.save_history()
        self.request['inventory_sha256'] = self.sha(self.inventory)
        result = self.freeze()
        self.assertIn('history_scan_has_unresolved_gaps', result['rejection_reasons'])
        self.probe.assert_not_called()
        self.assertEqual(result['matches'], [])

    def test_exposed_source_and_duplicate_ids_rejected(self):
        self.history['exposure']['9050000000'] = ['historical_reference']
        self.save_history()
        self.request['inventory_sha256'] = self.sha(self.inventory)
        self.request['matches'][1]['match_id'] = '9050000000'
        result = self.freeze()
        self.assertIn('duplicate_match_id', result['rejection_reasons'])
        self.assertIn('historically_exposed:9050000000', result['rejection_reasons'])
        self.probe.assert_not_called()

    def test_missing_holdout_and_protocol_drift_rejected(self):
        self.request['matches'][1]['role'] = 'development'
        self.protocol.write_text('changed', encoding='utf-8')
        result = self.freeze()
        self.assertIn('require_one_heldout_and_remaining_development', result['rejection_reasons'])
        self.assertIn('protocol_fingerprint_mismatch', result['rejection_reasons'])

    def test_wrong_source_hash_rejected(self):
        self.request['matches'][0]['replay_sha256'] = 'a' * 64
        result = self.freeze()
        self.assertIn('source_fingerprint_mismatch:9050000000', result['rejection_reasons'])
        self.assertEqual(result['matches'], [])

    def test_wrong_footer_never_admitted(self):
        self.probe.side_effect = None
        self.probe.return_value = {'status': 'rejected_metadata_probe', 'footer': None}
        self.assertEqual(self.freeze()['status'], 'rejected')

    def test_review_time_must_include_utc_offset(self):
        self.request['history_review']['reviewed_at_utc'] = '2026-10-23T12:00:00'
        self.assertIn('history_review_time_must_be_utc', self.freeze()['rejection_reasons'])


if __name__ == '__main__':
    unittest.main()
