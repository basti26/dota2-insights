"""Automatic minimap reports from generated standalone data, no live capture."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.minimap_index import discover, read_index, resolve_reference, sync
from tools.review_closed_record_content import generate
from tools.review_recordings import run


class MinimapIndexTests(unittest.TestCase):
    def setUp(self):
        from test_minimap_handoff import MinimapHandoffTests
        self.fixture = MinimapHandoffTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name) / 'insights'
        self.project.mkdir()
        self.releases = Path(temp.name) / 'releases'
        self.releases.mkdir()
        self.roots = {'helper_history': self.fixture.root, 'helper_releases': self.releases}
        data = self.project / 'data'
        data.mkdir()
        (data / 'items.json').write_bytes(b'{}')
        (data / 'items.meta.json').write_text(json.dumps({'cache_sha256': hashlib.sha256(b'{}').hexdigest()}))

    def report(self, result):
        return json.loads((Path(result['report']).parent / 'report.json').read_text(encoding='utf-8'))

    def copy_release(self):
        base = self.releases / 'helper-fixture/Dota2Helper/diagnostics'
        shutil.copytree(self.fixture.root / 'minimap-learning', base / 'minimap-learning')
        return base

    def test_source_release_discovery_deduplicates_and_never_rewrites_source(self):
        base = self.copy_release()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.fixture.root.rglob('*') if p.is_file()}
        first = sync(self.project, self.roots)
        self.assertEqual(len(first['entries']), 1)
        self.assertEqual(first['duplicate_packages_omitted'], 1)
        self.assertEqual(read_index(self.project / 'diagnostics/minimap-run-handoff/index.json')['entries'], first['entries'])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})
        only = sync(self.project, {'helper_releases': self.releases})
        self.assertEqual(resolve_reference(only['entries'][0], self.roots), (base, self.fixture.run))

    def test_wrong_layout_traversal_and_bad_scan_limits_refused(self):
        shutil.copytree(self.fixture.root / 'minimap-learning', self.releases / 'unrelated/minimap-learning')
        self.assertEqual(discover({'helper_releases': self.releases}), [])
        entry = sync(self.project, self.roots)['entries'][0]
        for source in ('../secret', '/absolute', 'minimap-learning/run_x/session.json', 'config/private.local.json'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                resolve_reference({**entry, 'source': source}, self.roots)
        for limit in (True, 0, 129):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                discover(self.roots, latest=limit)

    def test_single_command_runs_without_launcher_and_keeps_match_unknown(self):
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['runs_reviewed'], 0)
        self.assertEqual(result['minimap_runs_reviewed'], 1)
        report = self.report(result)['minimap_run_reports'][0]
        self.assertIsNone(report['match_id'])
        self.assertFalse(report['source_evidence_complete'])
        self.assertFalse(report['images_verified'])
        self.assertFalse(report['identity_verified'])
        self.assertEqual(report['streams_also_in_closed_launcher_scope'], [])
        self.assertIn('比赛归属未知', Path(result['report']).read_text(encoding='utf-8'))

    def test_same_run_different_valid_receipts_block_whole_group(self):
        self.copy_release()
        self.fixture.rows[0]['captured_at_utc'] = '2026-10-26T00:00:01.1Z'
        self.fixture.publish()
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['minimap_conflicting_run_packages'], 1)
        self.assertEqual(result['minimap_runs_reviewed'], 0)
        index = read_index(self.project / 'diagnostics/minimap-run-handoff/index.json')
        self.assertEqual(index['entries'][0]['error_type'], 'conflicting_minimap_run_receipts')

    def test_source_changed_after_index_prevents_new_success_directory(self):
        from dota2_insights.helper_handoff import sync as sync_legacy
        sync_legacy(self.project, self.roots)
        sync(self.project, self.roots)
        (self.fixture.folder / 'samples.jsonl').write_bytes(b'{}\n')
        with self.assertRaises(ValueError):
            generate(self.project, roots=self.roots)
        self.assertFalse((self.project / 'diagnostics/record-content').exists())

    def test_resealed_source_after_index_requires_resync(self):
        run(self.project, roots=self.roots)
        self.fixture.rows[0]['captured_at_utc'] = '2026-10-26T00:00:01.1Z'
        self.fixture.publish()
        with self.assertRaisesRegex(ValueError, 'receipt_changed'):
            generate(self.project, roots=self.roots)
        self.assertEqual(run(self.project, roots=self.roots)['minimap_runs_reviewed'], 1)

    def test_index_identity_or_kind_tampering_is_refused(self):
        run(self.project, roots=self.roots)
        path = self.project / 'diagnostics/minimap-run-handoff/index.json'
        document = json.loads(path.read_text())
        document['entries'][0]['chain_id'] = 'b' * 32
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, 'index_identity_conflict'):
            generate(self.project, roots=self.roots)
        document['kind'] = 'native_match_journal_index'
        path.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            read_index(path)

    def test_missing_marker_is_explicit_blocked_entry_not_zero_success(self):
        (self.fixture.receipt / 'record-handoff.COMPLETE').unlink()
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['minimap_runs_reviewed'], 0)
        self.assertEqual(result['minimap_run_skipped'], [{'state': 'blocked', 'reason': 'minimap_run_handoff_not_ready'}])

    def test_empty_run_is_not_same_as_missing_channel(self):
        self.fixture.rows = []
        self.fixture.session.update(frames_saved=0, images_saved=0, capture_attempts=0)
        self.fixture.publish()
        report = self.report(run(self.project, roots=self.roots))['minimap_run_reports'][0]
        self.assertEqual(report['sample_rows'], 0)
        self.assertIsNone(report['first_sample_at_utc'])
        self.assertNotIn('samples.jsonl', report['missing_at_close'])
        self.assertIn('context.jsonl', report['missing_at_close'])

    def test_atomic_write_failure_keeps_prior_index_and_releases_lock(self):
        sync(self.project, self.roots)
        folder = self.project / 'diagnostics/minimap-run-handoff'
        old = (folder / 'index.json').read_bytes()
        with patch('dota2_insights.match_journal_index.os.replace', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                sync(self.project, self.roots)
        self.assertEqual((folder / 'index.json').read_bytes(), old)
        self.assertFalse((folder / 'sync.lock').exists())
        self.assertFalse(list(folder.glob('.index-*.tmp')))

    def test_closed_launcher_overlap_is_marked_per_stream_without_match_binding(self):
        from test_helper_handoff import HandoffTests
        from dota2_insights.helper_handoff import sync as sync_legacy
        legacy = HandoffTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        f = self.fixture
        document = legacy.document
        document['recorders'] = {}
        document['local_run_folders'] = {'minimap': [f.folder.name]}
        registry = f.root / 'field-recording' / ('run_' + 'b' * 32) / 'recorders.json'
        legacy.write(registry, document)
        sync_legacy(self.project, self.roots, quiet_seconds=0)
        sync(self.project, self.roots)
        result = generate(self.project, roots=self.roots)
        self.assertEqual(result['runs_reviewed'], 1)
        report = self.report(result)['minimap_run_reports'][0]
        self.assertEqual(report['streams_also_in_closed_launcher_scope'], ['samples.jsonl'])
        self.assertIsNone(report['match_id'])
        self.assertEqual(report['sample_rows'], 1)
