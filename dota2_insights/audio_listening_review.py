"""Local, context-visible listening notes, kept separate from frozen predictions."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import wave


VERDICTS = ('unreviewed', 'audible', 'not_audible', 'uncertain')
PHASES = ('unknown', 'cast', 'impact', 'sustain', 'end', 'mixed')
PURPOSES = ('post_prediction_replay_review_only',
            'posthoc_listening_only_never_template_training_or_accuracy_labels',
            'post_prediction_candidate_diversity_review_only')
CONTEXT_POLICIES = ('replay_or_GSI_context_visible_not_blind_not_independent',
                    'candidate_context_visible_not_blind_not_independent')


def sha(body):
    return hashlib.sha256(body).hexdigest()


def read_json(path, limit=4 * 1024**2):
    with Path(path).open('rb') as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError('listening_json_size_limit')
    return json.loads(body), body


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def utc():
    return datetime.now(timezone.utc).isoformat()


def clip_bytes(folder, filename, fingerprint):
    if (not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}\.wav', filename)
            or not isinstance(fingerprint, str) or not re.fullmatch(r'[a-f0-9]{64}', fingerprint)):
        raise ValueError('invalid_listening_clip_reference')
    path = (folder / filename).resolve()
    if path.parent != folder or not path.is_file():
        raise ValueError('listening_clip_outside_source')
    with path.open('rb') as stream:
        body = stream.read(2 * 1024**2 + 1)
    if len(body) > 2 * 1024**2 or sha(body) != fingerprint:
        raise ValueError('listening_clip_size_or_hash_mismatch')
    with wave.open(io.BytesIO(body), 'rb') as stream:
        frames, rate = stream.getnframes(), stream.getframerate()
        channels, width = stream.getnchannels(), stream.getsampwidth()
        if (not 1 <= channels <= 2 or width not in (1, 2, 3, 4) or not 8000 <= rate <= 192000
                or not 0 < frames / rate <= 10):
            raise ValueError('unsupported_listening_clip_format')
        if len(stream.readframes(frames)) != frames * channels * width:
            raise ValueError('truncated_listening_clip')
    return body, frames / rate


def build_listening_page(review_path, output):
    review_path, output = Path(review_path).resolve(), Path(output).resolve()
    value, source = read_json(review_path)
    if (not isinstance(value, dict) or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('purpose') not in PURPOSES
            or not re.fullmatch(r'[a-f0-9]{64}', str(value.get('prediction_sha256', '')))):
        raise ValueError('frozen_post_prediction_review_required')
    rows = value.get('casts') if value['purpose'] == PURPOSES[0] else value.get('clips')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 2000:
        raise ValueError('invalid_listening_source_rows')
    clips, bodies, seen, total = [], [], set(), 0
    for row_index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError('invalid_listening_source_row')
        if 'clip' not in row:
            continue
        if len(clips) >= 40 or row['clip'] in seen:
            raise ValueError('listening_clip_count_or_duplicate')
        body, seconds = clip_bytes(review_path.parent, row['clip'], row.get('clip_sha256'))
        total += len(body)
        if total > 64 * 1024**2:
            raise ValueError('listening_total_size_limit')
        skill, game_time = row.get('skill'), row.get('game_time_s')
        if not isinstance(skill, str) or not re.fullmatch(r'[a-z0-9_]{1,120}', skill):
            raise ValueError('invalid_listening_skill')
        if game_time is not None and (type(game_time) not in (int, float) or not math.isfinite(game_time)):
            raise ValueError('invalid_listening_game_clock')
        clip_id = f'clip-{len(clips) + 1:03d}'
        clips.append({'clip_id': clip_id, 'file': clip_id + '.wav', 'sha256': sha(body),
                      'duration_s': seconds, 'source_row': row_index, 'skill': skill,
                      'game_time_s': game_time})
        bodies.append(body)
        seen.add(row['clip'])
    if not clips or review_path.read_bytes() != source:
        raise ValueError('empty_or_changed_listening_source')
    # Validate all input before creating a new package; never overwrite source.
    output.mkdir(parents=True, exist_ok=False)
    manifest = {'kind': 'audio_listening_review', 'schema_version': 1, 'created_at_utc': utc(),
                'status': 'preparing', 'review_source_sha256': sha(source),
                'prediction_sha256': value['prediction_sha256'], 'match_id': value.get('match_id'),
                'context_policy': CONTEXT_POLICIES[1] if value['purpose'] == PURPOSES[2] else CONTEXT_POLICIES[0],
                'training_eligible': False, 'accuracy_verified': False,
                'builder_source_sha256': sha(Path(__file__).read_bytes()), 'clips': clips}
    write_json(output / 'manifest.json', manifest)
    for clip, body in zip(clips, bodies):
        (output / clip['file']).write_bytes(body)
    manifest['status'] = 'ready'
    completed = (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    binding = sha(completed)
    payload = json.dumps({'manifest_sha256': binding, 'clips': clips,
                          'context_policy': manifest['context_policy']}, ensure_ascii=False, allow_nan=False)
    payload = payload.replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    description = ('这是已冻结的音频候选分层抽样，未提供录像/GSI施法真值；请判断指定声音是否清楚可闻。'
                   if value['purpose'] == PURPOSES[2] else
                   '这是带录像/GSI上下文的辅助听审。请判断指定声音是否清楚可闻，不确定就保留“无法判断”。')
    (output / 'index.html').write_text(PAGE.replace('__PAYLOAD__', payload).replace('__DESCRIPTION__', description), encoding='utf-8')
    # Readiness is published only after every clip and the page exist.
    (output / 'manifest.json').write_bytes(completed)
    return output, len(clips)


def import_listening_notes(page, labels_path):
    page = Path(page).resolve()
    manifest, original = read_json(page / 'manifest.json')
    if (not isinstance(manifest, dict) or manifest.get('kind') != 'audio_listening_review'
            or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
            or manifest.get('status') != 'ready'
            or manifest.get('context_policy') not in CONTEXT_POLICIES
            or manifest.get('training_eligible') is not False or manifest.get('accuracy_verified') is not False):
        raise ValueError('invalid_listening_package')
    clips = manifest.get('clips')
    if not isinstance(clips, list) or not 1 <= len(clips) <= 40:
        raise ValueError('invalid_listening_package_clips')
    expected = {}
    for index, clip in enumerate(clips, 1):
        key = f'clip-{index:03d}'
        if not isinstance(clip, dict) or clip.get('clip_id') != key or clip.get('file') != key + '.wav':
            raise ValueError('invalid_listening_package_clip')
        clip_bytes(page, clip['file'], clip.get('sha256'))
        expected[key] = clip['sha256']
    value, label_body = read_json(labels_path, 1024**2)
    if (not isinstance(value, dict) or value.get('kind') != 'audio_listening_notes'
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('manifest_sha256') != sha(original)
            or value.get('context_policy') != manifest['context_policy']):
        raise ValueError('listening_notes_source_mismatch')
    rows = value.get('notes')
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError('listening_notes_count_mismatch')
    notes, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid_listening_note')
        key = row.get('clip_id')
        if not isinstance(key, str) or key not in expected or key in seen or row.get('clip_sha256') != expected[key]:
            raise ValueError('listening_note_clip_mismatch')
        verdict, phase = row.get('verdict'), row.get('phase')
        heard, comment, played = row.get('heard'), row.get('comment'), row.get('played')
        if (verdict not in VERDICTS or phase not in PHASES or type(played) is not bool
                or not isinstance(heard, str) or len(heard) > 200
                or not isinstance(comment, str) or len(comment) > 1000
                or (verdict != 'unreviewed' and not played)
                or (verdict != 'audible' and phase != 'unknown')):
            raise ValueError('invalid_listening_note_fields')
        seen.add(key)
        notes.append({'clip_id': key, 'clip_sha256': expected[key], 'verdict': verdict,
                      'phase': phase, 'heard': heard, 'comment': comment, 'played': played})
    counts = dict(Counter(row['verdict'] for row in notes))
    if counts.get('unreviewed', 0) == len(notes):
        raise ValueError('no_reviewed_listening_notes')
    if (page / 'manifest.json').read_bytes() != original:
        raise ValueError('listening_package_changed')
    labels_root = page / 'notes'
    labels_root.mkdir(exist_ok=True)
    import uuid
    output = labels_root / ('review-' + uuid.uuid4().hex[:12])
    output.mkdir()
    result = {'kind': 'audio_listening_notes_receipt', 'schema_version': 1, 'created_at_utc': utc(),
              'manifest_sha256': sha(original), 'submitted_json_sha256': sha(label_body),
              'importer_source_sha256': sha(Path(__file__).read_bytes()),
              'context_policy': manifest['context_policy'], 'training_eligible': False,
              'accuracy_verified': False, 'counts': counts, 'notes': notes,
              'limitations': ['Context-visible subjective notes, not independent blind truth.',
                             'Not audible is not proof that a skill was unused.',
                             'No accuracy, recall or false-positive rate inferred.']}
    write_json(output / 'notes.json', result)
    return output, counts


PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>声音复听</title>
<style>body{background:#18232e;color:#edf3f7;font:16px system-ui;margin:24px auto;max-width:960px;padding:0 16px}button,select,input,textarea{font:inherit;padding:8px}button{cursor:pointer}article{padding:18px;background:#243440;border-radius:10px;margin:16px 0}label{display:block;margin:12px 0}textarea{width:90%;height:60px}audio{width:100%}.muted{color:#b0bfca}header{position:sticky;top:0;background:#18232e;padding:12px 0}#notice{color:#ffe090}</style>
<header><h1>声音复听</h1><p>__DESCRIPTION__</p>
<p class="muted">片段不自动播放；判断不会修改原录音和识别结果，也不会自动加入训练或计算准确率。页面不自动保存，关闭前请导出。</p>
<button id="save">导出本次判断</button> <label style="display:inline">恢复上次导出 <input id="restore" type="file" accept=".json"></label><span id="progress"></span><p id="notice"></p></header><main id="clips"></main>
<script>const data=__PAYLOAD__;const policy=data.context_policy||'replay_or_GSI_context_visible_not_blind_not_independent';
const names={zuus_thundergods_wrath:'宙斯 · 雷神之怒',zuus_arc_lightning:'宙斯 · 弧形闪电',zuus_lightning_bolt:'宙斯 · 雷击',tidehunter_ravage:'潮汐猎人 · 毁灭',magnataur_reverse_polarity:'猛犸 · 两极反转',enigma_black_hole:'谜团 · 黑洞',skeleton_king_reincarnation:'骷髅王 · 绝冥再生',kunkka_ghostship:'昆卡 · 幽灵船',pudge_dismember:'帕吉 · 肢解',doom_bringer_infernal_blade:'末日使者 · 阎刃',legion_commander_press_the_attack:'军团指挥官 · 强攻',legion_commander_moment_of_courage:'军团指挥官 · 勇气之霎'};
const notes=data.clips.map(c=>({clip_id:c.clip_id,clip_sha256:c.sha256,verdict:'unreviewed',phase:'unknown',heard:'',comment:'',played:false}));
const controls=[];let restoreGeneration=0;function node(tag,text){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e}
function gameClock(time){if(typeof time!=='number'||!Number.isFinite(time))return '未知';const seconds=Math.floor(Math.abs(time));return (time<0?'-':'')+Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0')}
function progress(){document.getElementById('progress').textContent=' 已复听判断 '+notes.filter(n=>n.verdict!=='unreviewed').length+' / '+notes.length}
function choice(options){const select=node('select');options.forEach(([value,text])=>{const o=node('option',text);o.value=value;select.append(o)});return select}
data.clips.forEach((c,i)=>{const n=notes[i],article=node('article');article.append(node('h2',(i+1)+' / '+(names[c.skill]||c.skill)));article.append(node('p','录像/GSI旁证时间：'+gameClock(c.game_time_s)+' · 片段 '+c.duration_s.toFixed(1)+' 秒（旁证不等于声音可闻）'));
const audio=node('audio');audio.controls=true;audio.preload='none';audio.src=c.file;article.append(audio);
const verdict=choice([['unreviewed','尚未判断'],['audible','听到指定声音'],['not_audible','没听到指定声音'],['uncertain','无法判断']]);verdict.disabled=true;
const phase=choice([['unknown','阶段不确定'],['cast','施放'],['impact','命中'],['sustain','持续'],['end','结束'],['mixed','多个阶段/混合']]);phase.disabled=true;
const heard=node('input');heard.maxLength=200;heard.placeholder='还听到了什么？可不填';const comment=node('textarea');comment.maxLength=1000;comment.placeholder='例如：人声盖住、疑似普通雷击、音量很小';
function refresh(){verdict.value=n.verdict;verdict.disabled=!n.played;phase.value=n.phase;phase.disabled=n.verdict!=='audible';heard.value=n.heard;comment.value=n.comment}
audio.addEventListener('playing',()=>{n.played=true;verdict.disabled=false});verdict.onchange=()=>{n.verdict=verdict.value;if(n.verdict!=='audible')n.phase='unknown';refresh();progress()};phase.onchange=()=>n.phase=phase.value;heard.oninput=()=>n.heard=heard.value;comment.oninput=()=>n.comment=comment.value;
for(const [title,input] of [['指定声音判断（先点击播放）',verdict],['声音阶段',phase],['其他声音',heard],['备注',comment]]){const label=node('label',title+' ');label.append(input);article.append(label)}document.getElementById('clips').append(article);controls.push(refresh)});
document.getElementById('save').onclick=()=>{const payload={kind:'audio_listening_notes',schema_version:1,manifest_sha256:data.manifest_sha256,context_policy:policy,notes};const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)],{type:'application/json'}));const a=node('a');a.href=url;a.download='audio-listening-notes.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);document.getElementById('notice').textContent='已发起下载，请确认文件已保存；恢复时选择同一个导出文件。'};
document.getElementById('restore').onchange=async e=>{
const generation=++restoreGeneration;
try{
const f=e.target.files[0];if(!f)return;if(f.size>1048576)throw Error('文件过大');
const text=await f.text();if(generation!==restoreGeneration)return;
const v=JSON.parse(text);
if(!v||v.kind!=='audio_listening_notes'||v.schema_version!==1||v.manifest_sha256!==data.manifest_sha256||v.context_policy!==policy||!Array.isArray(v.notes)||v.notes.length!==notes.length)throw Error('不是本页的判断文件');
const seen=new Set();const restored=notes.map(n=>{
const r=v.notes.find(x=>x&&x.clip_id===n.clip_id);
if(!r||r.clip_sha256!==n.clip_sha256||seen.has(r.clip_id)||!['unreviewed','audible','not_audible','uncertain'].includes(r.verdict)||!['unknown','cast','impact','sustain','end','mixed'].includes(r.phase)||typeof r.played!=='boolean'||typeof r.heard!=='string'||r.heard.length>200||typeof r.comment!=='string'||r.comment.length>1000||(r.verdict!=='unreviewed'&&!r.played)||(r.verdict!=='audible'&&r.phase!=='unknown'))throw Error('判断内容不完整或不匹配');
seen.add(r.clip_id);return {clip_id:n.clip_id,clip_sha256:n.clip_sha256,verdict:r.verdict,phase:r.phase,heard:r.heard,comment:r.comment,played:r.played}});
restored.forEach((r,i)=>Object.assign(notes[i],r));controls.forEach(refresh=>refresh());progress();document.getElementById('notice').textContent='已恢复；会覆盖当前页面的未导出修改。';
}catch(err){if(generation===restoreGeneration)document.getElementById('notice').textContent='未恢复：'+err.message}
finally{if(generation===restoreGeneration)e.target.value=''}
};
window.addEventListener('beforeunload',e=>{if(notes.some(n=>n.played||n.heard||n.comment)){e.preventDefault();e.returnValue=''}});progress();</script></html>'''
