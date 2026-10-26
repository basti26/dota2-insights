"""Synthetic manifests/fixtures and hidden idle UI; no DEM or network."""
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dota2_insights import portable_app as app
from tools import build_insights_app as build


class PortableInsightsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.files = ['Dota2Insights.exe', 'runtime/replay/Dota2ReplayWorker.exe',
                      'runtime/replay/runtime-manifest.json', 'data/validation-locks.json']
        for name in self.files:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b'inert fixture never executed')
        (self.root / 'data/validation-locks.json').write_text(
            json.dumps({'schema_version': 1, 'heldout_matches': ['123']}), encoding='utf-8')
        self.manifest = {'kind': 'portable_dota2_insights', 'schema_version': 1,
                         'files': [{'path': name, 'sha256': app.digest(self.root / name)} for name in self.files]}
        self.save()

    def save(self):
        (self.root / 'build-manifest.json').write_text(json.dumps(self.manifest), encoding='utf-8')

    def test_manifest_verifies_exact_files_and_retains_sealed_role(self):
        value = app.verify_application(self.root)
        self.assertEqual(len(value['files']), 4)
        self.assertEqual(app.public_locks(self.root)['heldout_matches'], ['123'])

    def test_frozen_worker_does_not_prepend_the_ui_source_directory(self):
        import runpy
        entry = Path(__file__).resolve().parents[1] / 'tools/run_replay_job.py'
        executable = self.root / 'application/runtime/replay/Dota2ReplayWorker.exe'
        before = list(sys.path)
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(executable)), patch.object(sys, 'path', before.copy()):
            values = runpy.run_path(str(entry))
            self.assertEqual(values['ROOT'], self.root / 'application')
            self.assertEqual(sys.path, before)

    def test_changed_worker_and_missing_files_are_not_accepted(self):
        (self.root / self.files[1]).write_bytes(b'changed')
        with self.assertRaises(ValueError):
            app.verify_application(self.root)
        (self.root / self.files[1]).unlink()
        with self.assertRaises(ValueError):
            app.verify_application(self.root)

    def test_manifest_refuses_duplicate_parent_absolute_and_self_entries(self):
        for name in ('../outside', '/outside', 'C:/outside', 'x\\y', 'build-manifest.json', self.files[0]):
            with self.subTest(name=name):
                self.manifest['files'] = copy.deepcopy(self.manifest['files'][:4])
                self.manifest['files'].append({'path': name, 'sha256': '0' * 64})
                self.save()
                with self.assertRaises(ValueError):
                    app.verify_application(self.root)
                self.manifest['files'].pop()

    def test_missing_required_worker_cannot_be_hidden_by_a_shortened_roster(self):
        self.manifest['files'] = self.manifest['files'][:1]
        self.save()
        with self.assertRaises(ValueError):
            app.verify_application(self.root)

    def test_invalid_lock_types_are_rejected(self):
        path = self.root / 'data/validation-locks.json'
        for value in ({'schema_version': True, 'heldout_matches': []},
                      {'schema_version': 1, 'heldout_matches': [123]},
                      {'schema_version': 1, 'heldout_matches': ['../123']}):
            path.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError):
                app.public_locks(self.root)

    def test_source_mode_cannot_pass_frozen_check_or_overwrite_a_receipt(self):
        output = self.root / 'check.json'
        with patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(app.write_check(output, self.root), 1)
        value = json.loads(output.read_text(encoding='utf-8'))
        self.assertFalse(value['passed'])
        self.assertFalse(value['dem_parsed'])
        with self.assertRaises(ValueError):
            app.write_check(output, self.root)

    def test_private_error_contents_are_not_recorded_as_public_check_codes(self):
        output = self.root / 'check.json'
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(app, 'verify_application', side_effect=RuntimeError('private path and account')):
            self.assertEqual(app.write_check(output, self.root), 1)
        self.assertNotIn('private path', output.read_text(encoding='utf-8'))

    def test_hidden_ui_does_not_start_jobs_and_closes_cleanly(self):
        result = app.check_hidden_ui(self.root)
        self.assertTrue(result['hidden_form_opened_closed'])
        self.assertFalse(result['parser_started'])

    def test_public_inputs_ignore_private_records_and_config(self):
        names = [*('dota2_insights/' + n + '.py' for n in build.UI_MODULES),
                 'dota2_insights/minimap/__init__.py', 'dota2_insights/minimap/offline_io.py',
                 'tools/__init__.py', 'tools/win_insights.py', 'tools/win_replay_analysis.py',
                 'docs/PORTABLE_APP.md', 'LICENSE.txt', 'tcl/tcl8.6/license.terms', 'tcl/tk8.6/license.terms',
                 'config/history.local.json', 'diagnostics/private.dem']
        for name in names:
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('synthetic', encoding='utf-8')
        with patch.object(sys, 'base_prefix', str(self.root)):
            selected = build.public_inputs(self.root)
            targets = set(selected.values())
            self.assertIn('data/validation-locks.json', targets)
            self.assertNotIn('config/history.local.json', targets)
            self.assertNotIn('diagnostics/private.dem', targets)
            (self.root / 'tools/win_insights.py').unlink()
            with self.assertRaises(ValueError):
                build.public_inputs(self.root)
