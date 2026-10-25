"""Run retrospective region stability analysis on existing frozen matches."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.ward_region_stability import analyze,render
from dota2_insights.postgame_store import sha


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database',type=Path,required=True)
    cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--map',type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve()
    if any(not p.resolve().is_relative_to(root) for p in (args.database,args.output)) or args.output.resolve()==root:
        cli.error('Use existing diagnostics facts and a new diagnostics output directory.')
    names=('ward_region_stability.py','ward_patterns.py','postgame_store.py','postgame_event_types.py')
    code={name:(ROOT/'dota2_insights'/name).read_bytes() for name in names};entry=Path(__file__).read_bytes()
    result=analyze(args.database,args.output);render(result,args.output,args.map)
    if any((ROOT/'dota2_insights'/name).read_bytes()!=body for name,body in code.items()) or Path(__file__).read_bytes()!=entry:
        raise ValueError('ward_stability_code_changed')
    (args.output/'source-code').mkdir()
    for name,body in code.items():(args.output/'source-code'/name).write_bytes(body)
    (args.output/'source-code'/Path(__file__).name).write_bytes(entry)
    receipt={'status':'complete','schema_version':result['schema_version'],'database_sha256':result['database_sha256'],
        'frozen_models_sha256':result['frozen_models_sha256'],'cohort_count':len(result['cohorts']),
        'training_matches':sum(r['role']=='training' for r in result['split'].values()),
        'heldout_matches':sum(r['role']=='heldout' for r in result['split'].values()),
        'source_code_sha256':sha((ROOT/'dota2_insights/ward_region_stability.py').read_bytes()),
        'dependency_sha256':{name:sha(body) for name,body in code.items()},'entrypoint_sha256':sha(entry),
        'scope':'retrospective_internal_stability_not_pristine_holdout_or_recommendations'}
    (args.output/'run-summary.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
