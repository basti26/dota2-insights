"""Review admitted native GSI journals with identity and source rechecks."""
from .match_journals import open_rows
from .self_economy_audit import SelfEconomyAudit


def review(folder, *, expected_package_id=None):
    audit = SelfEconomyAudit()
    with open_rows(folder, expected_package_id=expected_package_id) as (manifest, rows):
        for row in rows:
            audit.add(row, {'sequence': row['sequence']})
    result = audit.result()
    return {'match_id': manifest['match_id'], 'game_session_id': manifest['game_session_id'],
            'record_session_id': manifest['record_session_id'], 'run_id': manifest['run_id'],
            'recording_integrity': manifest['recording_integrity'],
            'rows_by_economy_status': result['rows_by_economy_status'], 'hero_rows': result['hero_rows'],
            'last_reported_gsi': result['last_reported_gsi'], 'self_economy_audit': result,
            'other_channels': 'not_closed_by_this_manifest',
            'scope': manifest['scope'], 'accuracy_verified': False, 'source_verified_before_and_after': True}


def render_markdown(reports, skipped, *, index_state):
    lines = ['\n## 原生逐局本人GSI', '', f'索引状态：{index_state}。',
             '只表示本通道已封存；其他通道未由此交付。与关闭启动器包重合的行不重复累计。', '']
    for report in reports:
        audit = report['self_economy_audit']
        display = audit['display_checks']
        lines += [f"### 比赛 {report['match_id']}", '',
            f"来源：{report['root_key']} / {report['source']}",
            f"原生包：{report['package_id']}",
            f"完整性：{report['recording_integrity']}；记录{audit['rows']}行。",
            f"与已关闭启动器的本人GSI范围重合：{report['also_in_closed_launcher_scope']}（不可相加）。",
            f"同包显示可比：普通{display['normal_comparable_rows']}行、熊灵{display['bear_comparable_rows']}行；金额不符{display['amount_mismatches']}，熊灵来源不符{display['bear_provenance_issues']}。",
            f"不可比原因：{display['uncomparable_reasons']}",
            '没有进行游戏画面、装备图像或完整净资产认证。', '']
    for entry in skipped:
        lines += [f"- 未分析：{entry.get('state', 'unknown')} / {entry.get('reason', '未通过交接')}"]
    return '\n'.join(lines) + '\n'
