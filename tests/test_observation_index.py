"""Equipment indexing/combined reports use generated journals in owned folders."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.observation_index import discover, read_index, resolve_reference, sync
from tools.review_closed_record_content import generate
from tools.review_recordings import run


class EquipmentIndexTests(unittest.TestCase):
    def setUp(self):
        from test_observation_handoff import ObservationHandoffTests
        self.fixture = ObservationHandoffTests()
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

    def test_repeat_sync_persists_only_references_and_leaves_sources_unchanged(self):
        paths = [self.fixture.source, *self.fixture.folder.iterdir()]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths}
        first, second = sync(self.project, self.roots), sync(self.project, self.roots)
        self.assertEqual(first['entries'], second['entries'])
        index = read_index(self.project / 'diagnostics/equipment-handoff/index.json')
        self.assertEqual(index['entries'], second['entries'])
        self.assertNotIn('blink', json.dumps(index))
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in paths})

    def test_release_discovery_and_duplicate_package_omission(self):
        base = self.releases / 'helper-fixture/Dota2Helper/diagnostics'
        shutil.copytree(self.fixture.root / 'matches', base / 'matches')
        only = sync(self.project, {'helper_releases': self.releases})
        self.assertEqual(resolve_reference(only['entries'][0], self.roots), (base, self.fixture.session))
        both = sync(self.project, self.roots)
        self.assertEqual(len(both['entries']), 1)
        self.assertEqual(both['duplicate_packages_omitted'], 1)

    def test_unknown_layout_not_recursively_scanned(self):
        shutil.copytree(self.fixture.root / 'matches', self.releases / 'unrelated/matches')
        self.assertEqual(discover({'helper_releases': self.releases}), [])

    def test_reference_cannot_escape_or_point_at_configuration_or_gsi(self):
        entry = sync(self.project, self.roots)['entries'][0]
        for source in ('../secret.json', 'config/private.local.json', '/absolute', 'self-economy/private/samples.jsonl'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                resolve_reference({**entry, 'source': source}, self.roots)

    def test_single_command_reports_equipment_without_inventing_gsi(self):
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['runs_reviewed'], 0)
        self.assertEqual(result['native_matches_reviewed'], 0)
        self.assertEqual(result['equipment_sessions_reviewed'], 1)
        equipment = self.report(result)['equipment_reports'][0]
        self.assertFalse(equipment['also_in_closed_launcher_scope'])
        self.assertEqual(equipment['linked_native_gsi_packages'], [])
        self.assertFalse(equipment['images_verified'])
        self.assertFalse(equipment['accuracy_verified'])
        self.assertIn('未用UTC猜归属', Path(result['report']).read_text(encoding='utf-8'))

    def test_exact_identity_links_gsi_but_same_time_different_session_does_not(self):
        from test_match_journals import MatchJournalTests
        gsi = MatchJournalTests()
        gsi.setUp()
        self.addCleanup(gsi.doCleanups)
        gsi.game, gsi.session = self.fixture.game, self.fixture.session
        gsi.folder = (self.fixture.root / 'self-economy' / ('self-economy-' + gsi.run)
                      / 'closed-matches/match_12345' / gsi.game)
        gsi.folder.mkdir(parents=True)
        gsi.manifest.update(game_session_id=gsi.game, record_session_id=gsi.session)
        gsi.rows = [gsi.row(1, False), gsi.row(2, True)]
        gsi.publish()
        first = self.report(run(self.project, roots=self.roots))
        self.assertEqual(first['equipment_reports'][0]['linked_native_gsi_packages'], [first['native_reports'][0]['package_id']])
        # All UTC values and the game ID are unchanged; only capture session differs.
        gsi.session = 'run_' + 'd' * 32
        gsi.manifest['record_session_id'] = gsi.session
        gsi.rows = [gsi.row(1, False), gsi.row(2, True)]
        gsi.publish()
        second = self.report(run(self.project, roots=self.roots))
        self.assertEqual(second['equipment_reports'][0]['linked_native_gsi_packages'], [])

    def test_missing_marker_is_explicitly_skipped(self):
        (self.fixture.folder / 'observation-handoff.COMPLETE').unlink()
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['equipment_sessions_reviewed'], 0)
        self.assertEqual(result['equipment_skipped'], [{'state': 'blocked', 'reason': 'equipment_handoff_not_ready'}])

    def test_changed_payload_or_resealed_manifest_after_index_prevents_success_report(self):
        from dota2_insights.helper_handoff import sync as sync_legacy
        sync_legacy(self.project, self.roots)
        sync(self.project, self.roots)
        self.fixture.row['attempt']['accepted'] = False
        self.fixture.write_payload()
        with self.assertRaises(ValueError):
            generate(self.project, roots=self.roots)
        self.assertFalse((self.project / 'diagnostics/record-content').exists())

    def test_index_identity_tampering_is_refused(self):
        from dota2_insights.helper_handoff import sync as sync_legacy
        sync_legacy(self.project, self.roots)
        index = sync(self.project, self.roots)
        index['entries'][0]['game_session_id'] = 'game_' + 'd' * 32
        path = self.project / 'diagnostics/equipment-handoff/index.json'
        path.write_text(json.dumps(index))
        with self.assertRaisesRegex(ValueError, 'equipment_index_identity_conflict'):
            generate(self.project, roots=self.roots)

    def test_failed_atomic_update_preserves_prior_index(self):
        sync(self.project, self.roots)
        path = self.project / 'diagnostics/equipment-handoff/index.json'
        before = path.read_bytes()
        with patch('dota2_insights.match_journal_index.os.replace', side_effect=OSError):
            with self.assertRaises(OSError):
                sync(self.project, self.roots)
        self.assertEqual(before, path.read_bytes())
        self.assertFalse((path.parent / 'sync.lock').exists())

    def test_empty_payload_remains_no_observations_in_combined_report(self):
        self.fixture.source.unlink()
        self.fixture.manifest.update(payload=None, accepted_observations_at_cutoff=0)
        self.fixture.write_manifest()
        equipment = self.report(run(self.project, roots=self.roots))['equipment_reports'][0]
        self.assertFalse(equipment['channel_has_observations'])
        self.assertEqual(equipment['hero_observation_counts'], {})
        self.assertFalse(equipment['accuracy_verified'])

    def test_closed_launcher_observation_scope_is_marked_as_overlap(self):
        from test_helper_handoff import HandoffTests
        from dota2_insights.helper_handoff import sync as sync_legacy
        legacy = HandoffTests()
        legacy.setUp()
        self.addCleanup(legacy.doCleanups)
        f = self.fixture
        document = legacy.document
        document.update(started_at_utc='2026-10-26T00:00:00Z', closed_at_utc='2026-10-26T00:00:11Z',
            observed_match_ids=['12345'], game_sessions={f.game: {'record_session_id': f.session, 'match_id': '12345'}},
            recorders={'recording': {'shutdown_complete': True}})
        legacy.write(f.root / 'field-recording' / ('run_' + 'e' * 32) / 'recorders.json', document)
        legacy.write(f.folder / 'recording-summary.json', dict(legacy.closed, closed_at_utc='2026-10-26T00:00:10Z'))
        sync_legacy(self.project, self.roots, quiet_seconds=0)
        sync(self.project, self.roots)
        result = generate(self.project, roots=self.roots)
        self.assertEqual(result['runs_reviewed'], 1)
        self.assertTrue(self.report(result)['equipment_reports'][0]['also_in_closed_launcher_scope'])

    def test_scan_bounds(self):
        for latest in (True, 0, 129):
            with self.subTest(latest=latest), self.assertRaises(ValueError):
                discover(self.roots, latest=latest)
