"""Inspect recorded ward spans without inventing ends or vision coverage."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.ward_interval_context import analyze,render
from dota2_insights.postgame_store import sha


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    for name in ('database','regions','map','output'):
        cli.add_argument('--'+name,type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve()
    if any(not p.resolve().is_relative_to(root) or p.resolve()==root for p in (args.database,args.regions,args.output)):
        cli.error('Use diagnostics sources and a new diagnostics output directory.')
    names=('ward_interval_context.py','postgame_store.py','ward_control_regions.py','ward_patterns.py',
           'ward_context_balance.py','ward_time_controls.py','postgame_context_cohort.py','ward_objective_opportunities.py')
    names=(*names,'postgame_metadata_store.py','replay_metadata_probe.py')
    code={n:(ROOT/'dota2_insights'/n).read_bytes() for n in names};entry=Path(__file__).read_bytes()
    result=analyze(args.database,args.regions,args.output);render(result,args.output,args.map)
    if Path(__file__).read_bytes()!=entry or any((ROOT/'dota2_insights'/n).read_bytes()!=b for n,b in code.items()):
        raise ValueError('ward_intervals_code_changed')
    (args.output/'source-code').mkdir()
    for name,body in {**code,Path(__file__).name:entry}.items():(args.output/'source-code'/name).write_bytes(body)
    receipt={'status':'complete','database_sha256':result['database_sha256'],'source_ward_categories':result['source_ward_counts'],
             'pair_slices':len(result['records']),'source_code_sha256':{n:sha(b) for n,b in code.items()},'entrypoint_sha256':sha(entry)}
    (args.output/'run-summary.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
