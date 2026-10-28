"""Generated source/release captures; no game, ETW, helper imports or network."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights import performance_index as indexer
from tools.review_recordings import run
from tools.review_closed_record_content import generate


class PerformanceIndexTests(unittest.TestCase):
    def setUp(self):
        from test_performance_handoff import PerformanceHandoffTests
        self.fixture = f = PerformanceHandoffTests()
        f.setUp()
        self.addCleanup(f.doCleanups)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        self.project, self.history, self.releases = (base/name for name in ('insights','history','releases'))
        for folder in (self.project, self.history, self.releases):
            folder.mkdir()
        target = self.history/'game-frames'/f.capture_folder.name
        shutil.copytree(f.capture_folder, target)
        f.capture_folder, f.folder = target, target/'closed-match'
        self.roots = {'helper_history':self.history, 'helper_releases':self.releases}
        (self.project/'data').mkdir()
        (self.project/'data/items.json').write_bytes(b'{}')
        (self.project/'data/items.meta.json').write_text(json.dumps({'cache_sha256':hashlib.sha256(b'{}').hexdigest()}))

    def report(self, result):
        return json.loads((Path(result['report']).parent/'report.json').read_text(encoding='utf-8'))

    def copy_release(self):
        base = self.releases/'helper-generated/Dota2Helper/diagnostics'
        shutil.copytree(self.history/'game-frames', base/'game-frames')
        return base

    def test_source_portable_duplicates_do_not_copy_or_count_twice(self):
        self.copy_release()
        before = {p:p.read_bytes() for p in self.history.rglob('*') if p.is_file()}
        result = indexer.sync(self.project, self.roots)
        self.assertEqual(result['duplicate_packages_omitted'], 1)
        self.assertEqual(len(result['entries']), 1)
        self.assertEqual(indexer.read_index(self.project/'diagnostics/performance-match-handoff/index.json')['entries'], result['entries'])
        self.assertEqual(before, {p:p.read_bytes() for p in before})
        self.assertFalse(list(self.project.rglob('*.csv')))
        self.assertTrue(result['index_time_is_not_data_time'])

    def test_daily_command_reports_native_capture_without_closed_launcher(self):
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['runs_reviewed'], 0)
        self.assertEqual(result['performance_game_scopes_reviewed'], 0)
        self.assertEqual(result['performance_match_scopes_reviewed'], 1)
        row = self.report(result)['performance_match_reports'][0]
        self.assertFalse(row['also_in_closed_launcher_scope'])
        self.assertFalse(row['counts_must_not_be_added_to_legacy'])
        self.assertEqual(row['linked_native_gsi_packages'], [])
        self.assertEqual(row['matches'][0]['covered_game_seconds'], 2)
        self.assertIn('逐局性能复核', Path(result['report']).read_text(encoding='utf-8'))

    def test_valid_conflicting_receipts_block_entire_capture_identity(self):
        self.copy_release()
        self.fixture.document['closed_at_utc'] = '2026-10-27T00:01:00Z'
        self.fixture.seal()
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['performance_match_conflicting_capture_packages'], 1)
        self.assertEqual(result['performance_match_scopes_reviewed'], 0)
        entry = indexer.read_index(self.project/'diagnostics/performance-match-handoff/index.json')['entries'][0]
        self.assertEqual(entry['error_type'], 'conflicting_performance_capture_receipts')

    def test_unsafe_layout_or_traversal_is_never_resolved(self):
        entry = indexer.sync(self.project, self.roots)['entries'][0]
        for source in ('../private','/absolute','config/local.json',entry['source']+'/record-handoff.json',
                       entry['source'].replace('game-frames','other')):
            with self.subTest(source=source), self.assertRaises(ValueError):
                indexer.resolve_reference({**entry, 'source':source}, self.roots)
        for limit in (True,0,129):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                indexer.discover(self.roots, latest=limit)

    def test_missing_complete_is_unavailable_not_zero_fps(self):
        (self.fixture.folder/'COMPLETE').unlink()
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['performance_match_scopes_reviewed'], 0)
        self.assertEqual(len(result['performance_match_skipped']), 1)
        self.assertIn('不作为零FPS', Path(result['report']).read_text(encoding='utf-8'))

    def test_source_change_after_sync_prevents_success_report(self):
        from dota2_insights.helper_handoff import sync as sync_legacy
        sync_legacy(self.project, self.roots)
        indexer.sync(self.project, self.roots)
        (self.fixture.capture_folder/'frames.csv').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            generate(self.project, roots=self.roots)
        self.assertFalse((self.project/'diagnostics/record-content').exists())

    def test_resealed_manifest_or_index_identity_change_requires_resync(self):
        run(self.project, roots=self.roots)
        path = self.project/'diagnostics/performance-match-handoff/index.json'
        index = json.loads(path.read_bytes())
        index['entries'][0]['match_id'] = '54321'
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(ValueError, 'identity_conflict'):
            generate(self.project, roots=self.roots)
        indexer.sync(self.project, self.roots)
        self.fixture.document['closed_at_utc'] = '2026-10-27T00:02:00Z'
        self.fixture.seal()
        with self.assertRaisesRegex(ValueError, 'package_changed'):
            generate(self.project, roots=self.roots)
        self.assertEqual(run(self.project, roots=self.roots)['performance_match_scopes_reviewed'], 1)

    def test_atomic_index_write_failure_preserves_previous_and_releases_lock(self):
        indexer.sync(self.project, self.roots)
        folder = self.project/'diagnostics/performance-match-handoff'
        before = (folder/'index.json').read_bytes()
        with patch('dota2_insights.match_journal_index.os.replace', side_effect=OSError('generated')):
            with self.assertRaises(OSError):
                indexer.sync(self.project, self.roots)
        self.assertEqual(before, (folder/'index.json').read_bytes())
        self.assertFalse((folder/'sync.lock').exists())
        self.assertFalse(list(folder.glob('*.tmp')))

    def test_reserved_budget_blocks_before_opening_large_sources(self):
        with patch.object(indexer, 'MAX_READ_BYTES', 1), patch.object(indexer, 'verify', side_effect=AssertionError('body opened')):
            index = indexer.sync(self.project, self.roots)
        self.assertEqual(index['entries'][0]['error_type'], 'PerformanceIndexBudgetExceeded')

    def test_false_small_index_budget_cannot_bypass_source_budget(self):
        run(self.project, roots=self.roots)
        path = self.project/'diagnostics/performance-match-handoff/index.json'
        index = json.loads(path.read_bytes())
        index['entries'][0]['source_bytes'] = 0
        path.write_text(json.dumps(index))
        with patch('dota2_insights.performance_handoff.digest', side_effect=AssertionError('body opened')):
            with self.assertRaisesRegex(ValueError, 'source_budget'):
                generate(self.project, roots=self.roots)

    def test_null_public_match_is_preserved_without_gsi_time_guess(self):
        self.fixture.bound['match_id'] = None
        for row in self.fixture.rows:
            row['match_id'] = None
        self.fixture.write()
        result = self.report(run(self.project, roots=self.roots))['performance_match_reports'][0]
        self.assertIsNone(result['matches'][0]['match_id'])
        self.assertEqual(result['linked_native_gsi_packages'], [])

    def test_gsi_link_requires_same_public_match_and_both_sessions(self):
        from test_match_journals import MatchJournalTests
        gsi = MatchJournalTests()
        gsi.setUp()
        self.addCleanup(gsi.doCleanups)
        gsi.game = self.fixture.bound['game_session_id']
        gsi.session = self.fixture.bound['record_session_id']
        gsi.folder = (self.history/'self-economy'/('self-economy-'+gsi.run)
                      /'closed-matches/match_12345'/gsi.game)
        gsi.folder.mkdir(parents=True)
        gsi.manifest.update(game_session_id=gsi.game, record_session_id=gsi.session)
        gsi.rows = [gsi.row(1, False), gsi.row(2, True)]
        gsi.publish()
        first = self.report(run(self.project, roots=self.roots))
        self.assertEqual(first['performance_match_reports'][0]['linked_native_gsi_packages'],
                         [first['native_reports'][0]['package_id']])
        # Same public match/timestamps, different runtime session: no association.
        gsi.session = 'run_'+'d'*32
        gsi.manifest['record_session_id'] = gsi.session
        gsi.rows = [gsi.row(1, False), gsi.row(2, True)]
        gsi.publish()
        second = self.report(run(self.project, roots=self.roots))
        self.assertEqual(second['performance_match_reports'][0]['linked_native_gsi_packages'], [])

    def test_legacy_and_cross_path_portable_overlap_not_counted_as_new_samples(self):
        from test_performance_review import PerformanceReviewTests
        old = PerformanceReviewTests()
        old.setUp()
        self.addCleanup(old.doCleanups)
        legacy = old.fixture
        f = self.fixture
        f.capture_folder, f.folder = old.folder, old.folder/'closed-match'
        f.folder.mkdir()
        f.document['capture_run_id'] = old.run
        f.document['scope'] = {'match_id':'12345', 'game_session_id':legacy.gid, 'record_session_id':legacy.sid}
        (old.folder/'collector.log').write_bytes(b'')
        f.seal()
        roots = {'helper_history':legacy.history}
        from dota2_insights.helper_handoff import sync as sync_legacy
        sync_legacy(self.project, roots, quiet_seconds=0)  # Generated, explicitly closed fixture only.
        indexer.sync(self.project, roots)
        result = generate(self.project, roots=roots)
        self.assertEqual(result['performance_game_scopes_reviewed'], 1)
        self.assertEqual(result['performance_match_scopes_reviewed'], 1)
        row = self.report(result)['performance_match_reports'][0]
        self.assertTrue(row['also_in_closed_launcher_scope'])
        self.assertTrue(row['counts_must_not_be_added_to_legacy'])
        portable = self.releases/'helper-copy/Dota2Helper/diagnostics/game-frames'/old.folder.name
        shutil.copytree(old.folder, portable)
        (portable/'closed-match/record-handoff.json').touch()
        roots['helper_releases'] = self.releases
        self.assertEqual(indexer.sync(self.project, roots)['duplicate_packages_omitted'], 1)
        row = self.report(generate(self.project, roots=roots))['performance_match_reports'][0]
        self.assertEqual(row['root_key'], 'helper_releases')
        self.assertFalse(row['legacy_same_path_overlap'])
        self.assertTrue(row['also_in_closed_launcher_scope'])
