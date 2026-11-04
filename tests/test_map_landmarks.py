import unittest

from dota2_insights.map_landmarks import extract


def entity(name,origin,extra=''):
    fields = f'classname = "{name}"\norigin = [ {origin} ]\nangles = [ 0, 0, 0 ]\nscales = [ 1, 1, 1 ]\n'+extra
    return '\t'*4+'values = \n'+'\t'*4+'{\n'+''.join('\t'*5+line+'\n' for line in fields.splitlines())+'\t'*4+'}\n'


def document():
    return ('<!-- kv3 encoding:text:generated -->\n'+entity('dota_minimap_boundary','-10, -20, 0')
        +entity('dota_minimap_boundary','30, 40, 1')
        +entity('world_bounds','0, 0, 0','min = "-5 -10 0"\nmax = "10 20 5"')
        +entity('npc_dota_lotus_pool','-3, 4, 6','targetname = "lotus-test"'))


class LandmarkTests(unittest.TestCase):
    def test_boundaries_are_distinct_from_world_bounds_and_origin_is_not_image_truth(self):
        result=extract(document())
        self.assertEqual(result['declared_minimap_window'],{'min_x':-10,'min_y':-20,'max_x':30,'max_y':40})
        self.assertEqual(result['world_bounds_entity']['min_xyz'],[-5,-10,0])
        self.assertEqual(result['landmark_count'],1)
        self.assertEqual(result['landmarks'][0]['origin_xyz'],[-3,4,6])
        self.assertFalse(result['independent_image_correspondence_verified'])

    def test_missing_duplicate_or_transformed_boundary_is_rejected(self):
        text=document()
        with self.assertRaisesRegex(ValueError,'expected_two'):
            extract(text.replace('dota_minimap_boundary','worldspawn',1))
        with self.assertRaisesRegex(ValueError,'transformed'):
            extract(text.replace('scales = [ 1, 1, 1 ]','scales = [ 2, 1, 1 ]',1))
        with self.assertRaisesRegex(ValueError,'expected_two'):
            extract(text+entity('dota_minimap_boundary','-40, -50, 0'))

    def test_missing_and_nonfinite_origins_never_become_zero_coordinates(self):
        with self.assertRaisesRegex(ValueError,'origin_missing'):
            extract(document().replace('origin = [ -3, 4, 6 ]','other = [ -3, 4, 6 ]'))
        with self.assertRaisesRegex(ValueError,'vector_invalid'):
            extract(document().replace('origin = [ -3, 4, 6 ]','origin = [ nan, 4, 6 ]'))

    def test_unparsed_entity_format_is_not_silently_dropped(self):
        with self.assertRaisesRegex(ValueError,'unparsed'):
            extract(document()+'classname = "npc_dota_lotus_pool"\n')
