"""Personal match ward review from frozen outputs and saved self GSI context."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.review_long_lived_wards import pixel, stamp

HEROES={'npc_dota_hero_enchantress':'魅惑魔女','npc_dota_hero_venomancer':'剧毒术士','npc_dota_hero_magnataur':'猛犸',
        'npc_dota_hero_vengefulspirit':'复仇之魂'}


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024**2),b''):value.update(block)
    return value.hexdigest()


def public_roster(reference, match_id):
    """Explicit SHA-bound API roster when a full packet archive is unavailable."""
    root=(ROOT/'diagnostics').resolve();values=[]
    for key in ('match','heroes'):
        source=reference[key];path=(root/source['file']).resolve()
        if not path.is_relative_to(root) or path.stat().st_size>4*1024**2:
            raise ValueError('public_roster_source_outside_budget')
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=source['sha256']:
            raise ValueError('public_roster_source_changed')
        values.append(json.loads(content))
    receipt,heroes=values
    match=receipt.get('data')
    if not isinstance(match,dict) or str(match.get('match_id'))!=match_id:
        raise ValueError('public_roster_match_mismatch')
    names={r.get('id'):r.get('name') for r in heroes.values() if isinstance(r,dict)}
    roster={};teams=Counter();slots=set()
    players=match.get('players')
    if not isinstance(players,list) or len(players)!=10:
        raise ValueError('public_roster_requires_ten_players')
    for player in players:
        slot=player.get('player_slot');hero=names.get(player.get('hero_id'))
        if (type(slot) is not int or slot not in (*range(5),*range(128,133))
                or slot in slots or not isinstance(hero,str)
                or not re.fullmatch(r'npc_dota_hero_[a-z0-9_]+',hero) or hero in roster):
            raise ValueError('public_roster_hero_or_team_invalid')
        team=2 if slot<128 else 3;roster[hero]=team;teams[team]+=1;slots.add(slot)
    if teams!={2:5,3:5}:
        raise ValueError('public_roster_requires_five_per_team')
    return roster


def analyze(data,context):
    matches=[];phases={};allrows=[]
    for match in sorted(data['matches'],key=lambda r:r['match_id']):
        mid=match['match_id'];ctx=context['contexts'][mid]
        hero=max(ctx['heroes'],key=ctx['heroes'].get)
        sources=[ROOT/'diagnostics/replay-analysis'/mid/'full-1hz',
                 ROOT/'diagnostics/replay-review-20261016'/(mid+'-v2'),
                 ROOT/'diagnostics/replay-review-20261016'/mid,
                 ROOT/'diagnostics/replay-review-20261018'/mid]
        archive=None
        for candidate in sources:
            if not (candidate/'hero_samples.jsonl').exists():continue
            metadata=json.loads((candidate/'manifest.json').read_text(encoding='utf-8'))
            complete=(metadata.get('export_complete') is True or
                      metadata.get('stop_reason')=='replay_eof' and not metadata.get('parse_error') and
                      metadata.get('truncated_at_tick') is None)
            if metadata['source_sha256']==match['manifest']['source_sha256'] and complete:
                archive=candidate;break
        if archive is None:
            reference=(context.get('public_roster_fallbacks')or{}).get(mid)
            if not reference:raise ValueError('verified_roster_archive_missing_'+mid)
            if (match['manifest'].get('match_id')!=mid or match['manifest'].get('complete') is not True
                    or match['manifest'].get('source_unchanged_stat') is not True):
                raise ValueError('complete_ward_export_required_for_public_roster')
            roster=public_roster(reference,mid)
            roster_source='diagnostics/'+reference['match']['file']
            roster_digest=reference['match']['sha256']
            roster_basis='public_API_match_roster_not_replay_packet'
        else:
            manifest=json.loads((archive/'manifest.json').read_text());sample=archive/'hero_samples.jsonl'
            if digest(sample)!=manifest['files']['hero_samples.jsonl']['sha256']:raise ValueError('roster_samples_digest_mismatch')
            roster={}
            with sample.open(encoding='utf-8') as f:
                for _,line in zip(range(10),f):
                    r=json.loads(line);roster[r['hero_id']]=r['team']
            roster_source=str(archive.relative_to(ROOT))
            roster_digest=manifest['files']['hero_samples.jsonl']['sha256']
            roster_basis='verified_replay_packet_archive'
        own_team=roster[hero]
        if ctx['teams'] and set(ctx['teams'])!={ {2:'radiant',3:'dire'}[own_team]}:raise ValueError('self_team_conflict')
        end=match['manifest']['match_end_game_time_s'];rows=[]
        for index,w in enumerate(match['wards']):
            if w['ward_type']!='observer' or w.get('after_match_end'):continue
            t=w.get('placed_game_time_s')
            if t is None or t>=end:continue
            ended=w.get('ended_game_time_s');duration=w.get('duration_s')
            complete=(w['end_kind'] in ('expired','killed') and ended is not None and
                      ended<=end and duration is not None and 0<=duration<=361)
            killer_team=roster.get(w.get('killer'))
            relation='ally' if killer_team==w.get('team') else 'enemy' if killer_team in (2,3) else 'unknown'
            side='ally' if w.get('team')==own_team else 'enemy' if w.get('team') in (2,3) else 'unknown'
            r={'match_id':mid,'index':index,'phase':int(t//600) if t>=0 else -1,'placed':t,'ended':ended,
                'duration':duration,'complete_lifetime':complete,'x':w.get('x'),'y':w.get('y'),
                'placer':w.get('placer'),'killer':w.get('killer'),'team':w.get('team'),'side':side,
                'own_hero':w.get('placer')==hero,'end_kind':w['end_kind'],'killer_relation':relation,
                'long_lived':bool(complete and duration>=300 and not(w['end_kind']=='killed' and relation=='ally')),
                'fast_enemy_kill':bool(complete and duration<=60 and w['end_kind']=='killed' and relation=='enemy')}
            rows.append(r)
            if r['phase']>=0:
                summary=phases.setdefault(r['phase'],Counter())
                summary['observers']+=1
                if side=='enemy':summary['enemy_observers']+=1;summary['enemy_long']+=r['long_lived']
                if side=='ally':summary['ally_observers']+=1;summary['ally_fast']+=r['fast_enemy_kill']
                summary['personal_fast']+=r['own_hero'] and r['fast_enemy_kill']
        brief=context['economy_briefs'].get(mid)
        if not brief:
            p=ROOT/'diagnostics/delivery-review-20261014-evening'/mid/'game-brief.json'
            b=json.loads(p.read_text(encoding='utf-8'));own=b['self_economy'];brief={
                'file':str(p.relative_to(ROOT/'diagnostics')),'samples':own['samples'],
                'display_mismatch_count':own['display_mismatch_count'],'observations':b['economy']['observations'],
                'issue_categories':b['economy']['issue_categories']}
        matches.append({'match_id':mid,'own_team':own_team,'hero_id':hero,'hero_zh':HEROES.get(hero,hero),
            'duration':end,'observers':len(rows),'enemy_long_count':sum(r['side']=='enemy' and r['long_lived'] for r in rows if r['phase']>=0),
            'ally_fast_count':sum(r['side']=='ally' and r['fast_enemy_kill'] for r in rows if r['phase']>=0),
            'personal_observers':sum(r['own_hero'] for r in rows),'personal_fast_count':sum(r['own_hero'] and r['fast_enemy_kill'] for r in rows if r['phase']>=0),
            'economy':brief,'roster_source':roster_source,'roster_basis':roster_basis,
            'roster_sha256':roster_digest,'source_sha256':match['manifest']['source_sha256']})
        allrows+=rows
    return {'schema_version':1,'frozen_at_utc':data['frozen_at_utc'],'matches':matches,
        'phases':{str(k):dict(v) for k,v in sorted(phases.items())},'wards':allrows,
        'limitations':['Long lifetime does not prove difficult dewarding or useful vision.',
            'Unknown and match-end-censored lifetimes are not completed observations.',
            'Own identity uses saved GSI and the explicitly recorded roster basis: same-DEM packets or SHA-bound public match API.',
            'Economy results reuse prior fixed match reports; no new image/recognition validation.',
            'Map calibration remains to be independently checked against these matches.']}


def render(result,folder):
    payload=json.dumps(result,ensure_ascii=False).replace('<','\\u003c')
    page='''<!doctype html><meta charset="utf-8"><title>本人眼位复查</title>
<style>body{font:16px sans-serif;background:#172127;color:#e4edf2;margin:24px}select{font:inherit;margin:8px;padding:6px}table{border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #43555f}a{color:#85d8ac}.map{overflow:auto;max-height:80vh;max-width:1400px}svg{width:100%;min-width:1100px}tr:target{background:#4b5a61}</style>
<h1>本人眼位：对手长存活眼与己方快速被排眼</h1><p>按本人GSI英雄和报告标注的阵容来源确定己方；来源为同DEM导出或SHA绑定的公开比赛阵容。红=对手、绿=己方；地图标号可点击跳转记录。长存活≥5分钟，不等于难排或有效视野；结束未知/比赛结束截尾不补成存活。</p>
<label>比赛<select id="match"></select></label><label>放眼时段<select id="phase"><option value="all">全场（含赛前）</option><option value="-1">赛前</option><option value="0">0～10分钟</option><option value="1">10～20分钟</option><option value="2">20～30分钟</option><option value="3">30～40分钟</option><option value="4">40～50分钟</option><option value="5">50～60分钟</option><option value="6">60～70分钟</option></select></label>
<label>查看<select id="kind"><option value="enemy-long">对手长存活假眼</option><option value="ally-fast">己方≤60秒被对手排掉</option><option value="personal">本人放置的假眼</option><option value="all">全部假眼</option></select></label><p id="info"></p>
<div class="map"><svg id="map" viewBox="0 0 1600 1505.9"><image href="map.jpg" width="1600" height="1505.9"/><g id="points"></g></svg></div>
<table><thead><tr><th>标记</th><th>放眼</th><th>结束</th><th>寿命</th><th>阵营/放眼者</th><th>结束情况</th><th>击杀者</th></tr></thead><tbody id="rows"></tbody></table><h2>经济固定复查</h2><p id="economy"></p><p>沿用各局既有简报与原图复核，不把同包记录零不一致当成全屏展示验收或装备准确率。旧问题包括升级灯过渡误归属、灰化莲花/榴弹混淆及高价冷却装备未知；完整结论见项目既有复盘。</p>
<script>const data=PAYLOAD,match=document.getElementById('match'),phase=document.getElementById('phase'),kind=document.getElementById('kind'),points=document.getElementById('points'),table=document.getElementById('rows'),ns='http://www.w3.org/2000/svg';for(const m of [...data.matches].reverse()){const o=document.createElement('option');o.value=m.match_id;o.textContent=m.match_id+' · '+m.hero_zh+' · '+(m.own_team===2?'天辉':'夜魇');match.append(o)}function time(v){if(v===null||v===undefined)return '未知';return(v<0?'-':'')+Math.floor(Math.abs(v)/60)+':'+String(Math.floor(Math.abs(v))%60).padStart(2,'0')}function svg(tag,attrs){const p=document.createElementNS(ns,tag);for(const[k,v]of Object.entries(attrs))p.setAttribute(k,v);return p}function render(){const m=data.matches.find(r=>r.match_id===match.value);const rows=data.wards.filter(r=>r.match_id===match.value&&(phase.value==='all'||String(r.phase)===phase.value)&&(kind.value==='all'||kind.value==='enemy-long'&&r.side==='enemy'&&r.long_lived||kind.value==='ally-fast'&&r.side==='ally'&&r.fast_enemy_kill||kind.value==='personal'&&r.own_hero));points.replaceChildren();table.replaceChildren();document.getElementById('info').textContent='本局对手长存活 '+m.enemy_long_count+'；己方快速被排 '+m.ally_fast_count+'；其中本人 '+m.personal_fast_count+'。当前筛选 '+rows.length+' 条。';document.getElementById('economy').textContent='原简报：本人记录 '+m.economy.samples+' 条，保存的不一致 '+m.economy.display_mismatch_count+'；装备观测 '+m.economy.observations+' 条。';rows.forEach((r,i)=>{if(r.x!==null&&r.y!==null){const x=((8878-8356)/2+(r.x-7487)/18322*8356)/8878*1600,y=(1-(r.y-7693)/18322)*8356/8878*1600;const a=svg('a',{href:'#ward-'+i});a.append(svg('circle',{cx:x,cy:y,r:9,fill:r.side==='enemy'?'#ed8181':'#65d792',stroke:'black','stroke-width':3}));const label=svg('text',{x:x+13,y:y+7,fill:'white',stroke:'black','stroke-width':4,'paint-order':'stroke','font-size':25});label.textContent=i+1;a.append(label);points.append(a)}const tr=document.createElement('tr');tr.id='ward-'+i;const status=r.complete_lifetime?({expired:'提取器标记到期',killed:'被击杀'}[r.end_kind]||r.end_kind):'未知/截尾';for(const value of[i+1,time(r.placed),time(r.ended),r.complete_lifetime?Math.round(r.duration)+'秒':'未完整确认',(r.side==='enemy'?'对手':'己方')+' '+r.placer,status,r.killer||'—']){const td=document.createElement('td');td.textContent=value;tr.append(td)}table.append(tr)})}match.onchange=render;phase.onchange=render;kind.onchange=render;render();</script>'''.replace('PAYLOAD',payload)
    (folder/'report.html').write_text(page,encoding='utf-8')


def main():
    cli=argparse.ArgumentParser(description=__doc__);cli.add_argument('--input',type=Path,required=True);cli.add_argument('--context',type=Path,required=True);cli.add_argument('--map',type=Path,required=True);cli.add_argument('--output',type=Path,required=True);args=cli.parse_args()
    if args.output.exists():cli.error('Use a new output directory.')
    data=json.loads(args.input.read_text());context=json.loads(args.context.read_text(encoding='utf-8'))
    result=analyze(data,context);result['source_sha256']=digest(args.input);result['context_sha256']=digest(args.context)
    args.output.mkdir(parents=True);shutil.copy2(args.map,args.output/'map.jpg')
    (args.output/'review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');render(result,args.output)
    print(json.dumps({'matches':[{k:m[k] for k in ['match_id','hero_zh','own_team','enemy_long_count','ally_fast_count','personal_fast_count','personal_observers']} for m in result['matches']],'phases':result['phases']},ensure_ascii=True,indent=2))


if __name__=='__main__':main()
