"""Build a source-verified per-match recording readiness report; no capture or network."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    from dota2_insights.history import history_roots, checked_output
    from dota2_insights.record_readiness import build_report, render_markdown
    report = build_report(ROOT / 'diagnostics/helper-handoff/index.json',
                          history_roots(ROOT / 'config/history.local.json'))
    output = checked_output(ROOT, ROOT / 'diagnostics/record-readiness' /
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2,
                                                  allow_nan=False), encoding='utf-8')
    (output / 'REPORT.md').write_text(render_markdown(report), encoding='utf-8')
    print(json.dumps({'report': str(output / 'REPORT.md'), 'run_states': report['run_states'],
        'match_entries': sum(len(r['matches']) for r in report['runs'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
