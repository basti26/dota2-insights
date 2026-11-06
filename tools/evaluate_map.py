"""Offline development evaluation; no final holdout release or production changes."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_holdout import load, save, sha
from dota2_insights.map_evaluation import evaluate, translation_review, ward_impact


def evidence_path(location):
    """Records use configured roots; only fixed own code files are exceptions."""
    path = Path(location).resolve()
    modules = ('map_compatibility.py', 'map_updates.py', 'map_prototype.py',
               'map_evaluation.py', 'map_glyph_review.py', 'map_glyph_probe.py')
    scripts = ('evaluate_map.py', 'declare_map_compatibility.py', 'associate_map_glyphs.py')
    allowed = {ROOT/'dota2_insights'/name for name in modules} | {ROOT/'tools'/name for name in scripts}
    return path if path in allowed else checked_input(ROOT, path)


def admit_prototype_metadata(prototype):
    """Check sealed-match policy from small public metadata before body hashes."""
    from dota2_insights.replay_holdout import refuse_locked_match
    manifest = load(prototype/'manifest.json', 128*1024)
    if 'ward_match_id' not in manifest:
        raise ValueError('prototype_manifest_identity_required')
    if manifest['ward_match_id'] is not None:
        refuse_locked_match(str(manifest['ward_match_id']))


def prepare(prototype, landmarks, output):
    """Prepare an honest missing-label inventory from existing frozen material."""
    from collections import Counter
    from dota2_insights.map_landmarks import extract
    from dota2_insights.map_prototype import open_scene, read_complete
    from dota2_insights.virtual_map import VirtualMap, WorldBounds
    from dota2_insights.map_evaluation import DEFAULT_POLICY, region
    prototype, landmarks = [checked_input(ROOT, p) for p in (prototype, landmarks)]
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_map_evaluation_output_required')
    admit_prototype_metadata(prototype)
    initial_paths = [prototype/name for name in ('COMPLETE', 'manifest.json', 'scene.json')]
    initial_paths += [landmarks/name for name in ('landmarks.json', 'COMPLETE', 'DATA.txt', 'default_ents.vents_c')]
    initial = {str(p): sha(p) for p in initial_paths}
    scene = open_scene(prototype)
    original = read_complete(landmarks, 'landmarks.json')
    model = VirtualMap.from_dict(scene['virtual_map_model'])
    plans = [Path(p) for p in scene['source_sha256'] if Path(p).name == 'plan.json']
    if len(plans) != 1:
        raise ValueError('evaluation_survey_plan_required')
    plan_path = checked_input(ROOT, plans[0])
    if sha(plan_path) != scene['source_sha256'][str(plan_path)]:
        raise ValueError('evaluation_survey_plan_changed')
    plan = load(plan_path)
    image_identity = {**scene['virtual_map_model']['identity'], 'client_build': int(plan['map_build'])}
    if (original['map_asset_sha256'] != model.identity.map_asset_sha256
            or original['client_build'] != model.identity.client_build):
        raise ValueError('evaluation_landmark_map_identity')
    source = landmarks/'DATA.txt'
    if sha(source) != original['source_export_sha256']:
        raise ValueError('evaluation_entity_export_changed')
    compiled = landmarks/'default_ents.vents_c'
    if sha(compiled) != original['resource_sha256']:
        raise ValueError('evaluation_entity_resource_changed')
    frame = scene['frames'][0]
    image = prototype/frame['image']
    tracked = [prototype/name for name in ('COMPLETE', 'manifest.json', 'scene.json')]
    tracked += [source, compiled, landmarks/'landmarks.json', landmarks/'COMPLETE', image, plan_path,
                Path(__file__), ROOT/'dota2_insights/map_evaluation.py', ROOT/'dota2_insights/map_landmarks.py',
                ROOT/'dota2_insights/virtual_map.py', ROOT/'dota2_insights/map_prototype.py']
    before = {str(p): sha(p) for p in tracked}
    if any(before.get(p) != h for p, h in initial.items()):
        raise ValueError('map_evaluation_source_changed')
    if before[str(plan_path)] != scene['source_sha256'][str(plan_path)]:
        raise ValueError('evaluation_survey_plan_changed')
    extended = extract(source.read_text(encoding='utf-8-sig'), include_towers=True)
    models = {'prototype_declared_window': scene['virtual_map_model']}
    for name, bounds in (('reference_window', [-10464, -10464, 10400, 10400]),
                         ('legacy_gem_centered_window', [-8897, -8691, 9425, 9631])):
        models[name] = VirtualMap(model.identity, WorldBounds(*bounds), model.coordinate_frame,
                                'reference', name+'; fixed hypothesis, not fitted or validated').to_dict()
    rows = []
    for point in extended['landmarks']:
        rows.append({'landmark_id': str(point['entity_ordinal']), 'world_xyz': point['origin_xyz'],
                     'world_source': original['source_export_sha256']+':entity:'+str(point['entity_ordinal']),
                     'image_sha256': sha(image), 'image_size': frame['size'],
                     'content_box_xywh': [0, 0, *frame['size']], 'role': 'development',
                     'historical_training_point': point['classname'] == 'npc_dota_tower',
                     'pixel_xy': None, 'missing_reason': 'no independent named glyph center; image is review target only'})
    data = {'schema_version': 1, 'role': 'development', 'map_identity': scene['virtual_map_model']['identity'],
            'image_map_identity': image_identity,
            'coordinate_frame': model.coordinate_frame, 'partition_bounds': scene['bounds'],
            'sealed_final_sha256': None, 'rows': rows}
    translation = {'schema_version': 1, 'map_identity': data['map_identity'],
                   'source_frame': 'gem_cell128_plus_vec_raw', 'target_frame': 'source2_map_centered', 'rows': []}
    pool = {'schema_version': 1, 'map_identity': data['map_identity'],
            'landmarks': extended['landmarks'], 'world_candidate_regions': dict(Counter(
                region(p['origin_xyz'], model.bounds) for p in extended['landmarks'])),
            'image_correspondences_confirmed': 0, 'final_split_created': False,
            'reason': 'candidate inventory only; old towers treated as historical training, no final pixels read',
            'source_sha256': before}
    open_scene(prototype)
    if any(sha(p) != h for p, h in before.items()):
        raise ValueError('map_evaluation_source_changed')
    output.mkdir(parents=True)
    files = {'development.json': data, 'models.json': models, 'policy.json': DEFAULT_POLICY,
             'translation-pairs.json': translation, 'translation-candidates.json': {'minus16384': [-16384, -16384]},
             'candidate-pool.json': pool}
    for name, value in files.items():
        save(output/name, value)
    save(output/'manifest.json', {'source_sha256': before, 'files': {name: sha(output/name) for name in files}})
    (output/'COMPLETE').write_text(sha(output/'manifest.json')+'\n', encoding='ascii')
    return {'kind': 'map_development_inputs', 'landmarks': len(rows), 'independent_labels': 0, 'production_ready': False}


def run(command, inputs, output, baseline=None):
    paths = {name: checked_input(ROOT, path) for name, path in inputs.items()}
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_map_evaluation_output_required')
    tracked = [p for p in paths.values() if p.is_file()]
    scene = None
    if command == 'ward-impact':
        # open_scene checks the small manifest's match lock before reading any
        # scene/ward body, and verifies its complete display-file roster.
        from dota2_insights.map_prototype import open_scene
        admit_prototype_metadata(paths['prototype'])
        tracked += [paths['prototype']/name for name in ('COMPLETE', 'manifest.json', 'scene.json')]
    tracked += [Path(__file__), ROOT/'dota2_insights/map_evaluation.py',
               ROOT/'dota2_insights/map_compatibility.py',
               ROOT/'dota2_insights/virtual_map.py', ROOT/'dota2_insights/ward_map.py',
               ROOT/'dota2_insights/map_prototype.py']
    before = {str(p): sha(p) for p in tracked}
    if command == 'ward-impact':
        scene = open_scene(paths['prototype'])
    values = {name: load(path) for name, path in paths.items() if name != 'prototype'}
    if 'compatibility' in values:
        from dota2_insights.map_compatibility import validate
        data = values['dataset']
        validate(values['compatibility'], data['image_map_identity'], data['map_identity'])
        for location, fingerprint in values['compatibility']['source_sha256'].items():
            path = evidence_path(location)
            if sha(path) != fingerprint:
                raise ValueError('compatibility_evidence_changed')
            before[str(path)] = fingerprint
    if command == 'windows':
        if values.get('compatibility') is None:
            result = evaluate(values['dataset'], values['models'], values.get('policy'))
        else:
            result = evaluate(values['dataset'], values['models'], values.get('policy'), compatibility=values['compatibility'])
    elif command == 'translation':
        result = translation_review(values['dataset'], values['candidates'])
    elif command == 'ward-impact':
        if scene.get('ward_candidates') is None:
            raise ValueError('ward_impact_prototype_has_no_wards')
        result = ward_impact(scene['ward_candidates'], values['models'], baseline)
        from dota2_insights.map_prototype import open_scene
        open_scene(paths['prototype'])
    else:
        raise ValueError('unknown_map_evaluation_command')
    if any(sha(p) != h for p, h in before.items()):
        raise ValueError('map_evaluation_source_changed')
    result['source_sha256'] = before
    result['sources_reverified'] = True
    output.mkdir(parents=True)
    save(output/'report.json', result)
    (output/'COMPLETE').write_text(sha(output/'report.json')+'\n', encoding='ascii')
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest='command', required=True)
    windows = sub.add_parser('windows')
    windows.add_argument('--dataset', required=True, type=Path)
    windows.add_argument('--models', required=True, type=Path)
    windows.add_argument('--policy', type=Path)
    windows.add_argument('--compatibility', type=Path)
    translation = sub.add_parser('translation')
    translation.add_argument('--dataset', required=True, type=Path)
    translation.add_argument('--candidates', required=True, type=Path)
    impact = sub.add_parser('ward-impact')
    impact.add_argument('--prototype', required=True, type=Path)
    impact.add_argument('--models', required=True, type=Path)
    impact.add_argument('--baseline', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--prototype', required=True, type=Path)
    prep.add_argument('--landmarks', required=True, type=Path)
    for parser in (windows, translation, impact, prep):
        parser.add_argument('--output', required=True, type=Path)
    args = vars(cli.parse_args())
    command, output, baseline = args.pop('command'), args.pop('output'), args.pop('baseline', None)
    if command == 'prepare':
        result = prepare(args['prototype'], args['landmarks'], output)
    else:
        result = run(command, {k: v for k, v in args.items() if v is not None}, output, baseline)
    print({'output': str(output), 'kind': result['kind'], 'production_ready': False})


if __name__ == '__main__':
    main()
