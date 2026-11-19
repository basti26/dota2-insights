"""Generated 3D points only; no real landmarks, frame timing or camera proof."""
import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from dota2_insights.native_camera_calibration import fit_orientation, project
from dota2_insights.native_camera_review import validate_orientation
import test_native_camera_calibration as fixtures


def orientation_for(pose, pitch):
    camera=pose['camera_xyz'];xy=pose['look_at_xy']
    horizontal=math.hypot(xy[0]-camera[0],xy[1]-camera[1])
    target=[*xy,camera[2]-horizontal*math.tan(math.radians(pitch))]
    delta=[target[i]-camera[i] for i in range(3)];distance=math.hypot(*delta)
    result={'kind':'alt_camera_orientation_candidate_v1','position_candidate':copy.deepcopy(pose),
        'look_at_xyz':target,'orientation_candidate':{
            'camera_to_target_distance':distance,'view_direction_unit':[v/distance for v in delta],
            'downward_pitch_degrees':math.degrees(math.atan2(-delta[2],horizontal)),
            'yaw_xy_degrees':pose['yaw_xy_degrees'],'roll_degrees':None,'fov_degrees':None,
            'viewport':None,'projection_calibrated':False},
        'data_time_utc':None,'atomic_pair_verified':False,'request_association_verified':False,
        'projection_calibrated':False,'live_usable':False}
    for key in ('alt_epoch','log_generation','log_observed_monotonic',
                'press_to_log_visible_candidate_ms','read_chunk_sha256','received_at_utc',
                'visible_in_read_byte_range','exact_log_flush_latency_verified',
                'game_control_sent','capture_frame_alignment_verified'):
        result[key]=copy.deepcopy(pose[key])
    validate_orientation(result)
    return result


class OrientationCalibrationTests(unittest.TestCase):
    def fixture(self, pitch=63.217, yaw=90., roll=11.3, size=(2560,1440)):
        pose,anchors,focal=fixtures.NativeCameraCalibrationTests().fixture(pitch,yaw,size)
        c,s=math.cos(math.radians(roll)),math.sin(math.radians(roll))
        center=(np.array(size)-1)/2
        for anchor in anchors:
            point=np.array(anchor['screen_xy'])-center
            anchor['screen_xy']=(np.array([[c,-s],[s,c]])@point+center).tolist()
        return orientation_for(pose,pitch),anchors,focal

    def test_recovers_focal_and_nonzero_image_roll_at_multiple_yaws_and_resolutions(self):
        for pitch,yaw,roll,size in ((63.217,90.,11.3,(2560,1440)),
                                    (51.333,160.,-8.2,(1920,1080)),
                                    (77.,-130.,0.,(2560,1440)),
                                    (67.,23.,-16.,(1920,1200))):
            orientation,anchors,focal=self.fixture(pitch,yaw,roll,size)
            before=copy.deepcopy((orientation,anchors))
            result=fit_orientation(orientation,anchors,size)
            self.assertEqual(result['status'],'conditional_candidate',result)
            self.assertAlmostEqual(result['focal_candidate_px'],focal,places=8)
            self.assertAlmostEqual(result['vertical_fov_candidate_degrees'],
                math.degrees(2*math.atan((size[1]-1)/(2*focal))),places=8)
            self.assertAlmostEqual(result['clockwise_image_roll_candidate_degrees'],roll,places=8)
            for anchor in anchors:
                self.assertTrue(np.allclose(project(result,anchor['world_xyz']),anchor['screen_xy']))
            for key in ('live_usable','coordinate_accuracy_verified','capture_frame_alignment_verified',
                        'atomic_pair_verified','anchor_truth_verified'):
                self.assertIs(result[key],False)
            self.assertEqual((orientation,anchors),before)

    def test_check_points_cannot_choose_or_refit_parameters(self):
        orientation,anchors,_=self.fixture()
        original=fit_orientation(orientation,anchors,(2560,1440))
        anchors[-1]['screen_xy'][0]+=40
        result=fit_orientation(orientation,anchors,(2560,1440))
        self.assertEqual(result['reason'],'anchor_reprojection_failed')
        self.assertIsNone(result['matrix_3d'])
        for key in ('focal_candidate_px','clockwise_image_roll_candidate_degrees','fit_rms_px'):
            self.assertEqual(result[key],original[key])
        self.assertGreater(result['check_max_error_px'],30)

    def test_wrong_reported_direction_is_not_silently_repaired(self):
        orientation,anchors,_=self.fixture()
        wrong=orientation_for(orientation['position_candidate'],71.217)
        result=fit_orientation(wrong,anchors,(2560,1440))
        self.assertEqual(result['status'],'unavailable')
        self.assertIsNone(result['matrix_3d'])

    def test_provenance_roles_duplicate_coordinates_and_region_conflict_stay_blocked(self):
        for change,reason in ((lambda o,a:a[0].update(source_sha256='bad'),'anchor_provenance_or_identity'),
                (lambda o,a:[r.update(role='fit') for r in a],'separate_fit_and_check_anchors_required'),
                (lambda o,a:a[-1].update(world_xyz=a[0]['world_xyz']),'duplicate_anchor_coordinates')):
            orientation,anchors,_=self.fixture();change(orientation,anchors)
            self.assertEqual(fit_orientation(orientation,anchors,(2560,1440))['reason'],reason)
        orientation,anchors,_=self.fixture()
        conflict={'version':'native_camera_region_check_v1','status':'conditional_conflict',
                  'viewport_identity_verified':True}
        self.assertEqual(fit_orientation(orientation,anchors,(2560,1440),region_check=conflict)['reason'],
                         'native_camera_frame_region_conflict')
        orientation['orientation_candidate']['fov_degrees']=70
        self.assertEqual(fit_orientation(orientation,anchors,(2560,1440))['status'],'unavailable')

    def test_cli_publishes_new_mode_without_installing_or_overwriting(self):
        from tools.calibrate_native_camera import main
        orientation,anchors,_=self.fixture()
        payload={'schema_version':1,'kind':'explicit_native_orientation_anchor_input',
                 'native_orientation':orientation,'anchors':anchors,'native_viewport':[2560,1440]}
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.json';output=Path(folder)/'result.json'
            from PIL import Image
            image=Path(folder)/'frame.png'
            Image.new('RGB',(2560,1440),(40,50,60)).save(image)
            payload['image_reference']={'file':'frame.png',
                'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}
            raw=json.dumps(payload).encode();path.write_bytes(raw)
            argv=['calibrate_native_camera','--input',str(path),'--output',str(output)]
            with patch('sys.argv',argv),redirect_stdout(io.StringIO()):main()
            result=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(result['status'],'conditional_candidate')
            self.assertEqual(result['input_sha256'],hashlib.sha256(raw).hexdigest())
            self.assertFalse(result['installed']);self.assertFalse(result['live_projection_enabled'])
            self.assertTrue(result['source_image_binding_verified'])
            self.assertFalse(result['pixel_annotation_truth_verified'])
            published=output.read_bytes()
            with patch('sys.argv',argv),self.assertRaises(FileExistsError):main()
            self.assertEqual(output.read_bytes(),published)
            self.assertEqual(path.read_bytes(),raw)
