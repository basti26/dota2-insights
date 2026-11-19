"""Only generated PNG fixtures; bindings are not landmark/pixel truth."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from dota2_insights.native_camera_image import open_image_reference
import test_native_orientation_calibration as fixtures


class CameraImageBindingTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.image=self.root/'frame.png'
        Image.new('RGB',(640,480),(40,50,60)).save(self.image)
        self.reference={'file':'frame.png','sha256':hashlib.sha256(self.image.read_bytes()).hexdigest()}

    def test_verified_bytes_and_size_do_not_claim_pixel_or_camera_truth(self):
        with open_image_reference(self.root,self.reference,[640,480],required=True) as receipt:
            self.assertTrue(receipt['source_image_binding_verified'])
            self.assertEqual(receipt['source_image_sha256'],self.reference['sha256'])
            self.assertEqual(receipt['native_image_size'],[640,480])
            self.assertFalse(receipt['pixel_annotation_truth_verified'])
        with open_image_reference(self.root,None,None) as receipt:
            self.assertFalse(receipt['source_image_binding_verified'])
        with self.assertRaisesRegex(ValueError,'reference_required'):
            with open_image_reference(self.root,None,[640,480],required=True):pass

    def test_wrong_hash_dimensions_and_unsafe_paths_are_refused(self):
        for ref,size in (({**self.reference,'sha256':'b'*64},[640,480]),
                         (self.reference,[1920,1080]),(self.reference,[640,True]),
                         ({**self.reference,'file':'../frame.png'},[640,480]),
                         ({**self.reference,'file':'C:/frame.png'},[640,480]),
                         ({**self.reference,'file':'folder\\frame.png'},[640,480]),
                         ({**self.reference,'file':'frame.txt'},[640,480])):
            with self.subTest(reference=ref,size=size),self.assertRaises(ValueError):
                with open_image_reference(self.root,ref,size,required=True):pass

    def test_changed_image_is_rejected_on_exit_before_publication(self):
        with self.assertRaisesRegex(ValueError,'calibration_image_changed'):
            with open_image_reference(self.root,self.reference,[640,480],required=True):
                self.image.write_bytes(self.image.read_bytes()+b'changed')

    def test_reparse_change_is_rejected_even_when_pixels_are_unchanged(self):
        original=Path.is_symlink
        with self.assertRaisesRegex(ValueError,'calibration_image_link_refused'):
            with patch.object(Path,'is_symlink',autospec=True) as is_link:
                changed=False
                is_link.side_effect=lambda path: (changed and path==self.image) or original(path)
                with open_image_reference(self.root,self.reference,[640,480],required=True):
                    changed=True

    def test_cli_missing_or_changed_image_never_publishes_candidate(self):
        from tools.calibrate_native_camera import main
        from dota2_insights import native_camera_calibration as calibration
        orientation,anchors,_=fixtures.OrientationCalibrationTests().fixture(size=(640,480))
        payload={'schema_version':1,'kind':'explicit_native_orientation_anchor_input',
            'native_orientation':orientation,'anchors':anchors,'native_viewport':[640,480]}
        source=self.root/'input.json';output=self.root/'candidate.json'
        source.write_text(json.dumps(payload),encoding='utf-8')
        argv=['calibrate_native_camera','--input',str(source),'--output',str(output)]
        with patch('sys.argv',argv),self.assertRaisesRegex(ValueError,'reference_required'):main()
        self.assertFalse(output.exists())
        payload['image_reference']=self.reference
        source.write_text(json.dumps(payload),encoding='utf-8')
        original=calibration.fit_orientation
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            self.image.write_bytes(self.image.read_bytes()+b'changed')
            return result
        with patch('sys.argv',argv),patch.object(calibration,'fit_orientation',side_effect=changed), \
                self.assertRaisesRegex(ValueError,'calibration_image_changed'):main()
        self.assertFalse(output.exists())

    def test_legacy_position_cli_keeps_explicit_unbound_status(self):
        from tools.calibrate_native_camera import main
        orientation,anchors,_=fixtures.OrientationCalibrationTests().fixture(roll=0,size=(640,480))
        payload={'schema_version':1,'kind':'explicit_native_camera_anchor_input',
            'native_position':orientation['position_candidate'],'anchors':anchors,'native_viewport':[640,480]}
        source=self.root/'legacy.json';output=self.root/'candidate.json'
        source.write_text(json.dumps(payload),encoding='utf-8')
        with patch('sys.argv',['calibrate_native_camera','--input',str(source),'--output',str(output)]), \
                redirect_stdout(io.StringIO()):main()
        result=json.loads(output.read_text(encoding='utf-8'))
        self.assertFalse(result['source_image_binding_verified'])
        self.assertFalse(result['live_projection_enabled'])
