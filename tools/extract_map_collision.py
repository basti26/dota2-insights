"""Decode one installed static collision resource twice, then validate geometry."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.history import checked_output
from dota2_insights.map_collision import extract, summarize, vertical_hits
from dota2_insights.replay_workbench import game_running
from dota2_insights.valve_resources import directory_bytes, read_vpk_directory, _read_resource

DECODER_SHA256 = 'a4bb4f9945db985efb2cad90a90d06867de5ba7e0447a2a4a69a07dace781028'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def guard():
    if game_running():
        raise InterruptedError('close_game_before_collision_extraction')


def decode(decoder, compiled, output, check=guard, block='PHYS'):
    if block not in {'PHYS', 'DATA', 'CTRL', 'RED2', 'MDAT'}:
        raise ValueError('unsupported_map_export_block')
    check()
    errors = output.with_suffix('.stderr')
    with output.open('xb', buffering=0) as stdout, errors.open('xb', buffering=0) as stderr:
        child = subprocess.Popen([str(decoder), '--input', str(compiled), '--block', block, '--threads', '1'],
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        started = time.monotonic()
        try:
            while child.poll() is None:
                check()
                if (time.monotonic() - started > 120 or output.stat().st_size > 64*1024**2
                        or errors.stat().st_size > 1024**2):
                    raise ValueError('collision_decoder_resource_limit')
                # Never inspect/seek the file handle shared with the child.
                time.sleep(.1)
            check()
            if child.returncode:
                raise ValueError('collision_decoder_failed')
        finally:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait(timeout=3)
    if output.stat().st_size > 64*1024**2 or errors.stat().st_size > 1024**2:
        raise ValueError('collision_decoder_output_limit')


def run(game, decoder, output):
    import numpy as np
    guard()
    game, decoder = Path(game).resolve(strict=True), Path(decoder).resolve(strict=True)
    output = checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_collision_output_required')
    if digest(decoder) != DECODER_SHA256:
        raise ValueError('unreviewed_collision_decoder')
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < 512*1024**2:
        raise ValueError('collision_low_disk_space')
    source = game / 'maps/dota.vpk'
    steam = (game / 'steam.inf').read_bytes()
    settings = dict(line.split('=',1) for line in steam.decode('utf-8-sig').splitlines() if '=' in line)
    raw = directory_bytes(source); entries, start = read_vpk_directory(raw)
    resource = 'maps/dota/world_physics.vmdl_c'
    body, _ = _read_resource(source, entries[resource], start, 16*1024**2)
    output.mkdir()
    compiled = output / 'world_physics.vmdl_c'; compiled.write_bytes(body)
    first, repeat = output/'PHYS.txt', output/'PHYS.repeat.txt'
    decode(decoder, compiled, first); decode(decoder, compiled, repeat)
    first_sha = digest(first)
    if digest(repeat) != first_sha:
        raise ValueError('collision_decoder_outputs_differ')
    meshes, attributes = extract(first.read_text(encoding='utf-8-sig'))
    report = summarize(meshes, attributes)
    arrays = {}
    for mesh in meshes:
        arrays[f'vertices_{mesh.ordinal}'] = mesh.vertices
        arrays[f'triangles_{mesh.ordinal}'] = mesh.triangles
    np.savez_compressed(output/'meshes.npz', **arrays)
    guard()
    report.update(client_build=int(settings['ClientVersion']), map_asset_sha256=hashlib.sha256(raw).hexdigest(),
        resource=resource, resource_sha256=hashlib.sha256(body).hexdigest(), decoder_sha256=DECODER_SHA256,
        source_export_sha256=first_sha, repeated_export_exact_bytes_equal=True,
        mesh_archive_sha256=digest(output/'meshes.npz'),
        source_unchanged=directory_bytes(source)==raw and (game/'steam.inf').read_bytes()==steam)
    if report['source_unchanged'] is not True:
        raise ValueError('installed_map_changed_during_collision_extraction')
    (output/'collision.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output/'COMPLETE').write_text(digest(output/'collision.json')+'\n', encoding='ascii')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game',type=Path,required=True)
    parser.add_argument('--decoder',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=run(args.game,args.decoder,args.output)
    print(json.dumps({key:report[key] for key in ('vertices','triangles','client_build','source_unchanged')},ensure_ascii=False))


if __name__=='__main__':
    main()
