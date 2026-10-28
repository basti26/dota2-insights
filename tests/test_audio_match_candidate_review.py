"""Native clips from completed generated-signal predictions; no real hearing."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import wave

from dota2_insights.audio_candidate_review import prepare_match,extract,_native_review_inputs
from dota2_insights.closed_audio_analysis import open_match_analysis_inputs,bank_fingerprint,seal_receipt


class NativeAudioCandidateReviewTests(unittest.TestCase):
    def setUp(self):
        from test_audio_match_analysis import AudioMatchAnalysisTests
        self.case=c=AudioMatchAnalysisTests();c.setUp();self.addCleanup(c.doCleanups)
        before=bank_fingerprint(c.base.bank)
        with open_match_analysis_inputs(c.native.folder) as inputs:c.execute(inputs)
        seal_receipt(c.output,inputs,before,bank_fingerprint(c.base.bank),package_id=inputs.package_id)
        self.output=c.base.root/'diagnostics/native-candidate-review'

    def prepare(self):
        c=self.case
        return prepare_match(c.native.folder,c.output,self.output,package_id=c.native.package_id)

    def test_native_cross_segment_PCM_is_exact_and_page_stays_unreviewed(self):
        from dota2_insights.audio_listening_review import build_listening_page,import_listening_notes,sha
        import dota2_insights.audio_match_handoff as source
        original=source._rows
        def guarded(folder,spec,limit,*,decode=True):
            if spec['file']=='reference-gsi.jsonl':self.assertFalse(decode,'clip export decoded GSI labels')
            return original(folder,spec,limit,decode=decode)
        with patch.object(source,'_rows',side_effect=guarded):result=self.prepare()
        c=self.case
        self.assertEqual(len(result['clips']),1)
        clip=result['clips'][0]
        self.assertEqual(clip['human_verdict'],'unreviewed')
        self.assertEqual(len(clip['sources']),2)
        with wave.open(str(self.output/clip['clip']),'rb') as stream:
            self.assertEqual(stream.getnframes(),28800)
            body=stream.readframes(28800)
        with wave.open(str(c.native.root/'audio-0001.wav'),'rb') as stream:first=stream.readframes(16000)
        with wave.open(str(c.native.root/'audio-0002.wav'),'rb') as stream:second=stream.readframes(12800)
        self.assertEqual(body,first+second)
        self.assertEqual(result['source_scope'],c.native.scope)
        self.assertFalse(result['full_capture_shutdown_required'])
        self.assertTrue(all(s['source']['source_package_id']==c.native.package_id for s in clip['sources']))
        page,count=build_listening_page(self.output/'review.json',self.output/'page')
        self.assertEqual(count,1)
        manifest=json.loads((page/'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['context_policy'],'candidate_context_visible_not_blind_not_independent')
        self.assertFalse(manifest['training_eligible'])
        labels={'kind':'audio_listening_notes','schema_version':1,'manifest_sha256':sha((page/'manifest.json').read_bytes()),
            'context_policy':manifest['context_policy'],'notes':[{'clip_id':v['clip_id'],'clip_sha256':v['sha256'],
                'verdict':'unreviewed','phase':'unknown','heard':'','comment':'','played':False} for v in manifest['clips']]}
        path=self.output/'generated-empty-notes.json';path.write_text(json.dumps(labels),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'no_reviewed_listening_notes'):import_listening_notes(page,path)
        self.assertFalse((page/'notes').exists())

    def test_missing_receipt_wrong_package_or_legacy_kind_cannot_export(self):
        c=self.case;path=c.output/'analysis-receipt.json';raw=path.read_bytes()
        path.unlink()
        with self.assertRaises(OSError):self.prepare()
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,'completed_closed_audio_analysis_required'):
            prepare_match(c.native.folder,c.output,self.output,package_id='0'*64)
        receipt=json.loads(raw);receipt.pop('source_kind')
        path.write_text(json.dumps(receipt),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'native_analysis_required'):self.prepare()
        self.assertFalse((self.output/'review.json').exists())

    def test_scope_receipt_conflict_is_not_relabelled(self):
        c=self.case;path=c.output/'analysis-receipt.json'
        receipt=json.loads(path.read_text(encoding='utf-8'))
        receipt['source_scope']['game_session_id']='game_'+'d'*32
        path.write_text(json.dumps(receipt),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'native_scope_conflict'):self.prepare()
        self.assertFalse((self.output/'review.json').exists())

    def test_native_capture_gap_is_not_silently_joined(self):
        c=self.case;c.rows[0]['ended_by']='field_paused';c.write_native()
        with _native_review_inputs(c.native.folder,c.native.package_id) as inputs:
            clip,reason=extract(inputs,{},'f'*32,.5,1.5)
        self.assertIsNone(clip)
        self.assertEqual(reason,'capture_gap_or_flush_boundary')

    def test_analysis_changed_during_export_has_no_final_marker(self):
        import dota2_insights.audio_candidate_review as module
        original=module.extract
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            (self.case.output/'predictions.jsonl').write_bytes(b'changed\n')
            return result
        with patch.object(module,'extract',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'analysis_changed_during_export'):self.prepare()
        self.assertFalse((self.output/'review.json').exists())

    def test_source_changed_during_export_has_no_final_marker(self):
        import dota2_insights.audio_candidate_review as module
        original=module.extract
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            (self.case.native.root/'audio-0001.wav').write_bytes(b'changed\n')
            return result
        with patch.object(module,'extract',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'digest_mismatch'):self.prepare()
        self.assertFalse((self.output/'review.json').exists())

    def test_package_can_be_pinned_by_completed_receipt_without_extra_hint(self):
        c=self.case
        result=prepare_match(c.native.folder,c.output,self.output)
        self.assertEqual(result['source_package_id'],c.native.package_id)
        self.assertEqual(len(result['clips']),1)
