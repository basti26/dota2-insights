"""One explicit command for bounded collection, merge and recoverable reports."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.professional_review_pipeline import Pipeline, WaitingForStage
from dota2_insights.professional_review_pipeline import digest
from dota2_insights.ward_map_reference import MAP_SHA256


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--batch',type=Path,action='append',default=[])
    cli.add_argument('--previous',type=Path)
    cli.add_argument('--map',type=Path)
    cli.add_argument('--collect-pi',action='store_true')
    cli.add_argument('--limit',type=int,default=8)
    cli.add_argument('--reconcile-paused-metric-clocks',action='store_true')
    cli.add_argument('--retain-missing-event-times',action='store_true')
    cli.add_argument('--select',action='append',default=[])
    cli.add_argument('--resume',action='store_true')
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args();diagnostics=ROOT/'diagnostics'
    for path in [args.output,*args.batch]+([args.previous] if args.previous else []):
        if path.resolve()==diagnostics or not path.resolve().is_relative_to(diagnostics):cli.error('use diagnostics inputs/output')
    if args.resume:
        if args.batch or args.previous or args.map or args.collect_pi or args.select or args.reconcile_paused_metric_clocks or args.retain_missing_event_times:
            cli.error('resume uses the original configuration; specify only --resume and --output')
        config=None
    else:
        if not args.map or not (args.batch or args.previous or args.collect_pi):cli.error('map and at least one batch/previous/collect source required')
        if digest(args.map,12*1024**2)!=MAP_SHA256:cli.error('use the pinned illustrative map')
        if not 1<=args.limit<=16:cli.error('limit must be between 1 and 16')
        selection={}
        for value in args.select:
            mid,sep,source=value.partition('=')
            if not sep or mid in selection:cli.error('use unique MATCH_ID=SOURCE_ID selections')
            selection[mid]=source
        config={'batches':[str(p.resolve()) for p in args.batch],
                'previous':str(args.previous.resolve()) if args.previous else None,'map':str(args.map.resolve()),
                'collect_pi':args.collect_pi,'limit':args.limit,'selection':selection,
                'reconcile_pauses':args.reconcile_paused_metric_clocks,'retain_missing_event_times':args.retain_missing_event_times}
    pipeline=Pipeline(args.output,config,resume=args.resume)
    try:result=pipeline.run()
    except WaitingForStage:
        print(json.dumps({'status':'waiting_for_stage','stage':pipeline.state['waiting_stage']}));return
    print(json.dumps({'status':result['status'],'summary':result.get('summary'),
                      'index':str(args.output/'index.html') if result['status'].startswith('complete') else None},ensure_ascii=False))


if __name__=='__main__':main()
