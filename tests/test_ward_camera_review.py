"""Closed synthetic evidence only; locks checked before event/image content."""
from copy import deepcopy
import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from dota2_insights.ward_camera_review import (
    _alt_motion_summary, _record_alt_motion, _return_motion_attachment, discover_and_review, review,
)
from dota2_insights.ward_ground_pose import GroundPoseReview


class WardCameraReviewTests(unittest.TestCase):
    def test_alt_motion_requires_same_scope_order_and_three_distinct_images(self):
        motion={}
        rows=[]
        for i in range(3):
            row={'kind':'location','frame_id':f'frame-{i}','captured_monotonic':10.+.5*i,
                 'width':2560,'height':1440,'reference_epoch':1,
                 'context':{'match_id':None,'game_session_id':'game','record_session_id':'record',
                            'gsi_source_id':f'gsi-{i}'},
                 'image':{'file':f'frame-{i}.jpg'},
                 'alt_motion_window':{'version':'ward_alt_motion_window_v1','id':'a'*32,'index':i,
                                      'alt_requested':True,'camera_cut_verified':False}}
            rows.append(row)
            _record_alt_motion(motion,row,2)
            self.assertEqual(_alt_motion_summary(motion)['complete_image_windows'],int(i==2))
        self.assertFalse(_alt_motion_summary(motion)['camera_cut_verified'])
        motion={}
        _record_alt_motion(motion,rows[0],2)
        bad=deepcopy(rows[1]);bad['context']['game_session_id']='other'
        with self.assertRaisesRegex(ValueError,'window_order'):
            _record_alt_motion(motion,bad,2)
        bad=deepcopy(rows[1]);bad['alt_motion_paint_status']='paint_candidate_recorded'
        with self.assertRaisesRegex(ValueError,'paint_status'):
            _record_alt_motion(motion,bad,2)
        rows[1]['alt_motion_paint_status']='no_prior_paint'
        del rows[1]['image']
        for row in rows[1:]:
            _record_alt_motion(motion,row,2)
        self.assertEqual(_alt_motion_summary(motion)['complete_image_windows'],0)
        self.assertEqual(_alt_motion_summary(motion)['windows'][0]['frames'][1]['paint_status'],'no_prior_paint')

    def test_alt_motion_pixel_return_is_conditional_and_flat_frames_stay_unknown(self):
        import numpy as np
        from dota2_insights.ward_alt_motion_pixels import classify

        try:
            import cv2
        except ImportError:
            self.skipTest('optional local image backend')

        def terrain(seed):
            rng=np.random.default_rng(seed)
            image=np.full((360,640,3),45,dtype=np.uint8)
            for x,y,r,color in zip(rng.integers(18,622,400),rng.integers(15,345,400),
                                   rng.integers(2,13,400),rng.integers(70,255,400)):
                cv2.circle(image,(int(x),int(y)),int(r),(int(color),)*3,-1,cv2.LINE_AA)
            return image

        first,middle=terrain(17),terrain(20)
        last=np.roll(first,2,axis=1)
        with patch('dota2_insights.ward_alt_motion_pixels.read_rgb',side_effect=[first,middle,last]):
            result=classify([1,2,3],(640,360))
        self.assertEqual(result['state'],'view_return_candidate')
        self.assertFalse(result['camera_cut_verified'])
        self.assertFalse(result['ward_world_alignment_verified'])
        with patch('dota2_insights.ward_alt_motion_pixels.read_rgb',return_value=np.zeros_like(first)):
            self.assertEqual(classify([1,2,3],(640,360))['state'],'insufficient_texture')

    def test_alt_return_checks_painted_markers_against_unchanged_terrain(self):
        import numpy as np
        try:
            import cv2
        except ImportError:
            self.skipTest('optional local image backend')
        rng=np.random.default_rng(519)
        ground=np.full((540,960,3),60,dtype=np.uint8)
        for x,y,r,c in zip(rng.integers(50,910,900),rng.integers(40,500,900),
                           rng.integers(3,16,900),rng.integers(30,150,900)):
            cv2.circle(ground,(int(x),int(y)),int(r),(int(c),)*3,-1,cv2.LINE_AA)

        def paint(dx,frame):
            pixels=ground.copy()
            markers={'render_frame_id':frame,'points':{},'footprints':{}}
            for key,(x,y) in {'obs':(390+dx,240),'sentry':(530+dx,285)}.items():
                polygon=[[x-16,y-13],[x+16,y-13],[x+16,y+13],[x-16,y+13]]
                markers['points'][key]=[x,y]
                markers['footprints'][key]=polygon
                border=np.zeros(ground.shape[:2],dtype=np.uint8)
                cv2.polylines(border,[np.asarray(polygon)],True,255,2)
                mask=border!=0
                pixels[mask]=.42*pixels[mask]+.58*np.array((140,222,255))
            return pixels,markers

        first,old=paint(0,'first_render')
        for shift,expected in ((0,'relative_attachment_observed'),(24,'painted_marker_terrain_disagreement')):
            last,new=paint(shift,'last_render')
            paints={'first':('same',old),'last':('same',new)}
            files=['first','middle','last']
            with patch('dota2_insights.ward_minimap_pixels.read_rgb',side_effect=[first,last]):
                result=_return_motion_attachment(files,(960,540),dict(zip(files,files)),paints)
            self.assertEqual(result['status'],expected)
            self.assertFalse(result['absolute_world_alignment_verified'])

    def orientation_event(self):
        import math
        event=self.native_event();base=event['telemetry']
        target=[*base['look_at_xy'],149.22257]
        delta=[target[i]-base['camera_xyz'][i] for i in range(3)];distance=math.hypot(*delta)
        keys=('alt_epoch','log_generation','log_observed_monotonic','press_to_log_visible_candidate_ms',
            'read_chunk_sha256','received_at_utc','visible_in_read_byte_range',
            'exact_log_flush_latency_verified','game_control_sent','capture_frame_alignment_verified')
        value={k:base[k] for k in keys}
        value.update(kind='alt_camera_orientation_candidate_v1',position_candidate=base,look_at_xyz=target,
            orientation_candidate={'camera_to_target_distance':distance,
                'view_direction_unit':[x/distance for x in delta],
                'downward_pitch_degrees':math.degrees(math.atan2(-delta[2],math.hypot(*delta[:2]))),
                'yaw_xy_degrees':math.degrees(math.atan2(delta[1],delta[0])),
                'roll_degrees':None,'fov_degrees':None,'viewport':None,'projection_calibrated':False},
            data_time_utc=None,atomic_pair_verified=False,request_association_verified=False,
            projection_calibrated=False,live_usable=False)
        event['kind']='native_camera_orientation';event['telemetry']=value
        return event

    def test_orientation_is_independently_recomputed_not_a_complete_camera_certificate(self):
        self.native_header();self.save_events([self.orientation_event()])
        result=review(self.run)['native_camera_summary']
        self.assertEqual(result['orientation_candidate_events'],1)
        self.assertEqual(result['position_events'],0)
        self.assertEqual(result['complete_camera_pairs'],0)
        self.assertFalse(result['world_to_screen_verified'])
        self.assertFalse(result['capture_frame_alignment_verified'])

    def test_orientation_bad_geometry_epoch_or_claims_are_rejected(self):
        self.native_header()
        for change in ('pitch','target','epoch','fov','atomic','source'):
            event=self.orientation_event();v=event['telemetry']
            if change=='pitch':v['orientation_candidate']['downward_pitch_degrees']+=5
            elif change=='target':v['look_at_xyz'][0]+=1
            elif change=='epoch':v['alt_epoch']+=1
            elif change=='fov':v['orientation_candidate']['fov_degrees']=70
            elif change=='atomic':v['atomic_pair_verified']=True
            else:v['position_candidate']['live_usable']=True
            self.save_events([event])
            with self.subTest(change=change),self.assertRaises(ValueError):review(self.run)

    def test_sampling_policy_remains_declared_not_false_fixed_interval(self):
        self.header.update(schema_version=2,image_format='jpeg',jpeg_quality=90,
            native_image_dimensions_preserved=True,image_sampling_policy='window_triplet',
            image_window_period_s=300.,image_window_planned_count=3,image_interval_s=None)
        self.row['schema_version']=2
        self.save();r=review(self.run)
        self.assertIsNone(r['image_interval_s'])
        self.assertEqual(r['declared_image_sampling_policy'],'window_triplet')
        self.assertEqual(r['declared_image_window_period_s'],300.)
        self.assertEqual(r['declared_image_window_planned_count'],3)
        self.assertEqual(r['image_files_verified'],0)
        self.assertFalse(r['coordinate_accuracy_verified'])

    def test_stability_diagnostics_integrated_after_package_admission(self):
        self.row['projection_stability']={'version':'ward_motion_confirmation_v1',
            'accepted':False,'reason':'projection_confirming','coordinate_accuracy_verified':False}
        self.save();result=review(self.run)
        self.assertEqual(result['projection_diagnostics']['reason_counts'],{'projection_confirming':1})
        self.assertFalse(result['projection_diagnostics']['complete_overlap_verified'])
        self.row['projection_stability']['accepted']=True;self.save()
        with self.assertRaisesRegex(ValueError,'stability_claim'):review(self.run)

    def test_minimap_only_projection_is_accounted_for_without_claiming_world_alignment(self):
        terrain={'version':'ward_terrain_trial_v1','status':'experimental_visual_reference',
                 'reason':'terrain_minimap_registered','coordinate_accuracy_verified':False,
                 'raw_to_minimap_screen':[[1,0,0],[0,1,0],[0,0,1]],'minimap_roi':[0,500,200,200],
                 'registration_age_s':0,'minimap_markers':[{'id':'mini','x':100.,'y':600.,'color':'#39eb70',
                    'tree_cross':False,'surface':'minimap'}]}
        self.row['terrain_registration']=terrain
        self.save_events([self.row,self.surface()]);r=review(self.run)
        self.assertEqual(r['terrain_registration_counts']['stored_minimap_marker_observations'],1)
        self.assertEqual(r['surface_linkage']['contradictions'],{})
        for key,value in (('registration_age_s',4),('coordinate_accuracy_verified',True)):
            old=terrain[key];terrain[key]=value;self.save_events([self.row])
            with self.assertRaises(ValueError):review(self.run)
            terrain[key]=old
        terrain['minimap_markers'][0]['x']=300;self.save_events([self.row])
        with self.assertRaises(ValueError):review(self.run)

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

    def test_calibrated_minimap_retained_pairs_require_matching_scope_packet_and_pixels(self):
        import numpy as np
        from PIL import Image
        from dota2_insights.ward_minimap_pixels import OPACITY, PALETTE

        self.v2_image()
        points = [{'id': f'ward-{x}-{y}', 'x': float(x), 'y': float(y), 'color': '#ff4141',
                   'surface': 'minimap', 'tree_cross': False}
                  for x in (40,70,100,130) for y in (500,530,560,590)]
        rows=[];image_files={}
        for index,shown in enumerate((False,True,False),1):
            row=deepcopy(self.row)
            now=100.+index
            row.update(frame_id=f'frame-{index}',captured_monotonic=now,reference_epoch=2,
                reference_packet_sha256='b'*64,
                calibrated_minimap={'reason':'minimap_calibrated_hud_confirmed',
                    'minimap_markers':deepcopy(points)},
                calibrated_minimap_surface_sample={'shown':shown,'alt_held':shown,
                    'changed_monotonic':now-.2,'render_frame_id':f'render-{index}'})
            name=f'frame-{index:04d}.jpg'
            path = self.run / name
            pixels = np.full((720,1280,3), (38,52,70), dtype=np.uint8)
            if shown:
                for point in points:
                    x, y = int(point['x']), int(point['y'])
                    block = pixels[y-2:y+3,x-2:x+3]
                    block[:] = np.round(block*(1-OPACITY)+np.asarray(PALETTE[point['color']])*OPACITY)
            Image.fromarray(pixels).save(path, quality=90)
            content = path.read_bytes()
            image_files[name]={'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()}
            row['image'].update(file=name,**image_files[name])
            rows.append(row)
        def save():
            self.save_events(rows)
            self.header['files'].update(image_files);self.header['images_saved']=len(image_files)
            self.save_header()
        save()
        result=review(self.run)['calibrated_minimap']
        self.assertFalse(result['pixel_visibility_verified'])
        self.assertEqual(result['retained_surface_frames'],3)
        self.assertEqual(result['pixel_comparisons'], [])
        self.assertEqual(result['candidate_composited_pairs'],[
            {'shown_image':'frame-0002.jpg','hidden_image':f'frame-{index:04d}.jpg',
             'capture_gap_s':1.,'transition':transition,'marker_count':16}
            for index,transition in ((1,'press'), (3,'release'))])
        checked=discover_and_review({'helper_history':self.root}, analyze_minimap_pixels=True)
        results=checked['reports'][0]['calibrated_minimap']['pixel_results_by_transition']
        self.assertEqual(results, {edge:{'distributed_marker_color_pattern_observed':1}
                                   for edge in ('press','release')})
        rows[2]['calibrated_minimap_surface_sample']['changed_monotonic']=101.5
        save()
        self.assertEqual([pair['transition'] for pair in review(self.run)['calibrated_minimap']['candidate_composited_pairs']], ['press'])
        rows[1]['calibrated_minimap']['minimap_markers'][0]['x']+=1
        save()
        self.assertEqual(review(self.run)['calibrated_minimap']['candidate_composited_pairs'],[])
        rows[1]['calibrated_minimap_surface_sample']['alt_held']=False
        save()
        with self.assertRaisesRegex(ValueError,'ward_calibrated_minimap_surface_sample'):
            review(self.run)
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

    def native_event(self):
        from math import degrees,atan2
        camera=[412.293823,-5294.683594,1188.453125];xy=[412.293793,-4694.683594]
        return {'schema_version':2,'kind':'native_camera_position','context':deepcopy(self.row['context']),
            'projection_applied':False,'frame_id':None,'capture_frame_alignment_verified':False,
            'telemetry':{'kind':'alt_camera_position_v1','camera_xyz':camera,'look_at_xy':xy,
                'look_at_z':None,'pitch_degrees':None,'roll_degrees':None,'fov_degrees':None,'data_time_utc':None,
                'yaw_xy_degrees':degrees(atan2(xy[1]-camera[1],xy[0]-camera[0])),
                'atomic_pair_verified':False,'request_association_verified':False,'projection_calibrated':False,
                'live_usable':False,'exact_log_flush_latency_verified':False,'game_control_sent':False,
                'capture_frame_alignment_verified':False,'log_observed_monotonic':100.,
                'press_to_log_visible_candidate_ms':80.,'alt_epoch':1,'log_generation':1,
                'visible_in_read_byte_range':[1000,1250],'read_chunk_sha256':'a'*64,
                'received_at_utc':'2026-11-06T00:13:42+00:00'}}

    def native_header(self):
        self.header.update(schema_version=2,image_format='jpeg',jpeg_quality=90,
            native_image_dimensions_preserved=True)

    def test_native_positions_have_separate_summary_not_zero_pairs_or_projection(self):
        self.native_header();event=self.native_event();self.save_events([event])
        result=review(self.run)
        self.assertEqual(result['native_camera_summary']['position_events'],1)
        self.assertEqual(result['native_camera_summary']['complete_camera_pairs'],0)
        self.assertFalse(result['native_camera_summary']['world_to_screen_verified'])

    def test_native_transition_recomputes_distance_and_requires_prior_image(self):
        from dota2_insights.ward_camera_review import _native_transition_links
        old=self.native_event()['telemetry']
        new={**old,'look_at_xy':[old['look_at_xy'][0]+256.,old['look_at_xy'][1]],'log_observed_monotonic':102.}
        base={**self.row,'captured_monotonic':100.1,'frame_id':'before','image':{'file':'frame-0001.jpg'},
              'native_camera_telemetry':{'candidate':old}}
        current={**base,'captured_monotonic':102.1,'frame_id':'after','image':{'file':'frame-0002.jpg'},
                 'native_camera_telemetry':{'candidate':new}}
        sample={'version':'native_camera_transition_pair_v1','before_frame_id':'before',
                 'before_captured_monotonic':100.1,'look_at_delta_world_units':256.,
                 'capture_frame_alignment_verified':False}
        current['native_camera_transition_sample']=sample
        pairs=_native_transition_links([(1,base),(2,current)],[],6)
        self.assertEqual(pairs[0]['frame_gap_s'],2.)
        self.assertFalse(pairs[0]['both_exact_position_events_present'])
        with self.assertRaisesRegex(ValueError,'transition_before_image'):
            _native_transition_links([(1,current),(2,base)],[],6)
        sample['look_at_delta_world_units']=257.
        with self.assertRaisesRegex(ValueError,'transition_scope_or_geometry'):
            _native_transition_links([(1,base),(2,current)],[],6)

    def test_installed_map_boundary_receipt_never_authorizes_frame_binding(self):
        self.native_header();self.save_events([self.native_event()])
        self.assertIsNone(review(self.run)['installed_map_evidence'])
        fingerprint={'bytes':123,'sha256':'e'*64}
        base={'schema_version':1,'asset':'game/dota/maps/dota.vpk','status':'same_on_disk_at_boundaries',
              'entry':fingerprint,'exit':dict(fingerprint),'same_file_stat_at_boundaries':True,
              'runtime_map_identity_verified':False,'capture_map_binding_verified':False}
        self.header['installed_map_evidence']=base;self.save_header()
        result=review(self.run)
        self.assertEqual(result['installed_map_evidence'],base)
        self.assertFalse(result['native_calibration_readiness']['calibration_ready'])
        roots={'helper_history':self.root}
        for target,relation in (('e'*64,'both_boundaries_match_target'),('f'*64,'both_boundaries_differ_from_target')):
            inspected=discover_and_review(roots,target_map_sha256=target)
            self.assertEqual(inspected['reports'][0]['installed_map_relation'],relation)
            self.assertFalse(inspected['reports'][0]['native_calibration_readiness']['calibration_ready'])
            self.assertEqual(inspected['target_map_review']['run_counts'],{relation:1})
            self.assertFalse(inspected['target_map_review']['capture_map_binding_verified'])
        self.header['installed_map_evidence']={**base,'status':'changed_on_disk',
            'exit':{'bytes':123,'sha256':'f'*64},'same_file_stat_at_boundaries':False}
        self.save_header()
        self.assertEqual(discover_and_review(roots,target_map_sha256='e'*64)['target_map_review']['run_counts'],
                         {'installation_changed_during_run':1})
        for invalid in ({**base,'capture_map_binding_verified':True},
                        {**base,'exit':{'bytes':123,'sha256':'f'*64}},
                        {**base,'status':'probe_incomplete'}):
            self.header['installed_map_evidence']=invalid;self.save_header()
            with self.subTest(invalid=invalid),self.assertRaisesRegex(ValueError,'ward_installed_map'):
                review(self.run)

    def test_saved_image_links_exact_receipt_but_never_claims_same_frame(self):
        self.v2_image()
        position = self.native_event()
        orientation = self.orientation_event()
        self.row.update(captured_monotonic=100.1, native_camera_telemetry={
            'version': 'alt_camera_position_v1', 'projection_ready': False, 'live_usable': False,
            'game_control_sent': False, 'candidate_recent': True, 'candidate': position['telemetry'],
            'candidate_age_since_log_visible_s': .1})
        mismatch = deepcopy(position)
        mismatch['context']['gsi_source_id'] = 'gsi_' + 'e'*32
        unrelated = self.orientation_event()
        unrelated['telemetry']['position_candidate']['read_chunk_sha256'] = 'b'*64
        later = self.orientation_event()
        later['telemetry'].update(log_observed_monotonic=100.2, press_to_log_visible_candidate_ms=280.)
        competing = self.native_event()
        competing['telemetry'].update(log_observed_monotonic=100.05,
            press_to_log_visible_candidate_ms=130., alt_epoch=2, read_chunk_sha256='c'*64)
        for key in ('camera_xyz', 'look_at_xy'):
            competing['telemetry'][key][0] += 100
        after_capture = deepcopy(competing)
        after_capture['telemetry'].update(log_observed_monotonic=100.2, press_to_log_visible_candidate_ms=280.)
        image = self.row['image']
        for events, status, linked in (
                ([position, orientation], 'orientation_receipt_available_before_frame', True),
                ([position, later], 'orientation_observed_after_frame', True),
                ([position, unrelated], 'orientation_event_missing', True),
                ([position, orientation, orientation], 'orientation_event_ambiguous', True),
                ([position, orientation, competing], 'competing_position_receipt_before_frame', False),
                ([position, orientation, after_capture], 'orientation_receipt_available_before_frame', True),
                ([mismatch, orientation], 'position_event_missing', False),
                ([position, position, orientation], 'position_event_ambiguous', False)):
            with self.subTest(status=status):
                self.save_events([*events, self.row])
                self.header['files'][image['file']] = {k: image[k] for k in ('bytes', 'sha256')}
                self.save_header()
                result = review(self.run)['native_calibration_readiness']
                links = result['image_log_linkage']
                self.assertEqual(links['counts'].get('images_with_position_receipt_verified', 0), int(linked))
                self.assertEqual(links['counts'][status], 1)
                self.assertFalse(links['capture_frame_alignment_verified'])
                self.assertFalse(result['calibration_ready'])


    def test_native_unknown_geometry_bool_vectors_bad_clocks_or_projections_refused(self):
        self.native_header()
        for key,value in [('look_at_z',0),('fov_degrees',70),('live_usable',True),
                          ('camera_xyz',[True,0,1]),('press_to_log_visible_candidate_ms',751),
                          ('yaw_xy_degrees',0),('read_chunk_sha256','bad')]:
            event=self.native_event();event['telemetry'][key]=value;self.save_events([event])
            with self.subTest(key=key),self.assertRaises(ValueError):review(self.run)
        event=self.native_event();event['projection_applied']=True;self.save_events([event])
        with self.assertRaises(ValueError):review(self.run)

    def test_native_snapshots_and_run_limits_reject_stale_or_excess_records(self):
        self.native_header();event=self.native_event();self.row['schema_version']=2
        self.row['native_camera_telemetry']={'version':'alt_camera_position_v1','projection_ready':False,
            'live_usable':False,'game_control_sent':False,'candidate_recent':True,
            'candidate':event['telemetry'],'candidate_age_since_log_visible_s':.2}
        self.save_events([self.row]);review(self.run)
        self.assertEqual(review(self.run)['native_calibration_readiness']['counts']['frames_with_native_candidate'],1)
        self.row['native_camera_telemetry']['candidate_age_since_log_visible_s']=.6
        self.save_events([self.row])
        with self.assertRaises(ValueError):review(self.run)
        self.save_events([event]*129)
        with self.assertRaisesRegex(ValueError,'native_camera_event_limit'):review(self.run)

class GroundPoseEvidenceTests(unittest.TestCase):
    def test_stationary_image_requires_distinct_surrounding_pose_samples(self):
        for label,second_id,second_x,second_game,second_time,expected in (
            ('stationary','next',200,'game-1',11.,1),
            ('moves','next',250,'game-1',11.,0),
            ('repeated','before',200,'game-1',11.,0),
            ('new_game','next',200,'game-2',11.,0),
            ('expired','next',200,'game-1',13.,0),
        ):
            with self.subTest(label=label):
                probe=GroundPoseReview()
                first={'context':{'game_session_id':'game-1','record_session_id':'run-1','gsi_source_id':'before',
                    'own_hero_pose':{'alive':True,'hero_id':'npc_dota_hero_axe','xpos':200,'ypos':320,
                                     'coordinate_basis':'gsi_centered_world_xy'}},
                    'image':{'file':'frame-0001.jpg'},'width':1280,'height':720,
                    'self_screen_anchor':{'x':500,'y':300}}
                probe.add(first,10.5,10.)
                second={'context':deepcopy(first['context'])}
                second['context'].update(gsi_source_id=second_id,game_session_id=second_game)
                second['context']['own_hero_pose']['xpos']=second_x
                probe.add(second,second_time,second_time)
                result=probe.summary()
                self.assertEqual(len(result['bracketed_same_position']),expected)
                self.assertEqual(result['bracketed_measured_ring_count'],0)
                self.assertFalse(result['world_to_image_alignment_verified'])

    def test_partial_ring_pixels_reject_solid_disc_and_short_arcs(self):
        import cv2
        import numpy as np
        from dota2_insights.ward_partial_ring import locate

        for label,arc,stroke,color,expected in (
            ('partial',210,1,(236,236,236),True),
            ('short',95,1,(236,236,236),False),
            ('solid',360,-1,(236,236,236),False),
            ('colored',360,1,(40,155,230),False),
        ):
            with self.subTest(label=label):
                image=np.full((1440,2560,3),(36,61,65),np.uint8)
                cv2.ellipse(image,(1080,800),(68,55),8,30,30+arc,color,stroke,cv2.LINE_AA)
                candidate=locate(image,(1060,750))['candidate']
                self.assertEqual(candidate is not None,expected)
                if candidate is not None:
                    np.testing.assert_allclose(candidate['center_xy'],[1080,800],atol=1)
                    self.assertFalse(candidate['ownership_verified'])


class TerrainPatchPixelEvidenceTests(unittest.TestCase):
    def test_still_drawn_points_are_admitted_as_a_candidate_disagreement_pair(self):
        from dota2_insights.ward_camera_review import _terrain_patch_pairs

        same_pixels={'points':{'a':[500,300],'b':[600,320]}}
        self.assertEqual(_terrain_patch_pairs([
            ('same capture','a.jpg',same_pixels),('same capture','b.jpg',same_pixels),
            ('different capture','c.jpg',same_pixels),
        ]),[{'images':['a.jpg','b.jpg'],'marker_displacements_px':{}}])


if __name__=='__main__':unittest.main()
