import unittest
from unittest.mock import patch, Mock

import cv2
import numpy as np

from dota2_insights.map_texture_registration import register, split_point


class TextureRegistrationTests(unittest.TestCase):
    def test_source_only_spatial_split_and_boundary_gap(self):
        self.assertEqual(split_point(32, 32, 2, 512, 512), 'train')
        self.assertEqual(split_point(128, 32, 2, 512, 512), 'validation')
        self.assertEqual(split_point(85, 32, 2, 512, 512), 'excluded_border')
        self.assertEqual(split_point(32, 32, 30, 512, 512), 'excluded_border')
        for xy in ((float('nan'), 0), (-1, 0), (512, 0)):
            with self.assertRaises(ValueError): split_point(*xy, 2, 512, 512)

    def test_blank_unrelated_images_never_create_a_zero_error_certificate(self):
        r = register(np.zeros((256,256,3),np.uint8), np.zeros((256,256,3),np.uint8))
        self.assertEqual(r['status'], 'insufficient_training_evidence')
        self.assertIsNone(r['matrix'])
        self.assertIsNone(r['validation_errors_native_px']['median'])
        self.assertFalse(r['formal_p2_pass'])

    def test_bounded_rgb_inputs_required(self):
        valid = np.zeros((256,256,3),np.uint8)
        for bad in (np.zeros((1025,256,3),np.uint8), valid.astype(float), np.zeros((256,256),np.uint8)):
            with self.assertRaises(ValueError): register(bad, valid)

    def test_validation_never_enters_fit_and_gross_mismatch_is_not_removed(self):
        source = [cv2.KeyPoint((c+.5)*512/6, (r+.5)*512/6, 2) for r in range(6) for c in range(6)]
        target = [cv2.KeyPoint(k.pt[0], k.pt[1], 2) for k in source]
        target[1] = cv2.KeyPoint(source[1].pt[0]+100, source[1].pt[1], 2)
        descriptors = np.ones((36,128),np.float32)
        detector = Mock(); detector.detectAndCompute.side_effect = [(source,descriptors),(target,descriptors)]
        pairs = [[cv2.DMatch(i,i,0,1),cv2.DMatch(i,(i+1)%36,0,10)] for i in range(36)]
        matcher = Mock(); matcher.knnMatch.side_effect = [pairs,pairs]
        with patch('cv2.SIFT_create', return_value=detector), patch('cv2.BFMatcher', return_value=matcher), \
                patch('cv2.estimateAffine2D', return_value=(np.array([[1.,0,0],[0,1.,0]]),np.ones((18,1),np.uint8))) as fit:
            r = register(np.zeros((512,512,3),np.uint8), np.zeros((512,512,3),np.uint8))
        fitted = fit.call_args.args[0]
        self.assertEqual(len(fitted),18)
        self.assertTrue(all(split_point(*xy,2,512,512)=='train' for xy in fitted.tolist()))
        self.assertEqual(r['validation_matches'],18)
        self.assertAlmostEqual(r['validation_errors_native_px']['max'],100,places=4)
        self.assertFalse(r['world_coordinates_read'])
        self.assertFalse(r['final_holdout_used'])

    def test_generated_affine_is_recovered_using_real_image_features(self):
        rng=np.random.default_rng(317)
        source=rng.integers(0,256,(512,512,3),dtype=np.uint8)
        source=cv2.GaussianBlur(source,(3,3),0)
        expected=np.float64([[.8,0,12],[0,.8,16]])
        target=cv2.warpAffine(source,expected,(460,460))
        r=register(source,target)
        self.assertIsNotNone(r['matrix'])
        self.assertGreaterEqual(r['train_inliers'],12)
        self.assertGreater(r['validation_matches'],10)
        np.testing.assert_allclose(r['matrix'],expected,atol=.5)
        self.assertLess(r['validation_errors_native_px']['p95'],1)
        self.assertFalse(r['production_ready'])

    def test_reflected_or_invalid_fit_is_not_a_candidate(self):
        # The real feature input remains fixed; only the fitted result is
        # replaced to exercise an orientation failure rather than a blank case.
        rng=np.random.default_rng(7)
        image=cv2.GaussianBlur(rng.integers(0,256,(512,512,3),dtype=np.uint8),(3,3),0)
        with patch('cv2.estimateAffine2D', return_value=(np.array([[-1.,0,512],[0,1.,0]]),np.ones((100,1),np.uint8))):
            r=register(image,image)
        self.assertIsNone(r['matrix'])
        self.assertEqual(r['status'],'no_valid_orientation_preserving_affine')
