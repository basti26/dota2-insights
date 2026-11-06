import math
import unittest
import cv2
import numpy as np
import test_native_camera_calibration as fixtures
from dota2_insights.native_circle_calibration import fit


class NativeCircleCalibrationTests(unittest.TestCase):
    def fixture(self,pitch=61.5,fov=58.5):
        pose,_,_=fixtures.NativeCameraCalibrationTests().fixture()
        camera=np.array([-5663.,-5737.,1309.])
        pose.update(camera_xyz=camera.tolist(),look_at_xy=[-5663.,-5137.],yaw_xy_degrees=90.)
        circles=[{'center_xyz':[-5712.,-4864.,256.],'radius':700.,'source_sha256':'a'*64},
                 {'center_xyz':[-5392.,-5192.,256.],'radius':700.,'source_sha256':'a'*64}]
        image=np.full((720,1280,3),45,np.uint8)
        p=math.radians(pitch)
        rotation=np.array([[1,0,0],[0,-math.sin(p),-math.cos(p)],[0,math.cos(p),-math.sin(p)]])
        focal=719/(2*math.tan(math.radians(fov)/2));center=np.array([639.5,359.5])
        angles=np.arange(720)*2*np.pi/720
        for circle in circles:
            world=np.array(circle['center_xyz'])+np.column_stack([700*np.cos(angles),700*np.sin(angles),np.zeros(720)])
            local=(world-camera)@rotation.T
            points=focal*local[:,:2]/local[:,2,None]+center
            cv2.polylines(image,[np.rint(points).astype(np.int32)],True,(40,245,40),2)
        return image,pose,circles

    def test_recovers_unseen_circle_parameters_without_building_sprite_centers(self):
        image,pose,circles=self.fixture()
        result=fit(image,pose,circles)
        self.assertEqual(result['status'],'conditional_circle_candidate',result)
        self.assertLess(abs(result['pitch_candidate_degrees']-61.5),.7)
        self.assertLess(abs(result['vertical_fov_candidate_degrees']-58.5),.7)
        self.assertFalse(result['world_to_screen_verified']);self.assertFalse(result['circle_identity_verified'])

    def test_blank_image_and_unknown_height_never_produce_camera(self):
        image,pose,circles=self.fixture()
        self.assertEqual(fit(np.full_like(image,45),pose,circles)['reason'],'insufficient_green_line_evidence')
        circles[0]['center_xyz'][2]=None
        self.assertEqual(fit(image,pose,circles)['status'],'unavailable')

    def test_circle_radius_source_is_required(self):
        image,pose,circles=self.fixture();circles[0]['source_sha256']=''
        self.assertEqual(fit(image,pose,circles)['status'],'unavailable')
        self.assertEqual(fit(image,pose,[None,None])['status'],'unavailable')
