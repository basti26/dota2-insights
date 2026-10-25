"""Inspect recorded-objective overlap and symmetric placement opportunities."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.ward_objective_opportunities import analyze,render
from dota2_insights.postgame_store import sha


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database',type=Path,required=True);cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--map',type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve()
    if args.output.resolve()==root or any(not p.resolve().is_relative_to(root) for p in (args.database,args.output)):
        cli.error('Use existing diagnostics facts and a new diagnostics output directory.')
    names=('ward_objective_opportunities.py','ward_patterns.py','postgame_store.py','postgame_context_cohort.py','postgame_metadata_store.py','replay_metadata_probe.py')
    code={n:(ROOT/'dota2_insights'/n).read_bytes() for n in names};entry=Path(__file__).read_bytes()
    result=analyze(args.database,args.output)
    render(result,args.output,args.map)
    receipt={k:result[k] for k in ('schema_version','database_sha256','recorded_target_events','timestamp_groups','summary_eligible_groups','primary_summary')}
    if Path(__file__).read_bytes()!=entry or any((ROOT/'dota2_insights'/n).read_bytes()!=b for n,b in code.items()):raise ValueError('opportunity_dependency_changed')
    receipt.update(status='complete',source_code_sha256=sha(code['ward_objective_opportunities.py']),dependency_sha256={n:sha(b) for n,b in code.items()})
    (args.output/'source-code').mkdir()
    for name,body in {**code,Path(__file__).name:entry}.items():
        (args.output/'source-code'/name).write_bytes(body)
    (args.output/'run-summary.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8',newline='\n');print(json.dumps(receipt))


if __name__=='__main__':main()
