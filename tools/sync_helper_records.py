"""Index the latest closed helper recording runs, without copying or analyzing payloads."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=int, default=5)
    args = parser.parse_args()
    from dota2_insights.history import history_roots
    from dota2_insights.helper_handoff import sync
    roots = history_roots(ROOT / 'config/history.local.json')
    result = sync(ROOT, roots, latest=args.latest)
    from dota2_insights.match_journal_index import sync as sync_native
    native = sync_native(ROOT, roots, latest=args.latest)
    from dota2_insights.observation_index import sync as sync_equipment
    equipment = sync_equipment(ROOT, roots, latest=args.latest)
    from dota2_insights.draft_phase_index import sync as sync_bp
    bp = sync_bp(ROOT, roots, latest=args.latest)
    from dota2_insights.minimap_index import sync as sync_minimap
    minimap = sync_minimap(ROOT, roots, latest=args.latest)
    print(json.dumps({'index': str(ROOT / 'diagnostics/helper-handoff/index.json'),
        'counts': result['counts'], 'runs': [{'state': r['state'], 'matches': [a['match_id'] for a in r.get('matches', [])],
                                            'already_indexed': r.get('already_indexed')} for r in result['runs']],
        'native_match_index': str(ROOT / 'diagnostics/native-match-handoff/index.json'),
        'native_match_journals': native['entries'],
        'native_duplicate_packages_omitted': native['duplicate_packages_omitted'],
        'equipment_index': str(ROOT / 'diagnostics/equipment-handoff/index.json'),
        'equipment_handoffs': equipment['entries'],
        'equipment_duplicate_packages_omitted': equipment['duplicate_packages_omitted'],
        'bp_phase_index': str(ROOT / 'diagnostics/draft-phase-handoff/index.json'),
        'bp_phase_handoffs':bp['entries'], 'bp_duplicate_packages_omitted':bp['duplicate_packages_omitted'],
        'bp_conflicting_binding_packages':bp['conflicting_binding_packages'],
        'minimap_run_index': str(ROOT / 'diagnostics/minimap-run-handoff/index.json'),
        'minimap_run_handoffs':minimap['entries'],
        'minimap_duplicate_packages_omitted':minimap['duplicate_packages_omitted'],
        'minimap_conflicting_run_packages':minimap['conflicting_run_packages']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
