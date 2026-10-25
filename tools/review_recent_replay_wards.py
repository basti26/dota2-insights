"""Review already exported wards/events and reuse frozen equipment comparisons."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import html
import json
import math
from pathlib import Path
from statistics import median
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.hero_identity import DEFAULT_HERO_NAMES, DEFAULT_HERO_NAMES_ZH, hero_id_for_name

NAMES = {hero_id_for_name(en): zh for en, zh in zip(DEFAULT_HERO_NAMES, DEFAULT_HERO_NAMES_ZH)}
ULTS = {'zuus_thundergods_wrath': '雷神之怒', 'faceless_void_chronosphere': '时间结界',
    'tidehunter_ravage': '毁灭', 'magnataur_reverse_polarity': '两极反转', 'enigma_black_hole': '黑洞',
    'dawnbreaker_solar_guardian': '天光现世', 'omniknight_guardian_angel': '守护天使',
    'silencer_global_silence': '全领域静默', 'disruptor_static_storm': '静态风暴'}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def clock(seconds):
    if seconds is None:
        return '未知'
    prefix = '-' if seconds < 0 else ''
    value = round(abs(seconds))
    return f'{prefix}{value//60}:{value%60:02d}'


def gaps(wards, end):
    if end is None:
        return []
    intervals = []
    for row in wards:
        start = row.get('placed_game_time_s')
        if start is None:
            continue
        stop = row.get('ended_game_time_s')
        # Unknown endings are right-censored, not classified as expired/killed.
        stop = end if stop is None else min(stop, end)
        if stop > 0 and start < end:
            intervals.append((max(0, start), stop))
    cursor, result = 0, []
    for start, stop in sorted(intervals):
        if start > cursor:
            result.append([cursor, start])
        cursor = max(cursor, stop)
    if cursor < end:
        result.append([cursor, end])
    return sorted(result, key=lambda r: r[1]-r[0], reverse=True)


def review(root, mid):
    folder = root / 'replay-ward-review-20261019' / mid
    archive = root / 'replay-review-20261018' / mid
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    previous = json.loads((archive/'manifest.json').read_text(encoding='utf-8'))
    if (not manifest['complete'] or not previous['export_complete']
            or manifest['source_sha256'] != previous['source_sha256']
            or manifest['match_id'] != mid or sha(folder/'wards.json') != manifest['files']['wards.json']['sha256']):
        raise ValueError('ward/archive provenance mismatch')
    events_path = archive / 'events.jsonl'
    if sha(events_path) != previous['files']['events.jsonl']['sha256']:
        raise ValueError('combat event digest mismatch')
    roster = {}
    with (archive/'hero_samples.jsonl').open(encoding='utf-8') as stream:
        for _, line in zip(range(10), stream):
            row = json.loads(line)
            roster[row['hero_id']] = row['team']
    deaths, casts, bkb = [], [], Counter()
    seen = set()
    for line in events_path.open(encoding='utf-8'):
        event = json.loads(line)
        if event.get('event_type') == 'DEATH' and event.get('target') in {'npc_dota_observer_wards', 'npc_dota_sentry_wards'}:
            deaths.append(event)
        if event.get('event_type') == 'ABILITY' and event.get('inflictor') in ULTS:
            key = event.get('event_tick'), event.get('attacker'), event.get('inflictor')
            if key not in seen:
                seen.add(key)
                casts.append({k: event.get(k) for k in ('game_time_s', 'event_tick', 'attacker', 'inflictor')})
        if event.get('event_type') == 'MODIFIER_ADD' and event.get('inflictor') == 'modifier_black_king_bar_immune':
            bkb[str(event.get('target'))] += 1
    wards = json.loads((folder/'wards.json').read_text(encoding='utf-8'))
    teams = []
    for team in (2, 3):
        placed = [r for r in wards if r['team'] == team and not r['after_match_end']]
        observers = [r for r in placed if r['ward_type'] == 'observer']
        sentries = [r for r in placed if r['ward_type'] == 'sentry']
        for row in placed:
            killer_team = roster.get(row.get('killer'))
            row['killer_relation'] = ('ally' if killer_team == team else 'enemy' if killer_team in (2, 3) else 'unknown') if row['end_kind'] == 'killed' else None
        enemy_killed = [r for r in observers if r['killer_relation'] == 'enemy']
        fast = sorted([r for r in enemy_killed if r['duration_s'] is not None and 0 <= r['duration_s'] <= 60], key=lambda r:r['duration_s'])
        lifetimes = [r['duration_s'] for r in observers if r['duration_s'] is not None]
        pairs = []
        for i, a in enumerate(observers):
            if a['x'] is None or a['y'] is None:
                continue
            for b in observers[i+1:]:
                if b['x'] is None or b['y'] is None:
                    continue
                dist = math.hypot(a['x']-b['x'], a['y']-b['y'])
                end = manifest['match_end_game_time_s']
                if end is None or a['placed_game_time_s'] is None or b['placed_game_time_s'] is None:
                    continue
                overlap = min(a['ended_game_time_s'] if a['ended_game_time_s'] is not None else end,
                    b['ended_game_time_s'] if b['ended_game_time_s'] is not None else end) - max(a['placed_game_time_s'], b['placed_game_time_s'])
                if dist <= 256 and overlap >= 30:
                    pairs.append({'first_tick': a['tick'], 'second_tick': b['tick'],
                        'distance_raw_world_units': round(dist, 2), 'overlap_seconds': round(overlap, 2)})
        teams.append({'team': team, 'observer_count': len(observers), 'sentry_count': len(sentries),
            'observer_enemy_killed': len(enemy_killed),
            'observer_ally_killed': sum(r['killer_relation']=='ally' for r in observers),
            'observer_unknown_killer': sum(r['end_kind']=='killed' and r['killer_relation']=='unknown' for r in observers),
            'observer_expired': sum(r['end_kind']=='expired' for r in observers),
            'observer_unobserved_end': sum(r['end_kind']=='unobserved' for r in observers),
            'observer_median_observed_lifetime_s': round(median(lifetimes), 2) if lifetimes else None,
            'fast_enemy_deward_count': len(fast), 'fast_enemy_deward_examples': fast[:4],
            'longest_no_observer_record_gaps': gaps(observers, manifest['match_end_game_time_s'])[:4],
            'nearby_overlapping_observer_pairs': pairs,
            'sentry_enemy_killed': sum(r['killer_relation']=='enemy' for r in sentries)})
    target = {'observer':'npc_dota_observer_wards', 'sentry':'npc_dota_sentry_wards'}
    corroborated = sum(any(e.get('target') == target[r['ward_type']] and e.get('attacker') == r.get('killer')
        and abs((e.get('event_tick') or 0)-r['killed_tick']) <= 1 for e in deaths)
        for r in wards if r['end_kind']=='killed')
    comparison = (archive/'economy-comparison.json') if mid != '9050542227' else root/'field-review-9050542227/replay-economy-comparison.json'
    economic = json.loads(comparison.read_text(encoding='utf-8'))
    if economic['replay_source_sha256'] != manifest['source_sha256']:
        raise ValueError('equipment comparison belongs to a different replay')
    return {'match_id': mid, 'ward_count': len(wards), 'teams': teams, 'wards': wards,
        'match_end_game_time_s': manifest['match_end_game_time_s'], 'ward_export_manifest_sha256': sha(folder/'manifest.json'),
        'combat_events_sha256': sha(events_path), 'ward_killed_records': sum(r['end_kind']=='killed' for r in wards),
        'killed_records_corroborated_same_attacker_tick_pm1': corroborated,
        'long_ultimate_events': casts, 'long_ultimate_counts': Counter(c['inflictor'] for c in casts),
        'bkb_modifier_add_events': bkb, 'equipment_comparison_counts': economic['counts'],
        'equipment_comparison_sha256': sha(comparison),
        'audio_available': mid == '9050542227'}


def map_html(reports):
    parts = ['<!doctype html><meta charset="utf-8"><title>三局眼位复查</title><style>body{font:16px sans-serif;background:#172027;color:#e8edf2;max-width:1100px;margin:24px auto}svg{background:#202d35;max-width:760px;width:100%}button{margin:5px;padding:8px} .r{fill:#68dc9c}.d{fill:#f07878}text{fill:#b9cbd4;font-size:12px}table{border-collapse:collapse}td,th{padding:5px;border:1px solid #486}</style>',
        '<h1>三局眼位：录像离线复查</h1><p>原始世界坐标散点，非经过标定的游戏地图。圆点为假眼，十字为真眼；绿=天辉，红=夜魇。悬停查看时间及英雄。记录不能当作实战可见信息。</p>',
        '<button onclick="document.querySelectorAll(\'.r\').forEach(e=>e.style.display=e.style.display===\'none\'?\'\':\'none\')">显示/隐藏天辉</button><button onclick="document.querySelectorAll(\'.d\').forEach(e=>e.style.display=e.style.display===\'none\'?\'\':\'none\')">显示/隐藏夜魇</button>']
    for report in reports:
        parts.append(f'<h2>{report["match_id"]}</h2><svg viewBox="0 0 760 760" role="img" aria-label="录像眼位坐标散点">')
        for value in range(8000, 28001, 4000):
            px = 40+(value-8000)/20000*680
            py = 720-(value-8000)/20000*680
            parts.append(f'<path d="M{px} 40V720 M40 {py}H720" stroke="#3b505c"/><text x="{px}" y="745">{value}</text><text x="0" y="{py}">{value}</text>')
        for row in report['wards']:
            if row['x'] is None or row['y'] is None or row['after_match_end']:
                continue
            x, y = 40+(row['x']-8000)/20000*680, 720-(row['y']-8000)/20000*680
            cls = 'r' if row['team']==2 else 'd'
            title = html.escape(f"{clock(row['placed_game_time_s'])} {NAMES.get(row['placer'], row['placer'])} {row['ward_type']}；{row['end_kind']} {clock(row['ended_game_time_s'])}；击杀者 {NAMES.get(row['killer'], row['killer'])}")
            if row['ward_type']=='observer':
                parts.append(f'<circle class="{cls}" cx="{x:.2f}" cy="{y:.2f}" r="5" stroke="#fff" stroke-width=".5"><title>{title}</title></circle>')
            else:
                parts.append(f'<path class="{cls}" d="M{x-4:.2f} {y-1:.2f}h3v-3h2v3h3v2h-3v3h-2v-3h-3Z"><title>{title}</title></path>')
        parts.append('</svg>')
    return '\n'.join(parts)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    if args.output.exists():
        cli.error('Use a new output directory.')
    reports = [review(ROOT/'diagnostics', mid) for mid in ('9050387292','9050449868','9050542227')]
    args.output.mkdir(parents=True)
    (args.output/'review.json').write_text(json.dumps({'matches': reports,
        'limitations': ['End/placement events are offline omniscient facts, not player-visible observations.',
            'Enemy-deward classification requires the killer hero to resolve to the other team.',
            'Zero observer records do not mean zero team vision: towers, creeps, heroes and skills also reveal.',
            'Near overlapping wards are review candidates, not automatically wasted wards.',
            'Ability/modifier log events are not confirmed audible sounds or unique item activations.',
            'Existing screenshot equipment comparisons are reused; no new recognition or independent acceptance.']},
        ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'ward-map.html').write_text(map_html(reports), encoding='utf-8')
    with (args.output/'ward-timeline.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        fields = ['match_id','team','ward_type','placer','placed_game_time_s','ended_game_time_s','duration_s','end_kind','killer','killer_relation','x','y','tick']
        writer=csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore'); writer.writeheader()
        for report in reports:
            writer.writerows(report['wards'])
    for report in reports:
        print(json.dumps({k:report[k] for k in ('match_id','ward_count','ward_killed_records','killed_records_corroborated_same_attacker_tick_pm1','long_ultimate_counts','equipment_comparison_counts')}, ensure_ascii=True))


if __name__=='__main__':
    main()
