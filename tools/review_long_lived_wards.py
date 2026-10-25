"""Descriptive ten-minute ward-location review from a frozen verified export."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import html
import json
import math
from pathlib import Path
import shutil
import statistics


def stamp(seconds):
    return f'{int(seconds)//60}:{int(seconds)%60:02d}'


def pixel(x,y,width=1600):
    return (((8878-8356)/2+(x-7487)/18322*8356)/8878*width,
            (1-(y-7693)/18322)*8356/8878*width)


def coarse_area(x,y):
    px,py=pixel(x,y,8878)
    col=max(0,min(2,int(px/8878*3)))
    row=max(0,min(2,int(py/8356*3)))
    return [['西北','北部','东北'],['西部','中央','东部'],['西南','南部','东南']][row][col]


def review(data):
    observations=[];excluded=Counter()
    for match in data['matches']:
        end=match['manifest'].get('match_end_game_time_s')
        teams={}
        for w in match['wards']:
            if w.get('placer') and w.get('team') in (2,3):teams.setdefault(w['placer'],set()).add(w['team'])
        for index,w in enumerate(match['wards']):
            if w['ward_type']!='observer':continue
            t=w.get('placed_game_time_s')
            if t is None or end is None or t<0 or t>=end or w.get('after_match_end'):
                excluded['pregame_postgame_or_missing_clock']+=1;continue
            if w.get('x') is None or w.get('y') is None or w.get('team') not in (2,3):
                excluded['missing_position_or_team']+=1;continue
            ended=w.get('ended_game_time_s');duration=w.get('duration_s')
            complete=(w.get('end_kind') in ('expired','killed') and ended is not None and ended<=end
                      and duration is not None and 0<=duration<=361)
            killer_teams=teams.get(w.get('killer'),set())
            relation=('ally' if killer_teams=={w['team']} else 'enemy' if killer_teams=={5-w['team']} else 'unknown')
            long=bool(complete and duration>=300 and not (w['end_kind']=='killed' and relation=='ally'))
            observations.append({'match_id':match['match_id'],'ward_index':index,'phase':int(t//600),
                'team':w['team'],'x':w['x'],'y':w['y'],'placed':t,'ended':ended,'duration':duration,
                'end_kind':w['end_kind'],'complete_lifetime':complete,'long_lived':long,
                'killer_relation':relation,'placer':w.get('placer'),'killer':w.get('killer')})
    candidates=[]
    phases=sorted({r['phase'] for r in observations})
    for phase in phases:
        for team in (2,3):
            pool=[r for r in observations if r['phase']==phase and r['team']==team]
            options=[]
            for seed in pool:
                if not seed['long_lived']:continue
                nearby=[r for r in pool if math.hypot(r['x']-seed['x'],r['y']-seed['y'])<=350]
                known=[r for r in nearby if r['complete_lifetime']]
                longs=[r for r in nearby if r['long_lived']]
                games=sorted({r['match_id'] for r in longs})
                if len(games)<3 or len(longs)/max(1,len(known))<.6 or statistics.median(r['duration'] for r in known)<300:continue
                options.append({'phase':phase,'team':team,'x':seed['x'],'y':seed['y'],'area':coarse_area(seed['x'],seed['y']),
                    'long_game_count':len(games),'long_count':len(longs),'known_count':len(known),'all_count':len(nearby),
                    'unknown_or_censored':len(nearby)-len(known),'median_seconds':round(statistics.median(r['duration'] for r in known),1),
                    'expired_count':sum(r['end_kind']=='expired' for r in longs),
                    'late_enemy_kill_count':sum(r['end_kind']=='killed' and r['killer_relation']=='enemy' for r in longs),
                    'late_unknown_kill_count':sum(r['end_kind']=='killed' and r['killer_relation']=='unknown' for r in longs),
                    'evidence':longs,'source_match_id':seed['match_id'],'source_ward_index':seed['ward_index']})
            chosen=[]
            for item in sorted(options,key=lambda r:(-r['long_game_count'],-r['long_count']/r['known_count'],-r['known_count'],r['x'],r['y'])):
                if any(math.hypot(item['x']-r['x'],item['y']-r['y'])<700 for r in chosen):continue
                item['label']=('R' if team==2 else 'D')+str(len(chosen)+1)
                chosen.append(item)
                if len(chosen)==3:break
            candidates+=chosen
    return {'schema_version':1,'frozen_at_utc':data['frozen_at_utc'],'match_count':len(data['matches']),
        'observations':observations,'candidates':candidates,'excluded':dict(excluded),
        'rules':{'phase_seconds':600,'long_seconds':300,'radius_world_units':350,'min_distinct_long_games':3,
                 'min_descriptive_long_fraction':.6,'median_min_seconds':300},
        'limitations':['Descriptive survival candidates, not proven difficult-to-deward locations or effectiveness.',
            'No survival-to-replay-end imputation for unknown deaths; no enemy opportunity/vision/control denominator.',
            'Expiry semantics and locations follow the pinned extractor; no new replay/frame verification.',
            'Nearby points can be on different terrain; the marked centre is an actual ward position, not a fabricated centroid.',
            'Killer relation uses unambiguous ward-placer team evidence only; other killers remain unknown.',
            'Candidates share games and are not an independent statistical success-rate estimate.']}


def render(result,output):
    from PIL import Image,ImageDraw,ImageFont
    font_path=Path('C:/Windows/Fonts/msyh.ttc')
    font=ImageFont.truetype(str(font_path),24) if font_path.exists() else ImageFont.load_default()
    parts=['<!doctype html><meta charset="utf-8"><title>每10分钟长期存活眼位</title>',
        '<style>body{font:16px sans-serif;background:#172127;color:#e5edf2;margin:24px}a{color:#8bd6b8}td,th{padding:9px;border-bottom:1px solid #43545a}svg{width:100%;min-width:950px}section{max-width:1400px}.map{overflow:auto;max-height:80vh}table{border-collapse:collapse}.note{color:#b7c8d0}</style>',
        '<h1>每10分钟：长期未被排的假眼候选</h1>',
        f'<p>冻结{result["match_count"]}场完整高分局，按放眼时间分组。只看假眼；每个点至少在3场比赛存活≥5分钟，已知完整寿命中长存活占比≥60%，寿命中位数≥5分钟。附近350游戏单位合并，标记是真实放眼点。</p>',
        '<p class="note">R=天辉放眼，D=夜魇放眼：你是天辉时关注D，你是夜魇时关注R。未知结束、比赛结束截尾不补成存活。长期未被排可能只是对方没经过或地图控制较好，不证明刁钻、难排或视野有效；底图标定待逐点核对。</p>']
    parts.append('<p><label>查看对象 <select id="perspective"><option value="all">双方眼位</option><option value="3">我方天辉：只看夜魇的眼</option><option value="2">我方夜魇：只看天辉的眼</option></select></label></p>')
    for phase in sorted({r['phase'] for r in result['observations']}):
        group=[r for r in result['observations'] if r['phase']==phase]
        chosen=[r for r in result['candidates'] if r['phase']==phase]
        parts.append(f'<section><h2>{phase*10}～{(phase+1)*10}分钟</h2><p>放眼{len(group)}条，寿命完整{sum(r["complete_lifetime"] for r in group)}条；达到重复证据门槛的点{len(chosen)}个。</p>')
        if not chosen:
            parts.append('<p>样本不足或未过门槛，不输出稳定点位。</p></section>');continue
        parts.append('<div class="map"><svg viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/>')
        image=Image.open(output/'map.jpg');image.thumbnail((1600,1600));draw=ImageDraw.Draw(image)
        for r in chosen:
            x,y=pixel(r['x'],r['y']);color='#65d792' if r['team']==2 else '#ed8181'
            label=html.escape(r['label']);tip=html.escape(f"{r['area']} · {r['long_game_count']}场长存活 · 中位{r['median_seconds']}秒")
            parts.append(f'<a data-team="{r["team"]}" href="#p{phase}-{label}"><circle cx="{x:.1f}" cy="{y:.1f}" r="11" fill="{color}" stroke="#111" stroke-width="3"><title>{tip}</title></circle><text x="{x+15:.1f}" y="{y+8:.1f}" fill="white" stroke="black" stroke-width="4" paint-order="stroke" font-size="28">{label}</text></a>')
            draw.ellipse((x-11,y-11,x+11,y+11),fill=color,outline='black',width=3)
            draw.text((x+15,y-14),r['label'],font=font,fill='white',stroke_width=3,stroke_fill='black')
        image.save(output/f'phase-{phase}.jpg',quality=91)
        parts.append('</svg></div><table><tr><th>地图标记</th><th>粗区域</th><th>长存活/寿命完整</th><th>重复比赛</th><th>寿命中位</th><th>结束未知/截尾</th><th>明细</th></tr>')
        for r in chosen:
            label=r['label'];details=[]
            for e in r['evidence']:
                details.append(f"比赛{e['match_id']} {stamp(e['placed'])}放 → {stamp(e['ended'])}结束，{e['duration']:.0f}秒，{e['end_kind']}，击杀归属{e['killer_relation']}")
            parts.append(f'<tr data-team="{r["team"]}" id="p{phase}-{label}"><td>{label}</td><td>{r["area"]}（{r["x"]:.0f},{r["y"]:.0f}）</td><td>{r["long_count"]}/{r["known_count"]}</td><td>{r["long_game_count"]}场</td><td>{r["median_seconds"]:.0f}秒</td><td>{r["unknown_or_censored"]}</td><td><details><summary>追溯记录</summary>'+''.join('<p>'+html.escape(d)+'</p>' for d in details)+'</details></td></tr>')
        parts.append('</table></section>')
    parts.append('<script>document.getElementById("perspective").onchange=e=>{for(const p of document.querySelectorAll("[data-team]"))p.style.display=e.target.value==="all"||p.dataset.team===e.target.value?"":"none"}</script>')
    (output/'report.html').write_text('\n'.join(parts),encoding='utf-8')


def main():
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('--input',type=Path,required=True);cli.add_argument('--map',type=Path,required=True);cli.add_argument('--output',type=Path,required=True);args=cli.parse_args()
    if args.output.exists():cli.error('Use a new output directory.')
    raw=args.input.read_bytes();data=json.loads(raw);result=review(data);result['source_sha256']=hashlib.sha256(raw).hexdigest()
    args.output.mkdir(parents=True);shutil.copy2(args.map,args.output/'map.jpg')
    (args.output/'review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    with (args.output/'observations.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(result['observations'][0]));writer.writeheader();writer.writerows(result['observations'])
    render(result,args.output)
    print(json.dumps({'matches':result['match_count'],'observations':len(result['observations']),'candidates':[{k:r[k] for k in ['phase','team','label','area','long_count','known_count','long_game_count','median_seconds']} for r in result['candidates']]},ensure_ascii=True,indent=2))


if __name__=='__main__':main()
