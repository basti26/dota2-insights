"""Drape a historical ward layer on the matching static ground mesh for review."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_collision import vertical_hits
from dota2_insights.minimap.offline_io import checked_match, new_binary_output
from tools.audit_ward_height_origin import read_mesh, read_bounded


RADIUS = 36.
TOLERANCE = 4.
MAX_PATCH_RISE = 24.
SURFACE_LIFT = 1.
RING = ((-1,-1),(1,-1),(1,1),(-1,1))


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def patch_for(anchor, ground):
    """Fit one connected tile around an observed placement height."""
    if not anchor['rasterized'] or anchor['world_z_candidate'] is None:
        return 'missing_position_or_height',None
    x,y=anchor['world_xy']
    z=anchor['world_z_candidate']
    heights=sorted({r['height'] for r in vertical_hits(ground,x,y)['hits']})
    eligible=[h for h in heights if abs(h-z)<=TOLERANCE]
    if len(eligible)!=1:
        return 'center_surface_unconfirmed',None
    center=eligible[0]
    points=[]
    for dx,dy in RING:
        u,v=x+dx*RADIUS,y+dy*RADIUS
        levels=sorted({r['height'] for r in vertical_hits(ground,u,v)['hits']
                       if abs(r['height']-center)<=MAX_PATCH_RISE})
        if len(levels)!=1:
            return 'corner_surface_unconfirmed',None
        points.append([u,v,levels[0]])
    return 'local_surface_candidate',{'center_z':center,'vertex_world_xyz':points+[[x,y,center]]}


def glyph_texture():
    image=Image.new('RGBA',(128,64))
    draw=ImageDraw.Draw(image)
    for i,color in enumerate(('#ffdc79','#91e2fe')):
        x=32+i*64
        draw.ellipse((x-26,6,x+26,58),fill='#202a2e')
        draw.ellipse((x-19,13,x+19,51),fill=color)
        draw.ellipse((x-6,26,x+6,38),fill='#202a2e')
    stream=io.BytesIO()
    image.save(stream,format='PNG')
    return stream.getvalue()


def glb(terrain, patches, *, transparent_terrain=False):
    """One self-contained glTF 2.0 scene, with game Z represented as glTF Y."""
    payload=bytearray()
    views=[]
    access=[]

    def view(raw,target=None):
        while len(payload)%4:payload.append(0)
        index=len(views)
        spec={'buffer':0,'byteOffset':len(payload),'byteLength':len(raw)}
        if target is not None:spec['target']=target
        views.append(spec)
        payload.extend(raw)
        return index

    def vector(value, *, component=5126, position=False):
        data=np.ascontiguousarray(value,dtype='<f4' if component==5126 else '<u4')
        shape='SCALAR' if data.ndim==1 else f'VEC{data.shape[1]}'
        item={'bufferView':view(data.tobytes(),34963 if component==5125 else 34962),
              'componentType':component,'count':len(data),'type':shape}
        if position:item.update(min=data.min(axis=0).tolist(),max=data.max(axis=0).tolist())
        access.append(item)
        return len(access)-1

    def xyz(world):
        mapped=world[:,[0,2,1]].copy()
        mapped[:,2]*=-1
        return mapped

    ground=[]
    for mesh in terrain:
        coordinates=mesh.vertices.astype(np.float32)
        attributes = {'POSITION': vector(xyz(coordinates), position=True)}
        if not transparent_terrain:
            light = np.clip((coordinates[:,2]+320.)/1050.,0.,1.)
            colors = np.column_stack((.09+.22*light,.18+.28*light,.20+.23*light))
            attributes['COLOR_0'] = vector(colors)
        ground.append({'attributes':attributes,
                       'indices':vector(mesh.triangles.ravel(),component=5125),'material':0})

    vertices=[]; texels=[]; indices=[]
    for marker,patch in patches:
        start=len(vertices)
        vertices.extend((x,y,z+SURFACE_LIFT) for x,y,z in patch['vertex_world_xyz'])
        left=.5 if marker['ward_type']=='sentry' else 0.
        texels.extend(((left,1.),(left+.5,1.),(left+.5,0.),(left,0.),(left+.25,.5)))
        indices.extend(start+i for i in (0,1,4,1,2,4,2,3,4,3,0,4))

    scene={'asset':{'version':'2.0','generator':'Foresight static terrain review'},'scene':0,
           'scenes':[{'nodes':[0]}],'nodes':[{'name':'Static map collision','mesh':0}],
           'meshes':[{'primitives':ground}],'bufferViews':views,'accessors':access,
           'extensionsUsed':['KHR_materials_unlit'],
           'materials':[{'doubleSided':True,'extensions':{'KHR_materials_unlit':{}}}]}
    if transparent_terrain:
        scene['materials'][0].update(alphaMode='MASK', alphaCutoff=.5,
            pbrMetallicRoughness={'baseColorFactor':[1., 1., 1., 0.]})
    if vertices:
        scene['nodes'].append({'name':'Candidate ward surface layer','mesh':1})
        scene['scenes'][0]['nodes'].append(1)
        scene['meshes'].append({'primitives':[{
            'attributes':{'POSITION':vector(xyz(np.asarray(vertices)),position=True),'TEXCOORD_0':vector(texels)},
            'indices':vector(indices,component=5125),'material':1}]})
        scene['materials'].append({'doubleSided':True,'alphaMode':'MASK','alphaCutoff':.5,
            'extensions':{'KHR_materials_unlit':{}},'pbrMetallicRoughness':{'baseColorTexture':{'index':0}}})
        scene['images']=[{'mimeType':'image/png','bufferView':view(glyph_texture())}]
        scene['samplers']=[{'wrapS':33071,'wrapT':33071}]
        scene['textures']=[{'source':0,'sampler':0}]
    scene['buffers']=[{'byteLength':len(payload)}]
    raw=json.dumps(scene,separators=(',',':'),allow_nan=False).encode()
    raw+=b' '*((-len(raw))%4)
    payload.extend(b'\0'*((-len(payload))%4))
    return (struct.pack('<4sII',b'glTF',2,28+len(raw)+len(payload))
            +struct.pack('<I4s',len(raw),b'JSON')+raw
            +struct.pack('<I4s',len(payload),b'BIN\0')+payload)


def prepare(layer,collision,out):
    raw=read_bounded(layer/'world-layer.json',1024**2)
    if read_bounded(layer/'COMPLETE',256).decode('ascii').strip()!=digest(raw):
        raise ValueError('ward_surface_layer_receipt')
    data=json.loads(raw)
    checked_match(data['source_match_id'])
    meta,meshes=read_mesh(collision)
    identity=data['map_identity']
    if (data['kind']!='ward_world_texture_review' or identity['client_build']!=meta['client_build']
            or identity['map_asset_sha256']!=meta['map_asset_sha256']):
        raise ValueError('ward_surface_map_identity')
    ground=[m for m in meshes if m.attribute_index==0]
    if not ground or not 1<=len(data['anchors'])<=5000:
        raise ValueError('ward_surface_source_count')
    patch_data=[]; rows=[]
    for anchor in data['anchors']:
        status,patch=patch_for(anchor,ground)
        if patch is not None:patch_data.append((anchor,patch))
        rows.append({'id':anchor['id'],'status':status,'ward_type':anchor['ward_type'],
                     'world_xy':anchor['world_xy'],'world_z_candidate':anchor['world_z_candidate'],'patch':patch})
    scene=glb(ground,patch_data)
    report={'kind':'ward_static_terrain_review_scene','map_identity':identity,'source_match_id':data['source_match_id'],
            'source_world_layer_sha256':digest(raw),'mesh_archive_sha256':meta['mesh_archive_sha256'],
            'scene_sha256':digest(scene),'terrain_triangle_count':sum(len(m.triangles) for m in ground),
            'patch_radius_world_units':RADIUS,'patch_render_lift_world_units':SURFACE_LIFT,
            'represented':len(patch_data),'total':len(rows),'anchors':rows,
            'live_projection_verified':False,'ground_height_verified':False}
    out.mkdir(parents=True,exist_ok=False)
    receipt=(json.dumps(report,indent=2,allow_nan=False)+'\n').encode()
    for filename,content in (('ward-terrain.glb',scene),('surface-layer.json',receipt),
                             ('COMPLETE',(digest(receipt)+'\n').encode('ascii'))):
        with new_binary_output(out/filename) as stream:stream.write(content)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('layer','collision','output'):parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    report=prepare(checked_input(ROOT,args.layer),checked_input(ROOT,args.collision),checked_output(ROOT,args.output))
    print(json.dumps({key:report[key] for key in ('represented','total','terrain_triangle_count','scene_sha256')}))


if __name__=='__main__':main()
