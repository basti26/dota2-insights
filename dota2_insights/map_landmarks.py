"""Static declared boundaries and landmark origins, not image correspondences."""
import math
import re

from .map_tree_inventory import VALUES, CLASS, ORIGIN

LANDMARKS = frozenset({'npc_dota_barracks','npc_dota_fort','npc_dota_watch_tower','npc_dota_lotus_pool',
    'ent_dota_fountain','npc_dota_xp_fountain','dota_item_rune_spawner_bounty','dota_item_rune_spawner_powerup'})


def xyz(text):
    try: result = [float(n.strip()) for n in text.replace(',', ' ').split()]
    except ValueError as exc: raise ValueError('landmark_vector_invalid') from exc
    if len(result) != 3 or any(not math.isfinite(v) or abs(v)>65536 for v in result):
        raise ValueError('landmark_vector_invalid')
    return result


def extract(text):
    if not isinstance(text,str) or len(text.encode('utf-8'))>32*1024**2 or '<!-- kv3 encoding:text:' not in text:
        raise ValueError('landmark_export_invalid')
    blocks=VALUES.findall(text)
    if not blocks or len(blocks)>20000:
        raise ValueError('landmark_entity_count_invalid')
    rows=[];boundaries=[];world=[]
    for index,block in enumerate(blocks):
        names=CLASS.findall(block)
        if len(names)!=1:raise ValueError('landmark_class_invalid')
        name=names[0]
        if name not in LANDMARKS|{'dota_minimap_boundary','world_bounds'}:continue
        origins=ORIGIN.findall(block)
        if len(origins)!=1:raise ValueError('landmark_origin_missing_or_duplicate')
        targets=re.findall(r'^\t{5}targetname = "([^"\r\n]*)"$',block,re.M)
        row={'entity_ordinal':index,'classname':name,'origin_xyz':xyz(origins[0]),
            'targetname':targets[0] if len(targets)==1 else None,
            'image_glyph_center_correspondence_verified':False}
        if len(targets)>1:raise ValueError('landmark_target_ambiguous')
        if name=='dota_minimap_boundary':
            angles=re.findall(r'^\t{5}angles = \[ ([^\]]+) \]$',block,re.M)
            scales=re.findall(r'^\t{5}scales = \[ ([^\]]+) \]$',block,re.M)
            if len(angles)!=1 or len(scales)!=1 or xyz(angles[0])!=[0,0,0] or xyz(scales[0])!=[1,1,1]:
                raise ValueError('transformed_minimap_boundary_unsupported')
            boundaries.append(row)
        elif name=='world_bounds':
            for field in ('min','max'):
                values=re.findall(r'^\t{5}'+field+r' = "([^"\r\n]+)"$',block,re.M)
                if len(values)!=1:raise ValueError('world_bounds_definition_missing')
                row[field+'_xyz']=xyz(values[0])
            world.append(row)
        else:rows.append(row)
    if len(re.findall(r'^\s*classname = "[^"\r\n]+"\s*$',text,re.M))!=len(blocks):
        raise ValueError('landmark_export_has_unparsed_entities')
    if len(boundaries)!=2 or len(world)!=1:
        raise ValueError('expected_two_minimap_boundaries_and_one_world_bounds')
    a,b=sorted([row['origin_xyz'] for row in boundaries])
    if a[0]>=b[0] or a[1]>=b[1]:raise ValueError('minimap_boundary_pair_invalid')
    return {'schema_version':1,'kind':'static_map_landmark_candidates',
        'coordinate_basis':'source2_map_entity_origin_xyz',
        'minimap_boundary_entities':boundaries,'declared_minimap_window':{'min_x':a[0],'min_y':a[1],'max_x':b[0],'max_y':b[1]},
        'world_bounds_entity':world[0],'landmarks':rows,'landmark_count':len(rows),
        'independent_image_correspondence_verified':False,'map_calibration_verified':False}
