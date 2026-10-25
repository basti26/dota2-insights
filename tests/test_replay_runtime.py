"""Generated portable payload/command checks; never parse a DEM or launch a game."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dota2_insights.replay_archive import GEM_VERSION, GEM_COMMIT, packaged_source
from dota2_insights.replay_holdout import refuse_locked_match
from dota2_insights.replay_runtime import (WORKER_NAME, RUNTIME_KIND, file_sha256,
    worker_directory, read_runtime_manifest, replay_command)
from dota2_insights.replay_workbench import dependency_status


class PortableReplayRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = worker_directory(self.root)
        self.runtime.mkdir(parents=True)
        for name in (WORKER_NAME, 'lib/generated.dat'):
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'generated inert data not an executable')
        self.manifest = {'kind': RUNTIME_KIND, 'schema_version': 1, 'worker': WORKER_NAME,
            'gem_version': GEM_VERSION, 'gem_commit': GEM_COMMIT, 'files': [
                {'path': name, 'sha256': file_sha256(self.runtime / name)}
                for name in (WORKER_NAME, 'lib/generated.dat')]}
        self.save()

    def save(self):
        (self.runtime / 'runtime-manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def test_full_verification_and_fixed_structured_command_with_spaces(self):
        value = read_runtime_manifest(self.runtime, verify_files=True)
        self.assertEqual(value['gem_commit'], GEM_COMMIT)
        source, job = self.root / 'with space/123.dem', self.root / 'job with space'
        command = replay_command(self.root, source, job, frozen=True)
        self.assertEqual(command, [str(self.runtime / WORKER_NAME), '--source', str(source), '--job', str(job)])
        self.assertNotIn(sys.executable, command)
        with patch.object(sys, 'frozen', True, create=True):
            self.assertIsNone(dependency_status(self.root))

    def test_source_mode_keeps_python_worker_without_optional_runtime(self):
        command = replay_command(self.root, self.root / '123.dem', self.root / 'job', frozen=False)
        self.assertEqual(command[:4], [sys.executable, '-B', '-u', str(self.root / 'tools/run_replay_job.py')])

    def test_executable_corruption_fails_fast_and_payload_corruption_fails_full(self):
        (self.runtime / 'lib/generated.dat').write_bytes(b'changed payload')
        read_runtime_manifest(self.runtime)  # UI check is intentionally lightweight.
        with self.assertRaisesRegex(ValueError, 'file_changed'):
            read_runtime_manifest(self.runtime, verify_files=True)
        (self.runtime / WORKER_NAME).write_bytes(b'changed executable')
        with self.assertRaisesRegex(ValueError, 'file_changed'):
            read_runtime_manifest(self.runtime)
        with patch.object(sys, 'frozen', True, create=True):
            self.assertIn('损坏', dependency_status(self.root))

    def test_missing_worker_or_manifest_never_falls_back_to_main_executable(self):
        (self.runtime / WORKER_NAME).unlink()
        with self.assertRaises(ValueError):
            replay_command(self.root, self.root / '123.dem', self.root / 'job', frozen=True)
        (self.runtime / 'runtime-manifest.json').unlink()
        with patch.object(sys, 'frozen', True, create=True):
            self.assertIsNotNone(dependency_status(self.root))

    def test_manifest_traversal_duplicate_unpinned_or_bad_version_rejected(self):
        original = deepcopy(self.manifest)
        for update in ({'gem_version': 'other'}, {'gem_commit': '0' * 40}, {'schema_version': True},
                       {'files': original['files'] * 2},
                       *({'files': [original['files'][0], {'path': name, 'sha256': 'a' * 64}]}
                         for name in ('../outside', 'C:/outside', '/outside', 'lib\\outside'))):
            with self.subTest(update=update):
                self.manifest = {**original, **update}
                self.save()
                with self.assertRaises(ValueError):
                    read_runtime_manifest(self.runtime)

    def test_full_file_limits_and_bad_hash_are_bounded(self):
        self.manifest['files'][0]['sha256'] = '../hash'
        self.save()
        with self.assertRaises(ValueError):
            read_runtime_manifest(self.runtime)
        (self.runtime / 'runtime-manifest.json').write_bytes(b' ' * (4 * 1024 ** 2 + 1))
        with self.assertRaisesRegex(ValueError, 'size'):
            read_runtime_manifest(self.runtime)

    def test_frozen_guard_requires_public_locks_and_preserves_lock_without_source_answers(self):
        with patch.object(sys, 'frozen', True, create=True):
            with self.assertRaisesRegex(ValueError, '缺失'):
                refuse_locked_match('123', self.root)
            (self.root / 'data').mkdir()
            (self.root / 'data/validation-locks.json').write_text(json.dumps(
                {'schema_version': 1, 'heldout_matches': ['456']}))
            refuse_locked_match('123', self.root)
            for value in ('456', 456):
                with self.assertRaisesRegex(ValueError, '封存'):
                    refuse_locked_match(value, self.root)

    def test_pyc_origins_use_only_existing_same_module_public_source(self):
        origin = self.root / 'adapter.pyc'
        source = origin.with_suffix('.py')
        self.assertEqual(packaged_source(origin), origin)
        source.write_text('# generated source')
        self.assertEqual(packaged_source(origin), source)
        self.assertEqual(packaged_source(source), source)


if __name__ == '__main__':
    unittest.main()
