"""Check all local declared rule sources, exporting only symbolic IDs and hashes."""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from dota2_insights.rule_source_audit import audit
    from dota2_insights.history import checked_output
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rules',type=Path,required=True)
    parser.add_argument('--bindings',type=Path,required=True)
    args=parser.parse_args()
    report=audit(args.rules,args.bindings)
    output=checked_output(ROOT,ROOT/'diagnostics/rule-source-audit'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True,exist_ok=False)
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'report':str(output/'report.json'),'counts':report['counts'],
        'all_sources_byte_bound':report['all_sources_byte_bound'],'activation_allowed':False},ensure_ascii=False))


if __name__=='__main__':main()
