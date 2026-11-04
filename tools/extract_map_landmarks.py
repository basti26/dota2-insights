"""Extract game-declared map windows and non-tower origins; never certify UI."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dota2_insights.history import checked_output
from dota2_insights.map_landmarks import extract
from dota2_insights.valve_resources import directory_bytes,read_vpk_directory,_read_resource
from dota2_insights.virtual_map import MapIdentity,WorldBounds,VirtualMap
from tools.extract_map_collision import digest,decode,guard,DECODER_SHA256


def run(game,decoder,output):
    guard();game=Path(game).resolve(strict=True);decoder=Path(decoder).resolve(strict=True)
    output=checked_output(ROOT,output)
    if output.exists():raise ValueError('new_landmark_output_required')
    if digest(decoder)!=DECODER_SHA256:raise ValueError('unreviewed_map_decoder')
    source=game/'maps/dota.vpk';raw=directory_bytes(source)
    entries,start=read_vpk_directory(raw);resource='maps/dota/entities/default_ents.vents_c'
    body,_=_read_resource(source,entries[resource],start,16*1024**2)
    steam=(game/'steam.inf').read_bytes()
    fields=dict(line.split('=',1) for line in steam.decode('utf-8-sig').splitlines() if '=' in line)
    output.mkdir(parents=True)
    compiled=output/'default_ents.vents_c';compiled.write_bytes(body)
    first,repeat=output/'DATA.txt',output/'DATA.repeat.txt'
    decode(decoder,compiled,first,block='DATA');decode(decoder,compiled,repeat,block='DATA')
    if digest(first)!=digest(repeat):raise ValueError('entity_exports_differ')
    result=extract(first.read_text(encoding='utf-8-sig'))
    sha=hashlib.sha256(raw).hexdigest();build=int(fields['ClientVersion'])
    result.update(map_asset_sha256=sha,client_build=build,source_resource=resource,
        resource_sha256=hashlib.sha256(body).hexdigest(),source_export_sha256=digest(first),decoder_sha256=DECODER_SHA256)
    model=VirtualMap(MapIdentity('dota',build,sha),WorldBounds(**result['declared_minimap_window']),
        'source2_map_centered','asset_extracted','Two dota_minimap_boundary entity origins; '+result['resource_sha256'])
    if directory_bytes(source)!=raw or (game/'steam.inf').read_bytes()!=steam:raise ValueError('map_changed_during_landmark_extraction')
    guard()
    (output/'landmarks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'asset-window-candidate.json').write_text(json.dumps(model.to_dict(),ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'COMPLETE').write_text(digest(output/'landmarks.json')+'\n',encoding='ascii')
    return result


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--game',type=Path,required=True);cli.add_argument('--decoder',type=Path,required=True)
    cli.add_argument('--output',type=Path,required=True);args=cli.parse_args()
    result=run(args.game,args.decoder,args.output)
    print(json.dumps({key:result[key] for key in ('landmark_count','declared_minimap_window','client_build')}))


if __name__=='__main__':main()
