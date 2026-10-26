import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from dota2_insights.rule_source_audit import audit
from dota2_insights.contracts.native_rules import rules_checksum


class SourceAuditTests(unittest.TestCase):
    def fixture(self):
        from test_distribution_bundle import rules_payload
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        root=Path(t.name)
        source=root/'hero.txt';source.write_bytes(b'generated hero source')
        rules=rules_payload()
        rules['sources'][0]['sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        rules['sha256']=rules_checksum(rules)
        rp=root/'rules.json';rp.write_text(json.dumps(rules))
        bp=root/'bindings.json';bp.write_text(json.dumps({'schema_version':1,'sources':[{'source_id':'hero','file':'hero.txt'}]}))
        return root,source,rp,bp

    def test_all_bytes_bound_never_implies_semantics_or_activation(self):
        root,source,rp,bp=self.fixture()
        r=audit(rp,bp)
        self.assertTrue(r['all_sources_byte_bound'])
        self.assertFalse(r['semantic_version_verified'])
        self.assertFalse(r['activation_allowed'])
        self.assertNotIn(str(root),json.dumps(r))

    def test_changed_source_reported_as_mismatch(self):
        root,source,rp,bp=self.fixture();source.write_bytes(b'changed')
        r=audit(rp,bp)
        self.assertEqual(r['counts'],{'source_hash_mismatch':1})
        self.assertFalse(r['all_sources_byte_bound'])

    def test_missing_binding_and_missing_file_stay_distinct(self):
        root,source,rp,bp=self.fixture();source.unlink()
        self.assertEqual(audit(rp,bp)['counts'],{'source_unavailable':1})
        bp.write_text(json.dumps({'schema_version':1,'sources':[]}))
        self.assertEqual(audit(rp,bp)['counts'],{'binding_missing':1})

    def test_unexpected_or_duplicate_bindings_refused(self):
        root,source,rp,bp=self.fixture()
        for sources in ([{'source_id':'other','file':'hero.txt'}],
                        [{'source_id':'hero','file':'hero.txt'}]*2):
            bp.write_text(json.dumps({'schema_version':1,'sources':sources}))
            with self.assertRaisesRegex(ValueError,'binding_identity'):audit(rp,bp)

    def test_sealed_target_refused_before_body_read(self):
        root,source,rp,bp=self.fixture()
        bp.write_text(json.dumps({'schema_version':1,'sources':[{
            'source_id':'hero','file':'match_9056629627/nonexistent.txt'}]}))
        with self.assertRaisesRegex(ValueError,'封存'):audit(rp,bp)

    def test_inspector_keeps_byte_binding_separate_from_patch_verification(self):
        from unittest.mock import patch
        from dota2_insights.distribution_bundle import publish,inspect
        root,source,rp,bp=self.fixture()
        folder=root/'delivery'
        with patch('dota2_insights.history.checked_output',side_effect=lambda _root,path:Path(path)):
            publish('rules',rp,folder,created_at_utc='2026-10-25T12:00:00Z')
        r=inspect(folder,game_version='7.41f',now_utc='2026-10-25T12:00:00Z',rule_source_bindings=bp)
        self.assertTrue(r['source_bytes_verified'])
        self.assertEqual(r['compatibility_status'],'semantic_version_unverified')
        self.assertFalse(r['activation_allowed'])
        source.write_bytes(b'changed')
        r=inspect(folder,game_version='7.41f',now_utc='2026-10-25T12:00:00Z',rule_source_bindings=bp)
        self.assertFalse(r['source_bytes_verified'])
