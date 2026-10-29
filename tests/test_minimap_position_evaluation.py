import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from dota2_insights.minimap_position_evaluation import load_labels, evaluate
from tools.evaluate_minimap_positions import run


class MinimapPositionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def bundle(self, name, mid):
        folder = self.root / name
        folder.mkdir()
        selected, marks = [], []
        for i, (x, y) in enumerate(((5, 5), (5, 30), (5, 55), (30, 5), (30, 30), (30, 55), (55, 5), (55, 55))):
            p = folder / f'sample-{i:06d}.png'
            Image.new('RGB', (64, 64), (i, 10 if mid=='12345' else 20, 0)).save(p)
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            selected.append({'sequence': i, 'image_sha256': digest,
                'source_record_status': 'exact_recorded_source_id', 'captured_at_utc': '2026-10-28T00:00:01Z',
                'position': {'match_id': mid, 'received_at_utc': '2026-10-28T00:00:00Z',
                             'alive': True, 'paused': False, 'source_age_s': 0, 'xpos': x*100, 'ypos': -y*100}})
            marks.append({'sequence': i, 'x': x, 'y': y, 'image_sha256': digest})
        samples, labels = folder/'samples.json', folder/'labels.json'
        samples.write_text(json.dumps({'selected': selected}), encoding='utf8')
        labels.write_text(json.dumps({'schema_version': 1, 'match_id': mid, 'marks': marks}), encoding='utf8')
        return samples, labels

    def test_affine_training_and_independent_match_error_near_zero(self):
        a, b = self.bundle('train', '12345'), self.bundle('test', '23456')
        result = evaluate(load_labels(*a), load_labels(*b))
        self.assertLess(result['heldout_error_px']['max'], 1e-9)
        self.assertFalse(result['calibration_verified'])

    def test_same_match_and_shared_image_leakage_refused(self):
        a = load_labels(*self.bundle('a', '12345'))
        with self.assertRaisesRegex(ValueError, 'same_match'):
            evaluate(a, a)
        b = dict(a, match_id='23456')
        with self.assertRaisesRegex(ValueError, 'shared_images'):
            evaluate(a, b)

    def test_missing_labels_produces_no_model_or_zero_error(self):
        a, b = self.bundle('a', '12345'), self.bundle('b', '23456')
        r = run(a[0], b[0], root=self.root)
        report = json.loads((Path(r['output'])/'report.json').read_text(encoding='utf8'))
        self.assertEqual(r['status'], 'insufficient_labels')
        self.assertIsNone(report['evaluation']['heldout_error_px'])
        self.assertNotIn('model', report['evaluation'])
        self.assertFalse(report['training']['labels_available'])

    def test_changed_image_sha_and_duplicate_marks_refused(self):
        samples, labels = self.bundle('a', '12345')
        data = json.loads(labels.read_text())
        data['marks'].append(data['marks'][0])
        labels.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'duplicate_label'):
            load_labels(samples, labels)
        data['marks'].pop()
        labels.write_text(json.dumps(data))
        (samples.parent/'sample-000000.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'image_changed'):
            load_labels(samples, labels)

    def test_outside_pixels_rejected_and_pause_excluded(self):
        samples, labels = self.bundle('a', '12345')
        data = json.loads(labels.read_text()); data['marks'][0]['x'] = 64
        labels.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'pixel_outside'):
            load_labels(samples, labels)
        data['marks'][0]['x'] = 5; labels.write_text(json.dumps(data))
        selected = json.loads(samples.read_text()); selected['selected'][0]['position']['paused'] = True
        samples.write_text(json.dumps(selected))
        r = load_labels(samples, labels)
        self.assertEqual(len(r['accepted']), 7)
        self.assertEqual(r['rejected'][0]['reason'], 'not_alive_or_pause_unknown')

    def test_large_callback_delay_and_naive_time_not_used_in_geometry_error(self):
        samples, labels = self.bundle('a', '12345')
        data = json.loads(samples.read_text())
        data['selected'][0]['captured_at_utc'] = '2026-10-28T00:00:10Z'
        data['selected'][1]['captured_at_utc'] = '2026-10-28T00:00:01'
        samples.write_text(json.dumps(data))
        self.assertEqual(len(load_labels(samples, labels)['rejected']), 2)

    def test_collinear_training_is_not_a_valid_map(self):
        a, b = load_labels(*self.bundle('a', '12345')), load_labels(*self.bundle('b', '23456'))
        for i, row in enumerate(a['accepted']):
            row['world'] = [i, i]
        with self.assertRaisesRegex(ValueError, 'collinear'):
            evaluate(a, b)
