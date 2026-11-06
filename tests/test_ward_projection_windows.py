import unittest
from dota2_insights.ward_projection_diagnostics import ProjectionDiagnostics


def frame(index, shift=0, jitter=0):
    return {'frame_id':f'frame-{index}','captured_monotonic':10+index*.5,
        'width':1920,'height':1080,'reference_epoch':0,'reference_packet_sha256':'d'*64,
        'context':{'match_id':'123456789','game_session_id':'game1','record_session_id':'record1'},
        'camera_method':'ward_terrain_trial_v1','world_to_screen':[[1,0,shift],[0,1,0],[0,0,1]],
        'projected_markers':[{'id':'one','x':200+shift+jitter,'y':300}],
        'sampling_window':{'version':'ward_projection_window_v1','id':'a'*32,'index':index,
            'planned_count':3,'period_s':15.,'max_gap_s':1.2},
        'projection_stability':{'version':'ward_motion_confirmation_v1','accepted':True,
            'coordinate_accuracy_verified':False,'reason':'motion_compensated_projection',
            'raw_world_to_screen':[[1,0,shift],[0,1,0],[0,0,1]],
            'previous_to_current':[[1,0,100],[0,1,0],[0,0,1]],
            'previous_captured_monotonic':9.5+index*.5,'correction_max_px':abs(jitter)}}


class ProjectionWindowsTests(unittest.TestCase):
    def summary(self,*rows):
        d=ProjectionDiagnostics()
        for row in rows:d.add(row)
        return d.summary()['sampled_windows']

    def test_normal_camera_pan_is_not_reported_as_marker_jitter(self):
        result=self.summary(frame(0),frame(1,100),frame(2,200))
        self.assertEqual(result['counts']['complete_three_frame_windows'],1)
        self.assertEqual(result['screen_movement_px']['median'],100)
        self.assertEqual(result['motion_compensated_residual_px']['maximum'],0)
        self.assertFalse(result['continuous_gameplay_coverage_verified'])

    def test_visible_residual_retained_after_camera_motion_compensation(self):
        result=self.summary(frame(0),frame(1,100,3))
        self.assertEqual(result['counts']['incomplete_or_discontinuous_windows'],1)
        self.assertEqual(result['motion_compensated_residual_px']['maximum'],3)

    def test_raw_model_noise_is_visible_even_when_applied_transport_has_zero_residual(self):
        current=frame(1,100)
        current['projection_stability']['raw_world_to_screen'][0][2]=103
        result=self.summary(frame(0),current)
        self.assertEqual(result['motion_compensated_residual_px']['maximum'],0)
        self.assertEqual(result['raw_model_motion_compensated_residual_px']['maximum'],3)

    def test_wrong_previous_time_missing_points_or_long_gap_not_measured(self):
        bad=frame(1,100);bad['projection_stability']['previous_captured_monotonic']=0
        empty=frame(1,100);empty['projected_markers']=[]
        gap=frame(1,100);gap['captured_monotonic']=15
        for row in (bad,empty,gap):
            result=self.summary(frame(0),row)
            self.assertIsNone(result['motion_compensated_residual_px']['median'])
            self.assertTrue(result['unmeasured_pair_reasons'])

    def test_new_match_reference_method_or_client_size_not_joined(self):
        for field,value in [('reference_epoch',1),('width',2560),('camera_method','other')]:
            changed=frame(1,100);changed[field]=value
            self.assertIsNone(self.summary(frame(0),changed)['motion_compensated_residual_px']['median'])
        changed=frame(1,100);changed['context']['game_session_id']='different'
        self.assertIsNone(self.summary(frame(0),changed)['motion_compensated_residual_px']['median'])

    def test_duplicate_index_invalid_window_metadata_refused(self):
        with self.assertRaisesRegex(ValueError,'duplicate_index'):self.summary(frame(0),frame(0))
        for key,value in [('planned_count',4),('index',True),('id','bad'),('max_gap_s',5),('period_s',True)]:
            row=frame(0);row['sampling_window'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'window_metadata'):self.summary(row)

    def test_legacy_has_no_fake_zero_residual_or_complete_window(self):
        row=frame(0);row.pop('sampling_window')
        result=self.summary(row)
        self.assertEqual(result['window_count'],0)
        self.assertEqual(result['frames_without_window'],1)
        self.assertIsNone(result['motion_compensated_residual_px']['maximum'])
