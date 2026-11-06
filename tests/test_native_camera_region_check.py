import unittest
from dota2_insights.native_camera_region_check import check


class NativeCameraRegionTests(unittest.TestCase):
    def run_case(self,point,**changes):
        args=dict(look_at_xy=point,world_bounds=[-100,-100,100,100],texture_size=[201,201],
            texture_to_crop=[[1,0,0],[0,1,0]],region_box=[80,80,120,120],validation_error_px=.5,
            region_identity_verified=True)
        args.update(changes);return check(**args)

    def test_centered_world_y_is_inverted_and_agreement_never_certifies(self):
        r=self.run_case([0,10])
        self.assertEqual(r['native_target_crop_xy'],[100,90])
        self.assertEqual(r['status'],'conditional_compatible')
        self.assertFalse(r['safe_for_calibration']);self.assertFalse(r['camera_consistency_verified'])

    def test_far_region_conflict_cannot_be_hidden_by_small_registration_error(self):
        r=self.run_case([90,-90])
        self.assertEqual(r['status'],'conditional_conflict')
        self.assertGreater(r['distance_outside_region_px'],90)

    def test_static_or_unverified_region_cannot_be_reported_as_camera_conflict(self):
        for identity in (False,None,1,'yes'):
            r=self.run_case([90,-90],region_identity_verified=identity)
            self.assertEqual(r['status'],'unavailable')
            self.assertEqual(r['reason'],'viewport_region_identity_unverified')
            self.assertFalse(r['viewport_identity_verified'])

    def test_uncertain_registration_or_wrong_shapes_remain_unavailable(self):
        for changes in [dict(validation_error_px=4),dict(world_bounds=[0,0,0,0]),
                        dict(texture_to_crop=[[0,1,0],[1,0,0]]),dict(look_at_xy=[True,0])]:
            self.assertEqual(self.run_case([0,0],**changes)['status'],'unavailable')

    def test_crop_translation_is_applied_once_and_margin_does_not_move_native_point(self):
        r=self.run_case([0,0],texture_to_crop=[[.5,0,5],[0,.5,10]],region_box=[50,50,60,60])
        self.assertEqual(r['native_target_crop_xy'],[55,60])
        self.assertEqual(r['status'],'conditional_compatible')
