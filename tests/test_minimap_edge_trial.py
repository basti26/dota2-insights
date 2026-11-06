import unittest
import cv2
import numpy as np
from dota2_insights.minimap_edge_trial import extract


def fixture(offset=0):
    image=np.full((380,380,3),(45,70,40),np.uint8)
    quad=np.array([[140+offset,155],[210+offset,155],[195+offset,190],[155+offset,190]])
    for i,j in ((0,3),(3,2),(2,1)):
        cv2.line(image,tuple(quad[i]),tuple(quad[j]),(235,195,110),2)
    return image,quad


class EdgeTrialTests(unittest.TestCase):
    def test_clean_measured_edges_contain_generated_outline(self):
        image,quad=fixture()
        r=extract(image)
        self.assertTrue(r['hypotheses'],r)
        self.assertLess(min(np.max(np.linalg.norm(np.array(h)*379-quad,axis=1)) for h in r['hypotheses']),5)
        self.assertFalse(r['outline_identity_verified'])
        self.assertFalse(r['production_ready'])

    def test_small_gap_can_be_joined_but_not_missing_edge(self):
        image,_=fixture();image[172:175,145:160]=(45,70,40)
        self.assertTrue(extract(image)['hypotheses'])
        image,_=fixture();image[145:190,135:165]=(45,70,40)
        self.assertFalse(extract(image)['hypotheses'])

    def test_clipped_and_bottom_only_are_not_completed(self):
        image,_=fixture(-150)
        self.assertFalse(extract(image)['hypotheses'])
        image=np.full((380,380,3),(45,70,40),np.uint8)
        cv2.line(image,(155,190),(195,190),(235,195,110),2)
        self.assertFalse(extract(image)['hypotheses'])

    def test_competing_shapes_not_selected_as_a_camera(self):
        image,_=fixture(-65);other,_=fixture(65)
        r=extract(np.maximum(image,other))
        self.assertGreaterEqual(len(r['hypotheses']),2)
        self.assertEqual(r['reason'],'ambiguous_measured_hypotheses')

    def test_bad_inputs_and_constant_crop(self):
        for image in (None,np.zeros((10,10,3),np.uint8),np.zeros((380,380),np.uint8),np.zeros((380,380,3),float)):
            self.assertEqual(extract(image)['reason'],'invalid_crop')
        self.assertFalse(extract(np.full((380,380,3),100,np.uint8))['hypotheses'])
