"""Build four-window cell maps from already frozen time controls."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.ward_control_regions import analyze,render
from dota2_insights.postgame_store import sha


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    for name in ('database','balance','map','output'):
        cli.add_argument('--'+name,type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve()
    if any(not p.resolve().is_relative_to(root) or p.resolve()==root for p in (args.database,args.balance,args.output)):
        cli.error('Use diagnostics sources and a new diagnostics output directory.')
    names=('ward_control_regions.py','ward_context_balance.py','ward_time_controls.py','postgame_store.py',
           'postgame_context_cohort.py','ward_patterns.py','ward_objective_opportunities.py')
    names=(*names,'postgame_metadata_store.py','replay_metadata_probe.py')
    code={n:(ROOT/'dota2_insights'/n).read_bytes() for n in names};entry=Path(__file__).read_bytes()
    result=analyze(args.database,args.balance,args.output)
    report=args.balance.resolve().parent/'report.html'
    href=Path(os.path.relpath(report,args.output.resolve())).as_posix() if report.is_file() else None
    render(result,args.output,args.map,balance_report_href=href)
    if Path(__file__).read_bytes()!=entry or any((ROOT/'dota2_insights'/n).read_bytes()!=b for n,b in code.items()):
        raise ValueError('control_regions_source_code_changed')
    (args.output/'source-code').mkdir()
    for name,body in {**code,Path(__file__).name:entry}.items():(args.output/'source-code'/name).write_bytes(body)
    receipt={'status':'complete','database_sha256':result['database_sha256'],
             'parameter_pairs':result['matched_parameter_pairs'],'slices':len(result['records']),
             'strata':len(result['summaries']),'source_code_sha256':{n:sha(b) for n,b in code.items()},'entrypoint_sha256':sha(entry)}
    (args.output/'run-summary.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
