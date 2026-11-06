import unittest
import test_native_camera_calibration as calibration_fixtures
from dota2_insights.native_landmark_projection import preview


class NativeLandmarkProjectionTests(unittest.TestCase):
    def fixture(self):
        pose,anchors,focal=calibration_fixtures.NativeCameraCalibrationTests().fixture(pitch=60.)
        from math import degrees,atan
        fov=degrees(2*atan(1439/(2*focal)))
        landmarks=[{'entity_ordinal':i,'origin_xyz':a['world_xyz'],'targetname':a['id']} for i,a in enumerate(anchors)]
        return pose,anchors,landmarks,fov

    def test_explicit_projection_matches_synthetic_geometry_but_never_claims_fit(self):
        pose,anchors,landmarks,fov=self.fixture()
        r=preview(pose,landmarks,(2560,1440),pitch_degrees=60.,vertical_fov_degrees=fov)
        self.assertEqual(r['status'],'sensitivity_preview')
        for row,anchor in zip(r['points'],anchors):
            self.assertTrue(row['inside_viewport'])
            for a,b in zip(row['screen_xy'],anchor['screen_xy']):self.assertAlmostEqual(a,b,places=5)
        self.assertFalse(r['calibration_fitted']);self.assertFalse(r['live_usable'])

    def test_missing_height_duplicate_origins_and_boolean_parameters_refused(self):
        pose,anchors,landmarks,fov=self.fixture()
        landmarks[0]['origin_xyz'][2]=None
        self.assertEqual(preview(pose,landmarks,(2560,1440),pitch_degrees=60.,vertical_fov_degrees=fov)['status'],'unavailable')
        pose,anchors,landmarks,fov=self.fixture();landmarks[-1]['entity_ordinal']=0
        self.assertEqual(preview(pose,landmarks,(2560,1440),pitch_degrees=60.,vertical_fov_degrees=fov)['status'],'unavailable')
        self.assertEqual(preview(pose,[],(2560,1440),pitch_degrees=True,vertical_fov_degrees=60.)['status'],'unavailable')
        self.assertEqual(preview(pose,[None],(2560,1440),pitch_degrees=60.,vertical_fov_degrees=60.)['status'],'unavailable')
