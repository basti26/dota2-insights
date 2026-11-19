"""Review explicit completed ward exports without re-parsing replays."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.ward_reference_export import build
from dota2_insights.ward_proximity import analyze, declared_map_version
from dota2_insights.replay_holdout import refuse_locked_match
from dota2_insights.match_journals import _json, _read


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--folder', type=Path, action='append', required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    if args.output.exists():
        cli.error('new output directory required')
    packets, sources, snapshots = [], {}, []
    for folder in args.folder:
        if folder.name.isdigit():
            refuse_locked_match(folder.name)
        manifest_raw = _read(folder/'manifest.json', 1024**2)
        manifest = _json(manifest_raw)
        mid = manifest.get('match_id')
        if not isinstance(mid, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', mid):
            raise ValueError('ward_proximity_match_identity_required')
        refuse_locked_match(mid)
        if folder.name.isdigit() and folder.name != mid:
            raise ValueError('ward_proximity_folder_identity_conflict')
        raw = _read(folder/'wards.json', 2*1024**2)
        packet = build(manifest, raw)
        packet['map_version'] = declared_map_version(manifest.get('map_version'))
        packets.append((packet, json.loads(raw)))
        sources[mid] = {'match_id': mid, 'wards_sha256': packet['source_wards_sha256'],
                        'replay_sha256': packet['source_replay_sha256'], 'map_version': packet['map_version']}
        snapshots.append((folder, manifest_raw, packet['source_wards_sha256']))
    result = analyze(packets)
    for folder, manifest_raw, wards_sha256 in snapshots:
        if (_read(folder/'manifest.json', 1024**2) != manifest_raw
                or hashlib.sha256(_read(folder/'wards.json', 2*1024**2)).hexdigest() != wards_sha256):
            raise ValueError('ward_proximity_source_changed_during_review')
    result['sources'] = list(sources.values())
    args.output.mkdir(parents=True)
    (args.output/'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    coverage = result['cross_match_inputs']
    lines = ['# 录像眼位重复与邻近复核', '',
             f"{len(result['match_ids'])}场，{result['ward_count']}个有效眼位。", '',
             f"重复导出合并 {result['duplicate_exports_omitted']} 份。跨局输入中，同声明版本 {coverage['same_declared_version']} 对，"
             f"版本不同 {coverage['different_version']} 对，版本未知 {coverage['unknown_version']} 对。",
             '只有同声明版本进行描述性跨局比较。距离使用原始世界坐标单位，地图落位及必砍树仍待独立核验。',
             '只比较相同眼类型；真眼与假眼搭配不计作重复。点簇是实插位置的固定半径邻域，禁止链式扩张。',
             '邻域可相互重叠，不能加总为互斥分区；频率不等于有效性或必插点。', '']
    for group in result['radii']:
        cross_match = group['cross_match_pair_count']
        lines += [f"## 半径 {group['radius']}", '',
                  f"配对数：{group['pair_counts']}。跨局邻近配对 {cross_match if cross_match is not None else '未比较'}。"
                  f"同方且观测存活重叠 {group['same_team_positive_overlap_pairs']} 对。", '',
                  '|眼类型|代表坐标|次数|涉及比赛|声明地图版本|结束记录|', '|---|---|---|---|---|---|']
        for h in group['hotspots'][:12]:
            lines.append(f"|{'假眼' if h['ward_type']=='observer' else '真眼'}|{h['x']:.0f}, {h['y']:.0f}|{h['placement_count']}|{h['match_count']}|{h['map_version'] or '未知'}|{h['end_kinds']}|")
        lines += ['', '具体原始眼编号、比赛时间、结束类型和邻近配对见 analysis.json。', '']
    rows = {r['id']: r for r in result['rows']}
    lines += ['## 256单位内同方复插证据', '',
              '|前眼→后眼编号|类型|距离|前眼结束记录|后眼距前眼结束|', '|---|---|---|---|---|']
    for p in result['radii'][1]['pairs']:
        if p['relation'] == 'same_team':
            gap = p['later_minus_first_end_s']
            lines.append(f"|{p['a']} → {p['b']}|{'假眼' if p['ward_type']=='observer' else '真眼'}|{p['distance']:.0f}|{rows[p['a']]['end_kind']}|{gap:.1f}秒|" if gap is not None else
                         f"|{p['a']} → {p['b']}|{p['ward_type']}|{p['distance']:.0f}|未知|未知|")
    lines += ['', '配对包含非相邻的复插，不代表连续补眼次数。',
              '本报告仅描述所选录像，未按独立队伍或系列赛消除相关性。', '']
    (args.output/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(json.dumps({'matches': result['match_ids'], 'wards': result['ward_count'],
                      'radii': [{k:g[k] for k in ('radius','pair_counts','same_team_positive_overlap_pairs')} for g in result['radii']]}))


if __name__ == '__main__':
    main()
