"""Export all placements from one complete ward pass; no runtime camera inference."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    from dota2_insights.history import checked_output
    from dota2_insights.ward_reference_export import collect,build
    cli=argparse.ArgumentParser(description=__doc__)
    source=cli.add_mutually_exclusive_group(required=True)
    source.add_argument('--pi-match')
    source.add_argument('--folder',type=Path,help='Already collected manifest.json and wards.json')
    cli.add_argument('--tree-reviews',type=Path)
    args=cli.parse_args()
    out=checked_output(ROOT,ROOT/'diagnostics/ward-overlay-export'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    if args.pi_match:
        from dota2_insights.pi_replay_library import Bridge
        collect(Bridge(),args.pi_match,out)
        folder=out
    else:
        folder=args.folder
        out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    # Public identity is checked before the source ward payload is opened.
    from dota2_insights.replay_holdout import refuse_locked_match
    refuse_locked_match(manifest.get('match_id'))
    if (manifest.get('complete') is not True or manifest.get('parse_error') is not None
            or manifest.get('independent_footer_matches_parser') is not True
            or manifest.get('parsed_match_id') != manifest.get('match_id')):
        raise ValueError('ward_reference_complete_identity_required')
    raw=(folder/'wards.json').read_bytes()
    reviews=json.loads(args.tree_reviews.read_text(encoding='utf-8')) if args.tree_reviews else None
    packet=build(manifest,raw,tree_reviews=reviews)
    (out/'ward-reference.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({'output':str(out),'points':len(packet['points']),'unavailable':len(packet['unavailable']),
                     'tree_unknown':sum(p['tree_clearance']=='unknown' for p in packet['points'])},ensure_ascii=False))


if __name__=='__main__':main()
