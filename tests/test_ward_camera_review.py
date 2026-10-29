"""Closed synthetic evidence only; locks checked before event/image content."""
from copy import deepcopy
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dota2_insights.ward_camera_review import review,discover_and_review


class WardCameraReviewTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.root=Path(t.name)
        self.run=self.root/'ward-camera'/('run_'+'a'*32);self.run.mkdir(parents=True)
        self.scope={'match_id':'123456789','game_session_id':'game_'+'b'*32,'record_session_id':'c'*32}
        self.row={'schema_version':1,'kind':'location','context':{**self.scope,'provider_at_utc':'2026-10-29T00:00:00Z'},
            'captured_at_utc':'2026-10-29T00:00:00Z','location_status':'unlocalized',
            'location_reason':'no_reference','world_to_screen':None,'projected_count':0,
            'width':1280,'height':720,'projected_markers':[],'projected_omitted':0,'frame_id':'frame-1'}
        self.row['context']['gsi_source_id']='gsi_'+'d'*32
        self.header={'schema_version':1,'kind':'ward_camera_evidence','run_id':'a'*32,'closed':True,'complete':True,
            'error_type':None,'coordinate_accuracy_verified':False,'contexts':[self.scope],
            'written':1,'images_saved':0,'dropped':0,'limited':False,'shutdown_timed_out':False}
        self.save()
    def save(self):
        content=(json.dumps(self.row)+'\n').encode()
        (self.run/'events.jsonl').write_bytes(content)
        self.header['files']={'events.jsonl':{'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}}
        raw=json.dumps(self.header).encode()
        (self.run/'CLOSED.json').write_bytes(raw);(self.run/'session.json').write_bytes(raw)
    def save_events(self, rows):
        raw=('\n'.join(json.dumps(r) for r in rows)+'\n').encode()
        (self.run/'events.jsonl').write_bytes(raw)
        self.header.update(written=len(rows),files={'events.jsonl':{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}})
        data=json.dumps(self.header).encode()
        (self.run/'CLOSED.json').write_bytes(data);(self.run/'session.json').write_bytes(data)
    def located(self):
        row=deepcopy(self.row)
        row.update(location_status='experimental_visual_reference',world_to_screen=[[1,0,0],[0,1,0],[0,0,1]],
            projected_count=1,projected_markers=[{'id':'ward-1','x':200.,'y':300.,'color':'#ff4141','tree_cross':False}],
            reprojection_error_px=.25)
        return row
    def surface(self, shown=True, reason='applied_to_tk_surface'):
        return {'schema_version':1,'kind':'surface','context':deepcopy(self.row['context']),
            'frame_id':'frame-1','shown':shown,'reason':reason,'os_visibility_verified':False}
    def test_exact_prior_location_links_to_surface_without_os_claim(self):
        self.save_events([self.located(),self.surface()]);result=review(self.run)
        self.assertEqual(result['surface_linkage']['matched_prior_location'],1)
        self.assertFalse(result['surface_linkage']['contradictions'])
        self.assertFalse(result['surface_linkage']['os_visibility_verified'])
        self.assertEqual(result['registration_error_px']['median'],.25)
        self.assertEqual(result['projection_summary']['stored_marker_observations'],1)
    def test_source_mismatch_never_joins_by_equal_time_or_frame(self):
        surface=self.surface();surface['context']['gsi_source_id']='gsi_'+'e'*32
        self.save_events([self.located(),surface]);result=review(self.run)['surface_linkage']
        self.assertEqual(result['matched_prior_location'],0)
        self.assertEqual(result['unknown_reasons'],{'no_recorded_location_for_exact_frame':1})
    def test_duplicate_or_future_location_remains_unknown(self):
        for rows,expected in [([self.located(),self.located(),self.surface()],'duplicate_location_identity'),
                              ([self.surface(),self.located()],'location_not_prior_to_surface')]:
            with self.subTest(expected=expected):
                self.save_events(rows);result=review(self.run)['surface_linkage']
                self.assertEqual(result['matched_prior_location'],0)
                self.assertEqual(result['unknown_reasons'],{expected:1})
    def test_shown_without_projection_and_hidden_reason_contradictions(self):
        self.save_events([self.row,self.surface()])
        self.assertEqual(review(self.run)['surface_linkage']['contradictions'],
            {'shown_without_localized_projected_points':1})
        self.save_events([self.located(),self.surface(False,'no_projected_points')])
        self.assertEqual(review(self.run)['surface_linkage']['contradictions'],
            {'hidden_as_no_points_but_projection_nonempty':1})
    def test_bad_or_duplicate_points_are_rejected(self):
        for change in ('duplicate','outside','nonfinite'):
            row=self.located()
            if change=='duplicate':row['projected_markers']*=2;row['projected_count']=2
            elif change=='outside':row['projected_markers'][0]['x']=1280
            else:row['projected_markers'][0]['x']=float('nan')
            self.save_events([row])
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'projection_point'):review(self.run)
    def test_omitted_points_and_unknown_error_are_not_zero_accuracy(self):
        row=self.located();row['projected_count']=257;row['projected_omitted']=1
        row['projected_markers']=[{**row['projected_markers'][0],'id':str(n)} for n in range(256)]
        row['reprojection_error_px']=None;self.save_events([row]);result=review(self.run)
        self.assertEqual(result['projection_summary']['omitted_marker_observations'],1)
        self.assertIsNone(result['registration_error_px']['median'])
        self.assertFalse(result['coordinate_accuracy_verified'])
    def v2_image(self):
        self.header.update(schema_version=2,image_format='jpeg',jpeg_quality=90,
            native_image_dimensions_preserved=True,image_interval_s=60.,images_saved=1)
        self.row.update(schema_version=2)
        content=b'generated encoding bytes checked only by SHA'
        image={'file':'frame-0001.jpg','bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),
            'width':1280,'height':720,'encoding':{'format':'jpeg','quality':90,'lossless':False}}
        self.row['image']=image
        self.save();(self.run/image['file']).write_bytes(content)
        self.header['files'][image['file']]={k:image[k] for k in ('bytes','sha256')}
        self.save_header()
    def save_header(self):
        data=json.dumps(self.header).encode()
        (self.run/'CLOSED.json').write_bytes(data);(self.run/'session.json').write_bytes(data)
    def test_v2_native_jpeg_is_explicitly_lossy_not_raw_pixel_proof(self):
        self.v2_image();r=review(self.run)
        self.assertEqual(r['declared_image_encoding_counts'],{'jpeg':1})
        self.assertFalse(r['image_pixels_decoded']);self.assertFalse(r['native_frame_pixel_hash_verified'])
        self.assertEqual(r['schema_version'],2)
    def test_v1_old_png_record_remains_supported(self):
        r=review(self.run);self.assertEqual(r['schema_version'],1)
        self.assertFalse(r['coordinate_accuracy_verified'])
    def test_v2_mixed_event_version_is_rejected(self):
        self.header.update(schema_version=2,image_format='jpeg',jpeg_quality=90,native_image_dimensions_preserved=True)
        self.save()
        with self.assertRaisesRegex(ValueError,'event_kind'):review(self.run)
    def test_v2_lying_lossless_or_dimension_claim_is_rejected(self):
        for key,value in (('encoding',{'format':'jpeg','quality':90,'lossless':True}),('width',1279)):
            self.v2_image();self.row['image'][key]=value
            raw=(json.dumps(self.row)+'\n').encode();(self.run/'events.jsonl').write_bytes(raw)
            self.header['files']['events.jsonl']={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()};self.save_header()
            with self.subTest(field=key),self.assertRaisesRegex(ValueError,'image_encoding_or_geometry'):review(self.run)
    def test_no_reference_is_evidence_not_map_success(self):
        r=review(self.run)
        self.assertEqual(r['location_status_counts'],{'unlocalized':1})
        self.assertFalse(r['coordinate_accuracy_verified']);self.assertFalse(r['os_visibility_verified'])
        self.assertEqual(discover_and_review({'helper_history':self.root})['reports'][0]['events'],1)
    def test_closed_loss_fields_must_be_explicit_and_typed(self):
        original=deepcopy(self.header)
        for key,value in [('dropped',None),('dropped',-1),('dropped',False),
                          ('limited',None),('limited',0),('shutdown_timed_out',None),
                          ('shutdown_timed_out',0),('images_saved',False)]:
            with self.subTest(field=key,value=value):
                self.header=deepcopy(original)
                if value is None:self.header.pop(key)
                else:self.header[key]=value
                self.save()
                with self.assertRaisesRegex(ValueError,'closed_loss_metadata'):review(self.run)
    def test_closed_false_loss_flags_are_not_inferred_from_missing_fields(self):
        self.header.pop('limited');self.save()
        with patch('dota2_insights.ward_camera_review._digest',side_effect=AssertionError('payload read')):
            with self.assertRaisesRegex(ValueError,'closed_loss_metadata'):review(self.run)
    def test_mutated_event_bytes_refused(self):
        (self.run/'events.jsonl').write_bytes(b'private changed body')
        with self.assertRaisesRegex(ValueError,'source_mismatch'):review(self.run)
    def test_locked_metadata_rejected_before_payload_hash(self):
        with patch('dota2_insights.ward_camera_review.refuse_locked_match',side_effect=ValueError('locked')), \
                patch('dota2_insights.ward_camera_review._digest',side_effect=AssertionError('payload read')):
            with self.assertRaisesRegex(ValueError,'locked'):review(self.run)
    def test_unclosed_not_read_or_inferred_running(self):
        (self.run/'CLOSED.json').unlink()
        with patch('dota2_insights.ward_camera_review._digest',side_effect=AssertionError('payload read')):
            r=discover_and_review({'helper_history':self.root})
        self.assertEqual(r['reports'],[]);self.assertEqual(r['not_closed_not_read'],1)
    def unpublished(self, **changes):
        self.header.update(changes);self.save();(self.run/'CLOSED.json').unlink()
    def test_failed_session_metadata_is_reported_without_payload_read(self):
        self.unpublished(closed=True,complete=False,error_type='OSError')
        with patch('dota2_insights.ward_camera_review._digest',side_effect=AssertionError('payload read')):
            result=discover_and_review({'helper_history':self.root})
        row=result['unpublished_sessions'][0]
        self.assertEqual(row['state'],'failed_closed_without_receipt');self.assertEqual(row['error_type'],'OSError')
        self.assertFalse(row['evidence_payload_read']);self.assertFalse(row['process_liveness_verified'])
        self.assertFalse(result['reports'])
    def test_unclosed_and_closed_without_receipt_are_distinct_but_not_live_claims(self):
        for closed,expected in [(False,'unclosed_liveness_unknown'),(True,'closed_without_receipt')]:
            with self.subTest(closed=closed):
                self.unpublished(closed=closed,complete=False)
                result=discover_and_review({'helper_history':self.root})['unpublished_sessions'][0]
                self.assertEqual(result['state'],expected);self.assertFalse(result['process_liveness_verified'])
    def test_untrusted_error_text_is_not_echoed(self):
        self.unpublished(closed=True,complete=False,error_type='private path or token')
        result=discover_and_review({'helper_history':self.root})
        self.assertEqual(result['unpublished_sessions'][0]['state'],'metadata_not_admitted')
        self.assertNotIn('private path',json.dumps(result))
    def test_pending_metadata_limit_does_not_imply_other_directories_are_empty(self):
        self.unpublished(closed=False,complete=False)
        other=self.run.with_name('run_'+'e'*32);other.mkdir()
        result=discover_and_review({'helper_history':self.root},latest=1)
        self.assertEqual(result['not_closed_not_read'],2)
        self.assertEqual(len(result['unpublished_sessions']),1)
        self.assertEqual(result['unpublished_metadata_omitted'],1)
    def test_unpublished_holdout_stays_blocked_without_payload_hash(self):
        self.unpublished(closed=True,complete=False,error_type='OSError')
        with patch('dota2_insights.ward_camera_review.refuse_locked_match',side_effect=ValueError('locked')), \
                patch('dota2_insights.ward_camera_review._digest',side_effect=AssertionError('payload read')):
            result=discover_and_review({'helper_history':self.root})
        self.assertEqual(result['unpublished_sessions'][0]['state'],'metadata_not_admitted')
        self.assertFalse(result['reports'])
    def test_receipt_published_during_metadata_check_requires_resync(self):
        from dota2_insights import ward_camera_review as module
        self.unpublished(closed=True,complete=False)
        original=module._header
        def racing(*args,**kwargs):
            value=original(*args,**kwargs);(self.run/'CLOSED.json').write_bytes(b'new publication');return value
        with patch.object(module,'_header',side_effect=racing):
            result=discover_and_review({'helper_history':self.root})
        self.assertEqual(result['unpublished_sessions'][0]['state'],'receipt_appeared_resync_required')
        self.assertFalse(result['reports'])
    def test_loss_retained_as_closed_with_gaps(self):
        self.header.update(complete=False,dropped=2);self.save()
        self.assertEqual(review(self.run)['state'],'closed_verified_with_gaps')
        self.header['complete']=True;self.save()
        with self.assertRaisesRegex(ValueError,'complete_with_loss'):review(self.run)
    def test_unknown_scope_and_unlocalized_projection_rejected(self):
        self.row['context']['match_id']='987654321';self.save()
        with self.assertRaisesRegex(ValueError,'undeclared_context'):review(self.run)
        self.row['context']['match_id']='123456789';self.row['world_to_screen']=[[1,0,0],[0,1,0],[0,0,1]];self.save()
        with self.assertRaisesRegex(ValueError,'unlocalized_projection'):review(self.run)
    def test_after_read_source_change_rejected(self):
        from dota2_insights import ward_camera_review as module
        original=module._digest;calls=[]
        def changed(path):
            calls.append(path)
            if len(calls)==2:return '0'*64
            return original(path)
        with patch.object(module,'_digest',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'source_changed'):review(self.run)
    def test_conflicting_closed_copies_are_not_arbitrarily_selected(self):
        other=self.root/'releases'/'foresight-test'/'Foresight'/'diagnostics'/'ward-camera'/self.run.name
        other.mkdir(parents=True)
        changed=deepcopy(self.header);changed['complete']=False
        (other/'CLOSED.json').write_text(json.dumps(changed))
        with patch('dota2_insights.ward_camera_review._digest',side_effect=AssertionError('payload read')):
            result=discover_and_review({'helper_history':self.root,'helper_releases':self.root/'releases'})
        self.assertFalse(result['reports']);self.assertEqual(result['skipped'][0]['state'],'conflicting_closed_versions')

if __name__=='__main__':unittest.main()
