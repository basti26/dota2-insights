"""Known synthetic QPC/CSV records, independent of Foresight imports/ETW."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights import performance_handoff as module
from dota2_insights.performance_review import BINARY_SHA256, VERSION


class PerformanceHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.run_id = 'c'*32
        self.capture_folder = Path(temporary.name)/('run_'+self.run_id)
        self.folder = self.capture_folder/'closed-match'
        self.folder.mkdir(parents=True)
        self.bound = {'match_id': '12345', 'game_session_id': 'game_'+'a'*32,
                      'record_session_id': 'run_'+'b'*32}
        self.capture = {'schema_version': 1, 'kind': 'presentmon_capture', 'run_id': self.run_id,
            'source_kind': 'windows', 'complete': True, 'shutdown_complete': True,
            'error': None, 'exitcode': 0, 'etw_loss_warning': False, 'presentmon_version': VERSION,
            'binary_sha256': BINARY_SHA256, 'metric_mode': 'v1', 'clock': 'raw_QPC_not_UTC',
            'application_filter': 'dota2.exe', 'context_format': 'configuration_refs_v1', 'qpc_frequency_hz': 1000}
        self.rows = [{**self.bound, 'run_id': self.run_id, 'game_provider_age_s': 1,
            'game_state': 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS', 'foreground_dota': True,
            'monotonic_s': n, 'qpc_ticks': n*1000} for n in (2,3,4)]
        (self.capture_folder/'frames.csv').write_text(
            'Application,ProcessID,SwapChainAddress,QPCTime,MsBetweenPresents,MsUntilDisplayed,MsBetweenDisplayChange,Dropped\n'
            'dota2.exe,42,0x1,2010,10,0,10,0\n'
            'dota2.exe,42,0x1,2020,10,0,10,0\n'
            'dota2.exe,42,0x1,3000,980,0,980,0\n'
            'dota2.exe,42,0x1,4000,1000,0,1000,0\n')
        (self.capture_folder/'collector.log').write_bytes(b'')
        self.document = {'schema_version': 1, 'kind': 'foresight_performance_match_handoff',
            'capture_run_id': self.run_id, 'scope': self.bound,
            'end_state': 'DOTA_GAMERULES_STATE_POST_GAME', 'collector_closed_before_publication': True,
            'coverage': 'recorded_capture_only_not_whole_match',
            'gameplay_metrics': 'require_fresh_foreground_QPC_windows_from_context',
            'closed_at_utc': '2026-10-27T00:00:01Z', 'end_provider_at_utc': '2026-10-27T00:00:00Z'}
        self.write()

    def seal(self):
        self.document['sources'] = {name: module.digest(self.capture_folder/name, limit)
                                    for name, limit in module.LIMITS.items()}
        raw = (json.dumps(self.document)+'\n').encode()
        self.package_id = hashlib.sha256(raw).hexdigest()
        (self.folder/'record-handoff.json').write_bytes(raw)
        (self.folder/'COMPLETE').write_bytes((self.package_id+'\n').encode('ascii'))

    def write(self):
        (self.capture_folder/'capture.json').write_text(json.dumps(self.capture))
        (self.capture_folder/'context.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in self.rows))
        self.seal()

    def test_known_qpc_intervals_use_existing_independent_metrics(self):
        report = module.review(self.folder, self.package_id)
        game = report['matches'][0]
        self.assertEqual(game['qpc_windows'], [[2000, 4000]])
        self.assertEqual(game['covered_game_seconds'], 2)
        stream = game['streams']['streams'][0]
        self.assertEqual(stream['displayed_frames']['average_fps'], 2)
        self.assertEqual(stream['displayed_frames']['intervals_over_100_ms'], 2)
        self.assertFalse(report['observation_shutdown_required'])
        self.assertFalse(game['whole_match_coverage_verified'])
        self.assertFalse(game['assistant_causality_verified'])

    def test_holdout_and_missing_complete_are_rejected_before_source_body(self):
        for heldout in (False, True):
            if heldout:
                self.bound['match_id'] = '9056629627'  # Public lock ID, no match body.
                self.write()
            else:
                (self.folder/'COMPLETE').unlink()
            with self.subTest(heldout=heldout), patch.object(module, 'digest', side_effect=AssertionError('body opened')):
                with self.assertRaises((OSError, ValueError)):
                    module.review(self.folder)

    def test_changed_package_or_source_is_refused(self):
        with self.assertRaises(ValueError):
            module.review(self.folder, '0'*64)
        (self.capture_folder/'frames.csv').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            module.review(self.folder, self.package_id)

    def test_mutation_during_metric_parse_cannot_return_success(self):
        original = module.read_presentmon_csv
        def mutate(path, **kwargs):
            result = original(path, **kwargs)
            with path.open('ab') as handle:
                handle.write(b'changed\n')
            return result
        with patch.object(module, 'read_presentmon_csv', side_effect=mutate), self.assertRaises(ValueError):
            module.review(self.folder)

    def test_collector_log_loss_is_rejected_even_when_manifest_denies_it(self):
        (self.capture_folder/'collector.log').write_text('2 ETW buffers were lost\n')
        self.seal()
        with self.assertRaisesRegex(ValueError, 'diagnostic'):
            module.review(self.folder)

    def test_false_or_missing_close_health_cannot_enter_metrics(self):
        for change in ({'shutdown_complete': False}, {'complete': False}, {'exitcode': True},
                       {'etw_loss_warning': True}, {'error': 'shutdown_timeout'},
                       {'binary_sha256': '0'*64}, {'run_id': 'd'*32}):
            with self.subTest(change=change):
                before = dict(self.capture)
                self.capture.update(change)
                self.write()
                with self.assertRaises(ValueError):
                    module.review(self.folder)
                self.capture = before

    def test_no_fresh_foreground_context_is_unknown_not_zero_fps(self):
        for changes in ({'foreground_dota': False}, {'game_provider_age_s': 6},
                        {'record_session_id': 'run_'+'d'*32}):
            for row in self.rows:
                row.update(changes)
            self.write()
            game = module.review(self.folder)['matches'][0]
            self.assertEqual(game['status'], 'insufficient_game_context')
            self.assertIsNone(game['streams'])

    def test_context_run_and_unfinished_tail_are_rejected(self):
        self.rows[0]['run_id'] = 'e'*32
        self.write()
        with self.assertRaisesRegex(ValueError, 'run_conflict'):
            module.review(self.folder)
        self.rows[0]['run_id'] = self.run_id
        self.write()
        p = self.capture_folder/'context.jsonl'
        p.write_bytes(p.read_bytes().rstrip(b'\n'))
        self.seal()
        with self.assertRaisesRegex(ValueError, 'tail'):
            module.review(self.folder)

    def test_null_match_id_keeps_explicit_local_sessions(self):
        self.bound['match_id'] = None
        for row in self.rows:
            row['match_id'] = None
        self.write()
        game = module.review(self.folder)['matches'][0]
        self.assertIsNone(game['match_id'])
        self.assertEqual(game['covered_game_seconds'], 2)

    def test_scope_contract_budgets_and_marker_exactness(self):
        self.document['sources']['frames.csv']['bytes'] = module.LIMITS['frames.csv']+1
        raw = json.dumps(self.document).encode()
        (self.folder/'record-handoff.json').write_bytes(raw)
        (self.folder/'COMPLETE').write_bytes((hashlib.sha256(raw).hexdigest()+'\n').encode('ascii'))
        with patch.object(module, 'digest', side_effect=AssertionError('body opened')):
            # capture.json may precede frames.csv, so enforce bounds directly on the first source.
            self.document['sources']['capture.json']['bytes'] = module.LIMITS['capture.json']+1
            raw = json.dumps(self.document).encode()
            (self.folder/'record-handoff.json').write_bytes(raw)
            (self.folder/'COMPLETE').write_bytes((hashlib.sha256(raw).hexdigest()+'\n').encode('ascii'))
            with self.assertRaises(ValueError):
                module.review(self.folder)
        self.write()
        (self.folder/'COMPLETE').write_text(self.package_id+'\n\n')
        with self.assertRaises(ValueError):
            module.review(self.folder)
