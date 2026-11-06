"""Offline explicit-anchor calibration; does not install or enable projection."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.input.is_symlink() or args.input.is_junction() or not args.input.is_file():
        raise ValueError('calibration_regular_input_required')
    with args.input.open('rb') as stream:raw=stream.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('calibration_input_limit')
    data=json.loads(raw)
    if data.get('schema_version')!=1 or data.get('kind')!='explicit_native_camera_anchor_input':
        raise ValueError('calibration_input_version')
    from dota2_insights.native_camera_calibration import fit
    result=fit(data.get('native_position'),data.get('anchors'),data.get('native_viewport'),
               region_check=data.get('region_check'))
    with args.input.open('rb') as stream:
        if stream.read(1024*1024+1)!=raw:raise ValueError('calibration_input_changed')
    result.update(input_sha256=hashlib.sha256(raw).hexdigest(),
                  installed=False,live_projection_enabled=False)
    with args.output.open('x',encoding='utf-8') as stream:
        json.dump(result,stream,ensure_ascii=False,allow_nan=False,indent=2)
    print(json.dumps({'status':result['status'],'reason':result['reason'],'live_projection_enabled':False}))


if __name__=='__main__':main()
