import math
import unittest
import numpy as np

from dota2_insights.native_camera_calibration import fit,project


class NativeCameraCalibrationTests(unittest.TestCase):
    def fixture(self,pitch=63.217,yaw=90.,size=(2560,1440)):
        camera=np.array([400.,-5000.,1300.]);focal=1400.*size[1]/1440
        y,p=math.radians(yaw),math.radians(pitch)
        forward=np.array([math.cos(p)*math.cos(y),math.cos(p)*math.sin(y),-math.sin(p)])
        right=np.array([math.sin(y),-math.cos(y),0.]);up=np.cross(right,forward)
        rotation=np.stack([right,-up,forward]);center=(np.array(size)-1)/2
        pixels=np.array([[500,350],[1100,350],[1900,400],[550,900],[1400,1000],[2000,1050],
                         [750,450],[1850,550],[800,1050],[1800,1000]],float)*np.array(size)/[2560,1440]
        anchors=[]
        for i,pixel in enumerate(pixels):
            depth=950+i*100
            local=np.r_[(pixel-center)/focal*depth,depth]
            world=camera+rotation.T@local
            anchors.append({'id':str(i),'role':'fit' if i<6 else 'check',
                'coordinate_basis':'centered_world_xyz','source_sha256':'a'*64,
                'world_xyz':world.tolist(),'screen_xy':pixel.tolist()})
        pose={'kind':'alt_camera_position_v1','camera_xyz':camera.tolist(),
            'look_at_xy':(camera[:2]+600*np.array([math.cos(y),math.sin(y)])).tolist(),
            'yaw_xy_degrees':yaw,'look_at_z':None,'pitch_degrees':None,'roll_degrees':None,
            'fov_degrees':None,'data_time_utc':None,'atomic_pair_verified':False,
            'request_association_verified':False,'projection_calibrated':False,'live_usable':False,
            'exact_log_flush_latency_verified':False,'game_control_sent':False,
            'capture_frame_alignment_verified':False,'log_observed_monotonic':1.,
            'press_to_log_visible_candidate_ms':50.,'alt_epoch':1,'log_generation':1,
            'visible_in_read_byte_range':[100,300],'read_chunk_sha256':'a'*64,
            'received_at_utc':'2026-11-06T00:00:00+00:00'}
        return pose,anchors,focal

    def test_recovers_pitch_and_focal_without_using_target_z_or_fixed_60(self):
        for pitch,yaw,size in [(63.217,90.,(2560,1440)),(51.333,160.,(1920,1080))]:
            pose,anchors,focal=self.fixture(pitch,yaw,size)
            result=fit(pose,anchors,size)
            self.assertEqual(result['status'],'conditional_candidate',result)
            self.assertAlmostEqual(result['pitch_candidate_degrees'],pitch,places=2)
            self.assertAlmostEqual(result['focal_candidate_px'],focal,places=1)
            for anchor in anchors:
                self.assertTrue(np.allclose(project(result,anchor['world_xyz']),anchor['screen_xy'],atol=.03))
            self.assertFalse(result['live_usable']);self.assertFalse(result['capture_frame_alignment_verified'])

    def test_wrong_check_is_never_silently_fitted_or_dropped(self):
        pose,anchors,_=self.fixture()
        anchors[-1]['screen_xy'][0]+=35
        result=fit(pose,anchors,(2560,1440))
        self.assertEqual(result['reason'],'anchor_reprojection_failed')
        self.assertIsNone(result['matrix_3d'])

    def test_missing_height_or_shifted_world_origin_is_not_guessed(self):
        pose,anchors,_=self.fixture()
        anchors[0]['world_xyz'][2]=None
        self.assertEqual(fit(pose,anchors,(2560,1440))['reason'],'anchor_numeric_or_height_missing')
        pose,anchors,_=self.fixture()
        for a in anchors:a['world_xyz'][0]+=16384
        self.assertNotEqual(fit(pose,anchors,(2560,1440))['status'],'conditional_candidate')

    def test_roles_duplicate_points_and_false_native_claims_refused(self):
        pose,anchors,_=self.fixture()
        for a in anchors:a['role']='fit'
        self.assertEqual(fit(pose,anchors,(2560,1440))['reason'],'separate_fit_and_check_anchors_required')
        pose,anchors,_=self.fixture();anchors[-1]['world_xyz']=anchors[0]['world_xyz']
        self.assertEqual(fit(pose,anchors,(2560,1440))['reason'],'duplicate_anchor_coordinates')
        pose,anchors,_=self.fixture();pose['projection_calibrated']=True
        self.assertEqual(fit(pose,anchors,(2560,1440))['status'],'unavailable')

    def test_spatially_concentrated_anchors_do_not_identify_camera(self):
        pose,anchors,_=self.fixture()
        for i,a in enumerate(anchors):a['screen_xy']=[1200+i,700+i*.1]
        self.assertEqual(fit(pose,anchors,(2560,1440))['reason'],'anchors_spatially_degenerate')

    def test_project_hides_unknown_height_behind_camera_and_offscreen(self):
        pose,anchors,_=self.fixture();r=fit(pose,anchors,(2560,1440))
        self.assertIsNone(project(r,[0,0,None]))
        self.assertIsNone(project(r,[400,-5000,5000]))
        self.assertIsNone(project(r,[1e7,0,0]))

    def test_region_conflict_blocks_even_a_perfect_anchor_fit(self):
        pose,anchors,_=self.fixture()
        region={'version':'native_camera_region_check_v1','status':'conditional_conflict',
                'viewport_identity_verified':True}
        result=fit(pose,anchors,(2560,1440),region_check=region)
        self.assertEqual(result['reason'],'native_camera_frame_region_conflict')
        self.assertIsNone(result['matrix_3d'])
        region['status']='conditional_compatible'
        result=fit(pose,anchors,(2560,1440),region_check=region)
        self.assertEqual(result['status'],'conditional_candidate')
        self.assertFalse(result['live_usable'])
        region['viewport_identity_verified']=False
        self.assertEqual(fit(pose,anchors,(2560,1440),region_check=region)['reason'],'invalid_region_check')
