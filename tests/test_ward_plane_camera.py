import unittest
import numpy as np
from dota2_insights.ward_plane_camera import recover,project


def camera(size=(1920,1080),focal=1400,yaw=.3,fy=None):
    angle=np.deg2rad(120);c,s=np.cos(angle),np.sin(angle)
    rx=np.array([[1,0,0],[0,c,-s],[0,s,c]])
    c,s=np.cos(yaw),np.sin(yaw)
    rotation=rx @ np.array([[c,-s,0],[s,c,0],[0,0,1]])
    k=np.array([[focal,0,(size[0]-1)/2],[0,focal if fy is None else fy,(size[1]-1)/2],[0,0,1]])
    center=np.array([16384.,14384.,2000.]);t=-rotation@center
    p=k @ np.column_stack([rotation,t])
    return p,p[:,[0,1,3]]


class PlaneCameraTests(unittest.TestCase):
    def test_automatic_review_keeps_candidate_unverified_and_excludes_inferred_outlines(self):
        from dota2_insights.ward_projection_diagnostics import ProjectionDiagnostics
        p,h=camera();row={'world_to_screen':h.tolist(),'camera_method':'ward_terrain_trial_v1',
                         'camera_geometry_inferred':False,'width':1920,'height':1080,'frame_id':'generated'}
        d=ProjectionDiagnostics();d.add(row)
        result=d.summary()['plane_camera_diagnostics']
        self.assertEqual(result['reason_counts'],{'conditional_metric_plane_camera':1})
        self.assertFalse(result['examples'][0]['camera_model_verified'])
        row['camera_geometry_inferred']=True;d=ProjectionDiagnostics();d.add(row)
        self.assertFalse(d.summary()['plane_camera_diagnostics']['examples'])

    def test_known_perspective_camera_recovers_elevated_points_and_native_resolutions(self):
        for size in [(1920,1080),(2560,1440)]:
            for yaw in [0,.3,-.4]:
                p,h=camera(size,yaw=yaw);result=recover(h,size)
                self.assertEqual(result['status'],'conditional_candidate',result)
                self.assertAlmostEqual(result['focal_candidate_px'],1400,places=5)
                self.assertAlmostEqual(result['camera_height_candidate'],2000,places=5)
                for z in [0,128,256,768]:
                    xyz=[16484.,16584.,z];q=p @ [*xyz,1]
                    np.testing.assert_allclose(project(result,xyz),q[:2]/q[2],atol=1e-6)
                self.assertFalse(result['camera_model_verified'])

    def test_arbitrary_homography_scale_does_not_change_depth_or_height(self):
        p,h=camera()
        for scale in [-1000.,.0001,3.]:
            result=recover(h*scale,(1920,1080))
            self.assertEqual(result['status'],'conditional_candidate')
            np.testing.assert_allclose(project(result,[16400,16400,256]),project(recover(h,(1920,1080)),[16400,16400,256]),atol=1e-6)

    def test_affine_or_singular_planes_cannot_produce_fake_camera(self):
        for h in [np.eye(3),np.diag([.8,-.48,1]),np.zeros((3,3)),[[float('nan')]*3]*3]:
            result=recover(h,(1920,1080))
            self.assertEqual(result['status'],'unavailable')
            self.assertIsNone(result['matrix_3d'])

    def test_square_pixel_assumption_cannot_be_certified_from_one_plane(self):
        # A wrong intrinsic assumption can fit Z0 but fail above it. Keep this
        # counterexample: passing algebra is not independent camera validation.
        p,h=camera(yaw=0,fy=1700);candidate=recover(h,(1920,1080))
        self.assertEqual(candidate['status'],'conditional_candidate')
        xyz=[16400,16400,0];q=p @ [*xyz,1]
        np.testing.assert_allclose(project(candidate,xyz),q[:2]/q[2],atol=1e-6)
        xyz[2]=512;q=p @ [*xyz,1]
        self.assertGreater(np.linalg.norm(np.array(project(candidate,xyz))-q[:2]/q[2]),10)
        self.assertFalse(candidate['camera_model_verified'])

    def test_reflected_or_behind_camera_points_not_shown(self):
        p,h=camera()
        reflected=np.array([[-1,0,1919],[0,1,0],[0,0,1.]]) @ h
        self.assertEqual(recover(reflected,(1920,1080))['status'],'unavailable')
        candidate=recover(h,(1920,1080))
        self.assertIsNone(project(candidate,[16384,14384,10000]))
        self.assertIsNone(project(candidate,[0,float('inf'),0]))
