"""Compare already prepared continuity crops without changing recognition."""
import argparse
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.item_crop_stability import probe


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--evidence',type=Path,action='append',required=True)
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args();root=ROOT/'diagnostics'
    if any(p.resolve()==root or not p.resolve().is_relative_to(root) for p in [*args.evidence,args.output]):
        cli.error('use prepared diagnostics evidence and a new diagnostics output')
    r=probe(args.evidence,args.output)
    print(json.dumps({k:r[k] for k in ('package_count','unknown_role_crops','episode_count','accuracy_verified')}))


if __name__=='__main__':main()
