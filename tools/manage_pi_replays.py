"""Pi replay library control; configuration lives in config/pi_replay_library.local.json."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dota2_insights.pi_replay_library import Bridge

cli=argparse.ArgumentParser(description=__doc__)
cli.add_argument('command',choices=('deploy','status','sync','work','collect'))
cli.add_argument('--limit',type=int,default=1)
args=cli.parse_args()
bridge=Bridge()
if args.command=='deploy': result=bridge.deploy()
elif args.command=='status': result=bridge.catalog()
elif args.command=='sync': result=bridge.sync(max(1,min(20,args.limit)))
elif args.command=='collect': result=bridge.execute(bridge.remote('collect','--count',max(1,min(3,args.limit))),timeout=1200)
else:
    result=[]
    for _ in range(max(1,min(20,args.limit))):
        row=bridge.work_one();result.append(row)
        print(json.dumps(row,ensure_ascii=True),flush=True)
        if row.get('analysis_status') in ('queue_empty','partial_or_failed'):break
print(json.dumps(result,ensure_ascii=True,indent=2))
