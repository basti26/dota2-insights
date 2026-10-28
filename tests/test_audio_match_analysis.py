"""Real classifier on generated signal; semantic reference access is observed."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dota2_insights.closed_audio_analysis import (open_match_analysis_inputs,
    open_analysis_inputs, bank_fingerprint, seal_receipt)


class AudioMatchAnalysisTests(unittest.TestCase):
    def setUp(self):
        from test_closed_audio_analysis import ClosedAudioAnalysisTests
        from test_audio_match_handoff import AudioMatchReaderTests
        self.base=base=ClosedAudioAnalysisTests();base.setUp();self.addCleanup(base.doCleanups)
        base.make_real_signal()
        self.native=f=AudioMatchReaderTests();f.setUp();self.addCleanup(f.doCleanups)
        capture=base.fixture.history/'match-audio'/f.root.name
        shutil.copytree(f.root,capture)
        f.root=capture;f.folder=capture/'closed-matches'/f.folder.name
        self.rows=deepcopy(base.audio.rows)
        for row in self.rows:
            row['recorded_source_scope']=dict(f.scope)
            shutil.copy2(base.audio.folder/row['audio_file'],capture/row['audio_file'])
        self.refs=[]
        for i in range(2):
            stamp=1793040000+i
            source={**f.source,'provider_at_utc':datetime.fromtimestamp(stamp,timezone.utc).isoformat(),
                    'gsi_source_id':'gsi_'+('c' if i==0 else 'd')*32}
            self.refs.append({'take_id':'f'*32,'audio_offset_s':.1+i,
                'recorded_at_utc':source['provider_at_utc'],'role':'post_test_reference_only',
                'context':{'match_id':'12345','game_session_id':'private_audio_session',
                    'provider_timestamp':stamp,'hero_id':'npc_dota_hero_synthetic',
                    'game_state':'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS','recorded_source_context':source,
                    'abilities':[{'name':'synthetic_skill','cooldown':0 if i==0 else 10}]}})
        self.write_native()
        self.output=base.root/'diagnostics/native-audio-analysis'
        self.args=SimpleNamespace(run=capture,bank=base.bank,output=self.output,engine='baseline',waveform_check=False)

    def write_native(self):
        specs=[]
        for name,rows in [('segments.jsonl',self.rows),('reference-gsi.jsonl',self.refs)]:
            raw=b''.join((json.dumps(row)+'\n').encode() for row in rows)
            (self.native.folder/name).write_bytes(raw)
            specs.append({'file':name,'bytes':len(raw),'rows':len(rows),'sha256':hashlib.sha256(raw).hexdigest()})
        self.native.doc['files']=specs;self.native.seal()

    def execute(self,inputs):
        from tools import analyze_match_audio
        with patch.object(analyze_match_audio,'ROOT',self.base.root):
            analyze_match_audio.analyze(self.args,inputs)

    def test_real_signal_runs_without_capture_summary_and_reference_is_after_freeze(self):
        import dota2_insights.audio_match_handoff as handoff
        original=handoff._rows;stages=[]
        def guarded(folder,spec,limit,*,decode=True):
            if spec['file']=='reference-gsi.jsonl':
                stages.append('semantic' if decode else 'byte_hash_only')
                if decode:self.assertTrue((self.output/'prediction-manifest.json').is_file())
            return original(folder,spec,limit,decode=decode)
        before=bank_fingerprint(self.base.bank)
        with patch.object(handoff,'_rows',side_effect=guarded):
            with open_match_analysis_inputs(self.native.folder,expected_package_id=self.native.package_id) as inputs:
                self.assertNotIn('recorded_source_scope',inputs.segment_rows()[0])
                self.assertEqual(set(inputs.segment_rows()[0]['take_label']),{'take_id'})
                self.assertFalse((self.native.root/'summary.json').exists())
                with self.assertRaisesRegex(ValueError,'requires_prediction_freeze'):list(inputs.reference_rows())
                self.execute(inputs)
                with self.assertRaisesRegex(ValueError,'after_verification_required'):
                    seal_receipt(self.output,inputs,before,before,package_id=inputs.package_id)
        receipt=seal_receipt(self.output,inputs,before,bank_fingerprint(self.base.bank),package_id=inputs.package_id)
        frozen=json.loads((self.output/'prediction-manifest.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(frozen['candidate_skills'].get('synthetic_skill',0),1)
        self.assertEqual(stages[0],'byte_hash_only')
        self.assertIn('semantic',stages)
        self.assertEqual(receipt['source_kind'],'native_audio_match_handoff')
        self.assertFalse(receipt['full_capture_shutdown_required'])
        self.assertFalse(receipt['accuracy_verified'])
        self.assertFalse(receipt['gsi_used_for_inference'])
        post=json.loads((self.output/'post-gsi-review.json').read_text(encoding='utf-8'))
        self.assertEqual(post['own_edge_skills'],{'synthetic_skill':1})

    def test_same_pcm_old_and_native_inputs_produce_identical_candidates(self):
        legacy=self.base.audio.folder/'reference-gsi.jsonl'
        legacy.write_bytes(b''.join((json.dumps(r)+'\n').encode() for r in self.refs))
        with open_analysis_inputs(self.base.audio.inspect(),self.base.fixture.roots,self.base.audio.folder) as inputs:
            self.base.execute(inputs)
        with open_match_analysis_inputs(self.native.folder) as inputs:
            self.execute(inputs)
        self.assertEqual((self.base.output/'predictions.jsonl').read_bytes(),(self.output/'predictions.jsonl').read_bytes())
        self.assertEqual((self.base.output/'scans.jsonl').read_bytes(),(self.output/'scans.jsonl').read_bytes())
        self.assertEqual((self.base.output/'post-gsi-review.json').read_bytes(),(self.output/'post-gsi-review.json').read_bytes())

    def test_waveform_change_blocks_final_receipt(self):
        with self.assertRaisesRegex(ValueError,'digest_mismatch'):
            with open_match_analysis_inputs(self.native.folder) as inputs:
                self.execute(inputs)
                (self.native.root/'audio-0001.wav').write_bytes(b'changed')
        self.assertFalse(inputs.after_verified)
        self.assertFalse((self.output/'analysis-receipt.json').exists())

    def test_reference_mutation_after_prediction_is_not_silently_used(self):
        with self.assertRaises(ValueError):
            with open_match_analysis_inputs(self.native.folder) as inputs:
                original=inputs.authorize_reference_review
                def authorize(output):
                    original(output)
                    (self.native.folder/'reference-gsi.jsonl').write_bytes(b'changed\n')
                inputs.authorize_reference_review=authorize
                self.execute(inputs)
        self.assertTrue((self.output/'prediction-manifest.json').exists())
        self.assertFalse((self.output/'analysis-receipt.json').exists())

    def test_prediction_mutation_cannot_unlock_reference(self):
        with self.assertRaisesRegex(ValueError,'prediction_freeze_invalid'):
            with open_match_analysis_inputs(self.native.folder) as inputs:
                original=inputs.authorize_reference_review
                def authorize(output):
                    original(output)
                    (output/'predictions.jsonl').write_bytes(b'changed\n')
                inputs.authorize_reference_review=authorize
                self.execute(inputs)
        self.assertFalse((self.output/'post-gsi-review.json').exists())
        self.assertFalse((self.output/'analysis-receipt.json').exists())

    def test_bad_reference_schema_is_deferred_but_cannot_complete_analysis(self):
        self.refs[0]['context']['recorded_source_context']['gsi_source_id']='bad'
        self.write_native()
        with self.assertRaises(ValueError):
            with open_match_analysis_inputs(self.native.folder) as inputs:self.execute(inputs)
        self.assertTrue((self.output/'prediction-manifest.json').exists())
        self.assertFalse((self.output/'analysis-receipt.json').exists())

    def test_incomplete_health_and_wrong_index_sha_refused(self):
        with self.assertRaisesRegex(ValueError,'index_source_changed'):
            with open_match_analysis_inputs(self.native.folder,expected_package_id='a'*64):self.fail('wrong index')
        self.native.doc['health']['limit_reached']=True;self.native.doc['recording_integrity']='gaps';self.native.seal()
        with self.assertRaisesRegex(ValueError,'gap_free_handoff'):
            with open_match_analysis_inputs(self.native.folder):self.fail('gapped capture')
        self.assertFalse(self.output.exists())

    def test_null_public_id_keeps_scope_in_receipt(self):
        f=self.native
        f.scope['match_id']=f.doc['match_id']=None
        f.doc['end_context']['match_id']=f.doc['end_context']['recorded_source_context']['match_id']=None
        for row in self.rows:row['recorded_source_scope']['match_id']=row['match_association']['match_id']=None
        for row in self.refs:row['context']['match_id']=row['context']['recorded_source_context']['match_id']=None
        from dota2_insights.audio_match_handoff import _scope_name
        target=f.root/'closed-matches'/_scope_name(f.scope)
        f.folder.rename(target);f.folder=target;self.write_native()
        before=bank_fingerprint(self.base.bank)
        with open_match_analysis_inputs(f.folder) as inputs:self.execute(inputs)
        receipt=seal_receipt(self.output,inputs,before,before,package_id=inputs.package_id)
        self.assertIsNone(receipt['source_scope']['match_id'])
        self.assertEqual(receipt['source_scope']['record_session_id'],f.scope['record_session_id'])
