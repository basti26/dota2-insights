"""Build descriptive event/ward windows from existing frozen facts only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.ward_event_windows import analyze, render


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--database',type=Path,required=True)
    cli.add_argument('--map',type=Path,required=True)
    cli.add_argument('--output',type=Path,required=True)
    cli.add_argument('--legacy-archive',type=Path,action='append',default=[],help='Completed same-DEM archives; no API/legacy mixing within one match.')
    cli.add_argument('--captured-context',type=Path,action='append',default=[],help='Complete frozen capture; baseline ward bytes and clocks must match.')
    args = cli.parse_args()
    root = (ROOT/'diagnostics').resolve()
    source, output = args.database.resolve(), args.output.resolve()
    if not source.is_relative_to(root) or not output.is_relative_to(root) or output == root or output.exists() or any(not p.resolve().is_relative_to(root) for p in (*args.legacy_archive,*args.captured_context)):
        cli.error('Use an existing diagnostics database and a new diagnostics output directory.')
    code_names=('ward_event_windows.py','postgame_legacy_context.py','replay_postgame_context.py','postgame_replay_context.py','postgame_event_types.py')
    code_bytes={name:(ROOT/'dota2_insights'/name).read_bytes() for name in code_names}
    result = analyze(source,output,args.legacy_archive,args.captured_context)
    render(result,output,args.map)
    if any((ROOT/'dota2_insights'/name).read_bytes()!=body for name,body in code_bytes.items()):
        raise ValueError('postgame_analysis_code_changed_during_run')
    (output/'source-code').mkdir()
    for name,body in code_bytes.items():(output/'source-code'/name).write_bytes(body)
    receipt = {k: result[k] for k in ('schema_version','database_sha256','selected_event_count',
        'distinct_matches_with_selected_events','event_family_counts','event_exclusions','ward_exclusions')}
    receipt.update(status='complete',source_code_sha256=hashlib.sha256(code_bytes['ward_event_windows.py']).hexdigest())
    receipt['dependency_source_sha256']={name:hashlib.sha256(code_bytes[name]).hexdigest()
        for name in ('postgame_legacy_context.py','replay_postgame_context.py','postgame_replay_context.py','postgame_event_types.py')}
    (output/'run-summary.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps(receipt,ensure_ascii=False))


if __name__ == '__main__':
    main()
