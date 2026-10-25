import json
from pathlib import Path
import tempfile
import unittest

from dota2_insights.ward_validation_package import admitted_development, build_development_package, sha
from dota2_insights.ward_validation_report import pending_report


class WardValidationPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / 'evidence'
        self.evidence.mkdir()
        self.freeze = self.root / 'freeze.json'
        code = {}
        for name in ('extract_replay_wards.py', 'parser.py', 'wards.py'):
            body = ('synthetic ' + name).encode()
            (self.evidence / name).write_bytes(body)
            code[name] = sha(body)
        extra = {}
        for name, key in (('protocol.md', 'protocol_sha256'), ('inventory.json', 'inventory_sha256'),
                          ('request.json', 'request_sha256')):
            body = name.encode()
            (self.evidence / name).write_bytes(body)
            extra[key] = sha(body)
        self.frozen = {'status': 'sources_frozen_pending_samples', 'code_sha256': code, **extra,
            'random_per_match': 12, 'special_per_match': 6,
            'matches': [{'match_id': '9050000001', 'role': 'development', 'replay_sha256': 'a' * 64,
                         'cohort_key': 'synthetic', 'metadata': {'gem_version': '0.13.0'}},
                        {'match_id': '9050000002', 'role': 'heldout'}]}
        self.save_freeze()
        self.extraction = self.root / 'extraction'
        self.extraction.mkdir()
        records = [{'match_id': '9050000001', 'team': 2 + i % 2, 'ward_type': 'observer',
                    'placed_game_time_s': i * 65, 'duration_s': 20, 'end_kind': 'unobserved'} for i in range(40)]
        body = json.dumps(records).encode()
        (self.extraction / 'wards.json').write_bytes(body)
        self.manifest = {'complete': True, 'parse_error': None, 'stop_reason': 'replay_eof',
            'match_id': '9050000001', 'parsed_match_id': '9050000001', 'source_sha256': 'a' * 64,
            'source_unchanged_stat': True, 'independent_footer_matches_parser': True,
            'adapter_sha256': code['extract_replay_wards.py'], 'parser_sha256': code['parser.py'],
            'ward_extractor_sha256': code['wards.py'], 'parser_version': '0.13.0',
            'files': {'wards.json': {'sha256': sha(body), 'bytes': len(body)}}, 'ward_count': 40}
        self.save_manifest()

    def save_freeze(self):
        body = json.dumps(self.frozen).encode()
        self.freeze.write_bytes(body)
        self.freeze.with_suffix('.sha256').write_text(sha(body), encoding='ascii')

    def save_manifest(self):
        (self.extraction / 'manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def build(self):
        return build_development_package(self.freeze, '9050000001', self.extraction, self.root / 'samples')

    def test_completed_export_gets_only_pending_blind_samples(self):
        value = self.build()
        self.assertEqual((value['random_selected'], value['special_selected']), (12, 6))
        self.assertFalse(value['visual_review_complete'])
        self.assertFalse(value['independent_validation_passed'])
        blind = json.loads((self.root / 'samples/review/blind.json').read_text(encoding='utf-8'))
        self.assertEqual(len(blind), 18)
        self.assertTrue(all(r['status'] == 'pending' and r['observed_team'] is None for r in blind))
        self.assertTrue(all(r['match_id'] == '9050000001' and r['role'] == 'development' for r in blind))
        self.assertEqual(len({r['record_index'] for r in blind}), 18)

    def test_holdout_rejected_before_ward_files_are_accessed(self):
        with self.assertRaisesRegex(ValueError, 'heldout_locked'):
            build_development_package(self.freeze, '9050000002', self.root / 'not_present', self.root / 'samples')
        self.assertFalse((self.root / 'samples').exists())

    def test_freeze_or_evidence_changes_rejected(self):
        (self.evidence / 'parser.py').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'freeze_evidence'):
            self.build()
        self.freeze.write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'freeze_fingerprint'):
            self.build()

    def test_incomplete_source_or_parser_change_rejected(self):
        for key, value in (('complete', False), ('source_sha256', 'b' * 64),
                           ('adapter_sha256', 'b' * 64), ('parsed_match_id', '9050000009')):
            original = self.manifest[key]
            self.manifest[key] = value
            self.save_manifest()
            with self.assertRaisesRegex(ValueError, 'completed_bound_extraction'):
                self.build()
            self.manifest[key] = original

    def test_changed_export_rejected_and_output_not_overwritten(self):
        (self.extraction / 'wards.json').write_text('[]', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'ward_export_fingerprint'):
            self.build()
        out = self.root / 'samples'
        out.mkdir()
        with self.assertRaisesRegex(ValueError, 'new_output'):
            self.build()

    def test_pending_report_has_no_inferred_verdict_or_answers(self):
        self.build()
        report = pending_report(self.freeze, [self.root / 'samples'], self.root / 'report')
        self.assertEqual(report['pending_count'], 18)
        self.assertIsNone(report['correct_count'])
        self.assertIsNone(report['mismatch_count'])
        self.assertFalse(report['independent_validation_passed'])
        page = (self.root / 'report/index.html').read_text(encoding='utf-8')
        self.assertNotIn('sealed-answers', page)
        self.assertNotIn('world_x', page)

    def test_pending_report_rejects_modified_blank(self):
        self.build()
        (self.root / 'samples/review/blind.json').write_text('[]', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'package_file_changed'):
            pending_report(self.freeze, [self.root / 'samples'], self.root / 'report')
