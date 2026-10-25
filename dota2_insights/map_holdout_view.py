"""Offline native-pixel annotation, without reference predictions or network."""
import json
from pathlib import Path


def write_view(destination, plan, template):
    payload = json.dumps({'plan': plan, 'observations': template}, ensure_ascii=False).replace('<', '\\u003c')
    text = '''<!doctype html><meta charset="utf-8"><title>非塔地标观察</title>
<style>body{background:#19212b;color:#eee;font:16px sans-serif;margin:24px}select,input,textarea,button{font:inherit;margin:5px;padding:5px}canvas{image-rendering:pixelated;max-width:95vw;outline:1px solid #889}label{display:inline-block}textarea{width:650px;max-width:90vw}#summary{white-space:pre-wrap}</style>
<h1>非塔地标观察 · 不显示预测点</h1>
<p>先独立辨认地标，再点图取中心；坐标为原图像素。辨认不了、没有独立图标或被遮挡，请选“无法判断”。单击不证明身份，也不认证世界坐标。</p>
<label>观察者<input id="reviewer"></label><br>
<label>资料<select id="case"></select></label><label>地标<select id="point"></select></label><label>原帧<select id="frame"></select></label><br>
<canvas id="map"></canvas><br>
<label>状态<select id="status"><option value="pending">待观察</option><option value="measured">已量取</option><option value="unjudgeable">无法判断</option></select></label>
<label><input id="identity" type="checkbox">已独立确认对应地标</label>
<label>测量不确定度（原图像素）<input id="uncertainty" type="number" min="0" step="0.25" value="0.5" style="width:90px"></label>
<p id="xy"></p><textarea id="reason" rows="3" placeholder="说明辨认依据，或无法判断的原因；不要抄预测位置"></textarea><br>
<button id="save">保存本项</button><button id="download">下载观察JSON</button><p id="message"></p><pre id="summary"></pre>
<script type="application/json" id="data">PAYLOAD</script><script>
const D=JSON.parse(document.getElementById('data').textContent),P=D.plan,O=D.observations,$=id=>document.getElementById(id);
let current=null,xy=null,frame=null,loadSerial=0;
function option(el,value,label){const e=document.createElement('option');e.value=value;e.textContent=label;el.append(e)}
for(const c of P.cases)option($('case'),c,c);
for(const p of P.points)option($('point'),p.id,p.category+' · '+p.name+' · '+p.id);
function summary(){const counts={pending:0,measured:0,unjudgeable:0};for(const r of O.rows)counts[r.status]++;$('summary').textContent='已量取 '+counts.measured+'，无法判断 '+counts.unjudgeable+'，待观察 '+counts.pending+'。仅是观察进度，尚未比较。'}
function draw(){const serial=++loadSerial;frame=P.frames.find(f=>f.case===$('case').value&&String(f.frame_index)===$('frame').value);const img=new Image();img.onload=()=>{if(serial!==loadSerial)return;const canvas=$('map');canvas.width=img.width;canvas.height=img.height;canvas.style.width=(img.width*2)+'px';canvas.style.height='auto';canvas.getContext('2d').drawImage(img,0,0);if(xy){const c=canvas.getContext('2d');c.strokeStyle='#fff';c.beginPath();c.arc(xy[0],xy[1],3,0,Math.PI*2);c.stroke()}};img.src=frame.image;$('xy').textContent=xy?'原图坐标：'+xy.join(', '):'尚未量取坐标';}
function chooseCase(){const fs=P.frames.filter(f=>f.case===$('case').value);$('frame').replaceChildren();for(const f of fs)option($('frame'),f.frame_index,f.frame_index);chooseRow()}
function chooseRow(){current=O.rows.find(r=>r.case===$('case').value&&r.point_id===$('point').value);$('status').value=current.status;$('identity').checked=current.identity_confirmed;xy=current.pixel_xy;const f=current.frame_index;if(f!==null)$('frame').value=String(f);$('uncertainty').value=current.uncertainty_px??.5;$('reason').value=current.reason;draw();summary()}
$('case').onchange=chooseCase;$('point').onchange=chooseRow;$('frame').onchange=()=>{xy=null;$('status').value='pending';$('identity').checked=false;draw()};
$('map').onclick=e=>{const canvas=$('map'),r=canvas.getBoundingClientRect();xy=[Math.round((e.clientX-r.left)*canvas.width/r.width*4)/4,Math.round((e.clientY-r.top)*canvas.height/r.height*4)/4];if(xy.some((v,i)=>v<0||v>=frame.size[i])){xy=null;return}$('status').value='measured';draw()};
$('save').onclick=()=>{const status=$('status').value,u=Number($('uncertainty').value),reason=$('reason').value.trim();if(status!=='pending'&&(!reason||!$('reviewer').value.trim())){$('message').textContent='请填写观察者与依据/无法判断原因。';return}if(status==='measured'&&(!xy||!$('identity').checked||!Number.isFinite(u)||u<0)){$('message').textContent='已量取需要坐标、独立身份确认和有效不确定度。';return}Object.assign(current,{status,frame_index:status==='pending'?null:frame.frame_index,pixel_xy:status==='measured'?xy:null,identity_confirmed:status==='measured'&&$('identity').checked,uncertainty_px:status==='measured'?u:null,reason});O.reviewer=$('reviewer').value.trim();$('message').textContent='已记在本页内存，请下载JSON保存在本机；关闭页面不会自动写文件。';summary()};
$('download').onclick=()=>{O.reviewer=$('reviewer').value.trim();const url=URL.createObjectURL(new Blob([JSON.stringify(O,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download='map-holdout-observations.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
chooseCase();
</script>'''.replace('PAYLOAD', payload)
    with Path(destination).open('x', encoding='utf-8') as stream:
        stream.write(text)
