import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.map_evaluation import DEFAULT_POLICY, evaluate, translation_review, ward_impact, associate_glyphs
from dota2_insights.virtual_map import MapIdentity, VirtualMap, WorldBounds
from dota2_insights.ward_map import bind
from tools.evaluate_map import run, prepare


def model(bounds=None):
    return VirtualMap(MapIdentity('dota', 10, 'a'*64),
                      WorldBounds(**(bounds or {'min_x': -9, 'min_y': -9, 'max_x': 9, 'max_y': 9})),
                      'source2_map_centered', 'reference', 'synthetic test only').to_dict()


def dataset():
    m = model()
    rows = []
    for y in (-6, 0, 6):
        for x in (-6, 0, 6):
            for offset in (-.5, 0, .5):
                wx = x + offset
                rows.append({'landmark_id': f'{x}:{y}:{offset}', 'image_sha256': 'b'*64,
                             'world_xyz': [wx, y, 0], 'world_source': 'synthetic asset',
                             'image_size': [200, 300], 'content_box_xywh': [10, 20, 180, 270],
                             'role': 'development', 'historical_training_point': False,
                             'pixel_xy': [10+(wx+9)/18*180, 20+(9-y)/18*270],
                             'confirmed_independently': True, 'prediction_guided': False,
                             'confirmation_source': 'synthetic independent test fixture',
                             'point_definition': 'synthetic center'})
    return {'schema_version': 1, 'role': 'development', 'map_identity': m['identity'],
            'image_map_identity': m['identity'],
            'coordinate_frame': 'source2_map_centered', 'partition_bounds': m['world_bounds'], 'rows': rows}


class MapEvaluationTests(unittest.TestCase):
    def test_blind_glyph_association_keeps_frozen_center_and_original_dataset(self):
        data = dataset(); data['rows'] = data['rows'][:1]
        row = data['rows'][0]; row['pixel_xy'] = None
        row['missing_reason'] = 'not reviewed'; row['content_box_xywh'] = [0, 0, *row['image_size']]
        observations = {'predictions_read': False, 'map_build': '10', 'rows': [
            {'case': 'test', 'frame_index': 1, 'icon': 'lotus', 'image_sha256': row['image_sha256'],
             'crop_width': 200, 'detection': {'predictions_used': False, 'overflow': False,
                                           'candidates': [{'pixel_xy': [32.5, 90.5], 'score': .95}]}}]}
        associations = {'role': 'development', 'prediction_guided': False, 'reviewer': 'synthetic test',
                        'rows': [{'landmark_id': row['landmark_id'], 'image_sha256': row['image_sha256'],
                                  'case': 'test', 'frame_index': 1, 'icon': 'lotus', 'candidate_index': 0,
                                  'visually_confirmed': True, 'basis': 'synthetic visual review'}]}
        before = copy.deepcopy(data)
        result = associate_glyphs(data, observations, associations)
        self.assertEqual(data, before)
        self.assertEqual(result['rows'][0]['pixel_xy'], [32.5, 90.5])
        self.assertIn('not_glyph_centroid', result['rows'][0]['point_definition'])
        self.assertIsNone(result['rows'][0]['measurement_uncertainty_px'])
        for mode in ('guided', 'overflow', 'duplicate', 'image', 'overwrite', 'role', 'negative'):
            d, o, a = copy.deepcopy(data), copy.deepcopy(observations), copy.deepcopy(associations)
            if mode == 'guided': o['rows'][0]['detection']['predictions_used'] = True
            if mode == 'overflow': o['rows'][0]['detection']['overflow'] = True
            if mode == 'duplicate': a['rows'] *= 2
            if mode == 'image': o['rows'][0]['image_sha256'] = 'f'*64
            if mode == 'overwrite': d['rows'][0]['pixel_xy'] = [1, 2]
            if mode == 'role': a['role'] = 'final'
            if mode == 'negative': a['rows'][0]['candidate_index'] = -1
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                associate_glyphs(d, o, a)

    def test_old_build_images_remain_explicit_and_cannot_certify_current_build(self):
        data = dataset(); data['image_map_identity'] = {**data['map_identity'], 'client_build': 9}
        with self.assertRaisesRegex(ValueError, 'build_compatibility_not_approved'):
            evaluate(data, {'m': model()})
        for row in data['rows']:
            row['pixel_xy'] = None; row['missing_reason'] = 'not independently labelled'
        result = evaluate(data, {'m': model()})
        self.assertFalse(result['image_context_matches_target'])
        self.assertEqual(result['models']['m']['status'], 'insufficient_evidence')

    def test_nine_regions_rectangular_crop_and_no_certification_even_perfect(self):
        data = dataset(); before = copy.deepcopy(data)
        result = evaluate(data, {'truth_for_test_only': model()})
        self.assertEqual(data, before)
        report = result['models']['truth_for_test_only']
        self.assertEqual(report['status'], 'within_provisional_limits')
        self.assertEqual(len(report['regions']), 9)
        self.assertEqual(report['spatial_errors_1024px']['count'], 27)
        self.assertLess(report['frame_errors_1024px']['max'], 1e-10)
        self.assertFalse(result['formal_p2_pass'])
        self.assertFalse(result['final_labels_read'])

    def test_repeated_frames_do_not_supply_missing_spatial_coverage(self):
        data = dataset(); row = data['rows'][0]
        data['rows'] = [{**row, 'image_sha256': f'{i:064x}'} for i in range(30)]
        report = evaluate(data, {'m': model()})['models']['m']
        self.assertEqual(report['spatial_errors_1024px']['count'], 1)
        self.assertEqual(report['frame_errors_1024px']['count'], 30)
        self.assertEqual(report['status'], 'insufficient_evidence')
        self.assertIsNone(report['regions']['north_east']['spatial_errors_1024px']['median'])

    def test_bad_region_fails_even_when_overall_median_is_small(self):
        data = dataset()
        for row in data['rows'][:3]:
            row['pixel_xy'][0] += 10
        report = evaluate(data, {'m': model()})['models']['m']
        self.assertLess(report['spatial_errors_1024px']['median'], 4)
        self.assertEqual(report['regions']['south_west']['status'], 'outside_provisional_limits')
        self.assertEqual(report['status'], 'outside_provisional_limits')

    def test_missing_pixels_are_unknown_not_zero(self):
        data = dataset()
        for row in data['rows']:
            row['pixel_xy'] = None; row['missing_reason'] = 'not independently labelled'
        report = evaluate(data, {'m': model()})['models']['m']
        self.assertIsNone(report['spatial_errors_1024px']['median'])
        self.assertEqual(report['regions']['north_east']['missing_unique_landmarks'], 3)
        self.assertEqual(report['status'], 'insufficient_evidence')

    def test_final_and_training_roles_never_evaluated(self):
        for role in ('train', 'final', 'holdout'):
            data = dataset(); data['role'] = role
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, 'development_only'):
                evaluate(data, {'m': model()})
        data = dataset(); data['rows'][0]['role'] = 'final'
        with self.assertRaisesRegex(ValueError, 'role_overlap'):
            evaluate(data, {'m': model()})

    def test_provenance_and_prediction_guidance_required(self):
        for key, value in (('prediction_guided', True), ('confirmed_independently', False),
                           ('confirmation_source', ''), ('point_definition', '')):
            data = dataset(); data['rows'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                evaluate(data, {'m': model()})

    def test_duplicates_changed_origins_and_image_context_are_rejected(self):
        for change in ('duplicate', 'origin', 'crop', 'alias'):
            data = dataset()
            if change == 'duplicate': data['rows'].append(copy.deepcopy(data['rows'][0]))
            if change == 'origin':
                row = copy.deepcopy(data['rows'][0]); row['image_sha256'] = 'c'*64
                row['world_xyz'][0] += 1; data['rows'].append(row)
            if change == 'crop': data['rows'][0]['content_box_xywh'] = [0, 0, 180, 270]
            if change == 'alias':
                row = copy.deepcopy(data['rows'][0]); row['landmark_id'] = 'alias'; data['rows'].append(row)
            with self.subTest(change=change), self.assertRaises(ValueError):
                evaluate(data, {'m': model()})

    def test_nonfinite_bool_wrong_frame_or_identity_and_outside_crop_rejected(self):
        for change in ('nan', 'bool', 'identity', 'frame', 'crop', 'partition'):
            data, m = dataset(), model()
            if change == 'nan': data['rows'][0]['world_xyz'][0] = float('nan')
            if change == 'bool': data['rows'][0]['pixel_xy'][0] = True
            if change == 'identity': m['identity']['client_build'] += 1
            if change == 'frame': m['coordinate_frame'] = 'gem_cell128_plus_vec_raw'
            if change == 'crop': data['rows'][0]['pixel_xy'][0] = -1
            if change == 'partition': data['rows'][0]['world_xyz'][0] = -99
            with self.subTest(change=change), self.assertRaises(ValueError):
                evaluate(data, {'m': m})

    def test_same_dataset_policy_and_regions_for_all_models(self):
        data = dataset()
        result = evaluate(data, {'a': model(), 'b': model({'min_x': -10, 'min_y': -10, 'max_x': 10, 'max_y': 10})})
        a, b = result['models']['a'], result['models']['b']
        self.assertEqual([r['region'] for r in a['rows']], [r['region'] for r in b['rows']])
        self.assertEqual([r['landmark_id'] for r in a['rows']], [r['landmark_id'] for r in b['rows']])
        self.assertEqual(b['status'], 'outside_provisional_limits')
        policy = {**DEFAULT_POLICY, 'minimum_unique_per_region': 1}
        with self.assertRaisesRegex(ValueError, 'region_minimum'):
            evaluate(data, {'a': model()}, policy)

    def test_translation_compared_in_world_units_without_any_window(self):
        data = {'schema_version': 1, 'map_identity': model()['identity'],
                'source_frame': 'gem_cell128_plus_vec_raw', 'target_frame': 'source2_map_centered',
                'rows': [{'object_id': 'tower', 'raw_xy': [16385, 16386], 'world_xy': [1, 2],
                          'same_object_confirmed': True, 'prediction_guided': False,
                          'raw_source': 'test replay', 'target_source': 'test asset', 'confirmation_source': 'test only'}]}
        result = translation_review(data, {'candidate': [-16384, -16384], 'wrong': [0, 0]})
        self.assertEqual(result['results']['candidate']['errors_world_units']['max'], 0)
        self.assertGreater(result['results']['wrong']['errors_world_units']['max'], 20000)
        self.assertFalse(result['translation_verified'])
        data['rows'] = []
        self.assertEqual(translation_review(data, {'candidate': [-16384, -16384]})['status'], 'insufficient_evidence')

    def test_translation_disallows_guided_duplicate_and_nonfinite_pairs(self):
        data = {'schema_version': 1, 'map_identity': model()['identity'],
                'source_frame': 'gem_cell128_plus_vec_raw', 'target_frame': 'source2_map_centered',
                'rows': [{'object_id': 'a', 'same_object_confirmed': False}]}
        with self.assertRaisesRegex(ValueError, 'independent_unique'):
            translation_review(data, {'a': [-16384, -16384]})
        data['rows'] = []
        with self.assertRaises(ValueError):
            translation_review(data, {'a': [float('inf'), 0]})

    def test_ward_impact_keeps_binding_and_raw_values_unchanged(self):
        m = VirtualMap.from_dict(model())
        layer = {'source_map_identity': None, 'translation_candidate_xy': [-16384, -16384],
                 'points': [{'id': 'ward', 'raw_world_xy': [16385, 16386]}]}
        layer['map_binding'] = bind(layer, m)
        before = copy.deepcopy(layer)
        result = ward_impact(layer, {'old': model(), 'new': model({'min_x': -18, 'min_y': -18, 'max_x': 18, 'max_y': 18})}, 'old')
        self.assertEqual(layer, before)
        self.assertEqual(result['source_map_relation'], 'unknown')
        self.assertGreater(result['results']['new']['displacement_1024px']['max'], 0)
        self.assertIsNone(result['conclusions_reversed'])
        self.assertFalse(result['formal_p3_pass'])
        different = model(); different['identity']['client_build'] += 1
        with self.assertRaises(ValueError):
            ward_impact(layer, {'old': model(), 'new': different}, 'old')
        legacy = copy.deepcopy(layer); legacy.pop('map_binding')
        legacy['source_map_identity'] = {**m.to_dict()['identity'], 'map_asset_sha256': 'c'*64}
        with self.assertRaisesRegex(ValueError, 'source_map_mismatch'):
            ward_impact(legacy, {'old': model(), 'new': model()}, 'old')

    def test_prototype_lock_is_checked_before_scene_hash_or_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); prototype = root/'prototype'; prototype.mkdir()
            (prototype/'manifest.json').write_text(json.dumps({'ward_match_id': 'synthetic-lock'}), encoding='utf-8')
            paths = {'prototype': prototype, 'models': root/'models.json'}
            with patch('tools.evaluate_map.checked_input', side_effect=lambda root, p: p), \
                    patch('tools.evaluate_map.checked_output', side_effect=lambda root, p: p), \
                    patch('dota2_insights.replay_holdout.refuse_locked_match', side_effect=ValueError('test_locked')), \
                    patch('tools.evaluate_map.sha') as hash_body:
                with self.assertRaisesRegex(ValueError, 'test_locked'):
                    run('ward-impact', paths, root/'out', 'old')
                hash_body.assert_not_called()

    def test_prepare_keeps_all_pixels_missing_and_never_creates_final_labels(self):
        from dota2_insights.map_holdout import sha
        from dota2_insights.map_landmarks import extract
        def entity(name, origin, extra=''):
            fields = f'classname = "{name}"\norigin = [ {origin} ]\nangles = [ 0, 0, 0 ]\nscales = [ 1, 1, 1 ]\n'+extra
            return '\t'*4+'values = \n'+'\t'*4+'{\n'+''.join('\t'*5+line+'\n' for line in fields.splitlines())+'\t'*4+'}\n'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); prototype, landmarks = root/'prototype', root/'landmarks'
            prototype.mkdir(); landmarks.mkdir()
            (prototype/'manifest.json').write_text(json.dumps({'ward_match_id': None}), encoding='utf-8')
            for name in ('scene.json', 'COMPLETE', 'frame.png'):
                (prototype/name).write_bytes(b'synthetic source')
            data = ('<!-- kv3 encoding:text:generated -->\n'+entity('dota_minimap_boundary', '-9, -9, 0')
                    +entity('dota_minimap_boundary', '9, 9, 0')
                    +entity('world_bounds', '0, 0, 0', 'min = "-9 -9 0"\nmax = "9 9 9"')
                    +entity('npc_dota_lotus_pool', '-2, 3, 0')+entity('npc_dota_tower', '4, -4, 0'))
            (landmarks/'DATA.txt').write_text(data, encoding='utf-8')
            (landmarks/'default_ents.vents_c').write_bytes(b'test compiled bytes')
            original = extract(data)
            original.update(map_asset_sha256='a'*64, client_build=10,
                            source_export_sha256=sha(landmarks/'DATA.txt'), resource_sha256=sha(landmarks/'default_ents.vents_c'))
            (landmarks/'landmarks.json').write_text(json.dumps(original), encoding='utf-8')
            (landmarks/'COMPLETE').write_text(sha(landmarks/'landmarks.json'), encoding='ascii')
            plan = prototype/'plan.json'; plan.write_text(json.dumps({'map_build': '10'}), encoding='utf-8')
            scene = {'virtual_map_model': model(), 'bounds': model()['world_bounds'], 'source_sha256': {str(plan): sha(plan)},
                     'frames': [{'image': 'frame.png', 'size': [200, 300]}]}
            with patch('tools.evaluate_map.checked_input', side_effect=lambda root, p: p), \
                    patch('tools.evaluate_map.checked_output', side_effect=lambda root, p: p), \
                    patch('dota2_insights.map_prototype.open_scene', return_value=scene):
                result = prepare(prototype, landmarks, root/'out')
                self.assertEqual(result['landmarks'], 2)
                self.assertEqual(result['independent_labels'], 0)
                rows = json.loads((root/'out/development.json').read_text(encoding='utf-8'))['rows']
                self.assertTrue(all(row['pixel_xy'] is None for row in rows))
                self.assertTrue(rows[1]['historical_training_point'])
                self.assertFalse((root/'out/final.json').exists())
                self.assertTrue((root/'out/COMPLETE').is_file())
                (landmarks/'DATA.txt').write_text('changed', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'export_changed'):
                    prepare(prototype, landmarks, root/'bad')
                self.assertFalse((root/'bad').exists())

    def test_cli_hashes_all_sources_and_refuses_changed_input_or_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            paths = {'dataset': folder/'data.json', 'models': folder/'models.json'}
            for key, value in (('dataset', dataset()), ('models', {'a': model()})):
                paths[key].write_text(json.dumps(value), encoding='utf-8')
            with patch('tools.evaluate_map.checked_input', side_effect=lambda root, p: p), \
                    patch('tools.evaluate_map.checked_output', side_effect=lambda root, p: p):
                result = run('windows', paths, folder/'good')
                self.assertTrue(result['sources_reverified'])
                self.assertTrue((folder/'good'/'COMPLETE').is_file())
                with self.assertRaisesRegex(ValueError, 'new_map_evaluation'):
                    run('windows', paths, folder/'good')
                original = evaluate
                def changed(*args):
                    result = original(*args)
                    paths['dataset'].write_text('{}', encoding='utf-8')
                    return result
                with patch('tools.evaluate_map.evaluate', side_effect=changed), self.assertRaisesRegex(ValueError, 'source_changed'):
                    run('windows', paths, folder/'bad')
                self.assertFalse((folder/'bad').exists())
