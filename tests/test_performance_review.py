"""Known synthetic QPC intervals and source lineage, no ETW or game startup."""
import hashlib
import json
from pathlib import Path
import unittest

from dota2_insights.performance_review import BINARY_SHA256, ContextDecoder, game_windows, review


class PerformanceReviewTests(unittest.TestCase):
    def setUp(self):
        from test_helper_handoff import HandoffTests
        self.fixture = HandoffTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.run = 'e' * 32
        self.folder = f.history / 'game-frames' / ('run_' + self.run)
        self.folder.mkdir(parents=True)
        f.document['recorders']['presentmon'] = {'run_id':self.run, 'shutdown_complete':True}
        f.write(f.registry, f.document)
        self.manifest = {'schema_version':1, 'kind':'presentmon_capture', 'run_id':self.run,
            'presentmon_version':'2.6.0', 'binary_sha256':BINARY_SHA256, 'metric_mode':'v1',
            'clock':'raw_QPC_not_UTC', 'source_kind':'windows', 'application_filter':'dota2.exe',
            'complete':True, 'shutdown_complete':True, 'status':'closed', 'error':None,
            'etw_loss_warning':False, 'exitcode':0, 'context_format':'configuration_refs_v1',
            'qpc_frequency_hz':1000, 'ended_at_utc':'2026-10-25T10:00:00Z', 'ended_reason':'user_stop'}
        self.rows = [self.context(i) for i in (2,3,4)]
        (self.folder / 'frames.csv').write_text('Application,ProcessID,SwapChainAddress,QPCTime,MsBetweenPresents,MsUntilDisplayed,MsBetweenDisplayChange,Dropped\n'
            'dota2.exe,42,0x1,2010,10,0,10,0\n'
            'dota2.exe,42,0x1,2020,10,0,10,0\n'
            'dota2.exe,42,0x1,3000,980,0,980,0\n'
            'dota2.exe,42,0x1,4000,1000,0,1000,0\n')
        self.write()

    def context(self, second):
        f = self.fixture
        return {'run_id':self.run, 'match_id':'12345', 'game_session_id':f.gid, 'record_session_id':f.sid,
            'game_state':'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS', 'foreground_dota':True,
            'game_provider_age_s':1, 'monotonic_s':second, 'qpc_ticks':second * 1000}

    def write(self):
        self.fixture.write(self.folder / 'capture.json', self.manifest)
        (self.folder / 'context.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in self.rows))

    def scope(self):
        return {'match_id':'12345', 'game_session_id':self.fixture.gid,
                'record_session_id':self.fixture.sid, 'frequency':1000}

    def test_known_frame_intervals_are_scoped_and_displayed_separate(self):
        result = review(self.fixture.inspect(), self.fixture.roots)
        game = result['matches'][0]
        self.assertEqual(game['qpc_windows'], [[2000,4000]])
        self.assertEqual(game['covered_game_seconds'], 2)
        stream = game['streams']['streams'][0]
        self.assertEqual(stream['displayed_frames']['average_fps'], 2)
        self.assertEqual(stream['displayed_frames']['intervals_over_100_ms'], 2)
        self.assertEqual(stream['displayed_frames']['p99_frame_ms'], 1000)
        self.assertFalse(game['assistant_causality_verified'])

    def test_unfocused_stale_cross_match_and_gap_do_not_bridge(self):
        for change in ({'foreground_dota':False}, {'game_provider_age_s':6}, {'match_id':'12346'}):
            rows = [self.context(2), {**self.context(3), **change}, self.context(4)]
            self.assertEqual(game_windows(rows, **self.scope()), [])
        self.assertEqual(game_windows([self.context(2), self.context(6)], **self.scope()), [])

    def test_clock_regression_or_wrong_frequency_are_rejected(self):
        with self.assertRaises(ValueError):
            game_windows([self.context(3), self.context(2)], **self.scope())
        self.assertEqual(game_windows([self.context(2), self.context(3)], **{**self.scope(),'frequency':10000}), [])

    def test_missing_game_context_stays_unknown_not_zero_fps(self):
        for row in self.rows:
            row['foreground_dota'] = False
        self.write()
        result = review(self.fixture.inspect(), self.fixture.roots)['matches'][0]
        self.assertEqual(result['status'], 'insufficient_game_context')
        self.assertIsNone(result['streams'])

    def test_capture_limits_partial_and_etw_loss_never_hide(self):
        self.manifest.update(complete=False, ended_reason='byte_limit')
        self.write()
        result = review(self.fixture.inspect(), self.fixture.roots)['matches'][0]
        self.assertEqual(result['status'], 'available_partial_capture')
        self.manifest['etw_loss_warning'] = True
        self.write()
        with self.assertRaises(ValueError):
            review(self.fixture.inspect(), self.fixture.roots)

    def test_changed_source_after_admission_is_refused(self):
        package = self.fixture.inspect()
        with (self.folder / 'frames.csv').open('ab') as stream:
            stream.write(b'changed\n')
        with self.assertRaises(ValueError):
            review(package, self.fixture.roots)

    def test_qpc_boundary_does_not_include_an_interval_started_outside(self):
        with (self.folder / 'frames.csv').open('a') as stream:
            stream.write('dota2.exe,42,0x1,2005,100,0,100,0\n')
        game = review(self.fixture.inspect(), self.fixture.roots)['matches'][0]
        stream = game['streams']['streams'][0]
        self.assertEqual(stream['displayed_frames']['interval_count'], 4)
        self.assertEqual(stream['boundary_excluded_metrics']['msbetweenpresents'], 1)

    def test_configuration_reference_decode_requires_prior_matching_definition(self):
        decoder = ContextDecoder()
        values = {'ocr_runtime':None, 'draft_ocr_runtime':None, 'cpu_scheduling':None}
        key = hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(',',':')).encode()).hexdigest()
        with self.assertRaises(ValueError):
            decoder.decode({'configuration_ref':key})
        self.assertEqual(decoder.decode({'configuration_ref':key, **values}), values)
        self.assertEqual(decoder.decode({'configuration_ref':key}), values)
        with self.assertRaises(ValueError):
            decoder.decode({'configuration_ref':key, **values})
