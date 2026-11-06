"""Sample fixed render blocks against a same-asset collision mesh, entirely offline."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dota2_insights.history import checked_input,checked_output
from dota2_insights.map_render_geometry import world_node,terrain_instances,select_instances,source_positions,SharedVertices
from dota2_insights.valve_resources import directory_bytes,read_vpk_directory,_read_resource
from tools.extract_map_collision import guard,decode,digest,DECODER_SHA256


def export(decoder,compiled,output):
    output.mkdir()
    with (output/'stdout.txt').open('xb',buffering=0) as stdout,(output/'stderr.txt').open('xb',buffering=0) as stderr:
        child=subprocess.Popen([str(decoder),'--input',str(compiled),'--output',str(output/'mesh'),
            '-d','--gltf_export_format','gltf','--threads','1'],stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        start=time.monotonic()
        try:
            while child.poll() is None:
                guard()
                if time.monotonic()-start>30 or sum(p.stat().st_size for p in output.rglob('*') if p.is_file())>32*1024**2:
                    raise ValueError('render_export_budget')
                time.sleep(.1)
            guard()
            if child.returncode:raise ValueError('render_export_failed')
        finally:
            if child.poll() is None:
                child.terminate()
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
    document=json.loads((output/'mesh.gltf').read_text(encoding='utf-8'))
    if document.get('buffers')!=[{'byteLength':(output/'mesh.bin').stat().st_size,'uri':'mesh.bin'}]:
        raise ValueError('render_export_single_local_buffer_required')
    return document,(output/'mesh.bin').read_bytes()


def run(game,decoder,collision,output):
    guard();game=Path(game).resolve(strict=True);decoder=Path(decoder).resolve(strict=True)
    collision=checked_input(ROOT,collision);output=checked_output(ROOT,output)
    if output.exists():raise ValueError('new_render_geometry_output_required')
    if digest(decoder)!=DECODER_SHA256:raise ValueError('unreviewed_render_decoder')
    report_sha=digest(collision)
    if (collision.parent/'COMPLETE').read_text(encoding='ascii').strip()!=report_sha:
        raise ValueError('render_collision_receipt')
    collision_data=json.loads(collision.read_text(encoding='utf-8'))
    archive=collision.parent/'meshes.npz';archive_sha=digest(archive)
    if archive_sha!=collision_data['mesh_archive_sha256']:raise ValueError('render_collision_archive')
    source=game/'maps/dota.vpk';raw=directory_bytes(source);entries,start=read_vpk_directory(raw)
    if hashlib.sha256(raw).hexdigest()!=collision_data['map_asset_sha256']:raise ValueError('render_map_identity_mismatch')
    with np.load(archive,allow_pickle=False) as meshes:
        if collision_data['meshes'][0]['collision_attribute']['collision_group']!='default':
            raise ValueError('render_default_collision_attribute_required')
        shared=SharedVertices(meshes['vertices_0'])
    output.mkdir(parents=True);assets=[]
    def resource(name,path):
        body,_=_read_resource(source,entries[name],start,4*1024**2);path.write_bytes(body)
        assets.append({'resource':name,'file':path.relative_to(output).as_posix(),'sha256':digest(path),'bytes':len(body)})
        return path
    world=resource('maps/dota/world.vwrld_c',output/'world.vwrld_c')
    node=resource('maps/dota/worldnodes/n0.vwnod_c',output/'n0.vwnod_c')
    for path in (world,node):
        first=path.with_suffix('.DATA.txt');repeat=path.with_suffix('.DATA.repeat.txt')
        decode(decoder,path,first,block='DATA');decode(decoder,path,repeat,block='DATA')
        if digest(first)!=digest(repeat):raise ValueError('render_world_decode_not_repeatable')
    placement=world_node(world.with_suffix('.DATA.txt').read_text(encoding='utf-8-sig'))
    instances=terrain_instances(node.with_suffix('.DATA.txt').read_text(encoding='utf-8-sig'),placement['prefix'])
    selected=select_instances(instances)
    (output/'selection.json').write_text(json.dumps({'policy':'nine_evenly_spaced_numeric_cell_ordinals_before_comparison',
        'available':len(instances),'selected':selected},indent=2),encoding='utf-8')
    rows=[]
    for instance in selected:
        guard();folder=output/f"cell-{instance['cell']:03}";folder.mkdir()
        compiled=resource(instance['resource'],folder/'render.vmdl_c')
        decode(decoder,compiled,folder/'MDAT.txt',block='MDAT')
        decode(decoder,compiled,folder/'MDAT.repeat.txt',block='MDAT')
        if digest(folder/'MDAT.txt')!=digest(folder/'MDAT.repeat.txt'):raise ValueError('render_mesh_decode_not_repeatable')
        document,buffer=export(decoder,compiled,folder/'first')
        export(decoder,compiled,folder/'repeat')
        for name in ('mesh.gltf','mesh.bin'):
            if digest(folder/'first'/name)!=digest(folder/'repeat'/name):raise ValueError('render_gltf_not_repeatable')
        vertices,bounds=source_positions(document,buffer,(folder/'MDAT.txt').read_text(encoding='utf-8-sig'))
        rows.append({**instance,**bounds,**shared.compare(vertices)})
    guard()
    if directory_bytes(source)!=raw or digest(collision)!=report_sha or digest(archive)!=archive_sha:
        raise ValueError('render_geometry_source_changed')
    files={p.relative_to(output).as_posix():digest(p) for p in output.rglob('*') if p.is_file()}
    result={'schema_version':1,'kind':'render_collision_frame_comparison','map_asset_sha256':hashlib.sha256(raw).hexdigest(),
        'world_node':placement,'available_terrain_instances':len(instances),'rows':rows,'source_files':files,
        'source_assets':assets,'decoder_sha256':DECODER_SHA256,'collision_report_sha256':report_sha,
        'collision_archive_sha256':archive_sha,'source_unchanged':True,'repeated_export_bytes_equal':True,
        'code_sha256':{name:digest(ROOT/name) for name in ['dota2_insights/map_render_geometry.py','tools/compare_map_render_geometry.py','tools/extract_map_collision.py']},
        'meaning':'same_asset_static_vertex_frame_evidence_not_independent_runtime_or_height_validation',
        'runtime_world_placement_verified':False,'walkable_ground_verified':False,'screen_projection_verified':False}
    (output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'COMPLETE').write_text(digest(output/'comparison.json')+'\n',encoding='ascii')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('game','decoder','collision','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();r=run(args.game,args.decoder,args.collision,args.output)
    print(json.dumps({'blocks':len(r['rows']),'distinct_per_block':sum(v['distinct_render_vertices'] for v in r['rows']),
        'shared_per_block':sum(v['shared_within_0_01_world_units'] for v in r['rows']),
        'source_unchanged':r['source_unchanged']}))
