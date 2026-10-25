"""Release gates on generated files/results; no real heldout data accessed."""
import copy
import json
from unittest.mock import patch
import unittest

from dota2_insights import ward_release_readiness as readiness
from dota2_insights.ward_observation_check import FIELDS
from dota2_insights.ward_validation_package import sha
import test_ward_validation_package as fixtures


class WardReadinessTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.WardValidationPackageTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.root, self.freeze = fixture.root, fixture.freeze
        for name in readiness.REVISION_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('generated source ' + name)
        self.revision = self.root / 'revision.json'
        self.value = readiness.revision_snapshot(self.root, self.freeze, 'synthetic-only')
        self.revision.write_text(json.dumps(self.value))

    def report(self, reviews=()):
        return readiness.release_readiness(self.root, self.freeze, self.revision, reviews)

    def comparison(self):
        return {'match_id': '9050000001', 'role': 'development',
                'source_freeze_sha256': sha(self.freeze.read_bytes()), 'cohort_key': 'synthetic',
                'package_sha256': 'a' * 64, 'batch_sha256': 'b' * 64,
                'observations_sha256': 'c' * 64, 'comparison_code_sha256': 'd' * 64,
                'checker_code_sha256': 'e' * 64, 'counts': {'pass': 144},
                'observer_prior_exposure': 'blind_declared',
                'samples': [{'sample_id': f'generated-{i}', 'fields': {key: 'pass' for key in FIELDS}}
                            for i in range(18)]}

    def test_empty_reviews_block_and_keep_heldout_locked(self):
        result = self.report()
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['development_coverage']['9050000001'], {'reviewed': 0, 'required': 18})
        self.assertEqual(result['heldout_matches_locked'], ['9050000002'])
        self.assertFalse(result['heldout_released'])
        self.assertFalse(result['independent_validation_passed'])

    def test_changed_or_incomplete_revision_blocks(self):
        source = self.root / readiness.REVISION_FILES[0]
        source.write_text('changed')
        self.assertTrue(any(r.startswith('revision_changed:') for r in self.report()['reasons']))
        self.value['files'].pop(readiness.REVISION_FILES[0])
        self.revision.write_text(json.dumps(self.value))
        self.assertIn('bound_complete_revision_manifest_required', self.report()['reasons'])

    def test_fabricated_all_pass_result_only_allows_explicit_review_never_releases(self):
        # Stubbed comparator here tests gate arithmetic only. Real comparator
        # intentionally cannot turn uncalibrated map points into pass verdicts.
        with patch.object(readiness, 'compare_batch', return_value=self.comparison()) as compare:
            result = self.report([{'package': 'generated', 'batch': 'generated'}])
        compare.assert_called_once_with('generated', 'generated')
        self.assertEqual(result['status'], 'ready_for_explicit_release_review')
        self.assertFalse(result['heldout_released'])
        self.assertFalse(result['independent_validation_passed'])

    def test_pending_mismatch_map_unknown_and_exposure_each_block(self):
        for kind in ('pending', 'mismatch', 'unjudgeable', 'exposure'):
            value = self.comparison()
            if kind == 'exposure':
                value['observer_prior_exposure'] = 'not_declared'
            else:
                value['samples'][0]['fields']['map_position'] = kind
            with self.subTest(kind=kind), patch.object(readiness, 'compare_batch', return_value=value):
                self.assertEqual(self.report([{'package': 'generated', 'batch': 'generated'}])['status'], 'blocked')

    def test_overlapping_batches_and_wrong_cohort_block(self):
        value = self.comparison()
        value['cohort_key'] = 'wrong'
        with patch.object(readiness, 'compare_batch', return_value=value):
            result = self.report([{'package': 'generated', 'batch': 'generated'}] * 2)
        self.assertTrue(any(r.startswith('overlapping_review_batches:') for r in result['reasons']))
        self.assertIn('review_source_or_cohort_mismatch:9050000001', result['reasons'])

    def test_revision_snapshot_checks_frozen_sources_and_requires_label(self):
        with self.assertRaisesRegex(ValueError, 'revision_label'):
            readiness.revision_snapshot(self.root, self.freeze, ' ')
        (self.fixture.evidence / 'parser.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'freeze_evidence'):
            readiness.revision_snapshot(self.root, self.freeze, 'synthetic-only')


if __name__ == '__main__':
    unittest.main()
