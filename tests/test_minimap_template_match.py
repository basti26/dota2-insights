"""Synthetic shape competition, unknown outputs, and reference provenance."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from dota2_insights.minimap_template_match import FAMILIES, load_bank, match_patch, variants


class TemplateMatchTests(unittest.TestCase):
    def bank(self, *, tie=False, complete=True):
        diamond = np.array([[0,0,1,0,0], [0,1,1,1,0], [1,1,1,1,1], [0,1,1,1,0], [0,0,1,0,0]], np.uint8)
        square = diamond.copy() if tie else np.ones((5,5), np.uint8)
        return {'coverage_complete': complete, 'templates': [
            {'family': family, 'resource_name': family, 'mask': mask, 'angle_degrees': 0, 'longest_side_px': 5}
            for family, mask in (('first', diamond), ('second', square))]}

    def image(self):
        image = np.zeros((12, 12, 3), np.uint8)
        image[3:8, 4:9, 1] = self.bank()['templates'][0]['mask'] * 255
        return image

    def test_exact_shape_has_margin_but_never_gets_hero_identity(self):
        result = match_patch(self.image(), self.bank())
        self.assertEqual(result['state'], 'shape_candidate_only')
        self.assertEqual(result['ranked_families'][0]['score'], 1)
        self.assertEqual(result['resource_family_candidate'], 'first')
        self.assertFalse(result['semantic_identity_verified'])
        self.assertIsNone(result['hero_id'])

    def test_identical_other_family_is_ambiguous(self):
        result = match_patch(self.image(), self.bank(tie=True))
        self.assertEqual(result['reason'], 'competing_family_ambiguity')
        self.assertEqual(result['family_gap'], 0)
        self.assertIsNone(result['resource_family_candidate'])

    def test_incomplete_or_hero_only_bank_cannot_force_a_candidate(self):
        result = match_patch(self.image(), self.bank(complete=False))
        self.assertEqual(result['reason'], 'incomplete_reference_bank')
        bank = self.bank()
        bank['templates'] = bank['templates'][:1]
        result = match_patch(self.image(), bank)
        self.assertEqual(result['reason'], 'insufficient_competing_families')

    def test_blank_and_additional_foreground_are_not_positive_proof(self):
        self.assertEqual(match_patch(np.zeros((12,12,3), np.uint8), self.bank())['state'], 'unknown')
        image = self.image()
        image[:, :, 1] = 255
        result = match_patch(image, self.bank())
        self.assertEqual(result['reason'], 'weak_shape_match')

    def test_rotation_variants_preserve_source_and_bound_dimensions(self):
        rgba = np.zeros((20, 20, 4), np.uint8)
        rgba[5:12, 5:8] = 255
        rgba[9:12, 5:15] = 255
        before = rgba.copy()
        bank = variants(rgba, angles=(0,90,180,270), sizes=(8,16))
        self.assertGreater(len(bank), 4)
        self.assertTrue(all(max(t['mask'].shape) in (8,16) for t in bank))
        np.testing.assert_array_equal(rgba, before)

    def test_reference_sha_and_missing_family_are_verified(self):
        from test_minimap_icon_catalog import IconCatalogTests, block
        from dota2_insights.minimap_icon_catalog import export
        fixture = IconCatalogTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        names = sorted({name for values in FAMILIES.values() for name in values})
        fixture.definitions.write_text(''.join(block(name) for name in names))
        export(fixture.source, fixture.output)
        bank = load_bank(fixture.output)
        self.assertTrue(bank['coverage_complete'])
        path = fixture.output / 'catalog.json'
        doc = json.loads(path.read_text(encoding='utf-8'))
        doc['entries'] = doc['entries'][1:]
        path.write_text(json.dumps(doc))
        self.assertFalse(load_bank(fixture.output)['coverage_complete'])
        icon = fixture.output / doc['entries'][0]['file']
        icon.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            load_bank(fixture.output)

    def test_variant_and_patch_limits_are_enforced(self):
        with self.assertRaises(ValueError):
            match_patch(np.zeros((1000,1000,3), np.uint8), self.bank())
        with self.assertRaises(ValueError):
            variants(np.zeros((20,20,4), np.uint8), sizes=(1000,))
