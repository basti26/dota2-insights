"""Build a Foresight minimap profile from a closed frame and same-build ward data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dota2_insights.history import checked_output
from dota2_insights.map_landmarks import extract
from dota2_insights.map_updates import read_prepared
from dota2_insights.minimap.offline_io import check_input_paths, checked_match, new_binary_output


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=2 * 1024**2):
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f'input exceeds size limit: {path.name}')
    return raw


def encoded_json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def fit_projection(annotations, towers, crop, client_height):
    x, y, right, bottom = crop
    roles = []
    used = set()
    for role in ('fit_points', 'holdout_points'):
        world, screen = [], []
        for point in annotations[role]:
            name = point['targetname']
            # Ambiguous map names are excluded by the caller.
            if name in used or name not in towers or point['world_xy'] != towers[name]:
                raise ValueError(f'tower identity or coordinates mismatch: {name}')
            used.add(name)
            px, py = point['observed_client_xy']
            if not (x <= px < right and y <= py < bottom):
                raise ValueError(f'tower annotation outside crop: {name}')
            world.append([*towers[name], 1])
            screen.append([px, py])
        design = np.asarray(world, dtype=float)
        if len(world) < 4 or np.linalg.matrix_rank(design) != 3:
            raise ValueError(f'need four or more non-collinear towers in {role}')
        roles.append((design, np.asarray(screen, dtype=float)))

    matrix = np.linalg.lstsq(*roles[0], rcond=None)[0]
    if (not .005 <= matrix[0, 0] <= .06 or not -.06 <= matrix[1, 1] <= -.005
            or max(abs(matrix[0, 1]), abs(matrix[1, 0])) > .003):
        raise ValueError('projection is outside the runtime orientation and scale limits')

    residuals = {}
    for role, (design, screen) in zip(('fit', 'holdout'), roles):
        errors = np.linalg.norm(design @ matrix - screen, axis=1)
        residuals[role] = {'count': len(errors), 'rmse_px': float(np.sqrt(np.mean(errors**2))),
                           'max_px': float(np.max(errors)), 'errors_px': errors.tolist()}
    scale = client_height / 1440
    if any(r['rmse_px'] > 2 * scale or r['max_px'] > 3 * scale for r in residuals.values()):
        raise ValueError(f'tower residuals exceed tolerance: {residuals}')
    return matrix, residuals


def prepare(args):
    check_input_paths(args.frame, args.annotations, args.extraction, args.replay_build, args.packet)
    build_raw = read(args.replay_build)
    replay = json.loads(build_raw)
    match = checked_match(replay['match_id'])
    replay_sha = replay['source_sha256']
    if (replay['schema_version'] != 'replay_bounded_metadata_probe_v1'
            or replay['status'] != 'verified_bounded_metadata'
            or replay['source_stat_unchanged'] is not True
            or checked_match(replay['footer']['match_id']) != match
            or replay_sha != replay['expected_source_sha256']
            or replay_sha != replay['source_sha256_after_read']):
        raise ValueError('verified replay build receipt required')

    extraction = read_prepared(args.extraction)
    identity = extraction['identity']
    build = identity['client_build']
    if identity['map_name'] != 'dota' or type(build) is not int or build != replay['game_build']:
        raise ValueError('replay and map extraction builds differ')

    packet_raw = read(args.packet)
    packet = json.loads(packet_raw)
    if (checked_match(packet['match_id']) != match or packet['source_replay_sha256'] != replay_sha
            or packet['source_replay_game_build'] != build or packet['schema_version'] != 1
            or packet['kind'] != 'historical_ward_reference'
            or packet['coordinate_basis'] != 'gem_cell128_plus_vec_raw_coordinates'
            or not packet['points']):
        raise ValueError('ward packet does not match verified replay and map build')

    annotation_raw = read(args.annotations)
    annotations = json.loads(annotation_raw)
    frame_raw = read(args.frame, 16 * 1024**2)
    frame_sha = digest(frame_raw)
    if (annotations['map'] != identity or annotations['source_frame'] != args.frame.name
            or annotations['source_frame_sha256'] != frame_sha):
        raise ValueError('frame or map identity does not match the annotations')

    width, height = annotations['client_wh']
    x, y, right, bottom = crop = args.crop
    if (type(width) is not int or type(height) is not int or not
            (640 <= width <= 4096 and 480 <= height <= 2160
             and 0 <= x < right <= width and 0 <= y < bottom <= height
             and 225 <= right-x <= 512 and 225 <= bottom-y <= 512)):
        raise ValueError('client size or minimap crop is outside the runtime limits')
    frame = cv2.imdecode(np.frombuffer(frame_raw, np.uint8), cv2.IMREAD_GRAYSCALE)
    if frame is None or frame.shape != (height, width):
        raise ValueError('decoded frame dimensions do not match the annotations')

    data = read(args.extraction / 'landmarks' / 'DATA.txt', 32 * 1024**2)
    data_sha = digest(data)
    if data_sha != extraction['outputs']['landmarks']['files_sha256']['DATA.txt']:
        raise ValueError('map entity export changed after validation')
    entities = extract(data.decode('utf-8-sig'), include_towers=True)['landmarks']
    tower_rows = [row for row in entities if row['classname'] == 'npc_dota_tower']
    names = [row['targetname'] for row in tower_rows]
    towers = {row['targetname']: row['origin_xyz'][:2] for row in tower_rows
              if row['targetname'] and names.count(row['targetname']) == 1}
    matrix, residuals = fit_projection(annotations, towers, crop, height)
    ok, png = cv2.imencode('.png', frame[y:bottom, x:right])
    if not ok:
        raise ValueError('could not encode the minimap reference')
    image = png.tobytes()
    profile = {
        'schema_version': 1, 'kind': 'ward_minimap_client_calibration', 'client_build': build,
        'client_size': [width, height], 'minimap_crop_xyxy': crop,
        'map_asset_sha256': identity['map_asset_sha256'], 'packet_sha256': digest(packet_raw),
        'reference_sha256': digest(image), 'world_xy1_to_client_xy_matrix': matrix.tolist(),
    }
    profile_raw = encoded_json(profile)
    receipt = {
        'client_build': build, 'map_asset_sha256': identity['map_asset_sha256'], 'match_id': match,
        'map_entity_export_sha256': data_sha, 'extraction_revision_id': extraction['revision_id'],
        'annotations_sha256': digest(annotation_raw), 'frame_sha256': frame_sha,
        'replay_build_receipt_sha256': digest(build_raw), 'ward_packet_sha256': profile['packet_sha256'],
        'reference_sha256': profile['reference_sha256'], 'profile_sha256': digest(profile_raw),
        'tower_residuals': residuals, 'scope': 'offline annotated minimap tower glyphs at one client layout',
        'ward_glyph_pixel_alignment_verified': False, 'main_view_projection_verified': False,
    }
    output = checked_output(ROOT, args.output)
    output.mkdir(parents=True, exist_ok=False)
    receipt_raw = encoded_json(receipt)
    for name, raw in (('ward-minimap.local.json', profile_raw), ('ward-minimap-reference.local.png', image),
                      ('verification.json', receipt_raw), ('COMPLETE', (digest(receipt_raw) + '\n').encode('ascii'))):
        with new_binary_output(output / name) as stream:
            stream.write(raw)
    return receipt


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for key in ('frame', 'annotations', 'extraction', 'replay-build', 'packet', 'output'):
        cli.add_argument(f'--{key}', type=Path, required=True)
    cli.add_argument('--crop', type=int, nargs=4, metavar=('LEFT', 'TOP', 'RIGHT', 'BOTTOM'), required=True)
    args = cli.parse_args()
    result = prepare(args)
    print(json.dumps({'output': str(args.output.resolve()), 'tower_residuals': result['tower_residuals']}))


if __name__ == '__main__':
    main()
