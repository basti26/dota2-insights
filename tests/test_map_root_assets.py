import struct
import unittest

from dota2_insights.map_root_assets import grid,trm,external_references,compare_trees,sample_grid


def grid_bytes():
    return struct.pack('<IfffIIii',0xFADEBEAD,64,32,32,2,3,-1,-1)+bytes([1,4,16,20,13,25])


def trm_bytes(points=((64,128,0),(128,192,1),(64,128,0))):
    names=b'world_layer_radiant_base\0\0'
    header=struct.pack('<4sIIII',b'trmp',1,64,2,len(points))+bytes(44)
    return header+names+b''.join(struct.pack('<iii',*p) for p in points)


def rerl_bytes():
    names=[b'maps/dota/world.vwrld\0',b'maps/dota/world_physics.vmdl\0']
    data=bytearray(struct.pack('<II',12,2)+bytes(4)+bytes(32)+b''.join(names))
    pos=44
    for i,name in enumerate(names):
        struct.pack_into('<Qq',data,12+i*16,i+1,pos-(12+i*16+8))
        pos+=len(name)
    return struct.pack('<IHHII4sII',28+len(data),12,1,8,1,b'RERL',8,len(data))+data


class RootAssetTests(unittest.TestCase):
    def test_grid_is_exact_raw_bytes_not_claimed_navigation_or_ground(self):
        result=grid(grid_bytes())
        self.assertEqual(result['values'].tolist(),[[1,4],[16,20],[13,25]])
        self.assertFalse(result['values'].flags.writeable)
        self.assertEqual(result['xy_bounds_candidate'],{'min_x':-64,'min_y':-64,'max_x':64,'max_y':128})
        self.assertEqual(result['raw_byte_counts'],{'1':1,'4':1,'13':1,'16':1,'20':1,'25':1})
        self.assertFalse(result['cell_flag_semantics_verified'])
        self.assertFalse(result['world_placement_verified'])

    def test_grid_invalid_size_dimensions_and_nonfinite_geometry_refused(self):
        raw=grid_bytes()
        for bad in (raw[:-1],raw+b'\0',bytes(4)+raw[4:],raw[:4]+struct.pack('<f',float('nan'))+raw[8:],
                raw[:16]+struct.pack('<I',0)+raw[20:]):
            with self.subTest(header=bad[:32]),self.assertRaises(ValueError):grid(bad)

    def test_grid_query_uses_half_open_cell_edges_and_keeps_unknown_outside(self):
        candidate=grid(grid_bytes())
        self.assertEqual(sample_grid(candidate,-64,-64)['raw_value'],1)
        self.assertEqual(sample_grid(candidate,0,0),{'column':1,'row':1,'raw_value':20,
            'semantics_verified':False,'xy_placement_verified':False})
        self.assertIsNone(sample_grid(candidate,64,0))
        self.assertIsNone(sample_grid(candidate,0,128))
        self.assertIsNone(sample_grid(candidate,-64.1,-64))
        with self.assertRaisesRegex(ValueError,'invalid'):sample_grid(candidate,float('nan'),0)

    def test_tree_catalog_keeps_layer_identity_duplicates_and_no_z(self):
        result=trm(trm_bytes())
        self.assertEqual(result['layers'],['world_layer_radiant_base',''])
        self.assertEqual(result['record_count'],3)
        self.assertEqual(result['duplicate_xy_layer_records'],1)
        self.assertEqual(result['points'][0]['xy'],[64,128])
        self.assertNotIn('z',result['points'][0])
        self.assertFalse(result['runtime_tree_ids_verified'])

    def test_tree_unknown_fields_truncation_and_bad_layers_refused(self):
        raw=trm_bytes()
        for bad in (raw[:-1],raw+b'\0',trm_bytes(((64,128,2),)),raw[:20]+b'X'+raw[21:]):
            with self.subTest(size=len(bad)),self.assertRaises(ValueError):trm(bad)

    def test_exact_tree_comparison_never_nearest_snaps_or_deduplicates(self):
        catalog=trm(trm_bytes())
        inventory={'trees':[
            {'id':'maps/dota/entities/world_layer_radiant_base.vents_c:entity-0','origin_xyz':[64,128,77]},
            {'id':'maps/dota/entities/default_ents.vents_c:entity-0','origin_xyz':[128,192,3]},
            {'id':'maps/dota/entities/default_ents.vents_c:entity-1','origin_xyz':[65,128,0]}]}
        result=compare_trees(catalog,inventory)
        self.assertEqual(result['exact_xy_layer_matches'],2)
        self.assertEqual(result['entity_records_missing_from_trm'],[{'xy':[65,128],'layer':'','multiplicity':1}])
        self.assertEqual(result['trm_records_not_paired_to_entities'],[{'xy':[64,128],'layer':'world_layer_radiant_base','multiplicity':1}])

    def test_dependency_parser_tracks_actual_resource_names(self):
        result=external_references(rerl_bytes())
        self.assertEqual(result,[{'id_hex':'0000000000000001','resource':'maps/dota/world.vwrld'},
            {'id_hex':'0000000000000002','resource':'maps/dota/world_physics.vmdl'}])

    def test_dependency_out_of_block_offsets_and_duplicate_ids_rejected(self):
        raw=rerl_bytes()
        bad=bytearray(raw);struct.pack_into('<q',bad,28+12+8,-100)
        with self.assertRaisesRegex(ValueError,'reference'):external_references(bytes(bad))
        bad=bytearray(raw);struct.pack_into('<Q',bad,28+12+16,1)
        with self.assertRaisesRegex(ValueError,'reference'):external_references(bytes(bad))
        with self.assertRaises(ValueError):external_references(raw[:-1])
