"""Guarded inference on generated short PCM, no game, real recording or network."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from dota2_insights.closed_audio_analysis import open_analysis_inputs, bank_fingerprint, seal_receipt


class ClosedAudioAnalysisTests(unittest.TestCase):
    def setUp(self):
        from test_audio_integrity_review import AudioIntegrityTests
        self.audio = AudioIntegrityTests()
        self.audio.setUp()
        self.addCleanup(self.audio.doCleanups)
        self.fixture = self.audio.fixture
        self.root = self.fixture.insights
        config = self.root / 'config'
        config.mkdir()
        (config/'history.local.json').write_text(json.dumps({'schema_version': 1, 'read_only': True,
            'roots': {'helper_history': str(self.fixture.history)}}), encoding='utf-8')
        from dota2_insights.ability_sound_matcher import descriptor, WINDOW, VERSION
        self.bank = self.root / 'bank'
        self.bank.mkdir()
        features = descriptor(np.zeros(WINDOW, dtype=np.float32))[None, :].astype(np.float32)
        np.save(self.bank / 'features.npy', features)
        self.meta = {'feature_version': VERSION, 'templates': [{'skill': 'synthetic_skill'}],
                     'features_sha256': hashlib.sha256((self.bank/'features.npy').read_bytes()).hexdigest(),
                     'source': 'generated_test_fixture_only'}
        (self.bank/'bank.json').write_text(json.dumps(self.meta), encoding='utf-8')
        folder = self.root / 'dota2_insights'
        folder.mkdir()
        from dota2_insights import ability_sound_matcher
        (folder/'ability_sound_matcher.py').write_bytes(Path(ability_sound_matcher.__file__).read_bytes())
        self.output = self.root / 'diagnostics/audio-test'
        self.args = SimpleNamespace(run=self.audio.folder, bank=self.bank, output=self.output,
                                    engine='baseline', waveform_check=False)

    def execute(self, inputs):
        from tools import analyze_match_audio
        with patch.object(analyze_match_audio, 'ROOT', self.root):
            analyze_match_audio.analyze(self.args, inputs)

    def test_predictions_frozen_before_reference_and_final_receipt_after_context(self):
        reference = self.audio.folder/'reference-gsi.jsonl'
        reference.write_text(json.dumps({'take_id': 'f'*32, 'audio_offset_s': .01,
            'context': {'match_id': '12345', 'abilities': []}})+'\n', encoding='utf-8')
        package = self.audio.inspect()
        before = bank_fingerprint(self.bank)
        with open_analysis_inputs(package, self.fixture.roots, self.audio.folder) as inputs:
            original = inputs.reference_rows
            def reference_after_prediction():
                self.assertTrue((self.output/'prediction-manifest.json').is_file())
                self.assertFalse((self.output/'analysis-receipt.json').exists())
                return original()
            inputs.reference_rows = reference_after_prediction
            self.execute(inputs)
            with self.assertRaisesRegex(ValueError, 'after_verification_required'):
                seal_receipt(self.output, inputs, before, before, package_id='a'*64)
        result = seal_receipt(self.output, inputs, before, bank_fingerprint(self.bank), package_id='a'*64)
        self.assertTrue(result['complete'])
        self.assertFalse(result['accuracy_verified'])
        self.assertFalse(result['gsi_used_for_inference'])
        self.assertTrue((self.output/'analysis-receipt.json').exists())

    def test_unclosed_capture_is_not_admitted(self):
        self.audio.summary.update(running=True, worker_alive=True, shutdown_complete=False)
        self.audio.write()
        with self.assertRaisesRegex(ValueError, 'one_admitted_capture'):
            with open_analysis_inputs(self.audio.inspect(), self.fixture.roots, self.audio.folder):
                self.fail('must not run inference')

    def make_real_signal(self):
        import wave
        from dota2_insights.ability_sound_matcher import descriptor, mono8k, WINDOW
        signal = np.random.default_rng(42).integers(-12000, 12000, 32000, dtype=np.int16)
        for i, row in enumerate(self.audio.rows):
            row.update(audio_seconds=1., take_audio_start_offset_s=float(i), ended_by='five_second_segment')
            path = self.audio.folder / row['audio_file']
            pcm = signal[i*16000:(i+1)*16000].astype('<i2')
            with wave.open(str(path), 'wb') as out:
                out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(pcm.tobytes())
            row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.audio.summary.update(audio_seconds=2., audio_bytes=64088)
        self.audio.write()
        blocks = [mono8k(signal[i:i+1600].astype(np.float32)/32768, 16000)
                  for i in range(0, 19200, 1600)]
        np.save(self.bank/'features.npy', descriptor(np.concatenate(blocks)[:WINDOW])[None, :])
        self.meta['features_sha256'] = hashlib.sha256((self.bank/'features.npy').read_bytes()).hexdigest()
        self.meta['templates'][0].update(hero_id='npc_dota_hero_synthetic', template_id='synthetic_tone')
        (self.bank/'bank.json').write_text(json.dumps(self.meta), encoding='utf-8')

    def test_real_sized_generated_signal_runs_classifier_and_seals_candidates(self):
        self.make_real_signal()
        before = bank_fingerprint(self.bank)
        with open_analysis_inputs(self.audio.inspect(), self.fixture.roots, self.audio.folder) as inputs:
            self.execute(inputs)
        receipt = seal_receipt(self.output, inputs, before, bank_fingerprint(self.bank), package_id='a'*64)
        frozen = json.loads((self.output/'prediction-manifest.json').read_text())
        self.assertGreaterEqual(frozen['candidate_skills'].get('synthetic_skill', 0), 1)
        self.assertGreater((self.output/'scans.jsonl').stat().st_size, 0)
        self.assertFalse(receipt['accuracy_verified'])

    def test_source_change_prevents_after_verification_and_receipt(self):
        with self.assertRaisesRegex(ValueError, 'digest_mismatch'):
            with open_analysis_inputs(self.audio.inspect(), self.fixture.roots, self.audio.folder) as inputs:
                self.execute(inputs)
                (self.audio.folder/'audio-0001.wav').write_bytes(self.audio.wav(10))
        self.assertFalse(inputs.after_verified)
        self.assertFalse((self.output/'analysis-receipt.json').exists())

    def test_changed_bank_or_frozen_predictions_cannot_seal(self):
        before = bank_fingerprint(self.bank)
        with open_analysis_inputs(self.audio.inspect(), self.fixture.roots, self.audio.folder) as inputs:
            self.execute(inputs)
        with self.assertRaisesRegex(ValueError, 'bank_changed'):
            seal_receipt(self.output, inputs, before, {'bank.json': 'wrong'}, package_id='a'*64)
        (self.output/'predictions.jsonl').write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'frozen_sources_conflict'):
            seal_receipt(self.output, inputs, before, before, package_id='a'*64)
        self.assertFalse((self.output/'analysis-receipt.json').exists())

    def test_failed_receipt_publish_leaves_no_partial_success_marker(self):
        before = bank_fingerprint(self.bank)
        with open_analysis_inputs(self.audio.inspect(), self.fixture.roots, self.audio.folder) as inputs:
            self.execute(inputs)
        with patch('dota2_insights.minimap.offline_io.os.link', side_effect=OSError('generated publish failure')):
            with self.assertRaises(OSError):
                seal_receipt(self.output, inputs, before, before, package_id='a'*64)
        self.assertFalse((self.output/'analysis-receipt.json').exists())
        self.assertEqual(list(self.output.glob('*.part')), [])

    def test_reference_foreign_identity_is_refused_after_prediction_freeze(self):
        (self.audio.folder/'reference-gsi.jsonl').write_text(json.dumps({'take_id': 'f'*32, 'audio_offset_s': .01,
            'context': {'match_id': '54321', 'abilities': []}})+'\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'reference_foreign_match'):
            with open_analysis_inputs(self.audio.inspect(), self.fixture.roots, self.audio.folder) as inputs:
                self.execute(inputs)
        self.assertTrue((self.output/'prediction-manifest.json').exists())
        self.assertFalse((self.output/'analysis-receipt.json').exists())


if __name__ == '__main__':
    unittest.main()
