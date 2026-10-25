"""Generated maps/observations only; no game files or held-out ward answers."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from dota2_insights import map_holdout as m
from dota2_insights.map_holdout_view import write_view


class MapHoldoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = self.root / 'capture'
        (self.case / 'frames').mkdir(parents=True)
        rows = []
        for number in m.PROTOCOL['frame_indices']:
            f = self.case / f'frames/{number}.png'
            Image.new('RGB', (128, 128), '#245632').save(f)
            rows.append({'frame_index': number, 'file': f'frames/{number}.png',
                'width': 128, 'height': 128, 'sha256': m.sha(f)})
        (self.case / 'frames.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        m.save(self.case / 'manifest.json', {'config': {'map_build': '6966', 'purpose': 'map_survey', 'match_id': ''},
            'frames.jsonl_sha256': m.sha(self.case / 'frames.jsonl')})
        m.save(self.case / 'tour-confirmed.json', {'roi': [0, 0, 1, 1]})
        points = [{'id': str(i), 'name': 'generated-'+str(i),
            'category': 'power_rune_spawn_origin' if i < 2 else 'barracks',
            'native_origin_xyz': [x, y, 0], 'parent_transform_unresolved': False}
            for i, (x, y) in enumerate([(-10,-10),(10,10),(-10,10),(10,-10),
                                        (-20,-20),(20,20),(-20,20),(20,-20)])]
        matrix = {'scale_x': 1, 'scale_y': -1, 'offset_x': 64, 'offset_y': 64}
        self.atlas = {'map_build': '6966', 'entities': points,
            'calibration': {'matrix_client': matrix, 'client_size': [128, 128], 'training': {'matches': []}}}
        m.save(self.root / 'atlas.json', self.atlas)
        m.save(self.root / 'request.json', {'cases': [
            {'id': 'day', 'folder': str(self.case), 'environment': 'generated_day'},
            {'id': 'night', 'folder': str(self.case), 'environment': 'generated_night'}]})
        self.package = self.root / 'package'

    def prepare(self):
        return m.prepare(self.root / 'atlas.json', self.root / 'request.json', self.package)

    def observed(self, *, dx=0):
        plan = self.prepare()
        obs = m.load(self.package / 'observations-template.json')
        points = {p['id']: p for p in self.atlas['entities']}
        obs['reviewer'] = 'generated_observer'
        for row in obs['rows']:
            p = points[row['point_id']]
            x, y = p['native_origin_xyz'][:2]
            row.update(status='measured', frame_index=31, pixel_xy=[64+x+dx, 64-y],
                       uncertainty_px=.1, identity_confirmed=True, reason='generated independent marker')
        path = self.root / 'observed.json'
        m.save(path, obs)
        return plan, obs, path

    def test_non_tower_selection_and_protocol_frozen_without_observations(self):
        plan = self.prepare()
        self.assertEqual(len(plan['points']), 8)
        self.assertEqual(len(plan['frames']), 6)
        self.assertEqual(plan['protocol']['maximum_error_reference_px'], 1.5)
        self.assertFalse(plan['map_certified'])
        self.assertTrue(all(r['status'] == 'pending' for r in m.load(self.package/'observations-template.json')['rows']))

    def test_freeze_does_not_read_predictions(self):
        _, _, observed = self.observed()
        original = m.load
        def guarded(path, *args, **kwargs):
            if Path(path).name == 'predictions.json':
                raise AssertionError('predictions read before frozen observation')
            return original(path, *args, **kwargs)
        with patch.object(m, 'load', side_effect=guarded):
            m.freeze(self.package, observed, self.root / 'batch')
        self.assertFalse(m.load(self.root / 'batch/freeze.json')['predictions_read'])

    def test_full_generated_holdout_can_support_only_its_pixel_scope(self):
        _, _, observed = self.observed()
        m.freeze(self.package, observed, self.root / 'batch')
        r = m.compare(self.package, self.root / 'batch', self.root / 'report')
        self.assertEqual(r['counts']['pass'], 16)
        self.assertEqual(r['coverage']['unique_points'], 8)  # Not 16 repeated points.
        self.assertTrue(r['p2_supported'])
        self.assertFalse(r['map_certified'])
        self.assertFalse(r['ward_validation_unlocked'])

    def test_shifted_points_fail_error_and_bias_without_refit(self):
        _, _, observed = self.observed(dx=2)
        matrix = copy.deepcopy(self.atlas['calibration'])
        m.freeze(self.package, observed, self.root / 'batch')
        r = m.compare(self.package, self.root / 'batch', self.root / 'report')
        self.assertFalse(r['p2_supported'])
        self.assertEqual(r['counts']['mismatch'], 16)
        self.assertEqual(self.atlas['calibration'], matrix)

    def test_opposite_case_biases_cannot_cancel_into_a_pass(self):
        _, obs, path = self.observed()
        for row in obs['rows']:
            row['pixel_xy'][0] += .3 if row['case'] == 'day' else -.3
        path.write_text(json.dumps(obs))
        m.freeze(self.package, path, self.root / 'batch')
        r = m.compare(self.package, self.root / 'batch', self.root / 'report')
        self.assertEqual(r['counts']['mismatch'], 0)
        self.assertAlmostEqual(r['bias_reference_xy'][0], 0)
        self.assertFalse(r['p2_supported'])

    def test_pending_and_unknown_do_not_become_zero_error_or_pass(self):
        self.prepare()
        path = self.package / 'observations-template.json'
        m.freeze(self.package, path, self.root / 'batch')
        r = m.compare(self.package, self.root / 'batch', self.root / 'report')
        self.assertEqual(r['counts']['pending'], 16)
        self.assertIsNone(r['error_distribution_reference_px'])
        self.assertIsNone(r['bias_reference_xy'])
        self.assertFalse(r['p2_supported'])

    def test_uncertain_measurements_are_unjudgeable(self):
        _, obs, path = self.observed()
        for row in obs['rows']:
            row['uncertainty_px'] = 5
        path.write_text(json.dumps(obs))
        m.freeze(self.package, path, self.root / 'batch')
        r = m.compare(self.package, self.root / 'batch', self.root / 'report')
        self.assertEqual(r['counts']['unjudgeable'], 16)
        self.assertFalse(r['p2_supported'])

    def test_missing_identity_or_full_roster_cannot_be_frozen(self):
        _, obs, path = self.observed()
        obs['rows'][0]['identity_confirmed'] = False
        path.write_text(json.dumps(obs))
        with self.assertRaisesRegex(ValueError, 'measurement'):
            m.freeze(self.package, path, self.root / 'bad')
        obs['rows'].pop()
        path.write_text(json.dumps(obs))
        with self.assertRaisesRegex(ValueError, 'full_roster'):
            m.freeze(self.package, path, self.root / 'missing')

    def test_cross_build_prepare_refused(self):
        p = self.case / 'manifest.json'
        v = m.load(p)
        v['config']['map_build'] = '6965'
        p.write_text(json.dumps(v))
        with self.assertRaisesRegex(ValueError, 'cross_build'):
            self.prepare()

    def test_game_replay_capture_refused_before_opening_images(self):
        p = self.case / 'manifest.json'
        v = m.load(p)
        v['config'].update(purpose='replay_review', match_id='123')
        p.write_text(json.dumps(v))
        with patch('PIL.Image.open', side_effect=AssertionError('replay image read')):
            with self.assertRaisesRegex(ValueError, 'only_map_surveys'):
                self.prepare()

    def test_changed_evidence_or_observation_is_refused(self):
        _, _, path = self.observed()
        m.freeze(self.package, path, self.root / 'batch')
        p = self.root / 'batch/observations.json'
        p.write_text(p.read_text() + ' ')
        with self.assertRaisesRegex(ValueError, 'freeze_changed'):
            m.compare(self.package, self.root / 'batch', self.root / 'bad-report')
        image = next((self.package / 'images').iterdir())
        image.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'evidence_changed'):
            m.read_plan(self.package)

    def test_no_source_overwrite_and_escaped_interactive_payload(self):
        self.prepare()
        with self.assertRaises(FileExistsError):
            self.prepare()
        plan = m.load(self.package / 'plan.json')
        template = m.load(self.package / 'observations-template.json')
        plan['points'][0]['name'] = '</script><script>bad()</script>'
        p = self.root / 'view.html'
        write_view(p, plan, template)
        self.assertNotIn('</script><script>bad()', p.read_text(encoding='utf-8'))
        self.assertNotIn('predicted_pixel_xy', p.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
