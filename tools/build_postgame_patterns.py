"""Build offline postgame facts and descriptive ward patterns from frozen exports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.postgame_store import create_store, sha
from dota2_insights.ward_patterns import describe, render


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--snapshot',type=Path,required=True)
    cli.add_argument('--context',type=Path,action='append',default=[])
    cli.add_argument('--map',type=Path,required=True)
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args()
    root=(ROOT/'diagnostics').resolve();output=args.output.resolve()
    if (output.exists() or output==root or not output.is_relative_to(root)
            or any(not p.resolve().is_relative_to(root) for p in [args.snapshot,*args.context])):
        cli.error('Use explicit diagnostics sources and a new diagnostics output directory.')
    output.mkdir(parents=True)
    receipt={'schema_version':1,'kind':'postgame_pattern_pipeline','status':'running',
             'source_code_sha256':{name:sha((ROOT/'dota2_insights'/name).read_bytes())
                                   for name in ('postgame_store.py','ward_patterns.py','postgame_event_types.py')}}
    def persist():
        (output/'run-status.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    persist()
    try:
        imported=create_store(args.snapshot,output/'facts',args.context)
        result=describe(output/'facts/facts.sqlite',output/'patterns')
        render(result,output/'patterns',args.map)
        receipt.update(status='complete',facts=imported,
            summary={k:result[k] for k in ('matches','ward_records','full_interval_placements',
                'partial_interval_placements_preserved_not_in_frequency','observer_enemy_sentry_proximity_count',
                'proximity_truncated','time_only_context_pair_count')},
            candidate_cells=sum(r['grid_offset']==0 and r['candidate_eligible'] for r in result['patterns']),
            both_team_cells=len(result['both_team_cells']),paired_time_changes=len(result['paired_time_changes']))
        persist()
    except Exception as exc:
        receipt.update(status='failed',error_type=type(exc).__name__)
        persist()
        raise
    print(json.dumps({k:receipt[k] for k in ('status','summary','candidate_cells','both_team_cells','paired_time_changes')},ensure_ascii=False))


if __name__=='__main__':
    main()
