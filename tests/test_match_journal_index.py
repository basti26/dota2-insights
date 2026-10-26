"""Source/release discovery and end-to-end reports from owned synthetic files."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.match_journal_index import discover, read_index, resolve_reference, sync
from dota2_insights.match_journal_review import review
from tools.review_closed_record_content import generate
from tools.review_recordings import run


class NativeIndexTests(unittest.TestCase):
    def setUp(self):
        from test_match_journals import MatchJournalTests
        self.fixture = MatchJournalTests()
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
        raw = b'{}'
        (data / 'items.json').write_bytes(raw)
        (data / 'items.meta.json').write_text(json.dumps({'cache_sha256': hashlib.sha256(raw).hexdigest()}))

    def copy_release(self):
        target = (self.releases / 'helper-fixture' / 'Dota2Helper' / 'diagnostics'
                  / self.fixture.folder.relative_to(self.fixture.root))
        shutil.copytree(self.fixture.folder, target)
        return target

    def test_repeat_sync_reuses_manifest_identity_without_changing_source(self):
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.fixture.folder.iterdir()}
        first, second = sync(self.project, self.roots), sync(self.project, self.roots)
        self.assertEqual(first['entries'], second['entries'])
        path = self.project / 'diagnostics/native-match-handoff/index.json'
        self.assertEqual(read_index(path)['entries'], second['entries'])
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.fixture.folder.iterdir()})

    def test_release_discovery_and_duplicate_package_omission(self):
        target = self.copy_release()
        only_release = sync(self.project, {'helper_releases': self.releases})
        entry = only_release['entries'][0]
        self.assertEqual(entry['root_key'], 'helper_releases')
        self.assertEqual(resolve_reference(entry, self.roots), target)
        both = sync(self.project, self.roots)
        self.assertEqual(len(both['entries']), 1)
        self.assertEqual(both['duplicate_packages_omitted'], 1)

    def test_unknown_release_layout_not_recursively_admitted(self):
        target = self.releases / 'unrelated' / self.fixture.folder.relative_to(self.fixture.root)
        shutil.copytree(self.fixture.folder, target)
        self.assertEqual(discover({'helper_releases': self.releases}), [])

    def test_source_reference_cannot_escape_or_redirect_to_configuration(self):
        entry = sync(self.project, self.roots)['entries'][0]
        for source in ('../secret.json', '/absolute', 'config/private.local.json', entry['source'] + '/manifest.json'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                resolve_reference({**entry, 'source': source}, self.roots)

    def test_expected_package_receipt_rejects_resealed_different_source(self):
        entry = sync(self.project, self.roots)['entries'][0]
        self.fixture.rows[0]['self_economy']['amount'] = 1
        self.fixture.publish()
        with self.assertRaisesRegex(ValueError, 'index_source_changed'):
            review(self.fixture.folder, expected_package_id=entry['package_id'])

    def test_failed_atomic_update_leaves_prior_index_and_releases_lock(self):
        sync(self.project, self.roots)
        path = self.project / 'diagnostics/native-match-handoff/index.json'
        before = path.read_bytes()
        with patch('dota2_insights.match_journal_index.os.replace', side_effect=OSError('fixture')):
            with self.assertRaises(OSError):
                sync(self.project, self.roots)
        self.assertEqual(before, path.read_bytes())
        self.assertFalse((path.parent / 'sync.lock').exists())
        self.assertFalse(list(path.parent.glob('*.tmp')))

    def test_corrupt_or_duplicate_index_identity_refused(self):
        value = sync(self.project, self.roots)
        value['entries'].append(dict(value['entries'][0]))
        path = self.project / 'diagnostics/native-match-handoff/index.json'
        path.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            read_index(path)

    def test_single_command_reviews_native_record_while_launcher_is_not_closed(self):
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['runs_reviewed'], 0)
        self.assertEqual(result['native_matches_reviewed'], 1)
        folder = Path(result['report']).parent
        document = json.loads((folder / 'report.json').read_text(encoding='utf-8'))
        native = document['native_reports'][0]
        self.assertEqual(native['last_reported_gsi']['amount'], 98)
        self.assertFalse(native['also_in_closed_launcher_scope'])
        self.assertTrue(native['source_verified_before_and_after'])
        self.assertIn('其他通道未由此交付', Path(result['report']).read_text(encoding='utf-8'))

    def test_index_identity_tampering_does_not_generate_success_report(self):
        from dota2_insights.helper_handoff import sync as sync_legacy
        sync_legacy(self.project, self.roots)
        value = sync(self.project, self.roots)
        value['entries'][0]['match_id'] = '99999'
        path = self.project / 'diagnostics/native-match-handoff/index.json'
        path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'index_identity_conflict'):
            generate(self.project, roots=self.roots)
        self.assertFalse((self.project / 'diagnostics/record-content').exists())

    def test_changed_payload_after_indexing_does_not_generate_success_report(self):
        from dota2_insights.helper_handoff import sync as sync_legacy
        sync_legacy(self.project, self.roots)
        sync(self.project, self.roots)
        with (self.fixture.folder / 'samples.jsonl').open('ab') as stream:
            stream.write(b'{}\n')
        with self.assertRaises(ValueError):
            generate(self.project, roots=self.roots)
        self.assertFalse((self.project / 'diagnostics/record-content').exists())

    def test_missing_marker_is_visible_as_gap_not_success(self):
        (self.fixture.folder / 'COMPLETE').unlink()
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['native_matches_reviewed'], 0)
        self.assertEqual(result['native_skipped'], [{'state': 'blocked', 'reason': 'native_handoff_not_ready'}])

    def test_discovery_limit_not_a_boolean_or_unbounded_scan(self):
        for limit in (True, 0, 129):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                discover(self.roots, latest=limit)

    def test_native_and_closed_launcher_overlap_is_marked_without_merging_counts(self):
        from test_helper_handoff import HandoffTests
        from dota2_insights.helper_handoff import sync as sync_legacy
        legacy = HandoffTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        f = self.fixture
        registry = f.root / 'field-recording' / ('run_' + 'a' * 32) / 'recorders.json'
        document = legacy.document
        document.update(started_at_utc='2026-10-26T00:00:00Z', closed_at_utc='2026-10-26T00:00:11Z',
                        observed_match_ids=[f.mid], game_sessions={f.game: {'record_session_id': f.session, 'match_id': f.mid}})
        document['recorders']['self_recording']['run_id'] = f.run
        legacy.write(registry, document)
        summary = dict(legacy.closed, run_id=f.run, closed_at_utc='2026-10-26T00:00:10Z')
        primary_folder = f.folder.parents[2]
        legacy.write(primary_folder / 'recording-summary.json', summary)
        (primary_folder / 'samples.jsonl').write_bytes((f.folder / 'samples.jsonl').read_bytes())
        sync_legacy(self.project, self.roots, quiet_seconds=0)
        sync(self.project, self.roots)
        result = generate(self.project, roots=self.roots)
        self.assertEqual(result['runs_reviewed'], 1)
        self.assertEqual(result['native_matches_reviewed'], 1)
        document = json.loads((Path(result['report']).parent / 'report.json').read_text(encoding='utf-8'))
        self.assertTrue(document['native_reports'][0]['also_in_closed_launcher_scope'])
        self.assertEqual(document['reports'][0]['own_gsi'][f.mid]['rows'], 2)
        self.assertEqual(document['native_reports'][0]['self_economy_audit']['rows'], 2)
