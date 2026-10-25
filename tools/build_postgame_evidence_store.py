"""Keep source versions, select one primary bundle, and build new frozen facts."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.postgame_evidence_store import build,inventory,render_inventory
from dota2_insights.postgame_store import sha


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database',type=Path,required=True);cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--legacy-archive',type=Path,action='append',default=[])
    cli.add_argument('--context-database',type=Path,action='append',default=[])
    cli.add_argument('--select',action='append',default=[],metavar='MATCH_ID=SOURCE_SHA256')
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve();selection={}
    if args.output.resolve()==root or any(not p.resolve().is_relative_to(root) for p in (args.database,args.output,*args.legacy_archive,*args.context_database)):
        cli.error('Use diagnostics inputs and a new diagnostics output.')
    for choice in args.select:
        mid,sep,sid=choice.partition('=')
        if not sep or not mid.isdigit() or len(sid)!=64 or any(c not in '0123456789abcdef' for c in sid) or mid in selection:
            cli.error('Each --select must be a unique MATCH_ID=SOURCE_SHA256.')
        selection[mid]=sid
    names=('postgame_evidence_store.py','postgame_store.py','postgame_legacy_context.py','postgame_event_types.py','replay_postgame_context.py')
    code={n:(ROOT/'dota2_insights'/n).read_bytes() for n in names};entry=Path(__file__).read_bytes()
    result=build(args.database,args.output,legacy_archives=args.legacy_archive,context_databases=args.context_database,selection=selection)
    report=inventory(args.output/'facts.sqlite')
    render_inventory(report,args.output)
    if Path(__file__).read_bytes()!=entry or any((ROOT/'dota2_insights'/n).read_bytes()!=body for n,body in code.items()):
        raise ValueError('evidence_build_code_changed')
    (args.output/'source-code').mkdir()
    for n,body in {**code,Path(__file__).name:entry}.items():(args.output/'source-code'/n).write_bytes(body)
    (args.output/'code-receipt.json').write_text(json.dumps({n:sha(b) for n,b in code.items()},indent=2),encoding='utf-8',newline='\n')
    print(json.dumps({k:result[k] for k in ('status','counts','additions','integrity_check')},ensure_ascii=False))


if __name__=='__main__':main()
