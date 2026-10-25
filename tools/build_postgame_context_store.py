"""Build a new immutable context-aware facts snapshot from existing data."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.postgame_context_store import build


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database',type=Path,required=True)
    cli.add_argument('--api-context',type=Path,action='append',default=[])
    cli.add_argument('--legacy-archive',type=Path,action='append',default=[])
    cli.add_argument('--captured-context',type=Path,action='append',default=[])
    cli.add_argument('--reconcile-paused-metric-clocks',action='store_true',help='Explicit final-clock projection only for metric packets inside recorded pauses; original bytes are retained.')
    cli.add_argument('--retain-missing-event-times',action='store_true',help='Keep original null event packet times as unknown; never fill them from final or event clocks.')
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve()
    inputs=[args.database,*args.api_context,*args.legacy_archive,*args.captured_context,args.output]
    if args.output.resolve()==root or any(not p.resolve().is_relative_to(root) for p in inputs):
        cli.error('All sources and the new output must be inside diagnostics.')
    names=('postgame_context_store.py','postgame_store.py','postgame_legacy_context.py','postgame_replay_context.py',
           'replay_postgame_context.py','ward_event_windows.py','postgame_event_types.py','postgame_capture_review.py')
    code={n:(ROOT/'dota2_insights'/n).read_bytes() for n in names}
    result=build(args.database,args.output,api_contexts=args.api_context,legacy_archives=args.legacy_archive,captured_contexts=args.captured_context,
        captured_metric_clock_policy='reconcile_recorded_pauses' if args.reconcile_paused_metric_clocks else 'strict',
        captured_event_clock_policy='retain_missing' if args.retain_missing_event_times else 'strict')
    if any((ROOT/'dota2_insights'/n).read_bytes()!=body for n,body in code.items()):raise ValueError('context_store_code_changed')
    (args.output/'source-code').mkdir()
    for n,body in code.items():(args.output/'source-code'/n).write_bytes(body)
    (args.output/'code-receipt.json').write_text(json.dumps({n:hashlib.sha256(body).hexdigest() for n,body in code.items()},indent=2),encoding='utf-8',newline='\n')
    print(json.dumps({k:result[k] for k in ('status','counts','packet_phases','integrity_check')},ensure_ascii=False))


if __name__=='__main__':main()
