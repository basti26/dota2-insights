"""Build time-distance sensitivity and pre-window counter coverage offline."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_context_balance import analyze, render
from dota2_insights.postgame_store import sha


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('database', 'opportunities', 'output'):
        cli.add_argument('--'+name, type=Path, required=True)
    args = cli.parse_args()
    root = (ROOT/'diagnostics').resolve()
    if any(not p.resolve().is_relative_to(root) or p.resolve()==root for p in (args.database,args.opportunities,args.output)):
        cli.error('Use diagnostics sources and a new diagnostics output directory.')
    names = ('ward_context_balance.py','ward_time_controls.py','postgame_store.py',
             'postgame_context_cohort.py','ward_objective_opportunities.py','ward_patterns.py')
    names=(*names,'postgame_metadata_store.py','replay_metadata_probe.py')
    code = {name:(ROOT/'dota2_insights'/name).read_bytes() for name in names}
    entry = Path(__file__).read_bytes()
    result = analyze(args.database,args.opportunities,args.output)
    render(result,args.output)
    if Path(__file__).read_bytes()!=entry or any((ROOT/'dota2_insights'/n).read_bytes()!=b for n,b in code.items()):
        raise ValueError('context_balance_code_changed')
    (args.output/'source-code').mkdir()
    for name,body in {**code,Path(__file__).name:entry}.items():
        (args.output/'source-code'/name).write_bytes(body)
    receipt = {'status':'complete','database_sha256':result['database_sha256'],
               'source_code_sha256':{n:sha(b) for n,b in code.items()},'entrypoint_sha256':sha(entry),
               'coverage':result['coverage']}
    (args.output/'run-summary.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps({'status':'complete','coverage':[{k:v for k,v in r.items() if k!='matched_target_group_refs'} for r in result['coverage']]}))


if __name__ == '__main__':
    main()
