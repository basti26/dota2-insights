"""Filled proposals and snapshot boundaries, not real hero-accuracy tests."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from PIL import Image

from dota2_insights.minimap_frame_match import evaluate_frame


class FrameMatchTests(unittest.TestCase):
    def image(self):
        image = np.zeros((372,372,3), np.uint8)
        cv2.circle(image, (150,150), 6, (0,255,0), -1)
        return image

    def bank(self):
        circle = np.zeros((13,13), np.uint8)
        cv2.circle(circle, (6,6), 6, 1, -1)
        return {'coverage_complete': True, 'templates': [
            {'family': name, 'resource_name': name, 'mask': mask, 'angle_degrees': 0, 'longest_side_px': 13}
            for name,mask in (('circle_resource',circle), ('square_resource',np.ones((13,13),np.uint8)))]}

    def test_filled_shape_is_compared_without_requiring_a_hole(self):
        image = self.image()
        before = image.copy()
        result = evaluate_frame(image, self.bank())
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['result']['state'], 'shape_candidate_only')
        self.assertFalse(result['identity_verified'])
        self.assertIsNone(result['hero_count'])
        np.testing.assert_array_equal(image, before)

    def test_candidate_limit_and_unsupported_patch_remain_explicit(self):
        image = self.image()
        fake = {'candidates': [{'box':[0,0,140,20]}], 'candidate_count_before_limit': 60,
                'candidate_limit_reached': True}
        with patch('dota2_insights.minimap_frame_match.probe', return_value=fake):
            result = evaluate_frame(image, self.bank())
        self.assertTrue(result['candidate_limit_reached'])
        self.assertEqual(result['candidate_count_before_limit'], 60)
        self.assertEqual(result['candidates'][0]['result']['state'], 'unavailable')

    def test_visual_selection_prioritizes_positive_shapes_without_duplicate_rows(self):
        from tools.match_minimap_frame_candidates import visual_indices
        rows = [{'result':{'state':'shape_candidate_only' if i in (8,35) else 'unknown',
                          'ranked_families':[{'family':'fixture'}]},
                 'candidate':{'text_band_overlaps':[], 'solid_square_overlaps':[]}} for i in range(40)]
        selected = visual_indices(rows, 4)
        self.assertEqual(selected[:2], [8,35])
        self.assertEqual(len(selected), len(set(selected)))
        self.assertLessEqual(len(selected), 4)


class FrameMatchToolTests(unittest.TestCase):
    def setUp(self):
        from test_minimap_icon_catalog import IconCatalogTests, block
        from dota2_insights.minimap_icon_catalog import export
        from dota2_insights.minimap_template_match import FAMILIES
        fixture = IconCatalogTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        folder = self.root / 'diagnostics/prepared/group'
        folder.mkdir(parents=True)
        self.png = folder / 'frame.png'
        Image.fromarray(FrameMatchTests().image()[:, :, ::-1]).save(self.png)
        self.entry = {'status': 'prepared_for_visual_review', 'image': 'frame.png', 'dimensions': [372,372],
            'pixel_sha_verified': True, 'declared_image_sha256': hashlib.sha256(self.png.read_bytes()).hexdigest(),
            'sample_ordinal': 1, 'source_evidence': {'line':1, 'line_sha256':'0'*64},
            'captured_at_utc':'2026-10-26T00:00:00Z'}
        self.batch = folder.parent / 'batch.json'
        self.document = {'schema_version':1, 'kind':'minimap_image_review_batch',
            'results':[{'directory':'group', 'result':{'entries':[self.entry]}}]}
        self.write_batch()
        names = sorted({name for values in FAMILIES.values() for name in values})
        fixture.definitions.write_text(''.join(block(name) for name in names))
        self.catalog = self.root / 'diagnostics/catalog'
        export(fixture.source, self.catalog)

    def write_batch(self):
        self.batch.write_text(json.dumps(self.document))

    def run_tool(self, **kwargs):
        from tools.match_minimap_frame_candidates import run
        return run(self.catalog, self.batch, root=self.root, **kwargs)

    def test_end_to_end_keeps_all_filled_candidates_and_source_unknown(self):
        result = self.run_tool()
        self.assertEqual(result['frames_compared'], 1)
        self.assertEqual(result['candidates_compared'], 1)
        report = json.loads((Path(result['output']) / 'report.json').read_text(encoding='utf-8'))
        self.assertIsNone(report['hero_count'])
        self.assertFalse(report['accuracy_verified'])
        self.assertEqual(report['source_provenance'], 'prepared_png_snapshot_original_source_logs_not_reverified_here')

    def test_changed_png_and_missing_prior_pixel_check_are_refused(self):
        self.png.write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.run_tool()
        Image.fromarray(FrameMatchTests().image()[:, :, ::-1]).save(self.png)
        self.entry['pixel_sha_verified'] = False
        self.write_batch()
        with self.assertRaises(ValueError):
            self.run_tool()

    def test_escaping_path_wrong_schema_and_boolean_limit_refused(self):
        self.document['results'][0]['directory'] = '../../../outside'
        self.write_batch()
        with self.assertRaises(ValueError):
            self.run_tool()
        self.document['results'][0]['directory'] = 'group'
        self.document['schema_version'] = True
        self.write_batch()
        with self.assertRaises(ValueError):
            self.run_tool()
        with self.assertRaises(ValueError):
            self.run_tool(max_frames=True)
