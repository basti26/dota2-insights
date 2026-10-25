"""Compare saved DEM/API ward records without using time to select pairs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.ward_clock_alignment import analyze, render


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database',type=Path,required=True)
    cli.add_argument('--heroes',type=Path,required=True)
    cli.add_argument('--heroes-sha256',required=True)
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve();output=args.output.resolve()
    if output.exists() or output==root or any(not p.resolve().is_relative_to(root) for p in (args.database,args.heroes,output)):
        cli.error('Use diagnostics sources and a new diagnostics output directory.')
    result=analyze(args.database,args.heroes,args.heroes_sha256,output)
    render(result,output)
    details=[r for r in result['matches'] if r['status']=='analyzed_ward_placements_only']
    summary={'schema_version':result['schema_version'],'status':'complete',
        'database_sha256':result['database_sha256'],'matches_analyzed':len(details),
        'matches_without_public_records':len(result['matches'])-len(details),
        'details':[{k:r[k] for k in ('match_id','candidate_transform','hero_pid_mismatches',
            'unique_pair_time_difference_s','conditional_zero_offset_compatible')}|
            {'unique_pairs':len(r['unique_identity_spatial_pairs']),
             'ambiguous_edges':len(r['ambiguous_coordinate_candidate_edges']),'unmatched':len(r['unmatched'])}
            for r in details],
        'source_code_sha256':hashlib.sha256((ROOT/'dota2_insights/ward_clock_alignment.py').read_bytes()).hexdigest()}
    (output/'run-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__':main()
