"""Create a self-contained map report from explicit completed ward exports."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.replay_holdout import refuse_locked_match
from dota2_insights.ward_phase_profile import analyze
from dota2_insights.ward_map_reference import load as load_map, project, MAP_SHA256
from dota2_insights.analysis_batch_registry import version, decoded


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    group = cli.add_mutually_exclusive_group(required=True)
    group.add_argument('--folder', type=Path, action='append')
    group.add_argument('--snapshot', type=Path, help='An existing deduplicated frozen snapshot.')
    cli.add_argument('--map', type=Path, help='Optional pinned 7.41 illustrative background.')
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    if args.output.exists() or not args.output.resolve().is_relative_to(ROOT/'diagnostics'):
        cli.error('new diagnostics output required')
    if len(args.folder or []) > 256:
        cli.error('at most 256 explicit matches')
    sources, inputs, manifest_hashes = [], [], {}
    if args.snapshot:
        with args.snapshot.open('rb') as stream:
            raw = stream.read(32*1024**2+1)
        if len(raw) > 32*1024**2:
            raise ValueError('ward_snapshot_budget')
        frozen = json.loads(raw)
        if (frozen.get('kind') != 'frozen_verified_ward_exports'
                or type(frozen.get('schema_version')) is not int or frozen['schema_version'] != 1
                or frozen.get('raw_replays_transferred') is not False
                or not isinstance(frozen.get('matches'), list) or not 1 <= len(frozen['matches']) <= 256):
            raise ValueError('ward_snapshot_kind')
        inputs.append((args.snapshot, raw))
        for entry in frozen['matches']:
            _, identity, manifest, clean = version(entry)
            sources.append((manifest, decoded(clean['wards_base64'], 5*1024**2)))
            manifest_hashes[identity['match_id']] = identity['manifest_sha256']
    for folder in args.folder or []:
        refuse_locked_match(folder.name)
        mraw = (folder/'manifest.json').read_bytes()
        if len(mraw) > 256*1024:
            raise ValueError('ward_manifest_budget')
        manifest = json.loads(mraw)
        refuse_locked_match(str(manifest.get('match_id')))
        with (folder/'wards.json').open('rb') as stream:
            raw = stream.read(2*1024**2+1)
        if len(raw) > 2*1024**2:
            raise ValueError('ward_payload_budget')
        sources.append((manifest, raw))
        manifest_hashes[str(manifest.get('match_id'))] = hashlib.sha256(mraw).hexdigest()
        inputs.extend([(folder/'manifest.json', mraw), (folder/'wards.json', raw)])
    result = analyze(sources)
    display_height = round(8356/8878*1600)
    map_url = ''
    if args.map:
        map_raw, image = load_map(args.map)
        inputs.append((args.map, map_raw))
        import io
        buffer = io.BytesIO()
        image.resize((1600, display_height)).save(buffer, format='JPEG', quality=90)
        map_url = 'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode()
    for row in result['rows']:
        row['map_x'], row['map_y'] = project(row['x'], row['y'])
    for s in result['strata']:
        for h in s['hotspots']:
            h['map_x'], h['map_y'] = project(h['x'], h['y'])
    result['input_manifest_sha256'] = manifest_hashes
    if args.snapshot:
        result['input_snapshot_sha256'] = hashlib.sha256(inputs[0][1]).hexdigest()
    payload = json.dumps(result, ensure_ascii=False).replace('<', '\\u003c')
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>眼位时间与位置</title>
<style>body{background:#14202a;color:#e5edf4;font:16px system-ui;margin:24px}select{font:inherit;margin:8px;padding:6px}main{display:grid;grid-template-columns:minmax(480px,1fr) minmax(400px,.8fr);gap:20px}svg{width:100%;background:#080e12}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid #425363;text-align:left}details{margin:12px 0}.note{color:#ffce77}button{font:inherit}tr:target{background:#364954}@media(max-width:950px){main{display:block}}</style>
<h1>职业眼位：什么时候，插哪里</h1><p>绿色假眼，红色真眼；白圈是256世界单位的邻近分组，<b>不是视野范围</b>。</p>
<p class="note">可选底图只用于声明 7.41 的场次。地图版本未知时按单场显示坐标示意，地图落位仍待独立核验。</p>
<label>来源组<select id="cohort"></select></label><label>时段<select id="phase"></select></label>
<label>阵营<select id="team"><option value="2">天辉</option><option value="3">夜魇</option></select></label>
<label>类型<select id="kind"><option value="observer">假眼</option><option value="sentry">真眼</option></select></label>
<p id="population"></p><p>实心点表示完整时段放置，橙色空心点表示尾段参考。邻域可能重叠。</p>
<main><svg viewBox="0 0 1600 HEIGHT"><image id="background" href="MAP" width="1600" height="HEIGHT"/><g id="points"></g></svg>
<section><table><thead><tr><th>编号</th><th>涉及比赛/完整比赛</th><th>放置次数</th><th>结束记录</th></tr></thead><tbody id="table"></tbody></table>
<details open><summary>本页来源与缺口</summary><div id="sources"></div></details><div id="members"></div></section></main>
<script>const D=PAYLOAD,NS='http://www.w3.org/2000/svg',C=document.getElementById('cohort'),P=document.getElementById('phase'),T=document.getElementById('team'),K=document.getElementById('kind');
function node(tag,text){let e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e}function svg(tag,attrs){let e=document.createElementNS(NS,tag);for(let[k,v]of Object.entries(attrs))e.setAttribute(k,v);return e}
function clock(t){let q=Math.floor(Math.abs(t));return (t<0?'-':'')+Math.floor(q/60)+':'+String(q%60).padStart(2,'0')}
for(let c of D.cohorts){let ms=D.matches.filter(m=>m.cohort_id===c.id);let label=c.metadata.map_version===null?ms[0].match_id+' · 地图版本未知':c.id.slice(0,6)+' · '+ms.length+'盘 · 地图版本 '+c.metadata.map_version;let o=node('option',label);o.value=c.id;C.append(o)}
function phases(){P.replaceChildren();for(let p of [...new Set(D.strata.filter(s=>s.cohort_id===C.value).map(s=>s.phase))]){let o=node('option',p===-1?'开局前（参考）':p*10+'–'+(p+1)*10+'分钟');o.value=p;P.append(o)}P.value=0;draw()}
function draw(){let s=D.strata.find(s=>s.cohort_id===C.value&&s.phase===+P.value&&s.team===+T.value&&s.ward_type===K.value);if(!s)return;
let cohort=D.cohorts.find(c=>c.id===s.cohort_id),unknownMap=cohort.metadata.map_version===null;
let background=document.getElementById('background');background.style.display=background.getAttribute('href')&&cohort.metadata.map_version==='7.41'?'':'none';
let frequency=s.frequency_match_ids.length?'频率分母 '+s.frequency_match_ids.length+' 盘。':unknownMap?'地图版本未知，仅提供单场描述。':'本时段无可比频率分母。';
document.getElementById('population').textContent='完整时段 '+s.full_match_ids.length+' 盘，未打满 '+s.partial_match_ids_reference_only.length+' 盘，此前已结束 '+s.ended_before_phase_ids.length+' 盘。完整时段放置 '+s.placements_full+' 次，尾段参考 '+s.placements_partial_reference_only+' 次。'+frequency;
let g=document.getElementById('points'),tb=document.getElementById('table'),sr=document.getElementById('sources'),md=document.getElementById('members');g.replaceChildren();tb.replaceChildren();sr.replaceChildren();md.replaceChildren();
for(let [name,ids] of [['完整时段',s.full_match_ids],['尾段参考',s.partial_match_ids_reference_only],['频率分母',s.frequency_match_ids],['阵营缺失，排除出该类型频率',s.unattributable_full_match_ids]])sr.append(node('p',name+' '+(ids.join('、')||'—')));
let unassigned=D.unassigned_team.filter(r=>r.cohort_id===s.cohort_id&&r.phase===s.phase&&r.ward_type===s.ward_type);if(unassigned.length)sr.append(node('p','阵营未知放置 '+unassigned.length+' 次。'));
let color=s.ward_type==='observer'?'#39ed78':'#ff5252';let rows=D.rows.filter(r=>r.cohort_id===s.cohort_id&&r.phase===s.phase&&r.team===s.team&&r.ward_type===s.ward_type);
for(let r of rows){let full=s.full_match_ids.includes(r.match_id),e=svg('circle',{cx:r.map_x,cy:r.map_y,r:6,fill:full?color:'none',stroke:full?'black':'#ffbc55','stroke-width':2});let title=svg('title',{});title.textContent=r.id+' · '+clock(r.placed_game_time_s)+(full?'':' · 尾段参考');e.append(title);g.append(e)}
s.hotspots.forEach((h,i)=>{let a=svg('a',{href:'#spot-'+i});a.append(svg('circle',{cx:h.map_x,cy:h.map_y,r:20,fill:'none',stroke:'white','stroke-width':3}));let tx=svg('text',{x:h.map_x+20,y:h.map_y-15,fill:color,stroke:'black','stroke-width':5,'paint-order':'stroke','font-size':30});tx.textContent=i+1;a.append(tx);g.append(a);let tr=node('tr');tr.id='spot-'+i;for(let v of [i+1,h.support_matches+'/'+(h.full_matches||'—'),h.placements,'被击杀 '+(h.end_kinds.killed||0)+' / 到期 '+(h.end_kinds.expired||0)+' / 其他 '+Object.entries(h.end_kinds).filter(([k])=>!['killed','expired'].includes(k)).reduce((n,[k,v])=>n+v,0)])tr.append(node('td',v));tb.append(tr);
let d=node('details'),summary=node('summary','位置 '+(i+1)+'：查看每次放置');d.append(summary);for(let id of h.members){let r=D.rows.find(r=>r.id===id);d.append(node('p',id+' · '+clock(r.placed_game_time_s)+' · '+(r.end_kind??'unknown')+' · 原始坐标 '+r.x.toFixed(1)+','+r.y.toFixed(1)))}md.append(d)});if(!s.hotspots.length)tb.append(node('tr','本时段没有重复邻域；不表示没有放眼。'))}
C.onchange=phases;P.onchange=T.onchange=K.onchange=draw;phases();</script></html>'''.replace('HEIGHT', str(display_height)).replace('MAP', map_url).replace('PAYLOAD', payload)
    if any(path.read_bytes() != raw for path, raw in inputs):
        raise ValueError('ward_phase_input_changed')
    args.output.mkdir(parents=True)
    (args.output/'profile.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    (args.output/'report.html').write_text(page, encoding='utf-8')
    (args.output/'run-summary.json').write_text(json.dumps({'status': 'complete', 'matches': len(result['matches']),
        'placements': len(result['rows']), 'excluded': result['excluded'], 'map_sha256': MAP_SHA256 if args.map else None,
        'report_sha256': hashlib.sha256(page.encode()).hexdigest(), 'current_map_certified': False}, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'matches': len(result['matches']), 'rows': len(result['rows'])}))


if __name__ == '__main__':
    main()
