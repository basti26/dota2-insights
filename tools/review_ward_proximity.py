"""Review explicit completed ward exports without re-parsing replays."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_reference_export import build
from dota2_insights.ward_proximity import analyze
from dota2_insights.replay_holdout import refuse_locked_match


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--folder', type=Path, action='append', required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    if args.output.exists():
        cli.error('new output directory required')
    packets, sources = [], []
    for folder in args.folder:
        refuse_locked_match(folder.name)
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        refuse_locked_match(str(manifest.get('match_id')))
        raw = (folder/'wards.json').read_bytes()
        packet = build(manifest, raw)
        packets.append((packet, json.loads(raw)))
        sources.append({'match_id': packet['match_id'], 'wards_sha256': hashlib.sha256(raw).hexdigest(),
                        'replay_sha256': packet['source_replay_sha256']})
    result = analyze(packets)
    result['sources'] = sources
    args.output.mkdir(parents=True)
    (args.output/'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 最新职业比赛：重复与邻近眼位', '',
             f"{len(result['match_ids'])}场，{result['ward_count']}个有效眼位。", '',
             '距离为原始世界坐标单位；不是视野半径。跨局地图版本尚未独立认证。',
             '只比较相同眼类型；真眼与假眼搭配不计作重复。点簇是实插位置的固定半径邻域，禁止链式扩张。',
             '邻域可相互重叠，不能加总为互斥分区；频率不等于有效性或必插点。', '']
    for group in result['radii']:
        lines += [f"## 半径 {group['radius']}", '',
                  f"配对数：{group['pair_counts']}；同方且观测存活重叠：{group['same_team_positive_overlap_pairs']}对。", '',
                  '|眼类型|代表坐标|次数|涉及比赛|结束记录|', '|---|---|---|---|---|']
        for h in group['hotspots'][:12]:
            lines.append(f"|{'假眼' if h['ward_type']=='observer' else '真眼'}|{h['x']:.0f}, {h['y']:.0f}|{h['placement_count']}|{h['match_count']}|{h['end_kinds']}|")
        lines += ['', '具体原始眼编号、比赛时间、结束类型和邻近配对见 analysis.json。', '']
    rows = {r['id']: r for r in result['rows']}
    lines += ['## 256单位内同方复插证据', '',
              '|前眼→后眼编号|类型|距离|前眼结束记录|后眼距前眼结束|', '|---|---|---|---|---|']
    for p in result['radii'][1]['pairs']:
        if p['relation'] == 'same_team':
            gap = p['later_minus_first_end_s']
            lines.append(f"|{p['a']} → {p['b']}|{'假眼' if p['ward_type']=='observer' else '真眼'}|{p['distance']:.0f}|{rows[p['a']]['end_kind']}|{gap:.1f}秒|" if gap is not None else
                         f"|{p['a']} → {p['b']}|{p['ward_type']}|{p['distance']:.0f}|未知|未知|")
    lines += ['', '配对包含非相邻的复插，不把每一对算成一次连续补眼；没有同时存活不等于没有视野覆盖浪费。',
              '样本仅三盘同一赛事，未按独立队伍或系列赛消除相关性，不推广为所有职业比赛偏好。', '']
    (args.output/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'matches': result['match_ids'], 'wards': result['ward_count'],
                      'radii': [{k:g[k] for k in ('radius','pair_counts','same_team_positive_overlap_pairs')} for g in result['radii']]}))


if __name__ == '__main__':
    main()
