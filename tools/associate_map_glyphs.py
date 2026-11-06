"""Associate visually reviewed development identities with frozen image detections."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_evaluation import associate_glyphs
from dota2_insights.map_holdout import load, save, sha
from dota2_insights.map_prototype import open_scene
from tools.evaluate_map import admit_prototype_metadata, evidence_path


def run(prototype, dataset, batch, associations, output):
    prototype, dataset, batch, associations = [checked_input(ROOT, p) for p in (prototype, dataset, batch, associations)]
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_glyph_association_output_required')
    admit_prototype_metadata(prototype)
    paths = [prototype/name for name in ('manifest.json', 'COMPLETE', 'scene.json')]
    paths += [dataset, batch/'freeze.json', batch/'observations.json', associations, Path(__file__),
              ROOT/'dota2_insights/map_evaluation.py', ROOT/'dota2_insights/map_prototype.py', ROOT/'tools/evaluate_map.py']
    before = {str(p): sha(p) for p in paths}
    scene = open_scene(prototype)
    sources = scene['source_sha256']
    packages = [checked_input(ROOT, p) for p in sources if Path(p).name == 'package.json']
    if len(packages) != 1 or sha(packages[0]) != sources[str(packages[0])]:
        raise ValueError('glyph_association_package_not_bound')
    before[str(packages[0])] = sources[str(packages[0])]
    freeze, obs = load(batch/'freeze.json'), load(batch/'observations.json')
    if (freeze.get('predictions_read') is not False or freeze['observations_sha256'] != sha(batch/'observations.json')
            or freeze['package_sha256'] != sha(packages[0]) or obs['package_sha256'] != freeze['package_sha256']):
        raise ValueError('glyph_association_freeze_changed')
    for location, fingerprint in obs['sources'].items():
        path = evidence_path(location)
        if sha(path) != fingerprint:
            raise ValueError('glyph_association_detector_source_changed')
        before[str(path)] = fingerprint
    data, labels = load(dataset), load(associations, 256*1024)
    if (labels['observations_sha256'] != sha(batch/'observations.json')
            or labels['dataset_sha256'] != sha(dataset)):
        raise ValueError('glyph_association_review_not_bound')
    frames = {r['image_sha256']: r for r in scene['frames']}
    if any(row['image_sha256'] not in frames for row in data['rows']):
        raise ValueError('glyph_association_image_not_in_prototype')
    result = associate_glyphs(data, obs, labels)
    open_scene(prototype)
    if any(sha(p) != h for p, h in before.items()):
        raise ValueError('glyph_association_sources_changed')
    output.mkdir(parents=True)
    save(output/'development.json', result)
    save(output/'receipt.json', {'source_sha256': before, 'development_sha256': sha(output/'development.json'),
                               'human_certified': False, 'formal_p2_pass': False})
    (output/'COMPLETE').write_text(sha(output/'receipt.json')+'\n', encoding='ascii')
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('prototype', 'dataset', 'batch', 'associations', 'output'):
        cli.add_argument('--'+name, type=Path, required=True)
    args = cli.parse_args()
    result = run(args.prototype, args.dataset, args.batch, args.associations, args.output)
    print({'observed_points': sum(r['pixel_xy'] is not None for r in result['rows']), 'formal_p2_pass': False})


if __name__ == '__main__':
    main()
