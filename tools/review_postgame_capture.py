"""Review an actual isolated context export, without launching a parser."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dota2_insights.postgame_capture_review import review


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--capture',type=Path,required=True)
    cli.add_argument('--baseline',type=Path,required=True)
    cli.add_argument('--output',type=Path,required=True)
    args=cli.parse_args();root=(ROOT/'diagnostics').resolve()
    if any(not path.resolve().is_relative_to(root) for path in (args.capture,args.baseline,args.output)) or args.output.resolve()==root:
        cli.error('Use diagnostics input files and a new diagnostics output directory.')
    print(json.dumps(review(args.capture,args.baseline,args.output),ensure_ascii=False))


if __name__=='__main__':main()
