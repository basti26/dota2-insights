"""Static/source payload checks only; never execute SSH, service commands or parsers."""
import ast
import base64
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from unittest.mock import patch

from dota2_insights import pi_deployment as pi


class DeploymentTests(unittest.TestCase):
    def fixture(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        root=Path(t.name)
        values={'dota2_insights/__init__.py':'',
            'dota2_insights/replay_library_store.py':'from pi_ward_worker import process_one\n',
            'tools/pi_ward_worker.py':'from library import something\n',
            'tools/extract_replay_wards.py':'from dota2_insights.replay_archive import parse_archive\n',
            'dota2_insights/replay_archive.py':'from .replay_holdout import refuse_locked_match\nimport generated_vendor\n',
            'dota2_insights/replay_holdout.py':'def refuse_locked_match(x): pass\n',
            'data/validation-locks.json':json.dumps({'schema_version':1,'heldout_matches':[]})}
        for name,text in values.items():
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(text,encoding='utf-8')
        return root

    def test_transitive_imports_package_init_and_public_locks_included(self):
        root=self.fixture();plan=pi.build_plan(root)
        for name in ('library.py','pi_ward_worker.py','extract_replay_wards.py',
            'dota2_insights/replay_archive.py','dota2_insights/replay_holdout.py',
            'dota2_insights/__init__.py','data/validation-locks.json'):
            self.assertIn(name,plan['files'])
        self.assertIn('generated_vendor',plan['external_import_roots'])
        self.assertFalse(plan['remote_deploy_executed'])
        self.assertFalse(plan['third_party_runtime_verified_on_pi'])

    def test_missing_internal_dependency_rejects_incomplete_deployment(self):
        root=self.fixture();(root/'dota2_insights/replay_holdout.py').unlink()
        with self.assertRaisesRegex(ValueError,'internal_module_missing'):pi.build_plan(root)

    def test_public_lock_file_missing_or_invalid_is_not_omitted(self):
        root=self.fixture();path=root/'data/validation-locks.json';path.unlink()
        with self.assertRaises(OSError):pi.build_plan(root)
        path.write_text(json.dumps({'schema_version':True,'heldout_matches':[]}))
        with self.assertRaisesRegex(ValueError,'public_locks_invalid'):pi.build_plan(root)

    def test_payload_contains_only_expected_sources_and_no_config(self):
        root=self.fixture();config=root/'config/private.local.json';config.parent.mkdir();config.write_text('PRIVATE_SENTINEL')
        payload=pi.deployment_sources(root)
        self.assertTrue(all(name.endswith(('.py','.json')) for name in payload))
        self.assertFalse(any('config/' in name for name in payload))
        self.assertFalse(any(b'PRIVATE_SENTINEL' in raw for raw in payload.values()))

    def test_managed_deploy_uses_closure_but_remote_execution_is_mocked(self):
        from dota2_insights import pi_replay_library as bridge
        with tempfile.TemporaryDirectory() as t:
            captured=[]
            def execute(_self,_command,*,source,timeout):
                captured.append(Path(source).read_text(encoding='utf-8'));return {'mocked':True}
            with patch.object(bridge.Bridge,'execute',execute),patch.object(bridge,'WORK',Path(t)):
                obj=bridge.Bridge({'enabled':True,'managed_on_pi':True,'remote_root':'/home/generated/analysis/.state'})
                self.assertEqual(obj.deploy(),{'mocked':True})
            tree=ast.parse(captured[0])
            loop=next(n for n in tree.body if isinstance(n,ast.For))
            payload=ast.literal_eval(loop.iter.func.value)
            self.assertIn('data/validation-locks.json',payload)
            self.assertIn('dota2_insights/replay_phase_evidence.py',payload)
            for name,encoded in payload.items():
                raw=base64.b64decode(encoded)
                if name.endswith('.py'):compile(raw,name,'exec')

    def test_actual_payload_imports_in_isolated_stdlib_only_child(self):
        # No site-packages, helper checkout or parent module cache is available.
        payload=pi.deployment_sources()
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            for name,raw in payload.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            code="""import sys
sys.path.insert(0,sys.argv[1])
import library, pi_ward_worker, extract_replay_wards
import dota2_insights.replay_archive, dota2_insights.replay_postgame_context
import dota2_insights.replay_phase_evidence, dota2_insights.replay_death_semantics
import dota2_insights.replay_visibility_recording
from dota2_insights.replay_holdout import refuse_locked_match
refuse_locked_match('12345')
try:
 refuse_locked_match('9056629627')
except ValueError:
 pass
else:
 raise AssertionError('public lock was not deployed')
print('isolated_imports_ok')
"""
            result=subprocess.run([sys.executable,'-I','-S','-c',code,str(root)],
                capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('isolated_imports_ok',result.stdout)
