"""Synthetic configuration, imports and locked IDs only; no historical reads."""
import ast
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from dota2_insights.history import history_location, history_roots, checked_input, checked_output

ROOT = Path(__file__).resolve().parents[1]


class ProjectBoundaryTests(unittest.TestCase):
    def test_missing_history_configuration_is_optional(self):
        with TemporaryDirectory() as folder:
            self.assertEqual(history_roots(Path(folder) / 'missing.json'), {})

    def test_nested_minimap_paths_resolve_to_analysis_repository(self):
        from dota2_insights.minimap import settings
        self.assertEqual(settings.ROOT, ROOT)
        self.assertEqual(settings.RECORDS, ROOT / 'diagnostics/minimap-learning')

    def test_history_configuration_is_read_only_and_never_creates_roots(self):
        with TemporaryDirectory() as folder:
            config = Path(folder) / 'history.json'
            target = Path(folder) / 'not_created'
            raw = json.dumps({'schema_version': 1, 'read_only': True,
                              'roots': {'helper_history': str(target)}}).encode()
            config.write_bytes(raw)
            self.assertEqual(history_roots(config)['helper_history'], target.resolve())
            with self.assertRaises(ValueError):
                history_location(config, 'helper_history')
            self.assertFalse(target.exists())
            self.assertEqual(config.read_bytes(), raw)

    def test_invalid_history_configurations_are_rejected(self):
        with TemporaryDirectory() as folder:
            config = Path(folder) / 'history.json'
            for value in ({'schema_version': True, 'read_only': True, 'roots': {}},
                          {'schema_version': 1, 'read_only': False, 'roots': {}},
                          {'schema_version': 1, 'read_only': True, 'roots': {'unknown': folder}},
                          {'schema_version': 1, 'read_only': True, 'roots': {'helper_history': '../helper'}}):
                config.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    history_roots(config)

    def test_no_helper_import_or_capture_dependency_in_analysis_package(self):
        forbidden = {'dota2_economy', 'dota2_minimap', 'mss', 'pyaudiowpatch', 'pyautogui'}
        for path in (ROOT / 'dota2_insights').rglob('*.py'):
            tree = ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                self.assertFalse(any(name.split('.')[0] in forbidden for name in names), str(path))

    def test_historical_inputs_are_explicit_and_outputs_cannot_write_history(self):
        with TemporaryDirectory() as folder:
            root = Path(folder) / 'insights'
            history = Path(folder) / 'records'
            (root / 'config').mkdir(parents=True)
            history.mkdir()
            (root / 'config/history.local.json').write_text(json.dumps({'schema_version': 1,
                'read_only': True, 'roots': {'helper_history': str(history)}}), encoding='utf-8')
            value = history / 'example.json'
            self.assertEqual(checked_input(root, value), value.resolve())
            with self.assertRaises(ValueError):
                checked_output(root, value)
            self.assertEqual(checked_output(root, root / 'diagnostics/new'), root / 'diagnostics/new')
            with self.assertRaises(ValueError):
                checked_input(root, Path(folder) / 'unconfigured.json')
            (root / 'config/history.local.json').write_text(json.dumps({'schema_version': 1,
                'read_only': True, 'roots': {'helper_history': str(root / 'diagnostics/old')}}), encoding='utf-8')
            with self.assertRaises(ValueError):
                checked_output(root, root / 'diagnostics/old/new.json')

    def test_cli_entry_without_helper_import_path(self):
        result = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/win_insights.py'), '--self-check'],
                                cwd=ROOT, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        value = json.loads(result.stdout)
        self.assertTrue(value['passed'])
        self.assertGreater(value['sealed_matches_refused'], 0)
        self.assertFalse(value['historical_payloads_read'])

    def test_sibling_tools_bootstrap_without_inheriting_the_tools_directory(self):
        for name in ('prepare_replay_item_audit.py', 'review_own_wards.py',
                     'review_replay_last_visible_inventory.py'):
            result = subprocess.run([sys.executable, '-I', str(ROOT / 'tools' / name), '--help'],
                                    cwd=ROOT, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_idle_analysis_window_does_not_start_parser(self):
        import tkinter as tk
        from unittest.mock import patch
        from tools import win_replay_analysis
        with TemporaryDirectory() as folder, patch.object(win_replay_analysis, 'ROOT', Path(folder)), \
                patch('subprocess.Popen', side_effect=AssertionError('parser started')):
            root = tk.Tk()
            root.withdraw()
            app = win_replay_analysis.ReplayAnalysisWindow(root)
            try:
                root.update_idletasks()
                self.assertFalse(app.queue.enabled)
                self.assertIsNone(app.queue.process)
            finally:
                app.close()
