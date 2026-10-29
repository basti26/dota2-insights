"""Synthetic IDs only: public and private lock lists must be additive."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.replay_holdout import refuse_locked_match


class LocalValidationLocksTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root/'data').mkdir(); (self.root/'config').mkdir()
        self.public = self.root/'data/validation-locks.json'
        self.local = self.root/'config/validation-locks.local.json'
        self.write(self.public, [])

    def write(self, path, matches):
        path.write_text(json.dumps({'schema_version':1,'heldout_matches':matches}), encoding='utf-8')

    def test_empty_public_without_local_allows_unlocked_id(self):
        refuse_locked_match('700000003', self.root)

    def test_local_and_public_union_cannot_override_each_other(self):
        self.write(self.public, ['700000001'])
        self.write(self.local, ['700000002'])
        for value in ('700000001', '700000002', 700000001, 700000002):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, '封存'):
                refuse_locked_match(value, self.root)
        refuse_locked_match('700000003', self.root)
        self.write(self.local, [])
        with self.assertRaisesRegex(ValueError, '封存'):
            refuse_locked_match('700000001', self.root)

    def test_local_changes_are_read_without_restart(self):
        refuse_locked_match('700000002', self.root)
        self.write(self.local, ['700000002'])
        with self.assertRaisesRegex(ValueError, '封存'):
            refuse_locked_match('700000002', self.root)

    def test_malformed_private_list_fails_closed(self):
        for value in ({}, {'schema_version':True,'heldout_matches':[]},
                      {'schema_version':1,'heldout_matches':[7]},
                      {'schema_version':1,'heldout_matches':['']},
                      {'schema_version':1,'heldout_matches':['７']},
                      {'schema_version':1,'heldout_matches':['7'*21]}):
            self.local.write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(value=value), self.assertRaises(ValueError):
                refuse_locked_match('700000003', self.root)
        self.local.write_text('{', encoding='utf-8')
        with self.assertRaises(ValueError):refuse_locked_match('700000003', self.root)
        self.local.write_bytes(b' '*16385)
        with self.assertRaisesRegex(ValueError, '超限'):refuse_locked_match('700000003', self.root)

    def test_local_list_cannot_replace_missing_public_policy(self):
        self.public.unlink(); self.write(self.local, [])
        with self.assertRaisesRegex(ValueError, '缺失'):
            refuse_locked_match('700000003', self.root)

    def test_self_check_verifies_both_policies_without_leaking_ids(self):
        from tools import win_insights
        self.write(self.public, ['700000001'])
        self.write(self.local, ['700000002'])
        with patch.object(win_insights, 'ROOT', self.root), \
                patch('dota2_insights.replay_workbench.dependency_status', return_value={}):
            result = win_insights.self_check()
        self.assertTrue(result['passed'])
        self.assertEqual(result['sealed_matches_refused'], 2)
        self.assertFalse(result['historical_payloads_read'])
        self.assertNotIn('700000001', json.dumps(result))
        self.assertNotIn('700000002', json.dumps(result))
