"""Generated HUD assets only; verify origin offsets and evidence rejection."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from PIL import Image

from dota2_insights.item_review_evidence import slot_crop


class CropTests(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root=Path(temp.name)
        path=root/'hud-000001.png'
        im=Image.new('RGB',(80,40),'red')
        im.paste('blue',(10,10,30,20))
        im.save(path)
        row={'hud_image':path.name,'hud_image_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'frame_geometry':{'width':80,'height':40,'origin_left':100,'origin_top':200,
                'client_width':300,'client_height':300},
            'attempt':{'hero':{'slots':[{'slot_id':'main_1','rect':[110,210,20,10]}]}}}
        return root,path,row,{'root_key':'helper_history','path':'observations.jsonl'}

    def test_client_coordinates_convert_using_explicit_saved_origin(self):
        root,path,row,ref=self.fixture()
        crop,binding=slot_crop(root,ref,row,'main_1')
        self.assertEqual(crop.size,(20,10))
        self.assertEqual(crop.getpixel((0,0)),(0,0,255))
        self.assertEqual(binding['crop_rect'],[10,10,20,10])
        self.assertFalse(binding['image_semantics_verified'])

    def test_image_tamper_is_rejected(self):
        root,path,row,ref=self.fixture()
        Image.new('RGB',(80,40),'green').save(path)
        with self.assertRaisesRegex(ValueError,'sha_mismatch'):
            slot_crop(root,ref,row,'main_1')

    def test_missing_geometry_does_not_guess_bottom_right_origin(self):
        root,path,row,ref=self.fixture()
        del row['frame_geometry']['origin_top']
        with self.assertRaisesRegex(ValueError,'explicit_geometry'):
            slot_crop(root,ref,row,'main_1')

    def test_outside_hud_rect_does_not_return_padded_black_pixels(self):
        root,path,row,ref=self.fixture()
        row['attempt']['hero']['slots'][0]['rect']=[0,0,20,10]
        with self.assertRaisesRegex(ValueError,'outside_saved_hud'):
            slot_crop(root,ref,row,'main_1')

    def test_path_traversal_and_unbound_image_names_rejected(self):
        for name in ('../hud-000001.png','C:/hud-000001.png','other.png'):
            root,path,row,ref=self.fixture()
            row['hud_image']=name
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'missing_bound'):
                slot_crop(root,ref,row,'main_1')

    def test_duplicate_slots_and_incorrect_image_size_rejected(self):
        root,path,row,ref=self.fixture()
        row['attempt']['hero']['slots']*=2
        with self.assertRaisesRegex(ValueError,'unique_slot'):
            slot_crop(root,ref,row,'main_1')
        row['attempt']['hero']['slots']=row['attempt']['hero']['slots'][:1]
        row['frame_geometry']['width']=81
        with self.assertRaisesRegex(ValueError,'geometry_mismatch'):
            slot_crop(root,ref,row,'main_1')

    def test_boolean_coordinates_and_missing_image_refused(self):
        root,path,row,ref=self.fixture()
        row['attempt']['hero']['slots'][0]['rect']=[True,210,20,10]
        with self.assertRaisesRegex(ValueError,'geometry_required'):
            slot_crop(root,ref,row,'main_1')
        path.unlink()
        with self.assertRaises(OSError):
            slot_crop(root,ref,row,'main_1')

    def test_sealed_image_path_refused_before_file_access(self):
        root,path,row,ref=self.fixture()
        ref['path']='match_9056629627/observations.jsonl'
        with self.assertRaisesRegex(ValueError,'封存'):
            slot_crop(root,ref,row,'main_1')
