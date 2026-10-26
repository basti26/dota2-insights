"""BP discovery and report integration from independently generated records."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from dota2_insights.draft_phase_index import discover, read_index, resolve_reference, sync
from tools.review_closed_record_content import generate
from tools.review_recordings import run


class DraftIndexTests(unittest.TestCase):
    def setUp(self):
        from test_draft_phase_handoff import DraftPhaseTests
        self.fixture = DraftPhaseTests()
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
        shutil.copytree(self.fixture.root / 'drafts', base / 'drafts')
        return base

    def test_discovery_deduplicates_release_and_does_not_rewrite_sources(self):
        base = self.copy_release()
        before = {p: p.read_bytes() for p in self.fixture.folder.iterdir()}
        index = sync(self.project, self.roots)
        self.assertEqual(len(index['entries']), 1)
        self.assertEqual(index['duplicate_packages_omitted'], 1)
        self.assertEqual(before, {p: p.read_bytes() for p in self.fixture.folder.iterdir()})
        self.assertEqual(read_index(self.project / 'diagnostics/draft-phase-handoff/index.json')['entries'], index['entries'])
        only = sync(self.project, {'helper_releases': self.releases})
        self.assertEqual(resolve_reference(only['entries'][0], self.roots), (base, self.fixture.sid))

    def test_unknown_layout_and_escaping_reference_are_refused(self):
        shutil.copytree(self.fixture.root / 'drafts', self.releases / 'unrelated/drafts')
        self.assertEqual(discover({'helper_releases': self.releases}), [])
        entry = sync(self.project, self.roots)['entries'][0]
        for source in ('../secret', 'config/private.local.json', '/absolute', 'drafts/../phase-handoff.json'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                resolve_reference({**entry, 'source': source}, self.roots)

    def test_explicit_late_binding_preferred_over_unbound_copy(self):
        self.fixture.unbound()
        self.copy_release()
        digest = self.fixture.bind()
        index = sync(self.project, self.roots)
        self.assertEqual(len(index['entries']), 1)
        self.assertEqual(index['entries'][0]['binding_package_id'], digest)
        self.assertEqual(index['entries'][0]['effective_match_id'], '12345')

    def test_conflicting_explicit_bindings_block_whole_duplicate_group(self):
        self.fixture.unbound()
        self.fixture.bind()
        self.copy_release()
        self.fixture.bind('12346')
        index = sync(self.project, self.roots)
        self.assertEqual(len(index['entries']), 1)
        self.assertEqual(index['entries'][0]['error_type'], 'conflicting_match_bindings')
        self.assertEqual(index['conflicting_binding_packages'], 1)
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['bp_phase_sessions_reviewed'], 0)
        self.assertEqual(len(result['bp_phase_skipped']), 1)

    def test_single_command_reports_bp_without_launcher_or_claiming_quality(self):
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['runs_reviewed'], 0)
        self.assertEqual(result['bp_phase_sessions_reviewed'], 1)
        bp = self.report(result)['bp_phase_reports'][0]
        self.assertEqual(bp['effective_match_id'], '12345')
        self.assertEqual(bp['linked_native_gsi_packages'], [])
        self.assertEqual(bp['streams_also_in_closed_launcher_scope'], [])
        self.assertFalse(bp['images_verified'])
        self.assertFalse(bp['recommendation_quality_verified'])

    def test_binding_added_after_index_requires_resync(self):
        self.fixture.unbound()
        run(self.project, roots=self.roots)
        self.fixture.bind()
        with self.assertRaisesRegex(ValueError, 'binding_receipt_changed'):
            generate(self.project, roots=self.roots)
        result = run(self.project, roots=self.roots)
        self.assertEqual(self.report(result)['bp_phase_reports'][0]['effective_match_id'], '12345')

    def test_tails_allowed_but_changed_prefix_refuses_new_report(self):
        run(self.project, roots=self.roots)
        path = self.fixture.folder / 'events.jsonl'
        with path.open('ab') as stream:
            stream.write(b'not-json-later-tail')
        self.assertEqual(generate(self.project, roots=self.roots)['bp_phase_sessions_reviewed'], 1)
        raw = path.read_bytes()
        path.write_bytes(raw.replace(b'selection', b'xxxxxion', 1))
        with self.assertRaises(ValueError):
            generate(self.project, roots=self.roots)

    def test_index_identity_tampering_refused(self):
        run(self.project, roots=self.roots)
        path = self.project / 'diagnostics/draft-phase-handoff/index.json'
        document = json.loads(path.read_text())
        document['entries'][0]['effective_match_id'] = '12346'
        path.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            generate(self.project, roots=self.roots)

    def test_match_link_requires_explicit_id_not_nearby_timestamps(self):
        from test_match_journals import MatchJournalTests
        gsi = MatchJournalTests()
        gsi.setUp()
        self.addCleanup(gsi.doCleanups)
        shutil.copytree(gsi.root / 'self-economy', self.fixture.root / 'self-economy')
        self.fixture.unbound()
        first = self.report(run(self.project, roots=self.roots))
        self.assertEqual(first['bp_phase_reports'][0]['linked_native_gsi_packages'], [])
        self.fixture.bind()
        second = self.report(run(self.project, roots=self.roots))
        self.assertEqual(second['bp_phase_reports'][0]['linked_native_gsi_packages'],
                         [second['native_reports'][0]['package_id']])

    def test_incomplete_binding_is_blocked_not_ignored(self):
        self.fixture.unbound()
        self.fixture.bind()
        (self.fixture.folder / 'phase-match-binding.COMPLETE').unlink()
        result = run(self.project, roots=self.roots)
        self.assertEqual(result['bp_phase_sessions_reviewed'], 0)
        self.assertEqual(result['bp_phase_skipped'][0]['state'], 'blocked')
