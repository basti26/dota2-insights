"""Keep bounded metadata evidence separately and explicitly select receipts."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.postgame_metadata_store import build,inventory,render
from dota2_insights.postgame_store import sha


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database',type=Path,required=True);cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--probe',type=Path,action='append',default=[])
    cli.add_argument('--append',action='store_true',help='Append to an existing metadata extension, preserving all old receipts and selections.')
    cli.add_argument('--select',action='append',default=[])
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve();selection={}
    if any(not p.resolve().is_relative_to(root) or p.resolve()==root for p in (args.database,args.output,*args.probe)):
        cli.error('Use diagnostics sources and a new diagnostics directory.')
    for value in args.select:
        mid,sep,sid=value.partition('=')
        if not sep or not mid.isdigit() or len(sid)!=64 or mid in selection:cli.error('Use unique MATCH_ID=RECEIPT_SHA256 selections.')
        selection[mid]=sid
    names=('postgame_metadata_store.py','postgame_store.py','replay_metadata_probe.py')
    code={n:(ROOT/'dota2_insights'/n).read_bytes() for n in names};entry=Path(__file__).read_bytes()
    result=build(args.database,args.output,args.probe,selection,append=args.append)
    render(inventory(args.output/'facts.sqlite'),args.output)
    if Path(__file__).read_bytes()!=entry or any((ROOT/'dota2_insights'/n).read_bytes()!=b for n,b in code.items()):
        raise ValueError('metadata_store_code_changed')
    (args.output/'source-code').mkdir()
    for n,b in {**code,Path(__file__).name:entry}.items():(args.output/'source-code'/n).write_bytes(b)
    (args.output/'code-receipt.json').write_text(json.dumps({n:sha(b) for n,b in code.items()},indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
