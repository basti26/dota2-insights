import struct
import unittest

from dota2_insights.map_assets import compiled_blocks, inventory
from dota2_insights.valve_resources import VPKEntry


class MapAssetTests(unittest.TestCase):
    def test_compiled_block_header_is_metadata_not_a_decoded_mesh(self):
        raw = struct.pack('<IHHII4sII', 32, 12, 1, 8, 1, b'DATA', 8, 4) + b'test'
        result = compiled_blocks(raw)
        self.assertEqual(result['blocks'], [{'tag': 'DATA', 'offset': 28, 'bytes': 4}])
        self.assertFalse(result['geometry_decoded'])
        corrupt = bytearray(raw); struct.pack_into('<I', corrupt, 24, 100)
        with self.assertRaisesRegex(ValueError, 'block'):
            compiled_blocks(bytes(corrupt))

    def test_inventory_separates_map_variants_and_resource_families(self):
        names = ['maps/dota/world.vwrld_c', 'maps/dota/worldnodes/mesh.vmdl_c',
            'maps/dota/world_physics.vphys_c', 'maps/dota/entities/default.vents_c',
            'maps/dota_desert/world.vwrld_c']
        entries = {name: VPKEntry(name, 1, b'', 32767, 0, 10) for name in names}
        result = inventory(entries)
        self.assertEqual(result['resource_count'], 4)
        self.assertEqual(result['families']['models'], [names[1]])
        self.assertEqual(result['families']['physics'], [names[2]])
        self.assertFalse(result['walkable_height_grid_verified'])

    def test_physics_in_model_container_is_not_missed_as_no_physics(self):
        names = ['maps/dota/world_physics.vmdl_c',
            'maps/dota/worldnodes/n0_s_cb_nomerge1_dota_nav_walkable.vmdl_c']
        result = inventory({name: VPKEntry(name, 1, b'', 32767, 0, 10) for name in names})
        self.assertEqual(result['families']['physics'], [])
        self.assertEqual(result['physics_model_resources'], [names[0]])
        self.assertEqual(result['navigation_helper_models'], [names[1]])
        self.assertFalse(result['terrain_geometry_decoded'])

    def test_map_root_assets_are_counted_without_including_other_variants(self):
        names=['maps/dota/world.vwrld_c','maps/dota.vmap_c','maps/dota.gnv','maps/dota.vhcg','maps/dota.trm',
            'maps/dota_desert.vmap_c','maps/dota_desert.gnv','maps/dota2.gnv']
        result=inventory({n:VPKEntry(n,1,b'',32767,0,10) for n in names})
        self.assertEqual(result['resource_count'],5)
        self.assertEqual(result['map_folder_resource_count'],1)
        self.assertEqual(set(result['map_root_resources']),set(names[1:5]))
