"""Render historical ward surface patches into paired, offline camera candidates."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.history import checked_input, checked_output
from dota2_insights.minimap.offline_io import checked_match, new_binary_output
from dota2_insights.native_camera_calibration import _basis
from dota2_insights.native_camera_review import validate_position
from tools.audit_ward_height_origin import read_bounded


def _read(path, limit):
    raw = read_bounded(path, limit)
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _projector(position, fit, offset):
    validate_position(position)
    camera = np.asarray(position['camera_xyz'], dtype=float)
    pitch = float(fit['pitch_degrees'])
    rotation = _basis(camera, position['yaw_xy_degrees'], pitch)
    center = np.asarray(fit['principal_point_xy'], dtype=float)
    focal = float(fit['focal_px'])
    if (center.shape != (2,) or not np.isfinite(center).all()
            or not 0 < focal < 10000 or not 0 < pitch < 90):
        raise ValueError('ward_surface_camera_intrinsics')
    camera[:2] += offset

    def project(world):
        local = (world-camera) @ rotation.T
        if np.any(local[:,2] <= 1):
            return None
        return local[:,:2] / local[:,2,None]*focal + center

    return project


def _raster(layer, projected, kind):
    points = [tuple(point) for point in projected[:4]]
    center = projected[4]
    inset = [tuple(center + .67*(point-center)) for point in projected[:4]]
    draw = ImageDraw.Draw(layer)
    draw.polygon(points, fill=(17,24,30,215))
    color = (255,220,117,235) if kind == 'observer' else (140,222,255,235)
    draw.polygon(inset, fill=color)


def prepare(surface_path, views_path, fit_path, offset_path, output):
    surface, surface_sha = _read(surface_path, 4*1024**2)
    checked_match(surface['source_match_id'])
    if (surface['kind'] != 'ward_static_terrain_review_scene'
            or read_bounded(surface_path.with_name('COMPLETE'), 256).decode('ascii').strip() != surface_sha):
        raise ValueError('ward_surface_projection_receipt')
    views, views_sha = _read(views_path, 128*1024)
    fit, fit_sha = _read(fit_path, 128*1024)
    offset, offset_sha = _read(offset_path, 128*1024)
    if (fit['kind'] != 'relative_ground_view_fit' or fit['input_sha256'] != views_sha
            or offset['relative_fit_sha256'] != fit_sha or offset['map_identity'] != surface['map_identity']
            or offset['atlas_reference_image_sha256'] != views['views'][0]['sha256']):
        raise ValueError('ward_surface_projection_source_mismatch')
    size = tuple(views['viewport'])
    delta = np.asarray(offset['candidate_world_xy_offset'], dtype=float)
    if (len(size) != 2 or not all(type(n) is int for n in size)
            or not 640 <= size[0] <= 4096 or not 480 <= size[1] <= 2160
            or len(views['views']) != 2 or len(views['views']) != len({v['sha256'] for v in views['views']})
            or delta.shape != (2,) or not np.isfinite(delta).all()
            or not 1 <= len(surface['anchors']) <= 5000):
        raise ValueError('ward_surface_projection_scope')
    models = [_projector(view['position'],fit['vertical_principal_point_free'],delta)
              for view in views['views']]
    layers = [Image.new('RGBA',size) for _ in models]
    rows = []
    for anchor in surface['anchors']:
        patch = anchor['patch']
        row = {'id':anchor['id'],'ward_type':anchor['ward_type'],
               'world_xy':anchor['world_xy'],'surface_status':anchor['status'],'views':[]}
        rows.append(row)
        if patch is None:
            continue
        world = np.asarray(patch['vertex_world_xyz'], dtype=float)
        if (world.shape != (5,3) or not np.isfinite(world).all()
                or world[4].tolist() != anchor['world_xy']+[patch['center_z']]
                or anchor['ward_type'] not in ('observer','sentry')):
            raise ValueError('ward_surface_projection_anchor')
        for model,layer in zip(models,layers,strict=True):
            points = model(world)
            inside = bool(points is not None and np.all((0 <= points) & (points < size)))
            row['views'].append({'footprint_inside_view':inside,
                                 'center_xy':points[4].tolist() if points is not None else None,
                                 'corners_xy':points[:4].tolist() if inside else None})
            if inside:
                _raster(layer, points, anchor['ward_type'])

    output.mkdir(parents=True,exist_ok=False)
    for i,layer in enumerate(layers,1):
        name = f'view-{i}-ward-layer.png'
        with new_binary_output(output/name) as stream:
            layer.save(stream, format='PNG')
    report = {
        'kind':'ward_surface_camera_review','map_identity':surface['map_identity'],'viewport':size,
        'historical_source_match_id':surface['source_match_id'],
        'inputs_sha256':{'surface':surface_sha,'views':views_sha,'fit':fit_sha,'offset':offset_sha},
        'camera_frame_sha256':[view['sha256'] for view in views['views']],
        'footprint_counts_by_views':dict(Counter(sum(v['footprint_inside_view'] for v in row['views'])
                                               for row in rows if row['views'])),
        'relative_feature_check_max_px':fit['vertical_principal_point_free']['residuals']['check']['max_px'],
        'absolute_landmark_check_max_px':max(offset['left_check_max_px'],offset['right_candidate_check_max_px']),
        'camera_frame_alignment_verified':False,'occlusion_tested':False,'live_usable':False,
        'anchors':rows,
    }
    with new_binary_output(output/'projection.json') as stream:
        stream.write((json.dumps(report,indent=2,allow_nan=False)+'\n').encode())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ('surface','views','fit','offset','output'):
        parser.add_argument(f'--{option}',type=Path,required=True)
    args = parser.parse_args()
    report = prepare(*(checked_input(ROOT,getattr(args,k)) for k in ('surface','views','fit','offset')),
                     checked_output(ROOT,args.output))
    print(json.dumps(report['footprint_counts_by_views']))


if __name__ == '__main__':
    main()
