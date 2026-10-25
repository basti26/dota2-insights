"""Read bounded replay metadata after verifying the exact frozen source SHA."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.replay-deps'));sys.path.insert(0,str(ROOT))
from dota2_insights.replay_metadata_probe import inspect_source


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--source',type=Path,required=True);cli.add_argument('--match-id',required=True)
    cli.add_argument('--expected-sha256',required=True);cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve();out=args.output.resolve()
    if not out.is_relative_to(root) or out.exists():cli.error('Use a new diagnostics output file.')
    code=(ROOT/'dota2_insights/replay_metadata_probe.py').read_bytes()
    result=inspect_source(args.source,args.match_id,args.expected_sha256)
    if (ROOT/'dota2_insights/replay_metadata_probe.py').read_bytes()!=code:raise ValueError('metadata_probe_code_changed')
    result['probe_source_sha256']=hashlib.sha256(code).hexdigest()
    out.write_text(json.dumps(result,indent=2),encoding='utf-8',newline='\n')
    print(json.dumps({k:result.get(k) for k in ('status','match_id','game_build','game_mode','elapsed_s','error_code')}))


if __name__=='__main__':main()
