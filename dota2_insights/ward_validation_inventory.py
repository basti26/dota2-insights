"""Offline, conservative exposure inventory; never certifies game correctness."""
from __future__ import annotations

import hashlib
import html
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3

MATCH = re.compile(r"(?<!\d)(\d{10})(?!\d)")
TEXT_LIMIT = 8 * 1024 * 1024


def render_inventory(result: dict, output: Path) -> None:
    """Human-readable preparation status; every completion claim stays false."""
    rows = ''.join('<tr><td>' + html.escape(row['match_id']) + '</td><td>' +
                   html.escape(', '.join(row['exclusions']) or '待核对历史及录像比赛号') + '</td></tr>'
                   for row in result['local_replays'])
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>眼位独立验证准备</title>'
            '<style>body{font:16px system-ui;max-width:1000px;margin:40px auto}td,th{padding:8px;border:1px solid #aaa}</style>'
            '<h1>眼位独立验证：离线准备</h1><p>尚未通过。未启动游戏，未完成逐眼画面核对，也未冻结验证名单。</p>'
            f'<p>历史文件 {len(result["history_sources"])}；扫描问题 {len(result["scan_issues"])}。'
            '文本提到比赛只作保守排除，不当作完整解析证明；扫描无报错也不保证历史全集齐全。</p>'
            '<p><a href="inventory.json">完整来源、指纹及扫描缺口</a></p>'
            '<table><tr><th>本地录像比赛号（文件名，尚需核对footer）</th><th>排除或待核对原因</th></tr>' + rows + '</table></html>')
    (output / 'index.html').write_text(page, encoding='utf-8')


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inventory(roots: list[Path], replay_roots: list[Path], catalog: Path) -> dict:
    """Scan explicitly supplied historical roots without reading ward outcomes.

    Text references are conservative exclusions, not proof of prior full parsing.
    Incomplete scans always prevent eligibility certification.
    """
    exposure: dict[str, set[str]] = {}
    issues: list[dict] = []
    sources = []
    seen: set[Path] = set()

    def note(mid, reason):
        exposure.setdefault(str(mid), set()).add(reason)

    def issue(path, reason):
        issues.append({'path': str(path), 'reason': reason})

    for root in roots:
        root = root.resolve()
        if not root.is_dir():
            issue(root, 'missing_history_root')
            continue
        for directory, dirs, files in os.walk(root, followlinks=False,
                                               onerror=lambda e: issue(e.filename, 'unreadable_directory')):
            for name in list(dirs):
                child = Path(directory) / name
                if child.is_symlink() or child.is_junction():
                    issue(child, 'linked_directory_not_scanned')
                    dirs.remove(name)
            for name in sorted(files):
                path = Path(directory) / name
                if path in seen:
                    continue
                seen.add(path)
                suffix = path.suffix.lower()
                if suffix not in {'.json', '.jsonl', '.md', '.html', '.sqlite', '.db'}:
                    continue
                try:
                    if suffix in {'.sqlite', '.db'}:
                        # SQLite read-only URI: never create a missing database.
                        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='matches'").fetchone()
                            ids = [str(row[0]) for row in db.execute('SELECT match_id FROM matches')] if exists else []
                        for mid in ids:
                            note(mid, 'analysis_database')
                    else:
                        if path.stat().st_size > TEXT_LIMIT:
                            issue(path, 'text_exceeds_scan_limit')
                            continue
                        ids = sorted(set(MATCH.findall(path.read_text(encoding='utf-8-sig'))))
                        for mid in ids:
                            note(mid, 'historical_reference')
                    sources.append({'path': str(path), 'sha256': digest(path), 'match_count': len(ids)})
                except (OSError, UnicodeError, sqlite3.Error):
                    issue(path, 'history_unreadable_or_unsupported')

    catalog_bytes = catalog.read_bytes()
    entries = json.loads(catalog_bytes)['matches']
    for mid, row in entries.items():
        if row.get('analysis_status') not in (None, 'waiting_for_replay'):
            note(mid, 'catalog_analysis_attempt')
    replays = []
    for root in replay_roots:
        if not root.is_dir():
            issue(root, 'missing_replay_root')
            continue
        for path in sorted(root.glob('*.dem')):
            mids = MATCH.findall(path.stem)
            if len(mids) != 1:
                issue(path, 'unresolved_replay_filename')
                continue
            mid = mids[0]
            try:
                replays.append({'match_id': mid, 'path': str(path), 'bytes': path.stat().st_size,
                                'sha256': digest(path), 'exclusions': sorted(exposure.get(mid, [])),
                                'eligibility': 'excluded' if mid in exposure else 'requires_footer_and_history_review'})
            except OSError:
                issue(path, 'unreadable_replay')
    return {'schema_version': 1, 'status': 'offline_preparation_only',
            'independent_validation_passed': False, 'frozen_matches': [],
            'history_scan_complete': not issues,
            'eligibility_certified': False,
            'catalog_sha256': hashlib.sha256(catalog_bytes).hexdigest(),
            'catalog_counts': {status: sum(r.get('analysis_status') == status for r in entries.values())
                               for status in sorted({str(r.get('analysis_status')) for r in entries.values()})},
            'catalog_outside_analysis_databases': sorted(mid for mid in entries
                if 'analysis_database' not in exposure.get(mid, set())),
            'exposure': {mid: sorted(reasons) for mid, reasons in sorted(exposure.items())},
            'history_sources': sources, 'scan_issues': issues, 'local_replays': replays}
