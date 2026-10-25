"""Render only pending blind review packages; no visual verdict is inferred."""
import html
import json
import math
from pathlib import Path
import shutil

from dota2_insights.ward_validation_package import admitted_development, sha


def clock(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return '未知'
    seconds = int(abs(value))
    return ('-' if value < 0 else '') + f'{seconds // 60}:{seconds % 60:02}'


def pending_report(freeze_path, packages, output):
    output = Path(output)
    if output.exists():
        raise ValueError('new_report_output_required')
    rows, manifests, frozen = [], [], None
    seen = set()
    for folder in map(Path, packages):
        body = (folder / 'package.json').read_bytes()
        if sha(body) != (folder / 'package.sha256').read_text(encoding='ascii').strip():
            raise ValueError('package_fingerprint_mismatch')
        package = json.loads(body)
        mid = package['match_id']
        frozen, match, freeze_bytes = admitted_development(freeze_path, mid)
        if (package.get('status') != 'samples_frozen_pending_visual_review'
                or package.get('source_freeze_sha256') != sha(freeze_bytes)
                or package.get('cohort_key') != match['cohort_key'] or mid in seen):
            raise ValueError('bound_unique_pending_package_required')
        seen.add(mid)
        for name, fingerprint in package['files'].items():
            path = (folder / name).resolve()
            if not path.is_relative_to(folder.resolve()) or sha(path.read_bytes()) != fingerprint:
                raise ValueError('package_file_changed_or_outside')
        blind = json.loads((folder / 'review/blind.json').read_text(encoding='utf-8'))
        if (len(blind) != package['random_selected'] + package['special_selected']
                or any(r.get('status') != 'pending' or r.get('match_id') != mid for r in blind)):
            raise ValueError('pending_blind_template_required')
        rows.extend(blind)
        manifests.append({'match_id': mid, 'package_sha256': sha(body), 'folder': str(folder.resolve()),
                          'random': package['random_selected'], 'special': package['special_selected']})
    if not frozen or len({r['sample_id'] for r in rows}) != len(rows):
        raise ValueError('nonempty_unique_sample_set_required')
    output.mkdir(parents=True)
    links = []
    for item in manifests:
        mid, folder = item['match_id'], Path(item['folder'])
        for suffix in ('json', 'csv'):
            shutil.copyfile(folder / ('review/blind.' + suffix), output / (mid + '-blank.' + suffix))
        links.append(f'<li>{mid}：<a href="{mid}-blank.csv">空白CSV</a> · '
                     f'<a href="{mid}-blank.json">空白JSON</a></li>')
    table = ''.join('<tr><td>' + html.escape(str(r['match_id'])) + '</td><td>' +
                    html.escape(str(r['sample_id'])) + '</td><td>' +
                    ('随机' if r['group'] == 'random' else '专项') + '</td><td>' +
                    clock(r['navigation_game_s']) + '</td><td>待画面核对</td><td>尚无截图</td></tr>' for r in rows)
    report = {'status': 'pending_visual_review', 'independent_validation_passed': False,
              'source_freeze_sha256': sha(Path(freeze_path).read_bytes()), 'packages': manifests,
              'sample_count': len(rows), 'planned_total': len(frozen['matches']) *
              (frozen['random_per_match'] + frozen['special_per_match']),
              'correct_count': None, 'mismatch_count': None, 'unjudgeable_count': None,
              'pending_count': len(rows), 'heldout_matches_locked':
              [m['match_id'] for m in frozen['matches'] if m['role'] == 'heldout']}
    (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>眼位画面核对准备</title>'
            '<style>body{font:16px system-ui;max-width:1250px;margin:32px auto;padding:0 12px}'
            'table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;padding:8px}</style>'
            '<h1>眼位独立核验：待画面核对</h1>'
            f'<p>已固定 {len(rows)} 个开发样本；总计划 {report["planned_total"]} 个。'
            '正确/不符/无法判断尚无观察结论，不计算准确率。封存场尚未开启。</p>'
            '<p>表中时间只作导航，赛前负时间保留；不是人工确认的放置时间。'
            '地图标定仍未认证；不同构建/模式/地图来源分开。</p>'
            '<p>先另存核对表填写观察和截图，不覆盖原样本包的空白表；提交盲表前不要查看程序答案。</p><ul>'
            + ''.join(links) + '</ul><table><tr><th>比赛</th><th>样本</th><th>分组</th><th>跳转参考</th>'
            '<th>核对状态</th><th>画面证据</th></tr>' + table + '</table></html>')
    (output / 'index.html').write_text(page, encoding='utf-8')
    return report
