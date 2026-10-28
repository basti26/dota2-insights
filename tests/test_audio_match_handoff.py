"""Independent authored contract/PCM fixtures; no live producer imports."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from dota2_insights.audio_match_handoff import verify, review, open_package, _scope_name


class AudioMatchReaderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)/('run_'+'f'*32); self.root.mkdir()
        self.scope = {'match_id':'12345', 'game_session_id':'game_'+'a'*32, 'record_session_id':'run_'+'b'*32}
        self.folder = self.root/'closed-matches'/_scope_name(self.scope); self.folder.mkdir(parents=True)
        self.source = {**self.scope, 'schema_version':1, 'gsi_source_id':'gsi_'+'c'*32,
            'provider_at_utc':datetime.fromtimestamp(1793040000,timezone.utc).isoformat(),
            'basis':'accepted_same_GSI_callback_not_audio_sample_time'}
        pcm = io.BytesIO()
        with wave.open(pcm,'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(16000)
            stream.writeframes(b'\x10\x00'*800)
        raw = pcm.getvalue(); (self.root/'audio-0001.wav').write_bytes(raw)
        self.segment = {'schema_version':1, 'audio_file':'audio-0001.wav', 'sha256':hashlib.sha256(raw).hexdigest(),
            'sample_rate':16000, 'channels':1, 'sample_width_bytes':2, 'audio_seconds':.05,
            'take_audio_start_offset_s':0., 'take_label':{'take_id':'e'*32},
            'skill_label':None, 'recognition_enabled':False,
            'timing_basis':'host_delivery_and_consume_boundaries_not_exact_audio_sample_clock',
            'match_association':{'match_id':'12345','game_session_id':'private_audio_session'},
            'recorded_source_scope':deepcopy(self.scope)}
        self.reference = {'take_id':'e'*32, 'audio_offset_s':.05, 'recorded_at_utc':'2026-10-26T18:40:00+00:00',
            'role':'post_test_reference_only', 'context':{'match_id':'12345', 'provider_timestamp':1793040000,
                'game_state':'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS', 'recorded_source_context':deepcopy(self.source)}}
        self.doc = {'schema_version':1, 'kind':'foresight_audio_match_handoff', **self.scope,
            'audio_capture_run_id':self.root.name,
            'end_context':{'match_id':'12345','provider_timestamp':1793040000,
                'game_state':'DOTA_GAMERULES_STATE_POST_GAME','clock_time_s':100,
                'recorded_source_context':deepcopy(self.source)},
            'end_request_consumed_after_tail_flush':True,
            'scope':'explicit_runtime_scope_audio_until_writer_consumed_post_game_not_whole_match_proof',
            'closed_at_utc':'2026-10-26T18:40:00+00:00',
            'waveforms':'immutable_capture_root_files_referenced_by_segments_sha_requires_reader_verification',
            'source_audio_run':'may_continue_recording_other_scopes',
            'health_scope':'cumulative_audio_worker_at_publication_not_per_match',
            'health':dict.fromkeys(('audio_error_present','reference_error_present','limit_reached','end_request_error_present'),False),
            'recording_integrity':'complete','other_channels':'not_closed_by_this_manifest'}
        self.write()

    def write(self):
        specs = []
        for name, rows in [('segments.jsonl',[self.segment]), ('reference-gsi.jsonl',[self.reference])]:
            raw = b''.join((json.dumps(r)+'\n').encode() for r in rows)
            (self.folder/name).write_bytes(raw)
            specs.append({'file':name,'bytes':len(raw),'rows':len(rows),'sha256':hashlib.sha256(raw).hexdigest()})
        self.doc['files'] = specs
        self.seal()

    def seal(self):
        raw = json.dumps(self.doc).encode()
        (self.folder/'manifest.json').write_bytes(raw)
        self.package_id = hashlib.sha256(raw).hexdigest()
        (self.folder/'COMPLETE').write_bytes(self.package_id.encode('ascii')+b'\n')

    def test_scope_is_readable_while_capture_writes_other_files(self):
        with open_package(self.folder) as package:
            (self.root/'summary.json').write_text('{"running":true}')
            (self.root/'segments.jsonl').write_text('another game is being appended\n')
            (self.root/'audio-0002.wav').write_bytes(b'unrelated later game')
            self.assertEqual(len(package['segments']),1)
        report = review(self.folder)
        self.assertEqual(report['segments_verified'],1)
        self.assertEqual(report['references_verified'],1)
        self.assertEqual(report['verified_audio_seconds'],.05)
        self.assertTrue(report['source_verified_before_and_after'])
        self.assertFalse(report['source_audio_run_must_be_stopped'])
        self.assertFalse(report['whole_match_coverage_verified'])
        self.assertFalse(report['skill_audibility_verified'])

    def test_missing_complete_rejected_before_journal_body(self):
        (self.folder/'COMPLETE').unlink()
        with patch('dota2_insights.audio_match_handoff._rows',side_effect=AssertionError('body too early')):
            with self.assertRaises(OSError):verify(self.folder)

    def test_locked_declaration_rejected_before_journals_or_waves(self):
        self.doc['match_id']='9056629627'; self.seal()
        with patch('dota2_insights.audio_match_handoff._rows',side_effect=AssertionError('sealed body touched')):
            with self.assertRaises(ValueError):verify(self.folder)

    def test_mutated_waveform_is_not_accepted_from_unchanged_metadata(self):
        (self.root/'audio-0001.wav').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'digest_mismatch'):verify(self.folder)

    def test_index_sha_and_exit_verification_detect_changed_package(self):
        old = self.package_id
        self.doc['closed_at_utc']='2026-10-26T18:40:01+00:00'; self.seal()
        with self.assertRaisesRegex(ValueError,'index_source_changed'):verify(self.folder,expected_package_id=old)
        with self.assertRaisesRegex(ValueError,'index_source_changed'):
            with open_package(self.folder):
                self.doc['closed_at_utc']='2026-10-26T18:40:02+00:00'; self.seal()

    def test_cross_scope_rows_rejected_even_with_fresh_hashes(self):
        for place in ('segment','reference','end'):
            old = deepcopy((self.segment,self.reference,self.doc))
            if place=='segment':self.segment['recorded_source_scope']['game_session_id']='game_'+'d'*32
            elif place=='reference':self.reference['context']['recorded_source_context']['record_session_id']='run_'+'d'*32
            else:self.doc['end_context']['recorded_source_context']['game_session_id']='game_'+'d'*32
            self.write()
            with self.subTest(place=place),self.assertRaises(ValueError):verify(self.folder)
            self.segment,self.reference,self.doc = old

    def test_path_traversal_budget_and_bad_end_not_opened(self):
        for change in ('path','budget','end'):
            old=deepcopy(self.doc)
            if change=='path':self.doc['files'][0]['file']='../segments.jsonl'
            elif change=='budget':self.doc['files'][0]['bytes']=64*1024**2+1
            else:self.doc['end_context']['game_state']='DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'
            self.seal()
            with self.subTest(change=change),patch('dota2_insights.audio_match_handoff._rows',side_effect=AssertionError('body too early')):
                with self.assertRaises(ValueError):verify(self.folder)
            self.doc=old

    def test_null_match_keeps_local_sessions_without_guessing(self):
        self.doc['match_id']=self.scope['match_id']=None
        self.segment['recorded_source_scope']['match_id']=self.segment['match_association']['match_id']=None
        self.reference['context']['match_id']=self.reference['context']['recorded_source_context']['match_id']=None
        self.doc['end_context']['match_id']=self.doc['end_context']['recorded_source_context']['match_id']=None
        other=self.root/'closed-matches'/_scope_name(self.scope)
        self.folder.rename(other);self.folder=other;self.write()
        report=review(self.folder)
        self.assertIsNone(report['match_id'])
        self.assertEqual(report['game_session_id'],self.scope['game_session_id'])

    def test_gap_health_is_preserved_and_must_agree(self):
        self.doc['health']['limit_reached']=True;self.seal()
        with self.assertRaisesRegex(ValueError,'health_invalid'):verify(self.folder)
        self.doc['recording_integrity']='gaps';self.seal()
        self.assertEqual(review(self.folder)['recording_integrity'],'gaps')
