"""Generated scope/WAV/registry fixtures, no live capture or external services."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights.audio_match_index import discover, resolve_reference, sync, read_index
from tools.review_recordings import run
from tools.review_closed_record_content import generate


class AudioMatchIndexTests(unittest.TestCase):
    def setUp(self):
        from test_audio_match_handoff import AudioMatchReaderTests
        self.fixture=f=AudioMatchReaderTests();f.setUp();self.addCleanup(f.doCleanups)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name)
        self.project=root/'insights';self.project.mkdir()
        self.history=root/'history';self.history.mkdir()
        self.releases=root/'releases';self.releases.mkdir()
        capture=self.history/'match-audio'/f.root.name
        shutil.copytree(f.root,capture)
        f.root=capture;f.folder=capture/'closed-matches'/f.folder.name
        self.roots={'helper_history':self.history,'helper_releases':self.releases}
        data=self.project/'data';data.mkdir()
        (data/'items.json').write_bytes(b'{}')
        (data/'items.meta.json').write_text(json.dumps({'cache_sha256':hashlib.sha256(b'{}').hexdigest()}))

    def report(self,result):
        return json.loads((Path(result['report']).parent/'report.json').read_text(encoding='utf-8'))

    def copy_release(self):
        base=self.releases/'helper-fixture/Dota2Helper/diagnostics'
        shutil.copytree(self.history/'match-audio',base/'match-audio')
        return base

    def test_source_release_dedup_and_reference_only_index(self):
        self.copy_release()
        before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in self.history.rglob('*') if p.is_file()}
        index=sync(self.project,self.roots)
        self.assertEqual(index['duplicate_packages_omitted'],1)
        self.assertEqual(len(index['entries']),1)
        self.assertEqual(read_index(self.project/'diagnostics/audio-match-handoff/index.json')['entries'],index['entries'])
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in before})
        self.assertFalse(list(self.project.rglob('*.wav')))
        self.assertTrue(index['index_time_is_not_data_time'])

    def test_single_command_reads_scope_without_closed_launcher(self):
        (self.fixture.root/'summary.json').write_text('{"running":true}')
        result=run(self.project,roots=self.roots)
        self.assertEqual(result['runs_reviewed'],0)
        self.assertEqual(result['audio_match_scopes_reviewed'],1)
        self.assertEqual(result['audio_match_segments_verified'],1)
        row=self.report(result)['audio_match_reports'][0]
        self.assertEqual(row['waveforms_also_in_closed_launcher_scope'],0)
        self.assertEqual(row['linked_native_gsi_packages'],[])
        self.assertFalse(row['native_GSI_journal_join_verified'])
        self.assertFalse(row['sample_exact_audio_GSI_timing_verified'])
        self.assertIn('逐局录音技术复核',Path(result['report']).read_text(encoding='utf-8'))

    def test_invalid_layout_limits_and_traversal(self):
        entry=sync(self.project,self.roots)['entries'][0]
        for source in ('../private','/absolute','config/local.json',entry['source']+'/manifest.json',
                       entry['source'].replace('match-audio','other')):
            with self.subTest(source=source),self.assertRaises(ValueError):
                resolve_reference({**entry,'source':source},self.roots)
        for limit in (True,0,129):
            with self.subTest(limit=limit),self.assertRaises(ValueError):discover(self.roots,latest=limit)

    def test_conflicting_valid_copies_block_whole_identity(self):
        self.copy_release()
        self.fixture.doc['closed_at_utc']='2026-10-26T18:40:01+00:00';self.fixture.seal()
        result=run(self.project,roots=self.roots)
        self.assertEqual(result['audio_match_conflicting_scope_packages'],1)
        self.assertEqual(result['audio_match_scopes_reviewed'],0)
        index=read_index(self.project/'diagnostics/audio-match-handoff/index.json')
        self.assertEqual(index['entries'][0]['error_type'],'conflicting_audio_match_receipts')

    def test_source_change_after_sync_prevents_new_success_report(self):
        from dota2_insights.helper_handoff import sync as sync_legacy
        sync_legacy(self.project,self.roots)
        sync(self.project,self.roots)
        (self.fixture.root/'audio-0001.wav').write_bytes(b'changed')
        with self.assertRaises(ValueError):generate(self.project,roots=self.roots)
        self.assertFalse((self.project/'diagnostics/record-content').exists())

    def test_resealed_source_requires_resync(self):
        run(self.project,roots=self.roots)
        self.fixture.doc['closed_at_utc']='2026-10-26T18:40:01+00:00';self.fixture.seal()
        with self.assertRaisesRegex(ValueError,'index_source_changed'):generate(self.project,roots=self.roots)
        self.assertEqual(run(self.project,roots=self.roots)['audio_match_scopes_reviewed'],1)

    def test_atomic_index_failure_preserves_previous_and_cleans_lock(self):
        sync(self.project,self.roots)
        folder=self.project/'diagnostics/audio-match-handoff'
        before=(folder/'index.json').read_bytes()
        with patch('dota2_insights.match_journal_index.os.replace',side_effect=OSError('generated')):
            with self.assertRaises(OSError):sync(self.project,self.roots)
        self.assertEqual(before,(folder/'index.json').read_bytes())
        self.assertFalse((folder/'sync.lock').exists())
        self.assertFalse(list(folder.glob('*.tmp')))

    def test_incomplete_manifest_becomes_unavailable_not_zero_audio(self):
        (self.fixture.folder/'COMPLETE').unlink()
        result=run(self.project,roots=self.roots)
        self.assertEqual(result['audio_match_scopes_reviewed'],0)
        self.assertEqual(len(result['audio_match_skipped']),1)
        self.assertIn('不作为零音频',Path(result['report']).read_text(encoding='utf-8'))

    def test_null_public_match_not_linked_by_time(self):
        f=self.fixture
        f.doc['match_id']=f.scope['match_id']=None
        f.segment['recorded_source_scope']['match_id']=f.segment['match_association']['match_id']=None
        f.reference['context']['match_id']=f.reference['context']['recorded_source_context']['match_id']=None
        f.doc['end_context']['match_id']=f.doc['end_context']['recorded_source_context']['match_id']=None
        from dota2_insights.audio_match_handoff import _scope_name
        target=f.root/'closed-matches'/_scope_name(f.scope)
        f.folder.rename(target);f.folder=target;f.write()
        result=run(self.project,roots=self.roots)
        row=self.report(result)['audio_match_reports'][0]
        self.assertIsNone(row['match_id'])
        self.assertEqual(row['linked_native_gsi_packages'],[])
        self.assertEqual(row['game_session_id'],f.scope['game_session_id'])

    def test_legacy_same_wav_overlap_is_marked_not_added(self):
        from test_audio_integrity_review import AudioIntegrityTests
        old=AudioIntegrityTests();old.setUp();self.addCleanup(old.doCleanups)
        legacy=old.fixture
        f=self.fixture
        target=old.folder/'closed-matches'/f.folder.name
        shutil.copytree(f.folder,target)
        f.folder=target;f.root=old.folder
        f.doc['audio_capture_run_id']=old.rid
        f.segment['sha256']=hashlib.sha256((old.folder/'audio-0001.wav').read_bytes()).hexdigest()
        f.write()
        roots={'helper_history':legacy.history}
        from dota2_insights.helper_handoff import sync as sync_legacy
        # Generated files have just been written; explicitly admit the closed
        # fixture without weakening the production quiet-period gate.
        sync_legacy(self.project,roots,quiet_seconds=0)
        sync(self.project,roots)
        result=generate(self.project,roots=roots)
        report=self.report(result)
        self.assertEqual(result['audio_segments_verified'],2)
        self.assertEqual(result['audio_match_segments_verified'],1)
        row=report['audio_match_reports'][0]
        self.assertEqual(row['waveforms_also_in_closed_launcher_scope'],1)
        self.assertTrue(row['counts_must_not_be_added_to_legacy'])
        # A copied portable run has a different path but the same declared
        # capture ID, file identity and verified bytes. Do not count it again.
        portable=self.releases/'helper-copied/Dota2Helper/diagnostics/match-audio'/old.rid
        shutil.copytree(old.folder,portable)
        (portable/'closed-matches'/f.folder.name/'manifest.json').touch()
        roots['helper_releases']=self.releases
        index=sync(self.project,roots)
        self.assertEqual(index['duplicate_packages_omitted'],1)
        copied=self.report(generate(self.project,roots=roots))['audio_match_reports'][0]
        self.assertEqual(copied['root_key'],'helper_releases')
        self.assertEqual(copied['legacy_same_path_overlap_count'],0)
        self.assertEqual(copied['waveforms_also_in_closed_launcher_scope'],1)
        self.assertTrue(copied['counts_must_not_be_added_to_legacy'])

    def test_exact_scope_links_native_gsi_but_same_times_other_session_does_not(self):
        from test_match_journals import MatchJournalTests
        gsi=MatchJournalTests();gsi.setUp();self.addCleanup(gsi.doCleanups)
        gsi.game=self.fixture.scope['game_session_id'];gsi.session=self.fixture.scope['record_session_id']
        gsi.folder=(self.history/'self-economy'/('self-economy-'+gsi.run)
                    /'closed-matches/match_12345'/gsi.game)
        gsi.folder.mkdir(parents=True)
        gsi.manifest.update(game_session_id=gsi.game,record_session_id=gsi.session)
        gsi.rows=[gsi.row(1,False),gsi.row(2,True)];gsi.publish()
        first=self.report(run(self.project,roots=self.roots))
        self.assertEqual(first['audio_match_reports'][0]['linked_native_gsi_packages'],
                         [first['native_reports'][0]['package_id']])
        gsi.session='run_'+'d'*32;gsi.manifest['record_session_id']=gsi.session
        gsi.rows=[gsi.row(1,False),gsi.row(2,True)];gsi.publish()
        second=self.report(run(self.project,roots=self.roots))
        self.assertEqual(second['audio_match_reports'][0]['linked_native_gsi_packages'],[])

    def test_index_identity_tampering_cannot_relabel_report(self):
        run(self.project,roots=self.roots)
        path=self.project/'diagnostics/audio-match-handoff/index.json'
        index=json.loads(path.read_text(encoding='utf-8'))
        index['entries'][0]['match_id']='54321'
        path.write_text(json.dumps(index),encoding='utf-8')
        before=set((self.project/'diagnostics/record-content').iterdir())
        with self.assertRaisesRegex(ValueError,'index_identity_conflict'):
            generate(self.project,roots=self.roots)
        self.assertEqual(before,set((self.project/'diagnostics/record-content').iterdir()))
