"""Export local-only terrain data for Foresight; never shares analysis source code."""
import argparse
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.history import checked_input,checked_output
from dota2_insights.map_holdout import load,save,sha
from dota2_insights.map_updates import read_revision
from dota2_insights.map_prototype import read_complete


def run(registration,revision,landmarks,output):
    registration,revision,landmarks=[checked_input(ROOT,p) for p in (registration,revision,landmarks)]
    output=checked_output(ROOT,output)
    if output.exists():raise ValueError('new_terrain_export_required')
    report=read_complete(registration,'registration.json');snapshot=read_revision(revision)
    assets=read_complete(landmarks,'landmarks.json')
    if (report.get('kind')!='frozen_terrain_image_registration_batch' or report.get('production_ready') is not False
            or assets['map_asset_sha256']!=snapshot['identity']['map_asset_sha256']
            or assets['client_build']!=snapshot['identity']['client_build']):
        raise ValueError('terrain_export_context')
    sources={str(registration/'registration.json'):sha(registration/'registration.json'),
             str(revision/'snapshot.json'):sha(revision/'snapshot.json'),
             str(landmarks/'landmarks.json'):sha(landmarks/'landmarks.json'),str(Path(__file__)):sha(__file__)}
    for path,value in report['source_sha256'].items():
        if sha(path)!=value:raise ValueError('terrain_export_source_changed')
        sources[path]=value
    textures=[checked_input(ROOT,p) for p in sources if Path(p).name=='dota-map.png']
    if len(textures)!=1:raise ValueError('terrain_export_texture_required')
    texture=textures[0]
    sizes={tuple(f['source_size']) for f in report['frames']}
    if len(sizes)!=1 or sha(texture)!=report['texture_sha256']:raise ValueError('terrain_export_texture_context')
    bundle={'schema_version':1,'kind':'ward_terrain_trial_reference','map_identity':snapshot['identity'],
            'texture_context_build':report['texture_context_build'],'world_bounds':assets['declared_minimap_window'],
            'raw_offset_xy':[-16384,-16384],
            'texture':{'file':'terrain.png','sha256':sha(texture),'size':list(next(iter(sizes)))},
            'registration_evidence_sha256':sha(registration/'registration.json'),
            'validation_status':'experimental','production_ready':False,
            'limits':['Historical ward source version may be unknown.','Terrain alignment does not certify camera or ground height.']}
    if any(sha(path)!=value for path,value in sources.items()):raise ValueError('terrain_export_source_changed')
    output.mkdir(parents=True);shutil.copyfile(texture,output/'terrain.png')
    if sha(output/'terrain.png')!=bundle['texture']['sha256']:raise ValueError('terrain_export_copy_changed')
    save(output/'bundle.json',bundle)
    save(output/'receipt.json',{'source_sha256':sources,'files':{name:sha(output/name) for name in ('terrain.png','bundle.json')}})
    (output/'COMPLETE').write_text(sha(output/'receipt.json')+'\n',encoding='ascii')
    return bundle


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    for name in ('registration','revision','landmarks','output'):cli.add_argument('--'+name,type=Path,required=True)
    a=cli.parse_args();r=run(a.registration,a.revision,a.landmarks,a.output)
    print({'texture_size':r['texture']['size'],'production_ready':False})
