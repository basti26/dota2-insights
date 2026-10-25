"""Cross-source ward placement checks; no clock correction or map mutation."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import html
import math
from pathlib import Path
import re
import sqlite3
import statistics

from .postgame_store import VERSION, bounded, number, sha
from .ward_patterns import COORDINATES

VERSION_ALIGNMENT = 'ward_placement_clock_alignment_v1'
MAX_DISTANCE = 10.0


def stats(values):
    if not values:
        return None
    values = sorted(values)
    return {'count': len(values), 'minimum': values[0], 'maximum': values[-1],
            'median': statistics.median(values)}


def analyze(database, heroes_path, heroes_sha256, output):
    database, heroes_path, output = map(lambda p: Path(p).resolve(), (database,heroes_path,output))
    if output.exists():
        raise ValueError('ward_clock_new_output_required')
    database_digest = sha(bounded(database,128*1024**2))
    hero_raw = bounded(heroes_path,256*1024)
    if not re.fullmatch('[a-f0-9]{64}',heroes_sha256) or sha(hero_raw)!=heroes_sha256:
        raise ValueError('ward_clock_hero_catalog_digest_mismatch')
    catalog = json.loads(hero_raw)
    catalog = list(catalog.values()) if isinstance(catalog,dict) else catalog
    if not isinstance(catalog,list) or not 2<=len(catalog)<=256:
        raise ValueError('ward_clock_catalog_shape')
    names = {}
    for row in catalog:
        identifier, name = row.get('id'),row.get('name')
        if (type(identifier) is not int or identifier in names or
                not isinstance(name,str) or not re.fullmatch('npc_dota_hero_[a-z0-9_]+',name)):
            raise ValueError('ward_clock_catalog_hero')
        names[identifier]=name
    if len(set(names.values()))!=len(names):
        raise ValueError('ward_clock_duplicate_catalog_identity')
    c=sqlite3.connect(database.as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
    try:
        meta=dict(c.execute('SELECT key,value FROM metadata'))
        if meta.get('schema_version')!=VERSION:
            raise ValueError('ward_clock_facts_schema')
        matches=[dict(r) for r in c.execute('SELECT * FROM matches')]
        wards=[dict(r) for r in c.execute('SELECT * FROM wards ORDER BY match_id,placed_s,ward_index')]
        public=[dict(r) for r in c.execute('SELECT * FROM public_ward_logs ORDER BY match_id,time_s,event_index')]
        players=[dict(r) for r in c.execute('SELECT * FROM player_summaries')]
    finally:
        c.close()
    if len(matches)>256 or len(wards)>100_000 or len(public)>100_000:
        raise ValueError('ward_clock_row_budget')
    by_dem,by_api,by_player=defaultdict(list),defaultdict(list),defaultdict(list)
    for row in wards:by_dem[row['match_id']].append(row)
    for row in public:by_api[row['match_id']].append(row)
    for row in players:by_player[row['match_id']].append(row)
    results=[]
    for match in matches:
        mid=match['match_id']
        if not by_api[mid]:
            results.append({'match_id':mid,'status':'no_public_ward_records_not_zero_placements',
                            'context_status':match['context_status']})
            continue
        roster={}
        for player in by_player[mid]:
            slot=player['player_slot'];name=names.get(player['hero_numeric'])
            if name is None or name in roster or slot not in (*range(5),*range(128,133)):
                raise ValueError('ward_clock_roster_identity_invalid')
            if player['context_sha256']!=match['context_sha256']:
                raise ValueError('ward_clock_roster_context_mismatch')
            roster[name]=slot
        if len(roster)!=10 or len(set(roster.values()))!=10:
            raise ValueError('ward_clock_ten_hero_roster_required')
        ds,ps=defaultdict(list),defaultdict(list)
        excluded=Counter();pid_checks=[]
        for row in by_dem[mid]:
            if row['coordinate_basis']!=COORDINATES or row['placement_status'] not in ('in_game','pregame'):
                excluded['dem_status_or_coordinate']+=1;continue
            detail=json.loads(row['record_json']);slot=roster.get(detail.get('placer'))
            if slot is None or row['team']!=(2 if slot<128 else 3):
                excluded['dem_hero_or_team_unconfirmed']+=1;continue
            # Identity matching comes from the hero name and API roster. The
            # pinned extractor's 0..9 player_id conversion is only corroboration.
            pid=detail.get('player_id')
            pid_slot=pid if type(pid) is int and 0<=pid<5 else pid+123 if type(pid) is int and 5<=pid<10 else None
            pid_checks.append(pid_slot==slot)
            ds[(slot,row['ward_type'])].append({**row,'hero_name':detail['placer']})
        for row in by_api[mid]:
            if row['context_sha256']!=match['context_sha256']:
                raise ValueError('ward_clock_api_context_mismatch')
            detail=json.loads(row['payload'])
            if (detail.get('entityleft') is not False or not number(detail.get('x'))
                    or not number(detail.get('y')) or not number(row['time_s'])):
                excluded['api_not_confirmed_placement_or_coordinate']+=1;continue
            ps[(row['player_slot'],row['ward_type'])].append({**row,'api_x':detail['x'],'api_y':detail['y']})
        rank_pairs=[];groups=[]
        for group in sorted(set(ds)|set(ps)):
            dem,api=ds[group],ps[group]
            equal=len(dem)==len(api)
            groups.append({'player_slot':group[0],'ward_type':group[1],'dem_count':len(dem),
                           'api_count':len(api),'equal_count':equal})
            if equal:
                rank_pairs.extend((a,b) for a,b in zip(dem,api))
        # Candidate transforms have no fitted offset or rotation. Coordinates
        # choose a model; they are not written into the source facts or map.
        training=[];holdout=[]
        for dem,api in rank_pairs:
            spatial_key=f"{dem['x']:.3f},{dem['y']:.3f}".encode()
            (training if int(sha(spatial_key)[:2],16)<128 else holdout).append((dem,api))
        candidates=[]
        for scale in (1,64,128,256):
            for swap in (False,True):
                residuals=[]
                for dem,api in training:
                    x,y=(api['api_y'],api['api_x']) if swap else (api['api_x'],api['api_y'])
                    residuals.append(math.dist((dem['x'],dem['y']),(x*scale,y*scale)))
                candidates.append({'scale':scale,'swap_axes':swap,'training_residual_world_units':stats(residuals)})
        candidates.sort(key=lambda r:r['training_residual_world_units']['median'] if r['training_residual_world_units'] else math.inf)
        chosen=candidates[0]
        model_supported=(len(training)>=3 and len(holdout)>=3 and
            chosen['training_residual_world_units']['median']<MAX_DISTANCE and
            candidates[1]['training_residual_world_units']['median']-chosen['training_residual_world_units']['median']>MAX_DISTANCE)

        def distance(dem,api):
            x,y=(api['api_y'],api['api_x']) if chosen['swap_axes'] else (api['api_x'],api['api_y'])
            return math.dist((dem['x'],dem['y']),(x*chosen['scale'],y*chosen['scale']))

        held_residuals=[distance(dem,api) for dem,api in holdout]
        model_supported=model_supported and max(held_residuals,default=math.inf)<=MAX_DISTANCE
        pairs=[];ambiguous=[];unmatched=[]
        for group in sorted(set(ds)|set(ps)):
            dem,api=ds[group],ps[group]
            edges=[(i,j) for i,a in enumerate(dem) for j,b in enumerate(api)
                   if model_supported and distance(a,b)<=MAX_DISTANCE]
            d_degree=Counter(i for i,j in edges);a_degree=Counter(j for i,j in edges)
            for i,j in edges:
                a,b=dem[i],api[j]
                row={'ward_index':a['ward_index'],'player_slot':group[0],'ward_type':group[1],
                    'hero_name':a['hero_name'],'api_event_index':b['event_index'],
                    'dem_time_s':a['placed_s'],'api_time_s':b['time_s'],
                    'api_minus_dem_s':b['time_s']-a['placed_s'],
                    'coordinate_residual_world_units':distance(a,b),
                    'dem_x':a['x'],'dem_y':a['y'],'api_x':b['api_x'],'api_y':b['api_y']}
                if d_degree[i]==1 and a_degree[j]==1:
                    pairs.append(row)
                else:
                    ambiguous.append(row)
            for i,a in enumerate(dem):
                if not d_degree[i]:unmatched.append({'source':'dem','ward_index':a['ward_index'],'player_slot':group[0],'ward_type':group[1]})
            for j,b in enumerate(api):
                if not a_degree[j]:unmatched.append({'source':'api','event_index':b['event_index'],'player_slot':group[0],'ward_type':group[1]})
        deltas=[r['api_minus_dem_s'] for r in pairs]
        integer_times=bool(pairs) and all(float(r['api_time_s']).is_integer() for r in pairs)
        interval=([max(deltas)-.5,min(deltas)+.5] if integer_times else None)
        rank_checks=[{'ward_index':a['ward_index'],'player_slot':roster[a['hero_name']],
            'ward_type':a['ward_type'],'api_event_index':b['event_index'],
            'coordinate_residual_world_units':distance(a,b),'api_minus_dem_s':b['time_s']-a['placed_s']}
            for a,b in rank_pairs] if model_supported else []
        results.append({'match_id':mid,'status':'analyzed_ward_placements_only',
            'context_sha256':match['context_sha256'],'replay_sha256':match['replay_sha256'],
            'groups':groups,'exclusions':dict(excluded),'hero_pid_checks':len(pid_checks),
            'hero_pid_mismatches':sum(not v for v in pid_checks),
            'transform_candidates':candidates,'candidate_transform':{k:chosen[k] for k in ('scale','swap_axes')},
            'training_pairs':len(training),'heldout_pairs':len(holdout),
            'heldout_coordinate_residual_world_units':stats(held_residuals),
            'coordinate_model_supported_within_this_match':model_supported,
            'unique_identity_spatial_pairs':pairs,'ambiguous_coordinate_candidate_edges':ambiguous,
            'unmatched':unmatched,'order_pairs_not_independent_clock_evidence':rank_checks,
            'unique_pair_time_difference_s':stats(deltas),
            'unique_pair_abs_difference_s':stats([abs(v) for v in deltas]),
            'reported_api_times_all_integer':integer_times,
            'conditional_constant_offset_interval_s_if_nearest_integer_rounding':interval,
            'conditional_zero_offset_compatible':interval[0]<=0<=interval[1] if interval else None,
            'automatic_correction_applied':False})
    if sha(bounded(database,128*1024**2))!=database_digest or sha(bounded(heroes_path,256*1024))!=heroes_sha256:
        raise ValueError('ward_clock_source_changed')
    result={'schema_version':VERSION_ALIGNMENT,'database_sha256':database_digest,
        'hero_catalog_sha256':heroes_sha256,'matches':results,
        'policy':{'pairing':'hero_roster_identity_plus_mutually_unique_spatial_candidate_no_time_tolerance',
            'spatial_tolerance_world_units':MAX_DISTANCE,'candidate_scales':[1,64,128,256],
            'spatial_training':'equal_count_identity_type_sequence_pairs_candidate_model_selection_only',
            'spatial_holdout':'coordinate_hash_partition_keeps_same_coordinate_on_same_side',
            'clock_evidence':'mutually_unique_identity_coordinate_pairs_only',
            'calibration_scope':'ward_placement_records_of_explicit_bound_match_only'},
        'limitations':['This is within-match corroboration, not a whole-map or cross-patch validation.',
            'Repeated coordinates remain ambiguous and are excluded from independent clock estimates.',
            'Integer rounding is an explicit hypothesis, not proof of the source implementation.',
            'A conditional offset interval is not a confidence interval or general clock-error bound.',
            'Ward placement agreement does not certify kill/objective clocks, log completeness or ground truth.',
            'No clock correction, coordinate merge, imputation or real-time change is applied.']}
    output.mkdir(parents=True)
    (output/'alignment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return result


def render(result, output):
    """Static chart/report of observed differences, not a correction profile."""
    esc=lambda value:html.escape(str(value))
    details=[r for r in result['matches'] if r['status']=='analyzed_ward_placements_only']
    parts=['''<!doctype html><meta charset="utf-8"><title>放眼跨来源时间核对</title>
<style>body{font:16px system-ui;background:#17232f;color:#e3ebf3;margin:24px}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #52677b}.warn{color:#f1cc87;max-width:1200px}.scroll{max-height:65vh;overflow:auto}svg{width:100%;max-width:1250px}a{color:#8cd8c3}code{overflow-wrap:anywhere}</style>
<h1>同一放眼：DEM与API时间是否一致？</h1>
<p class="warn">按英雄身份和互为唯一的空间候选配对，没有用时间差筛选。结果只覆盖具体比赛的放眼记录，不担保击杀/目标事件时钟、日志完整性或地图视野。重复位置产生的歧义会保留，不进入独立时钟估计。</p>''']
    parts.append(f'<p>事实库{len(result["matches"])}场，{len(details)}场有可比较的API放眼记录；其余缺少记录，不计为零放眼。<a href="alignment.json">完整JSON</a></p>')
    for match in details:
        pairs=match['unique_identity_spatial_pairs'];delta=match['unique_pair_time_difference_s']
        transform=match['candidate_transform'];held=match['heldout_coordinate_residual_world_units']
        parts.append(f'<h2>比赛{esc(match["match_id"])}</h2>')
        parts.append('<p>来源数据库SHA：<code>'+esc(result['database_sha256'])+'</code><br>API上下文SHA：<code>'+esc(match['context_sha256'])+'</code></p>')
        parts.append(f'<p>唯一配对{len(pairs)}条；歧义候选边{len(match["ambiguous_coordinate_candidate_edges"])}条；未匹配{len(match["unmatched"])}条。英雄身份与player_id旁证不一致{match["hero_pid_mismatches"]}条。</p>')
        parts.append(f'<p>本局坐标候选：API乘{transform["scale"]}，交换轴={transform["swap_axes"]}；训练{match["training_pairs"]}对，按坐标哈希留出{match["heldout_pairs"]}对。留出最大距离残差'+(f'{held["maximum"]:.3f}' if held else '未记录')+'世界单位。该换算只用于核对，未写回事实坐标。</p>')
        if delta:
            parts.append(f'<p>API−DEM：最小{delta["minimum"]:.6f}秒、中位{delta["median"]:.6f}秒、最大{delta["maximum"]:.6f}秒。中位数不自动当作偏移修正。</p>')
            limit=max(1,math.ceil(max(abs(r['api_minus_dem_s']) for r in pairs)))
            maximum=max(r['dem_time_s'] for r in pairs);minimum=min(0,min(r['dem_time_s'] for r in pairs))
            width=max(1,maximum-minimum)
            parts.append('<svg viewBox="0 0 1200 360" role="img" aria-label="游戏时刻与API减DEM时间差散点图"><rect width="1200" height="360" fill="#20313f"/>')
            for value in sorted({-limit,-.5,0,.5,limit}):
                y=170-value/limit*130
                parts.append(f'<line x1="100" y1="{y}" x2="1160" y2="{y}" stroke="#718397" stroke-dasharray="5,5"/><text x="8" y="{y+5}" fill="white">{value:+g}秒</text>')
            for minute in range(0,math.ceil(maximum/60)+1,10):
                x=100+(minute*60-minimum)/width*1060
                if x<=1160:parts.append(f'<text x="{x}" y="330" fill="white">{minute}分</text>')
            for pair in pairs:
                x=100+(pair['dem_time_s']-minimum)/width*1060;y=170-pair['api_minus_dem_s']/limit*130
                color='#70d59e' if pair['player_slot']<128 else '#ee8888'
                parts.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="4" fill="{color}"><title>'+esc(f"眼位#{pair['ward_index']} / 槽{pair['player_slot']} / Δ{pair['api_minus_dem_s']:.6f}秒")+'</title></circle>')
            parts.append('</svg><p>横轴为DEM游戏时刻，纵轴为API−DEM；绿=天辉、红=夜魇。散点不证明其他来源事件没有时钟误差。</p>')
        interval=match['conditional_constant_offset_interval_s_if_nearest_integer_rounding']
        if interval:
            parts.append(f'<details><summary>整数秒假设下的兼容区间</summary><p>如果API使用最近整数秒舍入且两边仅存在一个恒定偏移，兼容偏移为[{interval[0]:.6f}, {interval[1]:.6f}]秒。'+('包含零。' if match['conditional_zero_offset_compatible'] else '不包含零或交集为空。')+' 这是给定模型的代数约束，不是置信区间、真实误差上限或源实现证明。</p></details>')
        parts.append('<h3>逐条来源</h3><div class="scroll"><table><thead><tr><th>DEM索引</th><th>API槽/眼型/索引</th><th>DEM秒</th><th>API秒</th><th>API−DEM秒</th><th>空间残差</th></tr></thead><tbody>')
        for pair in sorted(pairs,key=lambda r:(r['dem_time_s'],r['ward_index'])):
            vals=(pair['ward_index'],f"{pair['player_slot']} / {pair['ward_type']} / {pair['api_event_index']}",
                f"{pair['dem_time_s']:.6f}",f"{pair['api_time_s']:.6f}",f"{pair['api_minus_dem_s']:.6f}",f"{pair['coordinate_residual_world_units']:.3f}")
            parts.append('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in vals)+'</tr>')
        parts.append('</tbody></table></div>')
    parts.append('<p>自动时钟修正未启用。经济复核继续引用既有每局报告，本轮没有重新采集或评估识别准确率。</p>')
    (Path(output)/'report.html').write_text(''.join(parts),encoding='utf-8',newline='\n')
