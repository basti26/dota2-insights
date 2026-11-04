import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.map_holdout import sha
from dota2_insights.map_window_review import project_window, review
from tools.review_map_window import run


def inputs():
    frame = {'case': 'day', 'frame_index': 1, 'size': [360, 360],
        'crop_origin_client_xy': [8, 100], 'image_sha256': 'f'*64}
    plan = {'map_build': '10', 'frames': [frame]}
    points = [{'id': 'a', 'classname': 'npc_dota_lotus_pool', 'category': 'lotus_pool',
        'native_origin_xyz': [-5, 5, 1]}]
    atlas = {'map_build': '10', 'entities': points, 'calibration': {'matrix_client': {
        'scale_x': 18, 'scale_y': -18, 'offset_x': 188, 'offset_y': 280}}}
    landmarks = {'map_asset_sha256': 'a'*64, 'client_build': 11,
        'declared_minimap_window': {'min_x': -10, 'min_y': -10, 'max_x': 10, 'max_y': 10},
        'landmarks': [{'classname': points[0]['classname'], 'origin_xyz': [-5, 5, 1]}]}
    icons = ['minimap_lotuspool', 'minimap_watcher', 'minimap_rune_bounty', 'minimap_rune_unknown']
    obs = {'map_build': '10', 'predictions_read': False, 'rows': [
        {'case': 'day', 'frame_index': 1, 'icon': icon, 'crop_width': 360, 'image_sha256': 'f'*64,
         'detection': {'predictions_used': False, 'overflow': False,
             'candidates': [{'pixel_xy': [90, 90]}] if icon == icons[0] else []}}
        for icon in icons]}
    return plan, obs, atlas, landmarks


class MapWindowTests(unittest.TestCase):
    def test_y_axis_and_no_clipping_or_ground_height_invention(self):
        bounds = inputs()[3]['declared_minimap_window']
        self.assertEqual(project_window([-10, 10, 999], bounds, [360, 360]), [0, 0])
        self.assertEqual(project_window([20, -10, 3], bounds, [360, 360]), [540, 360])
        with self.assertRaises(ValueError):
            project_window([float('nan'), 0, 0], bounds, [360, 360])
        with self.assertRaises(ValueError):
            project_window([0, 0, 0], bounds, [True, 360])

    def test_matching_assets_allow_cross_build_comparison_but_never_certify(self):
        args = inputs()
        before = copy.deepcopy(args)
        result = review(*args, 'a'*64)
        self.assertEqual(args, before)
        self.assertTrue(result['map_vpk_exact_bytes_compatible'])
        self.assertIsNone(result['external_dependencies_compatible'])
        self.assertFalse(result['formal_p2_pass'])
        self.assertFalse(result['parameters_fitted'])
        self.assertFalse(result['world_physics_placement_verified'])
        self.assertEqual(result['rows'][0]['asset_window']['errors_reference_px'], [0])
        self.assertEqual(result['rows'][1]['asset_window']['status'], 'unjudgeable_count_or_overflow')

    def test_different_map_bytes_or_changed_authored_origin_rejected(self):
        with self.assertRaisesRegex(ValueError, 'asset_mismatch'):
            review(*inputs(), 'b'*64)
        args = inputs()
        args[3]['landmarks'][0]['origin_xyz'][0] += .01
        with self.assertRaisesRegex(ValueError, 'origins_changed'):
            review(*args, 'a'*64)

    def test_missing_duplicate_or_wrong_image_observations_rejected(self):
        for action in ('missing', 'duplicate', 'image', 'build'):
            args = inputs()
            if action == 'missing': args[1]['rows'].pop()
            if action == 'duplicate': args[1]['rows'].append(copy.deepcopy(args[1]['rows'][0]))
            if action == 'image': args[1]['rows'][0]['image_sha256'] = '0'*64
            if action == 'build': args[1]['map_build'] = '11'
            with self.subTest(action=action), self.assertRaises(ValueError):
                review(*args, 'a'*64)

    def test_prediction_guidance_or_unresolved_parent_rejected(self):
        args = inputs()
        args[1]['rows'][0]['detection']['predictions_used'] = True
        with self.assertRaisesRegex(ValueError, 'not_frozen'):
            review(*args, 'a'*64)
        args = inputs()
        args[2]['entities'][0]['parent_transform_unresolved'] = True
        with self.assertRaisesRegex(ValueError, 'parent_transform'):
            review(*args, 'a'*64)

    def test_large_or_missing_candidate_sets_remain_unknown_not_zero_error(self):
        args = inputs()
        args[1]['rows'][0]['detection']['candidates'] *= 17
        result = review(*args, 'a'*64)
        self.assertIsNone(result['rows'][0]['asset_window']['errors_reference_px'])
        self.assertEqual(result['rows'][0]['asset_window']['status'], 'unjudgeable_candidate_budget')
        args = inputs()
        args[1]['rows'][0]['detection']['candidates'] = []
        self.assertIsNone(review(*args, 'a'*64)['rows'][0]['asset_window']['errors_reference_px'])

    def test_crop_mismatch_does_not_refit_tower_reference_or_change_source(self):
        args = inputs()
        args[3]['declared_minimap_window']['max_x'] = 30
        result = review(*args, 'a'*64)
        self.assertEqual(result['rows'][0]['asset_window']['status'], 'setwise_mismatch')
        self.assertEqual(result['rows'][0]['historical_tower_model']['errors_reference_px'], [0])
        self.assertFalse(result['identity_confirmed'])

    def test_file_freeze_and_receipts_required_before_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package, batch, lm = [root/name for name in ('package', 'batch', 'landmarks')]
            for folder in (package, batch, lm): folder.mkdir()
            plan, obs, atlas, landmark = inputs()
            def write(path, data): path.write_text(json.dumps(data), encoding='utf-8')
            write(package/'package.json', {})
            write(package/'plan.json', plan)
            before = root/'source-before.json'
            write(before, {'map_vpk': {'sha256': 'a'*64}})
            atlas_path = root/'atlas.json'
            atlas['sources'] = [{'file': str(before), 'sha256': sha(before)},
                {'file': str(root/'dota.vpk'), 'sha256': 'a'*64}]
            write(atlas_path, atlas)
            plan['source_hashes'] = {str(atlas_path): sha(atlas_path)}
            obs['package_sha256'] = sha(package/'package.json')
            write(batch/'observations.json', obs)
            freeze = {'predictions_read': False, 'observations_sha256': sha(batch/'observations.json'),
                'package_sha256': sha(package/'package.json')}
            write(batch/'freeze.json', freeze)
            (lm/'default_ents.vents_c').write_bytes(b'synthetic entity bytes')
            (lm/'DATA.txt').write_text('synthetic export', encoding='utf-8')
            landmark.update(resource_sha256=sha(lm/'default_ents.vents_c'), source_export_sha256=sha(lm/'DATA.txt'))
            write(lm/'landmarks.json', landmark)
            (lm/'COMPLETE').write_text(sha(lm/'landmarks.json'), encoding='ascii')
            with patch('tools.review_map_window.checked_input', side_effect=lambda r,p: Path(p)), \
                    patch('tools.review_map_window.checked_output', side_effect=lambda r,p: Path(p)), \
                    patch('tools.review_map_window.read_plan', return_value=(plan, {})):
                valid = root/'valid'
                self.assertTrue(run(package, batch, atlas_path, before, lm, valid)['sources_reverified'])
                obs['rows'][0]['detection']['candidates'] = []
                write(batch/'observations.json', obs)
                with self.assertRaisesRegex(ValueError, 'frozen_sources_changed'):
                    run(package, batch, atlas_path, before, lm, root/'bad')
                self.assertFalse((root/'bad').exists())
                write(batch/'freeze.json', {**freeze, 'observations_sha256': sha(batch/'observations.json')})
                (lm/'default_ents.vents_c').write_bytes(b'changed source')
                with self.assertRaisesRegex(ValueError, 'landmark_sources_changed'):
                    run(package, batch, atlas_path, before, lm, root/'bad2')
                self.assertFalse((root/'bad2').exists())
