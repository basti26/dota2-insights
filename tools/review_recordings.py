"""One offline command: sync closed sources, then produce the combined review."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(root=ROOT, *, roots=None, latest=5):
    from dota2_insights.history import history_roots
    from dota2_insights.helper_handoff import sync as sync_legacy
    from dota2_insights.match_journal_index import sync as sync_native
    from dota2_insights.observation_index import sync as sync_equipment
    from dota2_insights.draft_phase_index import sync as sync_bp
    from dota2_insights.minimap_index import sync as sync_minimap
    from tools.review_closed_record_content import generate
    root = Path(root).resolve()
    roots = roots if roots is not None else history_roots(root/'config/history.local.json')
    legacy = sync_legacy(root, roots, latest=latest)
    native = sync_native(root, roots, latest=latest)
    equipment = sync_equipment(root, roots, latest=latest)
    bp = sync_bp(root, roots, latest=latest)
    minimap = sync_minimap(root, roots, latest=latest)
    return {**generate(root, roots=roots), 'legacy_counts': legacy['counts'],
            'native_duplicate_packages_omitted': native['duplicate_packages_omitted'],
            'equipment_duplicate_packages_omitted':equipment['duplicate_packages_omitted'],
            'bp_duplicate_packages_omitted':bp['duplicate_packages_omitted'],
            'bp_conflicting_binding_packages':bp['conflicting_binding_packages'],
            'minimap_duplicate_packages_omitted':minimap['duplicate_packages_omitted'],
            'minimap_conflicting_run_packages':minimap['conflicting_run_packages']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(run(latest=args.latest), ensure_ascii=False))


if __name__ == '__main__':
    main()
