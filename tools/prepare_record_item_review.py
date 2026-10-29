"""Prepare SHA-verified HUD crops for a specific closed-record review report."""
import argparse
import json
from pathlib import Path
import re
import sys
from datetime import datetime,timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def prepare_report(report_path, *, root=ROOT, roots=None, continuity=False):
    from dota2_insights.history import history_roots,checked_input,checked_output
    from dota2_insights.closed_journals import read_package
    from dota2_insights.item_review_evidence import prepare, prepare_observation
    from dota2_insights.observation_index import read_index as read_equipment_index
    root=Path(root).resolve()
    path=checked_input(root,report_path)
    if path.stat().st_size>16*1024**2:raise ValueError('item_review_report_budget')
    report=json.loads(path.read_text(encoding='utf-8'))
    roots=roots if roots is not None else history_roots(root/'config/history.local.json')
    folder=root/'diagnostics/helper-handoff'
    index=json.loads((folder/'index.json').read_text(encoding='utf-8'))
    selected={r['launcher_run_id']:r for r in index['runs'] if r.get('package_id')}
    output=checked_output(root,root/'diagnostics/item-priority-review'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    results=[]
    for r in ([] if continuity else report['reports']):
        if not re.fullmatch('run_[a-f0-9]{32}',r['launcher_run_id']):raise ValueError('item_review_run_invalid')
        if len(results)>=25:raise ValueError('item_review_run_budget')
        pid=selected[r['launcher_run_id']]['package_id']
        if not re.fullmatch('[a-f0-9]{64}',pid):raise ValueError('item_review_package_invalid')
        package=read_package(folder/'packages'/(pid+'.json'))
        if package['launcher_run_id']!=r['launcher_run_id']:raise ValueError('item_review_run_mismatch')
        results.append(prepare(package,roots,r['item_review_priorities'],output/r['launcher_run_id']))
    legacy_count=len(results)
    equipment=report.get('equipment_reports',[])
    if not isinstance(equipment,list) or len(equipment)>128:
        raise ValueError('item_review_equipment_budget')
    if equipment:
        equipment_index=read_equipment_index(root/'diagnostics/equipment-handoff/index.json')
        indexed={entry['package_id']:entry for entry in equipment_index['entries'] if entry['state']=='ready_with_gaps'}
        for row in equipment:
            entry=indexed.get(row.get('package_id'))
            if entry is None or any(entry.get(key)!=row.get(key) for key in
                    ('root_key','source','match_id','game_session_id','record_session_id')):
                raise ValueError('item_review_equipment_not_indexed_or_changed')
            if continuity:
                from dota2_insights.equipment_continuity_evidence import prepare as prepare_continuity
                from dota2_insights.record_content_review import item_catalogue
                catalogue, _ = item_catalogue(root)
                results.append(prepare_continuity(entry, roots, catalogue,
                    output/('continuity-'+entry['package_id'])))
            else:
                priorities=row.get('item_review_priorities')
                if not isinstance(priorities,list):
                    raise ValueError('item_review_equipment_priorities_missing')
                results.append(prepare_observation(entry,roots,priorities,output/('equipment-'+entry['package_id'])))
    if continuity:
        from html import escape
        links = ['<!doctype html><meta charset="utf-8"><title>大件识别中断复核</title>',
                 '<h1>大件识别中断复核</h1><p>日志变化不是装备真值；图片仅供复查，尚无人工判定。</p><ul>']
        for result in results:
            folder = 'continuity-' + result['equipment_package_id']
            count = len(result['continuity']['episodes'])
            links.append(f'<li><a href="{folder}/index.html">比赛 {escape(str(result["match_id"]))}</a>：{count} 个展示事件</li>')
        links.append('</ul>')
        (output/'index.html').write_text(''.join(links), encoding='utf-8')
    return {'output':str(output),'runs':legacy_count,'equipment_packages':len(results)-legacy_count,
        'prepared':sum(e['status']=='prepared_for_visual_review' for r in results for e in r['entries']),
        'unavailable':sum(e['status']=='unavailable' for r in results for e in r['entries'])}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--continuity', action='store_true', help='重新准入并生成大件识别中断四阶段图片页（仅原生装备包）')
    args=parser.parse_args()
    print(json.dumps(prepare_report(args.report, continuity=args.continuity),ensure_ascii=False))


if __name__=='__main__':main()
