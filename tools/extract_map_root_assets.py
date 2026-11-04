"""Extract bounded map-root metadata/grid/catalog with full asset fingerprints."""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.history import checked_input, checked_output
from dota2_insights.map_holdout import load, save, sha
from dota2_insights.map_root_assets import external_references, grid, trm, compare_trees
from dota2_insights.valve_resources import directory_bytes, read_vpk_directory, _read_resource
from tools.extract_map_collision import guard


def run(game, output, tree_inventory=None):
    guard()
    game = Path(game).resolve(strict=True)
    output = checked_output(ROOT, output)
    if output.exists(): raise ValueError('new_map_root_output_required')
    source = game/'maps/dota.vpk'
    before = directory_bytes(source)
    steam = (game/'steam.inf').read_bytes()
    build = int(dict(s.split('=',1) for s in steam.decode('utf-8-sig').splitlines() if '=' in s)['ClientVersion'])
    map_sha = hashlib.sha256(before).hexdigest()
    entries, start = read_vpk_directory(before)
    contents, records = {}, []
    for name in ('maps/dota.vmap_c', 'maps/dota.gnv', 'maps/dota.trm', 'maps/dota.vhcg'):
        body, _ = _read_resource(source, entries[name], start, 16*1024**2)
        contents[Path(name).name] = body
        records.append({'resource': name, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'crc_verified': True})
    refs = external_references(contents['dota.vmap_c'])
    parsed_grid = grid(contents['dota.gnv'])
    grid_info = {k:v for k,v in parsed_grid.items() if k != 'values'}
    catalog = trm(contents['dota.trm'])
    tree_comparison, tree_source = None, None
    if tree_inventory is not None:
        path = checked_input(ROOT, tree_inventory)
        tree_sha = sha(path)
        verification_path = path.parent/'verification.json'
        verification_sha = sha(verification_path)
        if load(verification_path)['files'].get(path.name) != tree_sha:
            raise ValueError('map_root_tree_inventory_not_bound')
        inventory = load(path)
        if inventory['source_map_sha256'] != map_sha:
            raise ValueError('map_root_tree_asset_mismatch')
        tree_comparison = compare_trees(catalog, inventory)
        tree_source = {'file': str(path), 'sha256': tree_sha, 'verification_sha256': verification_sha}
    if directory_bytes(source) != before or (game/'steam.inf').read_bytes() != steam:
        raise ValueError('map_root_assets_changed')
    if tree_source and (sha(path) != tree_sha or sha(verification_path) != verification_sha):
        raise ValueError('map_root_tree_source_changed')
    guard()
    result = {'schema_version':1, 'kind':'local_map_root_asset_candidates', 'client_build': build,
        'map_asset_sha256':map_sha, 'resources':records, 'external_references':refs, 'grid':grid_info,
        'trm':catalog, 'tree_comparison':tree_comparison, 'tree_source':tree_source,
        'vhcg':{'status':'opaque_not_height_decoded','magic':contents['dota.vhcg'][:4].decode('ascii')},
        'world_physics_dependency_declared':any(r['resource']=='maps/dota/world_physics.vmdl' for r in refs),
        'physics_world_placement_verified':False, 'source_unchanged':True}
    output.mkdir(parents=True)
    for filename, body in contents.items(): (output/filename).write_bytes(body)
    save(output/'root-assets.json',result)
    (output/'COMPLETE').write_text(sha(output/'root-assets.json')+'\n',encoding='ascii')
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--game',type=Path,required=True)
    cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--tree-inventory',type=Path)
    args=cli.parse_args()
    r=run(args.game,args.output,args.tree_inventory)
    print({'resources':len(r['resources']),'grid':[r['grid']['width'],r['grid']['height']],
        'trm_records':r['trm']['record_count'],'tree_comparison':r['tree_comparison']})


if __name__=='__main__': main()
