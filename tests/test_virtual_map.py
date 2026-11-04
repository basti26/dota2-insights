from dataclasses import replace
import unittest

from dota2_insights.virtual_map import MapIdentity, WorldBounds, WorldPoint, VirtualMap, TranslationCandidate


class VirtualMapTests(unittest.TestCase):
    def setUp(self):
        self.identity = MapIdentity('dota', 1234, 'a' * 64)
        self.model = VirtualMap(self.identity, WorldBounds(-100, -200, 300, 600),
            'source2_map_centered', 'reference', 'generated asymmetric test window; no real map claim')

    def point(self, x, y, **kw):
        return WorldPoint(x, y, self.identity, 'source2_map_centered', **kw)

    def test_all_corners_y_direction_and_asymmetric_window(self):
        for xy, uv in [((-100, 600), (0, 0)), ((300, 600), (1, 0)),
                       ((-100, -200), (0, 1)), ((300, -200), (1, 1)), ((100, 200), (.5, .5))]:
            norm = self.model.world_to_norm(self.point(*xy), allow_unverified=True)
            self.assertEqual((norm.u, norm.v), uv)
            world = self.model.norm_to_world(norm, allow_unverified=True)
            self.assertEqual((world.x, world.y), xy)

    def test_roundtrip_is_not_a_geography_certificate_and_height_not_fabricated(self):
        original = self.point(23.5, -41.2, z=128)
        norm = self.model.world_to_norm(original, allow_unverified=True)
        world = self.model.norm_to_world(norm, allow_unverified=True)
        self.assertAlmostEqual(world.x, original.x)
        self.assertAlmostEqual(world.y, original.y)
        self.assertIsNone(world.z)
        self.assertEqual(original.z, 128)
        with self.assertRaisesRegex(ValueError, 'independent_landmark'):
            self.model.world_to_norm(original)
        self.assertEqual(self.model.validation_status, 'unverified')

    def test_build_asset_variant_and_frame_conflicts_are_rejected(self):
        for identity in [replace(self.identity, client_build=1235),
                         replace(self.identity, map_asset_sha256='b' * 64), replace(self.identity, map_name='dota_alt')]:
            with self.assertRaisesRegex(ValueError, 'mismatch'):
                self.model.world_to_norm(replace(self.point(0, 0), identity=identity), allow_unverified=True)
        with self.assertRaisesRegex(ValueError, 'frame_mismatch'):
            self.model.world_to_norm(replace(self.point(0, 0), frame='gsi_hero_world'), allow_unverified=True)

    def test_cross_model_normalized_points_cannot_be_silently_reused(self):
        norm = self.model.world_to_norm(self.point(0, 0), allow_unverified=True)
        changed = replace(self.model, bounds=WorldBounds(-200, -200, 300, 600))
        with self.assertRaisesRegex(ValueError, 'model_mismatch'):
            changed.norm_to_world(norm, allow_unverified=True)

    def test_outside_window_is_not_clipped_into_a_false_marker(self):
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.model.world_to_norm(self.point(-150, 0), allow_unverified=True)
        norm = self.model.world_to_norm(self.point(-150, 0), allow_unverified=True, allow_outside=True)
        self.assertLess(norm.u, 0)
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.model.norm_to_world(norm, allow_unverified=True)

    def test_translation_is_explicit_versioned_and_unverified(self):
        translation = TranslationCandidate(self.identity, 'gem_cell128_plus_vec_raw',
            'source2_map_centered', -16384, -16384, 'generated origin hypothesis')
        raw = WorldPoint(16484, 16584, self.identity, 'gem_cell128_plus_vec_raw', 20)
        with self.assertRaisesRegex(ValueError, 'not_independently_validated'):
            translation.apply(raw)
        point = translation.apply(raw, allow_unverified=True)
        self.assertEqual((point.x, point.y, point.z), (100, 200, 20))
        with self.assertRaisesRegex(ValueError, 'context_mismatch'):
            translation.apply(replace(raw, frame='gsi_hero_world'), allow_unverified=True)

    def test_model_serialization_and_fake_validation_refusal(self):
        encoded = self.model.to_dict()
        self.assertEqual(VirtualMap.from_dict(encoded).model_id, self.model.model_id)
        encoded['validation_status'] = 'passed'
        with self.assertRaisesRegex(ValueError, 'self_certified'):
            VirtualMap.from_dict(encoded)

    def test_invalid_numbers_and_bounds(self):
        for value in (True, float('nan'), float('inf'), '1'):
            with self.assertRaises(ValueError):
                self.point(value, 0)
        with self.assertRaisesRegex(ValueError, 'degenerate'):
            WorldBounds(1, 0, 1, 20)
        with self.assertRaisesRegex(ValueError, 'nonfinite_span'):
            WorldBounds(-1e308, 0, 1e308, 20)
        value = self.model.to_dict(); value['schema_version'] = True
        with self.assertRaises(ValueError):
            VirtualMap.from_dict(value)
