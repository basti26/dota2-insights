"""Review one writer-closed audio scope while other game recording may continue."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dota2_insights.audio_match_handoff import review
    from dota2_insights.history import checked_output
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path)
    parser.add_argument('--expected-package-id')
    args = parser.parse_args()
    report = review(args.folder, expected_package_id=args.expected_package_id)
    output = checked_output(ROOT, ROOT/'diagnostics/audio-match-review'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'output': str(output), **{k:v for k,v in report.items() if k!='waveform_sha256_by_file'}}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
