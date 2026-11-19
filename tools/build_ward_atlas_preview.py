"""Compile an offline terrain atlas preview from matching world ward surfaces."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from dota2_insights.history import checked_input, checked_output
from dota2_insights.minimap.offline_io import checked_match, new_binary_output
from dota2_insights.ward_ground_tile import fit, project_footprint
from tools.audit_ward_height_origin import read_bounded


def _json(raw):
    return (json.dumps(raw,indent=2,allow_nan=False)+'\n').encode()


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _texture(path, entry):
    name = entry['texture']
    if not re.fullmatch(r'ward-atlas-[A-Za-z0-9_-]{1,32}\.local\.png',name):
        raise ValueError('ward_atlas_texture_name')
    raw = read_bounded(path.with_name(name),1024**2)
    if _digest(raw)!=entry['texture_sha256']:
        raise ValueError('ward_atlas_texture_sha')
    with Image.open(io.BytesIO(raw)) as bitmap:
        width,height = bitmap.size
        if bitmap.format!='PNG' or not all(64<=n<=1024 for n in (width,height)):
            raise ValueError('ward_atlas_texture_size')
        bitmap.verify()
    return raw,(width,height)


def prepare(template_path, controls_path, surface_path, output):
    template_raw = read_bounded(template_path,32768)
    control_raw = read_bounded(controls_path,256*1024)
    surface_raw = read_bounded(surface_path,4*1024**2)
    template, controls, surface = (json.loads(raw) for raw in (template_raw,control_raw,surface_raw))
    source_match = checked_match(surface['source_match_id'])
    if (surface['kind']!='ward_static_terrain_review_scene'
            or read_bounded(surface_path.with_name('COMPLETE'),256).decode('ascii').strip()!=_digest(surface_raw)
            or not 1<=len(surface['anchors'])<=5000):
        raise ValueError('ward_atlas_surface_receipt')
    if (template.get('kind')!='ward_terrain_atlas' or template.get('schema_version')!=2
            or not 1<=len(template['tiles'])<=8 or controls.get('kind')!='ward_ground_tile_controls_v1'
            or controls['template_sha256']!=_digest(template_raw)):
        raise ValueError('ward_atlas_controls_scope')
    identity = template['client_build'],template['map_asset_sha256']
    report = {'kind':'ward_world_atlas_preview_review','client_build':identity[0],'map_asset_sha256':identity[1],
              'source_match_id':source_match,'preview_generated':False,'live_usable':False,
              'absolute_world_alignment_verified':False,'tiles':{},
              'inputs_sha256':{'template':_digest(template_raw),'controls':_digest(control_raw),
                               'surface':_digest(surface_raw)}}
    map_identity = surface['map_identity']
    if (map_identity['client_build'],map_identity['map_asset_sha256'])!=identity:
        report['status'] = 'ward_source_map_or_build_mismatch'
        tiles, textures = [],{}
    else:
        if controls['map_identity']!=map_identity:
            raise ValueError('ward_atlas_control_map_mismatch')
        tiles,textures = _compile(template_path,template['tiles'],controls['tiles'],surface['anchors'],report)
        report['status'] = 'offline_preview_generated' if tiles else 'no_supported_ward_tiles'

    output.mkdir(parents=True,exist_ok=False)
    files = {}
    if tiles:
        preview = {key:template[key] for key in ('schema_version','kind','client_build','client_size','map_asset_sha256')}
        preview['tiles'] = tiles
        preview['preview_is_offline_only'] = True
        files = {**textures,'ward-atlas-preview.local.json':_json(preview)}
        for name,raw in files.items():
            with new_binary_output(output/name) as stream:
                stream.write(raw)
        report['preview_generated'] = True
    report['artifact_sha256'] = {name:_digest(raw) for name,raw in files.items()}
    report_bytes = _json(report)
    with new_binary_output(output/'preview-review.json') as stream:
        stream.write(report_bytes)
    with new_binary_output(output/'COMPLETE') as stream:
        stream.write((_digest(report_bytes)+'\n').encode('ascii'))
    return report


def _compile(path, entries, controls, points, report):
    tiles,textures = [],{}
    known = {tile['id'] for tile in entries}
    if len(known)!=len(entries) or controls.keys()-known:
        raise ValueError('ward_atlas_control_tile_ids')
    for entry in entries:
        key = entry['id']
        measured = controls.get(key)
        if measured is None:
            report['tiles'][key] = {'status':'no_ground_control_input'}
            continue
        raw,size = _texture(path,entry)
        candidate = fit(measured['controls'],measured.get('plane_z'))
        report['tiles'][key] = candidate
        if candidate['status']!='candidate_ground_plane':
            continue
        anchors,footprints = [],{}
        for point in points:
            patch = point['patch']
            projected = project_footprint(candidate,patch['vertex_world_xyz']) if patch is not None else None
            if projected is None:
                continue
            xy = projected['center_xy']
            corners = np.asarray(projected['footprint_xy'],dtype=np.float32)
            if (not np.all((0<=corners) & (corners<np.asarray(size))) or not cv2.isContourConvex(corners)
                    or cv2.contourArea(corners)<4 or cv2.pointPolygonTest(corners,tuple(xy),False)<=0):
                continue
            ward = point['id'].replace(':','_')
            if (ward in footprints or not re.fullmatch('[A-Za-z0-9_-]{1,24}',ward)
                    or point['ward_type'] not in ('observer','sentry')):
                raise ValueError('ward_atlas_preview_ward_id')
            anchors.append([ward,*xy,point['ward_type']])
            footprints[ward] = projected['footprint_xy']
        candidate['supported_ward_ids'] = sorted(footprints)
        if len(anchors)>24:
            candidate['status'] = 'preview_tile_exceeds_marker_capacity'
        elif anchors:
            tiles.append({**entry,'anchors':anchors,'footprints':footprints})
            textures[entry['texture']] = raw
    return tiles,textures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('template','controls','surface','output'):
        parser.add_argument(f'--{name}',type=Path,required=True)
    args = parser.parse_args()
    report = prepare(*(checked_input(ROOT,getattr(args,k)) for k in ('template','controls','surface')),
                     checked_output(ROOT,args.output))
    print(json.dumps({'status':report['status'],'preview_generated':report['preview_generated']}))


if __name__=='__main__':
    main()
