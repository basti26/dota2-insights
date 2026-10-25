"""Plan at most three existing Pi replays, launch once, poll or collect results."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.pi_context_expansion import plan,request,SEED


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('action',choices=('plan','launch','poll','collect','resume'))
    cli.add_argument('--plan',type=Path,required=True);cli.add_argument('--database',type=Path)
    cli.add_argument('--count',type=int,default=3);cli.add_argument('--seed',default=SEED)
    cli.add_argument('--game-build',type=int,help='Explicit already-resolved build; freezes the filtered population before parsing.')
    cli.add_argument('--match-id');cli.add_argument('--output',type=Path)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve()
    paths=[p for p in (args.plan,args.database,args.output) if p is not None]
    if any(not p.resolve().is_relative_to(root) or p.resolve()==root for p in paths):cli.error('Use diagnostics paths.')
    if args.action=='plan':
        if args.database is None:cli.error('--database required for plan')
        result=plan(args.database,args.plan,count=args.count,seed=args.seed,game_build=args.game_build)
        print(json.dumps({'status':'plan_frozen','selected':result['selected'],'candidate_count':result['candidate_count']}))
    else:print(json.dumps(request(args.plan,args.action,match_id=args.match_id,output=args.output),ensure_ascii=False))


if __name__=='__main__':main()
