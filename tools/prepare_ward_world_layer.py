"""Export a transparent world-aligned review layer with one record per ward."""

import argparse
import hashlib
import io
import json
import math
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.history import checked_output
from dota2_insights.map_updates import read_prepared
from dota2_insights.minimap.offline_io import check_input_paths, checked_match, new_binary_output
from dota2_insights.ward_reference_export import build


SIDE = 1537  # The central texel is exactly at the world origin for symmetric bounds.
RAW_ORIGIN = 16384
COLORS = {'observer': (255,220,121,255), 'sentry': (145,226,254,255)}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit):
    with path.open('rb') as stream:
        raw = stream.read(limit+1)
    if len(raw)>limit:
        raise ValueError('world_layer_input_size')
    return raw


def ward_world_anchor(point):
    x, y = (point[key] for key in ('x', 'y'))
    raw_z = point.get('height_observation', {}).get('raw_z')
    return {'id':point['id'], 'ward_type':point['ward_type'], 'world_xy':[x-RAW_ORIGIN,y-RAW_ORIGIN],
            'raw_position':[x,y,raw_z], 'world_z_candidate':raw_z-RAW_ORIGIN if raw_z is not None else None,
            'world_z_verified':False,'placed_game_time_s':point['placed_game_time_s']}


def world_texture(packet, bounds):
    left,bottom,right,top=(bounds[key] for key in ('min_x','min_y','max_x','max_y'))
    if not all(math.isfinite(v) for v in (left,bottom,right,top)) or not left<right or not bottom<top:
        raise ValueError('world_layer_bounds')
    sx,sy=(SIDE-1)/(right-left),(SIDE-1)/(top-bottom)
    matrix=[[sx,0,-sx*left],[0,-sy,sy*top],[0,0,1]]
    image=Image.new('RGBA',(SIDE,SIDE))
    draw=ImageDraw.Draw(image)
    anchors=[]
    for point in packet['points']:
        anchor=ward_world_anchor(point)
        x,y=anchor['world_xy']
        u,v=sx*(x-left),sy*(top-y)
        visible=left<=x<=right and bottom<=y<=top
        anchors.append({**anchor,'texel_xy':[u,v] if visible else None,'rasterized':visible})
        if visible:
            draw.ellipse((u-5,v-5,u+5,v+5),fill=(24,33,37,240))
            draw.ellipse((u-3,v-3,u+3,v+3),fill=COLORS[point['ward_type']])
    data=io.BytesIO()
    image.save(data,format='PNG')
    return matrix,anchors,data.getvalue()


def verified_replay_packet(replay_build, ward_directory, client_build):
    check_input_paths(replay_build,ward_directory)
    replay_raw=read(replay_build,128*1024)
    replay=json.loads(replay_raw)
    identifier=checked_match(replay['match_id'])
    replay_sha=replay['source_sha256']
    if (replay['status']!='verified_bounded_metadata' or replay['source_stat_unchanged'] is not True
            or replay['source_sha256_after_read']!=replay_sha or replay['expected_source_sha256']!=replay_sha
            or checked_match(replay['footer']['match_id'])!=identifier):
        raise ValueError('world_layer_replay_provenance')
    if client_build!=replay['game_build']:
        raise ValueError('world_layer_build_mismatch')

    manifest_raw=read(ward_directory/'manifest.json',128*1024)
    manifest=json.loads(manifest_raw)
    if (checked_match(manifest['match_id'])!=identifier or manifest['source_sha256']!=replay_sha
            or manifest['source_unchanged_stat'] is not True or manifest['stop_reason']!='replay_eof'):
        raise ValueError('world_layer_ward_source')
    packet=build(manifest,read(ward_directory/'wards.json',4*1024**2))
    if manifest_raw!=read(ward_directory/'manifest.json',128*1024) or replay_raw!=read(replay_build,128*1024):
        raise ValueError('world_layer_receipt_changed')
    return packet, digest(replay_raw)


def prepare(args):
    check_input_paths(args.extraction,args.replay_build,args.ward_directory)

    extraction=read_prepared(args.extraction)
    identity=extraction['identity']
    if identity['map_name']!='dota':
        raise ValueError('world_layer_build_mismatch')
    packet,build_receipt_sha=verified_replay_packet(args.replay_build,args.ward_directory,identity['client_build'])
    landmarks_raw=read(args.extraction/'landmarks/landmarks.json',128*1024)
    spec=extraction['outputs']['landmarks']
    if digest(landmarks_raw)!=spec['sha256']:
        raise ValueError('world_layer_landmark_provenance')
    landmarks=json.loads(landmarks_raw)
    if (landmarks['client_build']!=identity['client_build']
            or landmarks['map_asset_sha256']!=identity['map_asset_sha256']):
        raise ValueError('world_layer_landmark_identity')

    bounds=landmarks['declared_minimap_window']
    matrix,anchors,layer=world_texture(packet,bounds)
    result={'schema_version':1,'kind':'ward_world_texture_review','map_identity':identity,
            'source_match_id':packet['match_id'],'source_replay_sha256':packet['source_replay_sha256'],
            'source_build_receipt_sha256':build_receipt_sha,
            'source_ward_sha256':packet['source_wards_sha256'],'source_landmarks_sha256':digest(landmarks_raw),
            'size_px':[SIDE,SIDE],'world_bounds':bounds,'raw_coordinate_origin_hypothesis':RAW_ORIGIN,
            'world_xy1_to_texel_xy1':matrix,'anchors':anchors,'unavailable':packet['unavailable'],
            'layer_sha256':digest(layer),'ground_height_verified':False,'live_projection_verified':False}
    destination=checked_output(ROOT,args.output)
    destination.mkdir(parents=True,exist_ok=False)
    raw=(json.dumps(result,indent=2,allow_nan=False)+'\n').encode()
    for name,content in (('ward-layer.png',layer),('world-layer.json',raw),('COMPLETE',(digest(raw)+'\n').encode('ascii'))):
        with new_binary_output(destination/name) as stream:
            stream.write(content)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('extraction','replay-build','ward-directory','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    result=prepare(args)
    print(json.dumps({'anchors':len(result['anchors']),
                      'rasterized':sum(point['rasterized'] for point in result['anchors']),
                      'layer_sha256':result['layer_sha256']}))


if __name__=='__main__':
    main()
