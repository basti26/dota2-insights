"""Observed static render geometry bridge; shared vertices are not gameplay truth."""
import itertools
import math
import re
import numpy as np


def _vector(text,count):
    values=[float(v.strip()) for v in text.split(',')]
    if len(values)!=count or not all(math.isfinite(v) for v in values):
        raise ValueError('render_geometry_vector')
    return values


def world_node(text):
    section=re.search(r'^\tm_worldNodes =\s*\[(.*?)^\t\]',text,re.M|re.S)
    if section is None:raise ValueError('render_world_nodes_missing')
    origins=re.findall(r'm_vOrigin = \[ ([^\]]+) \]',section[1])
    prefixes=re.findall(r'm_worldNodePrefix = "([^"\r\n]+)"',section[1])
    if len(origins)!=1 or len(prefixes)!=1:raise ValueError('render_single_world_node_required')
    origin=_vector(origins[0],3)
    if origin!=[0,0,0]:raise ValueError('render_world_origin_requires_adapter')
    return {'origin':origin,'prefix':re.sub(r'\\+', '/', prefixes[0])}


def terrain_instances(text,prefix):
    section=re.search(r'^\tm_sceneObjects =\s*\[(.*?)^\t\]',text,re.M|re.S)
    if section is None:raise ValueError('render_scene_objects_missing')
    objects=re.findall(r'^\t\t\{\r?\n(.*?)^\t\t\},?\r?$',section[1],re.M|re.S)
    result=[];seen=set()
    for body in objects:
        model=re.findall(r'^\t{3}m_renderableModel = resource:"([^"\r\n]*)"',body,re.M)
        if len(model)!=1:raise ValueError('render_model_reference_ambiguous')
        match=re.fullmatch(re.escape(prefix)+r'_lr0_c([0-9]+)_s_cb_mesh_worldspace_texture_blend\.vmdl',model[0])
        if match is None:continue
        transform=re.search(r'^\t{3}m_vTransform =\s*\[(.*?)^\t{3}\]',body,re.M|re.S)
        if transform is None:raise ValueError('render_instance_transform_missing')
        rows=re.findall(r'^\t{4}\[ ([^\]]+) \],?\r?$',transform[1],re.M)
        matrix=[_vector(row,4) for row in rows]
        if matrix!=[[1,0,0,0],[0,1,0,0],[0,0,1,0]]:
            raise ValueError('render_instance_transform_requires_adapter')
        if model[0] in seen:raise ValueError('render_duplicate_terrain_instance')
        seen.add(model[0]);result.append({'resource':model[0]+'_c','cell':int(match[1]),'transform':matrix})
    if not result:raise ValueError('render_terrain_instances_missing')
    return sorted(result,key=lambda row:row['cell'])


def select_instances(instances):
    if len(instances)<9:raise ValueError('render_nine_instances_required')
    return [instances[round(k*(len(instances)-1)/8)] for k in range(9)]


def source_positions(document,buffer,mdat_text):
    """Explicit export recipe (gltf Z,X,Y)/0.0254; bounds check, no fitting."""
    if (document.get('asset',{}).get('version')!='2.0' or len(document.get('buffers',[]))!=1
            or document['buffers'][0].get('byteLength')!=len(buffer) or len(buffer)>32*1024**2
            or len(document.get('meshes',[]))!=1 or len(document.get('nodes',[]))!=1
            or document.get('skins') or document.get('animations')):
        raise ValueError('render_gltf_static_layout')
    node=document['nodes'][0]
    if (node.get('mesh')!=0 or set(node)-{'name','mesh','matrix'}
            or np.asarray(node.get('matrix',np.eye(4).flatten())).shape!=(16,)
            or not np.allclose(node.get('matrix',np.eye(4).flatten()),np.eye(4).flatten(),rtol=0,atol=1e-6)):
        raise ValueError('render_gltf_node_transform')
    points=[];used=set()
    for primitive in document['meshes'][0]['primitives']:
        index=primitive['attributes']['POSITION']
        if type(index) is not int or not 0<=index<len(document['accessors']):raise ValueError('render_gltf_accessor')
        if index in used:continue
        used.add(index);accessor=document['accessors'][index]
        if accessor.get('componentType')!=5126 or accessor.get('type')!='VEC3' or accessor.get('sparse') or accessor.get('normalized'):
            raise ValueError('render_gltf_position_format')
        view_index=accessor.get('bufferView')
        if type(view_index) is not int or not 0<=view_index<len(document['bufferViews']):raise ValueError('render_gltf_buffer_view')
        view=document['bufferViews'][view_index]
        count=accessor.get('count');stride=view.get('byteStride',12)
        begin=view.get('byteOffset',0)+accessor.get('byteOffset',0)
        if (type(count) is not int or not 1<=count<=1000000 or view.get('buffer')!=0
                or type(stride) is not int or not 12<=stride<=256
                or begin<view.get('byteOffset',0) or begin+(count-1)*stride+12>view.get('byteOffset',0)+view['byteLength']
                or view.get('byteOffset',0)+view['byteLength']>len(buffer)):
            raise ValueError('render_gltf_buffer_bounds')
        values=np.ndarray((count,3),dtype='<f4',buffer=buffer,offset=begin,strides=(stride,4)).astype(float)
        if not np.isfinite(values).all():raise ValueError('render_gltf_nonfinite')
        points.append(values[:,[2,0,1]]/.0254)
    if not points:raise ValueError('render_gltf_no_positions')
    positions=np.concatenate(points)
    lower=re.findall(r'm_vMinBounds = \[ ([^\]]+) \]',mdat_text)
    upper=re.findall(r'm_vMaxBounds = \[ ([^\]]+) \]',mdat_text)
    if len(lower)!=1 or len(upper)!=1:raise ValueError('render_mdat_bounds_ambiguous')
    declared=np.array([_vector(lower[0],3),_vector(upper[0],3)])
    bounds=np.array([positions.min(0),positions.max(0)])
    maximum=float(np.abs(bounds-declared).max())
    if maximum>.01:raise ValueError('render_export_coordinate_recipe_disagrees_with_bounds')
    return positions,{'declared_bounds':declared.tolist(),'decoded_bounds':bounds.tolist(),
                      'maximum_bounds_difference_world_units':maximum,'recipe':'gltf_ZXY_div_0.0254_v1'}


class SharedVertices:
    def __init__(self,collision):
        values=np.asarray(collision,float)
        if values.ndim!=2 or values.shape[1]!=3 or not 1<=len(values)<=1000000 or not np.isfinite(values).all():
            raise ValueError('render_collision_vertices')
        self.lower=values.min(0);self.upper=values.max(0)
        self.buckets={}
        for point in values:
            self.buckets.setdefault(tuple(np.floor(point/.02).astype(int)),[]).append(point)

    def compare(self,positions):
        positions=np.asarray(positions,float)
        if positions.ndim!=2 or positions.shape[1]!=3 or len(positions)>1000000 or not np.isfinite(positions).all():
            raise ValueError('render_positions_invalid')
        unique=np.unique(positions,axis=0);errors=[];matched=[];unmatched=[]
        for point in unique:
            cell=np.floor(point/.02).astype(int)
            choices=[candidate for delta in itertools.product((-1,0,1),repeat=3)
                     for candidate in self.buckets.get(tuple(cell+delta),())]
            distance=float(np.linalg.norm(np.asarray(choices)-point,axis=1).min()) if choices else None
            if distance is not None and distance<=.01:
                errors.append(distance);matched.append(point.tolist())
            else:unmatched.append(point.tolist())
        outside=sum(bool(np.any(np.asarray(p)<self.lower-.01) or np.any(np.asarray(p)>self.upper+.01)) for p in unmatched)
        return {'distinct_render_vertices':len(unique),'shared_within_0_01_world_units':len(errors),
                'unmatched_vertices':len(unmatched),'shared_distance_median':float(np.median(errors)) if errors else None,
                'unmatched_inside_collision_bounds':len(unmatched)-outside,
                'unmatched_outside_collision_bounds':outside,
                'shared_distance_maximum':max(errors) if errors else None,
                'matched_positions':matched,'unmatched_positions':unmatched,
                'coordinate_frame_evidence_only':True,'runtime_world_placement_verified':False,
                'walkable_ground_verified':False}
