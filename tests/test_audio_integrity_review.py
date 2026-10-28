"""Generated PCM only; no recorder, listening label, internet or real match."""
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import wave

import numpy as np

from dota2_insights.audio_integrity_review import review, waveform


class AudioIntegrityTests(unittest.TestCase):
    def setUp(self):
        from test_helper_handoff import HandoffTests
        self.fixture = f = HandoffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.rid = 'run_' + 'e' * 32
        self.folder = f.history / 'match-audio' / self.rid
        self.folder.mkdir(parents=True)
        f.document['recorders']['audio_samples'] = {'run_id': self.rid, 'shutdown_complete': True}
        f.write(f.registry, f.document)
        self.rows = []
        for i, value in enumerate((0, 32767)):
            raw = self.wav(value)
            filename = f'audio-{i+1:04d}.wav'
            (self.folder / filename).write_bytes(raw)
            self.rows.append({'schema_version': 1, 'audio_file': filename, 'sha256': hashlib.sha256(raw).hexdigest(),
                'audio_seconds': .05, 'sample_rate': 16000, 'channels': 1, 'sample_width_bytes': 2,
                'take_audio_start_offset_s': i * .05, 'take_label': {'take_id': 'f' * 32},
                'skill_label': None, 'recognition_enabled': False,
                'timing_basis': 'host_delivery_and_consume_boundaries_not_exact_audio_sample_clock',
                'match_association': {'match_id': '12345'}})
        self.summary = {'run_id': self.rid, 'kind': 'match_audio_record_only', 'recognition_enabled': False,
            'closed_at_utc': '2026-10-25T10:00:00Z', 'complete': True, 'shutdown_complete': True,
            'running': False, 'worker_alive': False, 'finalizing': False, 'error': None,
            'segments_written': 2, 'audio_bytes': 3288, 'audio_seconds': .1,
            'audio_source': 'system_mix'}
        self.write()

    @staticmethod
    def wav(value):
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as out:
            out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000)
            out.writeframes(np.full(800, value, dtype='<i2').tobytes())
        return stream.getvalue()

    def write(self):
        self.fixture.write(self.folder / 'summary.json', self.summary)
        (self.folder / 'segments.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows), encoding='utf-8')

    def inspect(self):
        return self.fixture.inspect()

    def test_waveforms_and_totals_verified_without_audibility_claim(self):
        result = review(self.inspect(), self.fixture.roots)['captures'][0]
        self.assertEqual(result['segments_verified'], 2)
        self.assertEqual(result['exact_silence_segments'], 1)
        self.assertEqual(result['low_level_segments'], 1)
        self.assertEqual(result['samples_at_int16_limit'], 800)
        self.assertFalse(result['skill_audibility_verified'])
        self.assertFalse(result['sample_exact_wall_clock_verified'])
        self.assertIn('writer_snapshot', result['segments'][0]['association_basis'])

    def test_missing_or_mutated_waveform_refused(self):
        package = self.inspect()
        (self.folder / 'audio-0002.wav').write_bytes(self.wav(0))
        with self.assertRaisesRegex(ValueError, 'digest_mismatch'):
            review(package, self.fixture.roots)
        (self.folder / 'audio-0002.wav').unlink()
        with self.assertRaises(OSError):
            review(package, self.fixture.roots)

    def test_optional_runtime_scope_preserved_and_missing_remains_unknown(self):
        scope = {'match_id': '12345', 'game_session_id': 'game_'+'a'*32,
                 'record_session_id': 'run_'+'b'*32}
        self.rows[0]['recorded_source_scope'] = scope
        self.write()
        capture = review(self.inspect(), self.fixture.roots)['captures'][0]
        self.assertEqual(capture['segments_with_explicit_runtime_scope'], 1)
        self.assertEqual(capture['segments'][0]['recorded_source_scope'], scope)
        self.assertIsNone(capture['segments'][1]['recorded_source_scope'])
        self.assertFalse(capture['native_gsi_source_join_verified'])

    def test_runtime_scope_conflict_is_rejected_before_waveform_body(self):
        self.rows[0]['recorded_source_scope'] = {'match_id': '54321',
            'game_session_id': 'game_'+'a'*32, 'record_session_id': 'run_'+'b'*32}
        self.write()
        package = self.inspect()
        with patch('dota2_insights.audio_integrity_review.waveform', side_effect=AssertionError('body too early')):
            with self.assertRaisesRegex(ValueError, 'source_match_conflict'):
                review(package, self.fixture.roots)

    def test_overlap_duplicate_path_traversal_and_duration_refused(self):
        for change in ({'take_audio_start_offset_s': .01}, {'audio_file': 'audio-0001.wav'},
                       {'audio_file': '../outside.wav'}, {'audio_seconds': .1}):
            old = copy.deepcopy(self.rows)
            self.rows[1].update(change)
            self.write()
            with self.assertRaises(ValueError):
                review(self.inspect(), self.fixture.roots)
            self.rows = old

    def test_summary_totals_not_used_as_waveform_evidence(self):
        self.summary['audio_bytes'] += 1
        self.write()
        with self.assertRaisesRegex(ValueError, 'summary_totals_conflict'):
            review(self.inspect(), self.fixture.roots)

    def test_closed_package_pipeline_includes_audio_and_explicit_missing_performance(self):
        from dota2_insights.helper_handoff import sync
        from tools.review_closed_record_content import generate
        f = self.fixture
        data = f.insights / 'data'
        data.mkdir()
        raw = b'{}'
        (data / 'items.json').write_bytes(raw)
        (data / 'items.meta.json').write_text(json.dumps({'cache_sha256': hashlib.sha256(raw).hexdigest()}))
        sync(f.insights, f.roots, quiet_seconds=0)
        result = generate(f.insights, roots=f.roots)
        self.assertEqual(result['audio_segments_verified'], 2)
        self.assertEqual(result['performance_game_scopes_reviewed'], 0)
        path = Path(result['report'])
        doc = json.loads((path.parent / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(doc['audio_integrity_reports'][0]['captures'][0]['segments_verified'], 2)
        self.assertEqual(doc['performance_reports'][0]['unavailable'][0]['reason'], 'fps_not_recorded')
        self.assertIn('录音技术复核', path.read_text(encoding='utf-8'))

    def test_pipeline_does_not_publish_report_on_failed_waveform(self):
        from dota2_insights.helper_handoff import sync
        from tools.review_closed_record_content import generate
        f = self.fixture
        data = f.insights / 'data'
        data.mkdir()
        raw = b'{}'
        (data / 'items.json').write_bytes(raw)
        (data / 'items.meta.json').write_text(json.dumps({'cache_sha256': hashlib.sha256(raw).hexdigest()}))
        sync(f.insights, f.roots, quiet_seconds=0)
        (self.folder / 'audio-0001.wav').write_bytes(self.wav(10))
        with self.assertRaisesRegex(ValueError, 'digest_mismatch'):
            generate(f.insights, roots=f.roots)
        self.assertFalse((f.insights / 'diagnostics/record-content').exists())

    def test_unclosed_audio_is_unavailable_not_zero_verified_capture(self):
        self.summary.update(running=True, worker_alive=True, shutdown_complete=False)
        self.write()
        result = review(self.inspect(), self.fixture.roots)
        self.assertEqual(result['captures'], [])
        self.assertEqual(result['unavailable'], [{'reason': 'audio_not_closed_verified'}])

    def test_waveform_change_during_review_is_refused(self):
        package = self.inspect()
        from dota2_insights import audio_integrity_review as module
        original = module.waveform
        def change(raw, expected):
            result = original(raw, expected)
            if expected['audio_file'] == 'audio-0002.wav':
                (self.folder / 'audio-0001.wav').write_bytes(self.wav(20))
            return result
        with patch.object(module, 'waveform', change):
            with self.assertRaisesRegex(ValueError, 'changed_during_review'):
                review(package, self.fixture.roots)

    def test_locked_identity_checked_before_waveform_read(self):
        self.rows[0]['match_association']['match_id'] = '9056629627'
        self.write()
        with patch('dota2_insights.audio_integrity_review.waveform', side_effect=AssertionError('must not decode')):
            with self.assertRaises(ValueError):
                review(self.inspect(), self.fixture.roots)

    def test_truncated_pcm_and_empty_data_refused_even_with_matching_hash(self):
        for raw in (self.wav(0)[:-2], self.wav(0)[:44]):
            expected = {**self.rows[0], 'sha256': hashlib.sha256(raw).hexdigest()}
            with self.assertRaisesRegex(ValueError, 'truncated'):
                waveform(raw, expected)


if __name__ == '__main__':
    unittest.main()
