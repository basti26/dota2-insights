"""Independent sampling and listener workflow with generated PCM only."""
import io
import json
import unittest
from unittest.mock import Mock, patch
import wave

from dota2_insights import audio_blind_review as blind_review, audio_listening_review as listening
from dota2_insights.audio_blind_review import prepare_blind_match
from dota2_insights.audio_listening_review import BLIND_CONTEXT_POLICY, import_listening_notes, sha


def wave_bytes(sample):
    body = io.BytesIO()
    with wave.open(body, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(sample.to_bytes(2, 'little') * 32000)
    return body.getvalue()


class BlindAudioReviewTests(unittest.TestCase):
    def setUp(self):
        from test_audio_match_analysis import AudioMatchAnalysisTests
        self.case = AudioMatchAnalysisTests()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.output = self.case.base.root / 'diagnostics/blind-audio'

    def test_cli_samples_pcm_without_reading_reference_semantics_or_predictions(self):
        from dota2_insights import audio_match_handoff
        from tools import review_audio_listening

        original = audio_match_handoff._rows

        def expect_deferred(folder, spec, limit, *, decode=True):
            if spec['file'] == 'reference-gsi.jsonl':
                self.assertFalse(decode)
            return original(folder, spec, limit, decode=decode)

        c = self.case
        native = c.native
        argv = ['review_audio_listening', 'sample-blind', '--audio-match-package', str(native.folder),
                '--expected-package-id', native.package_id, '--output', str(self.output), '--seed', '42']
        with patch.object(audio_match_handoff, '_rows', side_effect=expect_deferred), \
                patch.object(review_audio_listening, 'ROOT', c.base.root), patch('sys.argv', argv):
            review_audio_listening.main()
        review = json.loads((self.output / 'review.json').read_bytes())
        self.assertEqual(review['sampling']['eligible_windows'], 1)
        self.assertEqual(review['clips'][0]['frames'], 32000)
        self.assertEqual(len(review['clips'][0]['sources']), 2)
        self.assertFalse(c.output.exists())

        page = self.output / 'listening-page'
        manifest_raw = (page / 'manifest.json').read_bytes()
        manifest = json.loads(manifest_raw)
        self.assertEqual(manifest['context_policy'], BLIND_CONTEXT_POLICY)
        self.assertNotIn('prediction_sha256', manifest)
        self.assertNotIn('match_id', manifest)
        self.assertNotIn('synthetic_skill', (page / 'index.html').read_text(encoding='utf-8'))
        clip = manifest['clips'][0]
        self.assertEqual(sha((page / clip['file']).read_bytes()), review['clips'][0]['clip_sha256'])
        labels = self.output / 'notes-test.json'
        labels.write_text(json.dumps({'kind': 'audio_listening_notes', 'schema_version': 1,
            'manifest_sha256': sha(manifest_raw), 'context_policy': BLIND_CONTEXT_POLICY,
            'notes': [{'clip_id': clip['clip_id'], 'clip_sha256': clip['sha256'], 'verdict': 'uncertain',
                       'phase': 'unknown', 'heard': '', 'comment': 'generated fixture', 'played': True}]}))
        receipt, counts = import_listening_notes(page, labels)
        self.assertEqual(counts, {'uncertain': 1})
        result = json.loads((receipt / 'notes.json').read_bytes())
        self.assertFalse(result['accuracy_verified'])
        self.assertTrue(result['clips_verified_before_and_after'])

        read_json = listening.read_json
        def changed_clip(path, *args):
            result = read_json(path, *args)
            if path == labels:
                (page / clip['file']).write_bytes(wave_bytes(3))
            return result
        with patch.object(listening, 'read_json', side_effect=changed_clip):
            with self.assertRaisesRegex(ValueError, 'clip_size_or_hash_mismatch'):
                import_listening_notes(page, labels)
        self.assertEqual(len(list((page / 'notes').iterdir())), 1)

    def test_sampler_skips_identical_bytes_then_admits_next_window(self):
        c = self.case
        a, b = wave_bytes(1), wave_bytes(2)
        windows = [('a' * 32, offset) for offset in (0, 32000, 64000)]
        sampler = Mock()
        sampler.sample.return_value = windows
        extracted = [({'body': body, 'sources': []}, None) for body in (a, a, b)]
        with patch.object(blind_review, '_windows', return_value=(windows, 0)), \
             patch.object(blind_review.random, 'Random', return_value=sampler), \
             patch.object(blind_review, 'extract', side_effect=extracted):
            result = prepare_blind_match(c.native.folder, self.output, package_id=c.native.package_id, seed=42, total=2)
        self.assertEqual([row['clip_sha256'] for row in result['clips']], [sha(a), sha(b)])
        self.assertEqual(result['sampling']['duplicate_wav_windows_skipped'], 1)
        self.assertEqual(result['sampling']['inspected_windows'], 3)

    def test_blind_page_rejects_overlaps_and_identical_audio_under_new_names(self):
        self.output.mkdir(parents=True)
        a, b = wave_bytes(1), wave_bytes(2)
        first = {'clip': 'one.wav', 'clip_sha256': sha(a), 'take_id': 'a' * 32,
                 'from_frame': 0, 'frames': 32000}
        source = self.output / 'review.json'
        (self.output / 'one.wav').write_bytes(a)
        for second_start, body, reason in ((16000, b, 'overlapping_blind_listening_windows'),
                                           (32000, a, 'duplicate_blind_listening_audio')):
            second = {**first, 'clip': 'two.wav', 'clip_sha256': sha(body), 'from_frame': second_start}
            (self.output / 'two.wav').write_bytes(body)
            source.write_text(json.dumps({'schema_version': 1, 'purpose': listening.BLIND_PURPOSE,
                'target': listening.BLIND_TARGET, 'source_verified_before_and_after': True,
                'source_package_id': 'f' * 64, 'clips': [first, second]}))
            destination = self.output / 'page'
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                listening.build_listening_page(source, destination)
            self.assertFalse(destination.exists())

    def test_paused_segments_are_never_combined_to_fill_a_window(self):
        c = self.case
        c.rows[0]['ended_by'] = 'field_paused'
        c.write_native()
        with self.assertRaisesRegex(ValueError, 'no_full_windows'):
            prepare_blind_match(c.native.folder, self.output, package_id=c.native.package_id, seed=42)
        self.assertFalse(self.output.exists())
