"""Export diverse clips from completed blind predictions; no inference or GSI labels."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('run', 'analysis', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--handoff-package',type=Path)
    source.add_argument('--audio-match-package',type=Path)
    parser.add_argument('--expected-package-id')
    args = parser.parse_args()
    from dota2_insights.history import checked_input, checked_output, history_roots
    from dota2_insights.closed_journals import read_package
    from dota2_insights.audio_candidate_review import prepare,prepare_match
    from dota2_insights.audio_listening_review import build_listening_page
    output = checked_output(ROOT, args.output)
    folder,analysis=checked_input(ROOT,args.run),checked_input(ROOT,args.analysis)
    if args.audio_match_package is not None:
        scope=checked_input(ROOT,args.audio_match_package)
        if scope.parent.parent!=folder:
            raise ValueError('audio_candidate_capture_folder_conflict')
        result=prepare_match(scope,analysis,output,package_id=args.expected_package_id)
    else:
        if args.expected_package_id is not None:
            raise ValueError('native_expected_package_id_requires_native_audio_scope')
        package = checked_input(ROOT, args.handoff_package)
        result = prepare(read_package(package), history_roots(ROOT/'config/history.local.json'),
                         folder,analysis,output,package_id=package.stem)
    page = None
    if result['clips']:
        page, _ = build_listening_page(output/'review.json', output/'listening-page')
    print(json.dumps({'output': str(output), 'clips': len(result['clips']), 'unavailable': len(result['unavailable']),
                      'counts': result['counts'], 'page': str(page/'index.html') if page else None}))


if __name__ == '__main__':
    main()
