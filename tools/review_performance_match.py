"""Review one stopped, sealed per-match capture; no ETW or game startup."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dota2_insights.performance_handoff import review
    from dota2_insights.performance_review import render_markdown
    from dota2_insights.history import checked_output
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--expected-package-id')
    args = parser.parse_args()
    report = review(args.folder, args.expected_package_id)
    report['launcher_run_id'] = 'native_capture_'+report['capture_run_id']
    output = checked_output(ROOT, ROOT/'diagnostics/performance-match-review'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output/'REPORT.md').write_text(render_markdown([report]), encoding='utf-8')
    print(json.dumps({'output': str(output), 'matches': len(report['matches']),
                      'source_verified_before_and_after': True}, ensure_ascii=False))


if __name__ == '__main__':
    main()
