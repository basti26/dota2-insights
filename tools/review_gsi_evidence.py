"""Sync native GSI, review named field evidence and export original own coordinates."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(root=ROOT, *, roots=None, latest=2):
    from dota2_insights.history import history_roots, checked_output
    from dota2_insights.match_journal_index import sync, resolve_reference
    from dota2_insights.match_journals import open_rows
    from dota2_insights.gsi_evidence_census import GsiEvidenceCensus, POSITION_COLUMNS, render_markdown
    root = Path(root).resolve()
    roots = roots if roots is not None else history_roots(root/'config/history.local.json')
    index = sync(root, roots, latest=latest)
    reports, tracks, skipped = [], [], []
    for entry in index['entries']:
        if entry['state'] != 'ready_with_gaps':
            skipped.append({'state': entry['state'], 'reason': 'native_gsi_not_ready'})
            continue
        folder = resolve_reference(entry, roots)
        census = GsiEvidenceCensus(collect_positions=True)
        with open_rows(folder, expected_package_id=entry['package_id']) as (manifest, rows):
            for line, row in enumerate(rows, 1):
                census.add(row, line=line)
        reports.append({'match_id': manifest['match_id'], 'game_session_id': manifest['game_session_id'],
            'record_session_id': manifest['record_session_id'], 'source_package_id': entry['package_id'],
            'source': entry['source'], 'root_key': entry['root_key'], 'source_payload_sha256': manifest['payload']['sha256'],
            'source_verified_before_and_after': True, 'census': census.result()})
        tracks.append(census.positions)
    output = checked_output(root, root/'diagnostics/gsi-evidence'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    for report, positions in zip(reports, tracks):
        filename = 'positions-' + report['match_id'] + '-' + report['game_session_id'] + '.csv'
        with (output/filename).open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=POSITION_COLUMNS)
            writer.writeheader(); writer.writerows(positions)
        report['positions_file'] = filename
        report['positions_sha256'] = hashlib.sha256((output/filename).read_bytes()).hexdigest()
    (output/'report.json').write_text(json.dumps({'reports': reports, 'skipped': skipped}, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    (output/'REPORT.md').write_text(render_markdown(reports), encoding='utf-8')
    return {'output': str(output), 'native_packages': len(reports),
            'exported_positions': sum(len(rows) for rows in tracks), 'skipped': skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=int, default=2)
    print(json.dumps(run(latest=parser.parse_args().latest), ensure_ascii=False))


if __name__ == '__main__':
    main()
