"""Compare a complete replay's raw ward height with its build's static mesh."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.map_collision import CollisionMesh, vertical_hits
from dota2_insights.minimap.offline_io import check_input_paths, checked_match, new_binary_output
from dota2_insights.replay_archive import sha256_file
from dota2_insights.replay_height import FIELDS, height_observation


ORIGIN_OFFSET = 16384
NEAR_COLLISION_TOLERANCE = 4.


def read_bounded(path, limit):
    if path.is_symlink() or path.is_junction():
        raise ValueError('ward_height_source_link')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('ward_height_source_size')
    return raw


def read_mesh(folder):
    check_input_paths(folder)
    meta_path = folder/'collision.json'
    raw = read_bounded(meta_path, 128*1024)
    meta = json.loads(raw)
    archive = folder/'meshes.npz'
    if (meta['kind'] != 'decoded_static_collision_mesh_candidates'
            or meta.get('source_unchanged') is not True
            or read_bounded(folder/'COMPLETE', 256).decode('ascii').strip() != hashlib.sha256(raw).hexdigest()
            or archive.is_symlink() or archive.is_junction() or archive.stat().st_size > 128*1024**2
            or sha256_file(archive) != meta['mesh_archive_sha256']):
        raise ValueError('ward_height_mesh_provenance')
    with np.load(archive, allow_pickle=False) as arrays:
        meshes = [CollisionMesh(row['mesh'], row['attribute_index'], row['surface_property_index'],
                  arrays[f"vertices_{row['mesh']}"], arrays[f"triangles_{row['mesh']}"],
                  tuple(row['declared_min']), tuple(row['declared_max'])) for row in meta['meshes']]
    if meta_path.read_bytes() != raw or sha256_file(archive) != meta['mesh_archive_sha256']:
        raise ValueError('ward_height_mesh_changed')
    return meta, meshes


def compare_placement(row, meshes):
    observation = row.get('height_observation')
    if observation is None:
        return {'status':'raw_height_unavailable'}
    if (not isinstance(observation, dict) or not isinstance(observation.get('components'), dict)
            or row.get('height_association') != 'same_native_placement_event_and_entity_callback'):
        raise ValueError('ward_height_observation_shape')
    if observation.get('status') != 'recorded':
        return {'status':'raw_height_unavailable'}
    parts = observation['components']
    if set(parts) != set(FIELDS) or observation != height_observation(parts):
        raise ValueError('ward_height_observation_math')
    xy = [row[key]-ORIGIN_OFFSET for key in ('x','y')]
    if not all(math.isfinite(number) for number in xy):
        raise ValueError('ward_height_nonfinite_xy')
    candidate_z = observation['raw_z']-ORIGIN_OFFSET
    intersections = vertical_hits(meshes, *xy, attribute_indices={0})
    candidates = sorted({hit['height'] for hit in intersections['hits']})
    distances = [abs(height-candidate_z) for height in candidates]
    nearby = [height for height,error in zip(candidates,distances) if error <= NEAR_COLLISION_TOLERANCE]
    return {'status':'compared' if candidates else 'collision_surface_missing',
            'candidate_local_xyz':[*xy,candidate_z], 'collision_z_candidates':candidates,
            'nearest_collision_distance':min(distances, default=None), 'surfaces_within_tolerance':nearby,
            'selected_ground_z':None}


def compare_replay(folder, build, mesh_meta, meshes):
    check_input_paths(folder, build)
    build_path = Path(build)
    build_raw = read_bounded(build_path, 128*1024)
    probe = json.loads(build_raw)
    match_id = checked_match(probe['match_id'])
    manifest_path = folder/'manifest.json'
    manifest_raw = read_bounded(manifest_path, 128*1024)
    manifest = json.loads(manifest_raw)
    checked_match(manifest['match_id'])
    if (probe.get('status') != 'verified_bounded_metadata'
            or probe['game_build'] != mesh_meta['client_build']
            or probe['footer']['match_id'] != match_id
            or manifest['match_id'] != match_id or manifest['parsed_match_id'] != match_id
            or manifest['source_sha256'] != probe['source_sha256']
            or manifest.get('complete') is not True or manifest.get('independent_footer_matches_parser') is not True
            or manifest.get('parse_error') is not None or manifest.get('stop_reason') != 'replay_eof'):
        raise ValueError('ward_height_build_or_replay_mismatch')
    ward_path = folder/'wards.json'
    raw = read_bounded(ward_path, 4*1024**2)
    ward_sha = hashlib.sha256(raw).hexdigest()
    if manifest['files']['wards.json'] != {'bytes':len(raw),'sha256':ward_sha}:
        raise ValueError('ward_height_ward_payload_changed')
    wards = json.loads(raw)
    if not isinstance(wards,list) or len(wards) != manifest['ward_count'] or len(wards) > 5000:
        raise ValueError('ward_height_ward_count')
    points = []
    for index, ward in enumerate(wards):
        if ward.get('match_id') != match_id:
            raise ValueError('ward_height_cross_match')
        points.append({'id':f'{match_id}:{index}', **compare_placement(ward,meshes)})
    if (ward_path.read_bytes() != raw or manifest_path.read_bytes() != manifest_raw
            or build_path.read_bytes() != build_raw):
        raise ValueError('ward_height_replay_changed')
    distances = [p['nearest_collision_distance'] for p in points if p['status']=='compared']
    return {'match_id':match_id,'ward_source_sha256':ward_sha,'replay_sha256':manifest['source_sha256'],
            'wards_total':len(points),'compared':len(distances),
            'nearest_collision_within_4':sum(d<=NEAR_COLLISION_TOLERANCE for d in distances),
            'maximum_nearest_collision_distance':max(distances, default=None),
            'with_multiple_collision_surfaces':sum(len(p.get('collision_z_candidates',[]))>1 for p in points),
            'multiple_with_one_within_tolerance':sum(len(p.get('collision_z_candidates',[]))>1
                 and len(p['surfaces_within_tolerance'])==1 for p in points),
            'points':points}


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--collision',type=Path,required=True)
    cli.add_argument('--source',nargs=2,action='append',required=True,type=Path,metavar=('WARD_DIR','BUILD_JSON'))
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args()
    meta,meshes=read_mesh(args.collision)
    matches=[compare_replay(folder,build,meta,meshes) for folder,build in args.source]
    if len({r['match_id'] for r in matches}) != len(matches):
        raise ValueError('ward_height_duplicate_replay')
    report={'client_build':meta['client_build'],'map_asset_sha256':meta['map_asset_sha256'],
            'mesh_archive_sha256':meta['mesh_archive_sha256'],'origin_offset_hypothesis':ORIGIN_OFFSET,
            'collision_tolerance':NEAR_COLLISION_TOLERANCE,'matches':matches,
            'world_origin_verified':False,'ward_ground_height_verified':False,'pixel_alignment_verified':False}
    with new_binary_output(args.output) as stream:
        stream.write(json.dumps(report,indent=2,allow_nan=False).encode('utf-8'))
    print(json.dumps({k:sum(row[k] for row in matches) for k in ('wards_total','compared','nearest_collision_within_4',
          'with_multiple_collision_surfaces','multiple_with_one_within_tolerance')}))


if __name__ == '__main__':
    main()
