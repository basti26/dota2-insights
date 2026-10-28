"""Diverse generated candidates and PCM clips; no human verdicts or real recording."""
from collections import Counter
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import wave

from dota2_insights.audio_candidate_review import select, prepare, extract
from dota2_insights.closed_audio_analysis import open_analysis_inputs, bank_fingerprint, seal_receipt


class CandidateSelectionTests(unittest.TestCase):
    def row(self, skill, at):
        return {'skill': skill, 'take_id': 'a'*32, 'window_start_s': float(at), 'window_end_s': float(at)+1.2}

    def test_rare_skill_not_starved_by_first_fifty_common_candidates(self):
        rows = [self.row('common_skill', i) for i in range(200)] + [self.row('rare_skill', 50)]
        selected, policy = select(rows)
        counts = Counter(r['prediction']['skill'] for r in selected)
        self.assertEqual(counts, {'common_skill': 3, 'rare_skill': 1})
        times = {r['prediction']['window_start_s'] for r in selected if r['prediction']['skill'] == 'common_skill'}
        self.assertEqual(times, {0., 100., 199.})
        self.assertEqual(policy['omitted_skills'], [])

    def test_duplicate_windows_deduplicate_and_budget_is_explicit(self):
        row = self.row('same', 0)
        selected, policy = select([row, copy.deepcopy(row), self.row('other', 1)], total=1)
        self.assertEqual(len(selected), 1)
        self.assertEqual(policy['candidate_skill_counts']['same'], 1)
        self.assertEqual(len(policy['omitted_skills']), 1)

    def test_bad_identity_and_windows_refused(self):
        for change in ({'skill': '../bad'}, {'take_id': None}, {'window_start_s': -1}, {'window_end_s': float('nan')}):
            row = {**self.row('skill', 0), **change}
            with self.assertRaises(ValueError):
                select([row])


class CandidateExportTests(unittest.TestCase):
    def setUp(self):
        from test_closed_audio_analysis import ClosedAudioAnalysisTests
        self.case = c = ClosedAudioAnalysisTests()
        c.setUp()
        self.addCleanup(c.doCleanups)
        c.make_real_signal()
        self.package = c.audio.inspect()
        before = bank_fingerprint(c.bank)
        with open_analysis_inputs(self.package, c.fixture.roots, c.audio.folder) as inputs:
            c.execute(inputs)
        seal_receipt(c.output, inputs, before, bank_fingerprint(c.bank), package_id='a'*64)
        self.output = c.root / 'diagnostics/candidate-review'

    def prepare(self):
        c = self.case
        return prepare(self.package, c.fixture.roots, c.audio.folder, c.output, self.output, package_id='a'*64)

    def test_sample_exact_cross_segment_clip_and_candidate_only_listening_page(self):
        from dota2_insights.audio_listening_review import build_listening_page, import_listening_notes, sha
        result = self.prepare()
        self.assertEqual(len(result['clips']), 1)
        self.assertEqual(len(result['clips'][0]['sources']), 2)
        with wave.open(str(self.output/result['clips'][0]['clip']), 'rb') as stream:
            self.assertEqual(stream.getnframes(), 28800)  # 0..1.8s, no invented padding samples.
            self.assertEqual(stream.getframerate(), 16000)
        self.assertEqual(result['clips'][0]['human_verdict'], 'unreviewed')
        page, count = build_listening_page(self.output/'review.json', self.output/'page')
        self.assertEqual(count, 1)
        manifest = json.loads((page/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['context_policy'], 'candidate_context_visible_not_blind_not_independent')
        html = (page/'index.html').read_text(encoding='utf-8')
        self.assertIn('未提供录像/GSI施法真值', html)
        self.assertFalse(manifest['training_eligible'])
        self.assertFalse(result['accuracy_verified'])
        labels = {'kind': 'audio_listening_notes', 'schema_version': 1,
            'manifest_sha256': sha((page/'manifest.json').read_bytes()), 'context_policy': manifest['context_policy'],
            'notes': [{'clip_id': c['clip_id'], 'clip_sha256': c['sha256'], 'verdict': 'uncertain',
                       'phase': 'unknown', 'heard': '', 'comment': 'generated contract fixture, not a real hearing label',
                       'played': True} for c in manifest['clips']]}
        labels_path = self.output/'generated-contract-notes.json'
        labels_path.write_text(json.dumps(labels), encoding='utf-8')
        notes, counts = import_listening_notes(page, labels_path)
        self.assertEqual(counts.get('uncertain'), 1)
        self.assertFalse(json.loads((notes/'notes.json').read_text(encoding='utf-8'))['training_eligible'])

    def test_missing_receipt_and_changed_predictions_refused(self):
        c = self.case
        path = c.output/'analysis-receipt.json'
        raw = path.read_bytes()
        path.unlink()
        with self.assertRaises(OSError):
            self.prepare()
        path.write_bytes(raw)
        (c.output/'predictions.jsonl').write_bytes(b'changed\n')
        with self.assertRaisesRegex(ValueError, 'predictions_changed'):
            self.prepare()
        self.assertFalse((self.output/'review.json').exists())

    def test_capture_pause_boundary_is_unavailable_not_concatenated(self):
        c = self.case
        c.audio.rows[0]['ended_by'] = 'field_paused'
        c.audio.write()
        with open_analysis_inputs(c.audio.inspect(), c.fixture.roots, c.audio.folder) as inputs:
            body, reason = extract(inputs, c.fixture.roots, 'f'*32, .5, 1.5)
        self.assertIsNone(body)
        self.assertEqual(reason, 'capture_gap_or_flush_boundary')

    def test_analysis_changed_during_export_leaves_no_final_review(self):
        from dota2_insights import audio_candidate_review as module
        original = module.extract
        def changed(*args, **kwargs):
            clip = original(*args, **kwargs)
            with (self.case.output/'analysis-receipt.json').open('ab') as stream:
                stream.write(b' ')
            return clip
        with patch.object(module, 'extract', changed):
            with self.assertRaisesRegex(ValueError, 'analysis_changed_during_export'):
                self.prepare()
        self.assertFalse((self.output/'review.json').exists())


if __name__ == '__main__':
    unittest.main()
