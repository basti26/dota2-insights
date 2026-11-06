"""Register frozen official terrain to saved crops, without world-coordinate input."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_holdout import load, save, sha, local
from dota2_insights.map_prototype import open_scene
from dota2_insights.map_texture_registration import register
from tools.evaluate_map import admit_prototype_metadata


def run(prototype, texture, output):
    import numpy as np
    from PIL import Image
    prototype, texture = [checked_input(ROOT, p) for p in (prototype, texture)]
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_texture_registration_output_required')
    admit_prototype_metadata(prototype)
    tracked = [prototype/name for name in ('COMPLETE', 'manifest.json', 'scene.json')]
    tracked += [texture, Path(__file__), ROOT/'dota2_insights/map_texture_registration.py', ROOT/'dota2_insights/map_evaluation.py',
                ROOT/'dota2_insights/map_prototype.py', ROOT/'tools/evaluate_map.py']
    before = {str(p): sha(p) for p in tracked}
    scene = open_scene(prototype)
    # Only the old atlas's immutable file receipt is consumed; no calibration
    # matrix, entities or world positions are passed to the registration core.
    atlas_paths = [checked_input(ROOT, p) for p in scene['source_sha256'] if Path(p).name == 'atlas.json']
    if len(atlas_paths) != 1 or sha(atlas_paths[0]) != scene['source_sha256'][str(atlas_paths[0])]:
        raise ValueError('registration_bound_texture_receipt_required')
    atlas = load(atlas_paths[0])
    texture_sources = {row['file']: row['sha256'] for row in atlas['sources']}
    if texture_sources.get(str(texture)) != sha(texture):
        raise ValueError('registration_official_texture_not_bound')
    before[str(atlas_paths[0])] = sha(atlas_paths[0])
    if not 1 <= len(scene['frames']) <= 20:
        raise ValueError('registration_frame_budget')
    def rgb(path):
        with Image.open(path) as im:
            if not 64 <= min(im.size) <= max(im.size) <= 1024:
                raise ValueError('registration_image_budget')
            return np.asarray(im.convert('RGB'))
    source = rgb(texture)
    results = []
    for frame in scene['frames']:
        path = local(prototype, frame['image'])
        before[str(path)] = sha(path)
        if before[str(path)] != frame['image_sha256']:
            raise ValueError('registration_frame_receipt_changed')
        target = rgb(path)
        if list(target.shape[1::-1]) != frame['size']:
            raise ValueError('registration_frame_dimensions_changed')
        result = register(source, target)
        result.update(case=frame['case'], frame_index=frame['frame_index'], image=frame['image'], image_sha256=before[str(path)])
        results.append(result)
    open_scene(prototype)
    if any(sha(path) != value for path, value in before.items()):
        raise ValueError('registration_sources_changed')
    report = {'schema_version': 1, 'kind': 'frozen_terrain_image_registration_batch',
              'texture_sha256': sha(texture), 'texture_context_build': str(atlas['map_build']),
              'source_sha256': before, 'frames': results,
              'distinct_validation_source_locations_rounded_px': len({tuple(round(v) for v in row['source_xy'])
                  for frame in results for row in frame['rows'] if row['role'] == 'validation'}),
              'world_transform_fitted': False, 'glyphs_or_tower_fit_used': False,
              'formal_p2_pass': False, 'formal_p3_pass': False, 'production_ready': False}
    output.mkdir(parents=True)
    save(output/'registration.json', report)
    (output/'COMPLETE').write_text(sha(output/'registration.json')+'\n', encoding='ascii')
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('prototype', 'texture', 'output'):
        cli.add_argument('--'+name, type=Path, required=True)
    args = cli.parse_args()
    r = run(args.prototype, args.texture, args.output)
    print({'frames': len(r['frames']), 'estimated': sum(v['matrix'] is not None for v in r['frames']), 'formal_p2_pass': False})


if __name__ == '__main__':
    main()
