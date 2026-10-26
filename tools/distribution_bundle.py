"""Publish/inspect offline delivery packages; never install them into Foresight."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from dota2_insights.distribution_bundle import publish,inspect
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('publish')
    p.add_argument('--kind',choices=('rules','statistics'),required=True)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p=sub.add_parser('inspect')
    p.add_argument('--package',type=Path,required=True)
    p.add_argument('--game-version')
    p.add_argument('--game-mode')
    p.add_argument('--rank')
    p.add_argument('--region')
    p.add_argument('--max-age',type=float,default=86400)
    p.add_argument('--rule-source-bindings',type=Path)
    args=parser.parse_args()
    if args.command=='publish':
        r=publish(args.kind,args.source,args.output)
        print(json.dumps({'package_id':r['package_id'],'output':str(args.output),
            'automatic_activation':False},ensure_ascii=False))
    else:
        scope={k:getattr(args,k) for k in ('game_mode','rank','region') if getattr(args,k) is not None}
        print(json.dumps(inspect(args.package,game_version=args.game_version,
            expected_scope=scope or None,max_age_s=args.max_age,
            rule_source_bindings=args.rule_source_bindings),ensure_ascii=False))


if __name__=='__main__':main()
