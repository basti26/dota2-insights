"""Generated RGB pixels test image provenance; never live minimap accuracy."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from dota2_insights.minimap_index import sync
from dota2_insights.minimap_review_evidence import prepare_native, prepare_legacy


class MinimapImageReviewTests(unittest.TestCase):
    def setUp(self):
        from test_minimap_index import MinimapIndexTests
        self.fixture = MinimapIndexTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f = self.fixture.fixture
        (self.f.folder / 'frames').mkdir()
        self.png = self.f.folder / 'frames/frame-000001.png'
        Image.new('RGB', (120, 120), (10, 20, 30)).save(self.png)
        self.f.rows[0].update(image_sha256=hashlib.sha256(self.png.read_bytes()).hexdigest(),
            pixel_sha256=hashlib.sha256(bytes((30, 20, 10)) * 120 * 120).hexdigest(),
            client_size=[1920, 1080], roi=[10, 950, 120, 120], layout_segment=1,
            time_basis='generated_fixture', duplicate_pixels=False)
        self.f.publish()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name) / 'output'

    def prepare(self, **kwargs):
        entry = sync(self.fixture.project, self.fixture.roots)['entries'][0]
        return prepare_native(entry, self.fixture.roots, self.output, **kwargs)

    def test_original_bytes_pixel_sha_and_geometry_bound_to_exact_source_line(self):
        result = self.prepare()
        entry = result['entries'][0]
        self.assertTrue(result['all_selected_images_verified'])
        self.assertEqual((self.output / entry['image']).read_bytes(), self.png.read_bytes())
        self.assertEqual(entry['dimensions'], [120, 120])
        self.assertTrue(entry['pixel_sha_verified'])
        raw = (self.f.folder / 'samples.jsonl').read_bytes()
        self.assertEqual(entry['source_evidence']['line_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertTrue((self.output / 'contact.png').exists())
        self.assertFalse(result['hero_identity_verified'])
        self.assertFalse(result['map_calibration_verified'])
        self.assertFalse(result['visual_verdicts_entered'])

    def test_spread_selection_includes_largest_gap_and_keeps_diagnostics_factual(self):
        base = self.f.rows[0]
        self.f.rows = [{**base, 'sequence': i + 1, 'captured_at_utc': f'2026-10-26T00:{minute:02d}:{second:02d}Z',
                        'layout_segment': 1 if i < 3 else 2}
                       for i, (minute, second) in enumerate(((0, 0), (0, 1), (0, 2), (1, 40), (1, 41), (1, 42)))]
        self.f.session['frames_saved'] = 6
        self.f.publish()
        result = self.prepare(max_images=4)
        self.assertEqual({e['sample_ordinal'] for e in result['entries']}, {1, 3, 4, 6})
        self.assertEqual(result['max_observed_capture_gap_s'], 98)
        self.assertEqual(result['adjacent_reused_image_references'], 5)
        self.assertEqual(result['recorded_layout_changes'], 1)

    def test_clock_regression_is_not_hidden_by_sorting(self):
        self.f.rows.append({**self.f.rows[0], 'sequence': 2, 'captured_at_utc': '2026-10-26T00:00:00Z'})
        self.f.session['frames_saved'] = 2
        self.f.publish()
        result = self.prepare()
        self.assertEqual(result['capture_clock_regressions'], 1)
        self.assertIsNone(result['max_observed_capture_gap_s'])

    def test_missing_image_and_wrong_image_sha_are_unavailable_not_verified(self):
        self.png.unlink()
        result = self.prepare()
        self.assertEqual(result['entries'][0]['status'], 'unavailable')
        self.assertFalse(result['all_selected_images_verified'])
        self.assertFalse((self.output / 'contact.png').exists())

    def test_png_geometry_and_pixel_sha_mismatch_refused(self):
        self.f.rows[0]['roi'] = [10, 950, 121, 120]
        self.f.publish()
        result = self.prepare()
        self.assertIn('geometry', result['entries'][0]['reason'])
        self.output = self.output.with_name('second')
        self.f.rows[0]['roi'] = [10, 950, 120, 120]
        self.f.rows[0]['pixel_sha256'] = '0' * 64
        self.f.publish()
        result = self.prepare()
        self.assertEqual(result['entries'][0]['reason'], 'minimap_review_pixel_sha_mismatch')

    def test_image_reference_cannot_escape_to_other_user_files(self):
        self.f.rows[0]['image'] = '../private.local.json'
        self.f.publish()
        result = self.prepare()
        self.assertEqual(result['entries'][0]['reason'], 'minimap_review_bound_image_missing')

    def test_mutation_during_image_preparation_does_not_publish_success_manifest(self):
        import dota2_insights.minimap_review_evidence as module
        original = module._image
        def mutate(*args):
            result = original(*args)
            with (self.f.folder / 'samples.jsonl').open('ab') as stream:
                stream.write(b'{}\n')
            return result
        with patch.object(module, '_image', mutate), self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse((self.output / 'evidence.json').exists())

    def test_prepared_original_changing_before_publication_is_refused(self):
        import dota2_insights.minimap_review_evidence as module
        original = module._image
        def mutate(*args):
            result = original(*args)
            self.png.write_bytes(b'changed after read')
            return result
        with patch.object(module, '_image', mutate), self.assertRaisesRegex(ValueError, 'prepared_image_changed'):
            self.prepare()
        self.assertFalse((self.output / 'evidence.json').exists())

    def test_history_output_and_invalid_limits_refused(self):
        self.output = self.f.root / 'must-not-write'
        with self.assertRaisesRegex(ValueError, 'overlaps_history'):
            self.prepare()
        self.assertFalse(self.output.exists())
        self.output = self.fixture.project / 'diagnostics/invalid'
        with self.assertRaises(ValueError):
            self.prepare(max_images=1000)
        self.assertFalse(self.output.exists())

    def test_legacy_admitted_journal_uses_same_original_pixel_check(self):
        from test_helper_handoff import HandoffTests
        legacy = HandoffTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        legacy.history = self.f.root
        legacy.registry = self.f.root / 'field-recording' / ('run_' + 'b' * 32) / 'recorders.json'
        legacy.document['recorders'] = {}
        legacy.document['local_run_folders'] = {'minimap': [self.f.folder.name]}
        legacy.write(legacy.registry, legacy.document)
        package = legacy.inspect()
        reference = next(j['source'] for c in package['components'] if c['kind'] == 'minimap'
                         for j in c['journals'] if Path(j['source']['path']).name == 'samples.jsonl')
        result = prepare_legacy(package, self.fixture.roots, reference, self.output)
        self.assertTrue(result['all_selected_images_verified'])
        self.assertEqual(result['kind'], 'legacy_minimap_image_review')
        self.assertEqual((self.output / result['entries'][0]['image']).read_bytes(), self.png.read_bytes())

    def test_one_command_prepares_native_and_keeps_success_manifest(self):
        from tools.prepare_minimap_review import prepare
        result = prepare(self.fixture.project, roots=self.fixture.roots, max_images=2)
        self.assertEqual(result['runs'], 1)
        self.assertEqual(result['prepared'], 1)
        self.assertEqual(result['unavailable'], 0)
        batch = json.loads((Path(result['output']) / 'batch.json').read_text(encoding='utf-8'))
        self.assertFalse(batch['visual_verdicts_entered'])

    def test_continuous_windows_select_neighbors_within_image_budget(self):
        base = self.f.rows[0]
        self.f.rows = [{**base, 'sequence': i + 1, 'captured_at_utc': f'2026-10-26T00:00:{i:02d}Z'} for i in range(30)]
        self.f.session['frames_saved'] = 30
        self.f.publish()
        result = self.prepare(max_images=12, selection='continuous_windows')
        self.assertLessEqual(len(result['entries']), 12)
        self.assertEqual(len(result['selection_windows']), 3)
        for window in result['selection_windows']:
            ordinals = [e['sample_ordinal'] for e in result['entries'] if window['window_id'] in e['window_ids']]
            self.assertEqual(ordinals, list(range(window['first_sample_ordinal'], window['last_sample_ordinal'] + 1)))
