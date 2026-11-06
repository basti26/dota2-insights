import copy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.map_compatibility import create, validate
from dota2_insights.map_evaluation import evaluate
from dota2_insights.virtual_map import MapIdentity, WorldBounds, VirtualMap
from tools.evaluate_map import run, evidence_path
from tools.declare_map_compatibility import run as declare


SOURCE = asdict(MapIdentity('dota', 9, 'a'*64))
TARGET = asdict(MapIdentity('dota', 10, 'a'*64))


def declaration():
    return create(SOURCE, TARGET, {'old-receipt': 'b'*64, 'new-receipt': 'c'*64})


def data():
    return {'schema_version': 1, 'role': 'development', 'map_identity': TARGET, 'image_map_identity': SOURCE,
            'coordinate_frame': 'source2_map_centered',
            'partition_bounds': {'min_x': -10, 'min_y': -10, 'max_x': 10, 'max_y': 10},
            'rows': [{'landmark_id': 'a', 'image_sha256': 'd'*64, 'world_xyz': [0, 0, 0],
                      'world_source': 'synthetic', 'image_size': [100, 100], 'content_box_xywh': [0, 0, 100, 100],
                      'role': 'development', 'historical_training_point': False, 'pixel_xy': [50, 50],
                      'confirmed_independently': True, 'prediction_guided': False,
                      'confirmation_source': 'synthetic test', 'point_definition': 'test center'}]}


def models():
    return {'a': VirtualMap(MapIdentity(**TARGET), WorldBounds(-10, -10, 10, 10),
                            'source2_map_centered', 'reference', 'synthetic').to_dict()}


class MapCompatibilityTests(unittest.TestCase):
    def test_fixed_own_source_proofs_are_readable_without_broadening_record_roots(self):
        from tools.evaluate_map import ROOT
        expected = ROOT/'dota2_insights/map_glyph_review.py'
        self.assertEqual(evidence_path(expected), expected)
        with self.assertRaisesRegex(ValueError, 'outside_configured_roots'):
            evidence_path(ROOT/'dota2_insights/history.py')

    def test_explicit_declaration_does_not_relax_world_model_or_certify_pixels(self):
        value = declaration()
        validate(value, SOURCE, TARGET)
        m = VirtualMap.from_dict(models()['a'])
        with self.assertRaises(ValueError): m.require_compatible(MapIdentity(**SOURCE))
        with self.assertRaisesRegex(ValueError, 'not_approved'): evaluate(data(), models())
        report = evaluate(data(), models(), compatibility=value)
        self.assertEqual(report['development_compatibility_id'], value['declaration_id'])
        self.assertFalse(report['image_context_matches_target'])
        self.assertFalse(report['ui_equivalence_verified'])
        self.assertFalse(report['formal_p2_pass'])
        self.assertEqual(report['models']['a']['status'], 'insufficient_evidence')

    def test_declaration_is_directional_not_transitive_and_cannot_change_scope(self):
        value = declaration()
        for source, target in ((TARGET, SOURCE), (SOURCE, {**TARGET, 'client_build': 11})):
            with self.assertRaises(ValueError): validate(value, source, target)
        for key, changed in (('scope', 'production'), ('ui_equivalence_verified', True),
                             ('calibration_verified', True), ('production_ready', True), ('declaration_id', '0'*64),
                             ('schema_version', True), ('production_ready', 0)):
            modified = {**value, key: changed}
            with self.subTest(key=key), self.assertRaises(ValueError): validate(modified, SOURCE, TARGET)

    def test_different_map_variant_bytes_empty_evidence_and_identity_alias_rejected(self):
        for target in (SOURCE, {**TARGET, 'map_name': 'other'}, {**TARGET, 'map_asset_sha256': 'e'*64}):
            with self.assertRaises(ValueError): create(SOURCE, target, {'a': 'b'*64, 'b': 'c'*64})
        for evidence in ({}, {'a': 'b'*64}, {'a': 'b'*64, 'b': 'invalid'}):
            with self.assertRaises(ValueError): create(SOURCE, TARGET, evidence)

    def test_final_role_stays_rejected_even_with_declaration(self):
        value = data(); value['role'] = 'final'
        with self.assertRaisesRegex(ValueError, 'development_only'):
            evaluate(value, models(), compatibility=declaration())

    def test_consumer_rechecks_evidence_and_leaves_no_complete_on_change(self):
        from dota2_insights.map_holdout import sha
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = [root/'old.json', root/'new.json']
            for p in evidence: p.write_text('{}', encoding='utf-8')
            value = create(SOURCE, TARGET, {str(p): sha(p) for p in evidence})
            files = {'dataset': data(), 'models': models(), 'compatibility': value}
            paths = {}
            for name, obj in files.items():
                p = root/(name+'.json'); p.write_text(json.dumps(obj), encoding='utf-8'); paths[name] = p
            with patch('tools.evaluate_map.checked_input', side_effect=lambda root, p: Path(p)), \
                    patch('tools.evaluate_map.checked_output', side_effect=lambda root, p: Path(p)):
                result = run('windows', paths, root/'good')
                self.assertTrue(result['sources_reverified'])
                self.assertIn(str(evidence[0]), result['source_sha256'])
                evidence[0].write_text('changed', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'evidence_changed'):
                    run('windows', paths, root/'bad')
                self.assertFalse((root/'bad').exists())

    def test_generator_binds_historical_receipt_plan_atlas_and_target_revision(self):
        from dota2_insights.map_holdout import sha
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); prototype, revision = root/'prototype', root/'revision'
            prototype.mkdir(); (revision/'source/maps').mkdir(parents=True)
            def write(path, value): path.write_text(json.dumps(value), encoding='utf-8')
            write(prototype/'manifest.json', {'ward_match_id': None})
            for p in (prototype/'COMPLETE', prototype/'scene.json', revision/'COMPLETE',
                      revision/'snapshot.json', revision/'source/maps/dota.vpk', revision/'source/steam.inf'):
                p.write_bytes(b'synthetic source')
            receipt, atlas, plan = root/'source-before.json', root/'atlas.json', root/'plan.json'
            write(receipt, {'map_vpk': {'sha256': 'a'*64, 'bytes': 10}})
            write(atlas, {'map_build': '9', 'sources': [{'file': str(receipt), 'sha256': sha(receipt)},
                                                     {'file': 'dota.vpk', 'sha256': 'a'*64}]})
            write(plan, {'map_build': '9', 'source_hashes': {str(atlas): sha(atlas)}})
            scene = {'virtual_map_model': models()['a'], 'source_sha256': {str(p): sha(p) for p in (receipt, atlas, plan)}}
            snapshot = {'identity': TARGET, 'map_vpk_bytes': 10}
            with patch('tools.declare_map_compatibility.checked_input', side_effect=lambda root, p: Path(p)), \
                    patch('tools.declare_map_compatibility.checked_output', side_effect=lambda root, p: Path(p)), \
                    patch('tools.declare_map_compatibility.open_scene', return_value=scene), \
                    patch('tools.declare_map_compatibility.read_revision', return_value=snapshot):
                result = declare(prototype, revision, root/'out')
                self.assertEqual(result['source_identity'], SOURCE)
                self.assertEqual(result['target_identity'], TARGET)
                self.assertTrue((root/'out/COMPLETE').is_file())
                snapshot['map_vpk_bytes'] = 11
                with self.assertRaisesRegex(ValueError, 'receipts_disagree'):
                    declare(prototype, revision, root/'bad')
                self.assertFalse((root/'bad').exists())
