"""Descriptive, local-reference crop similarity; no item acceptance or training."""
import base64
from collections import defaultdict
from html import escape
from io import BytesIO
import json
from pathlib import Path
import re

from .postgame_store import bounded, sha
from .replay_holdout import refuse_locked_match

ROLES = ('known','first_unknown','last_unknown','next_known')


def duplication_summary(results):
    """Explicit evidence units, without collapsing distinct capture rows or roles."""
    source_rows, crops, pairs = set(), set(), set()
    aliases, crop_sources = defaultdict(list), defaultdict(set)
    for r in results:
        source = (r['package_id'], r['query_evidence']['line'], r['query_evidence']['line_sha256'])
        crop = r['query_crop_sha256']
        source_rows.add(source); crops.add(crop)
        pairs.add((r['reference_crop_sha256'], crop))
        aliases[(source, r['episode'], crop)].append(r['role'])
        crop_sources[crop].add(source)
    return {'query_role_rows': len(results), 'distinct_source_rows': len(source_rows),
            'distinct_crop_bytes': len(crops), 'distinct_reference_query_byte_pairs': len(pairs),
            'same_episode_source_crop_role_aliases': [
                {'package_id': source[0], 'line': source[1], 'line_sha256': source[2],
                 'episode': episode, 'crop_sha256': crop, 'roles': roles}
                for (source, episode, crop), roles in sorted(aliases.items()) if len(roles) > 1],
            'same_crop_bytes_across_source_rows': [
                {'crop_sha256': crop, 'source_rows': len(sources)}
                for crop, sources in sorted(crop_sources.items()) if len(sources) > 1],
            'independent_trials_verified': False,
            'meaning': 'source rows, byte identities and role aliases are different units; none proves item truth or statistical independence'}


def compare(reference, target):
    import cv2
    import numpy as np
    from PIL import Image
    arrays = []
    for raw in (reference,target):
        with Image.open(BytesIO(raw)) as im:
            if not (16 <= im.width <= 512 and 16 <= im.height <= 512):
                raise ValueError('item_stability_crop_dimensions')
            rgb = np.asarray(im.convert('RGB'))
        gray = cv2.cvtColor(cv2.resize(rgb,(96,72),interpolation=cv2.INTER_LINEAR),cv2.COLOR_RGB2GRAY)
        arrays.append(gray.astype('float32'))
    a,b=arrays
    if float(a.std()) < 1 or float(b.std()) < 1:
        return {'aligned_ncc':None,'unshifted_ncc':None,'unshifted_inner_ncc':None,'alignment_gain':None,
                'best_shift_xy':None,'reason':'insufficient_variance'}
    # Fixed 8-pixel registration search on a normalized 96x72 crop.
    # The inner patch is not an item/clock mask; score cannot prove identity.
    patch=a[8:-8,8:-8]
    if float(patch.std()) < 1:
        return {'aligned_ncc':None,'unshifted_ncc':None,'unshifted_inner_ncc':None,'alignment_gain':None,
                'best_shift_xy':None,'reason':'insufficient_inner_variance'}
    result=cv2.matchTemplate(b,patch,cv2.TM_CCOEFF_NORMED)
    _,peak,_,position=cv2.minMaxLoc(result)
    plain=float(np.corrcoef(a.ravel(),b.ravel())[0,1])
    return {'aligned_ncc':float(peak),'unshifted_ncc':plain,'unshifted_inner_ncc':float(result[8,8]),
            'alignment_gain':float(peak)-float(result[8,8]),
            'best_shift_xy':[position[0]-8,position[1]-8],'reason':None}


def probe(evidence_paths, output):
    output=Path(output)
    if output.exists():raise ValueError('item_stability_new_output_required')
    if not 1 <= len(evidence_paths) <= 8:raise ValueError('item_stability_evidence_budget')
    inputs, groups, unavailable, seen_packages = [], [], [], set()
    for source in evidence_paths:
        source=Path(source);raw=bounded(source,4*1024**2);m=json.loads(raw)
        if (m.get('kind')!='equipment_continuity_evidence' or type(m.get('schema_version')) is not int
                or m['schema_version']!=1 or m.get('source_verified_before_and_after') is not True
                or not re.fullmatch('[a-f0-9]{64}',str(m.get('equipment_package_id')))):
            raise ValueError('item_stability_prepared_evidence_required')
        refuse_locked_match(str(m['match_id']))
        if m['equipment_package_id'] in seen_packages:raise ValueError('item_stability_duplicate_package')
        seen_packages.add(m['equipment_package_id'])
        inputs.append((source,raw));entries=[];seen=set()
        if not isinstance(m.get('entries'),list) or len(m['entries'])>240:
            raise ValueError('item_stability_entry_budget')
        for e in m['entries']:
            key=e['episode_index'],e['role']
            if (type(key[0]) is not int or not 0<=key[0]<60 or key[1] not in ROLES or key in seen):
                raise ValueError('item_stability_unique_role_required')
            seen.add(key)
            if e['status']!='prepared_for_visual_review':
                unavailable.append({'package_id':m['equipment_package_id'],'episode':key[0],'role':key[1]})
                continue
            relative=e.get('image','')
            if not re.fullmatch(r'crops/[0-9]{3}-(known|first_unknown|last_unknown|next_known)\.png',relative):
                raise ValueError('item_stability_crop_path')
            path=source.parent/relative
            if not path.resolve().is_relative_to(source.parent.resolve()):raise ValueError('item_stability_crop_escape')
            crop=bounded(path,2*1024**2)
            if sha(crop)!=e.get('crop_sha256'):raise ValueError('item_stability_crop_changed')
            evidence=e.get('evidence')
            if (not isinstance(evidence,dict) or type(evidence.get('line')) is not int or evidence['line']<1
                    or not re.fullmatch('[a-f0-9]{64}',str(evidence.get('line_sha256')))):
                raise ValueError('item_stability_source_row_identity')
            inputs.append((path,crop));entries.append({**e,'raw':crop})
        groups.append((m,entries))
    results, previews = [], []
    for m,entries in groups:
        refs=[e for e in entries if e['role']=='known']
        by_episode={e['episode_index']:e for e in refs}
        for q in entries:
            if q['role'] not in ('first_unknown','last_unknown'):continue
            ref=by_episode.get(q['episode_index'])
            if ref is None:
                unavailable.append({'package_id':m['equipment_package_id'],'episode':q['episode_index'],'role':q['role'],'reason':'no_known_reference'})
                continue
            if (q['hero_id']!=ref['hero_id'] or q['slot_id']!=ref['slot_id']
                    or q['last_known_item']!=ref['last_known_item'] or ref['item_id']!=ref['last_known_item']):
                raise ValueError('item_stability_reference_identity')
            controls=[];control_seen=set()
            for other in refs:
                key=other['last_known_item'],other['crop_sha256']
                if other['last_known_item']==ref['last_known_item'] or key in control_seen:continue
                control_seen.add(key)
                score=compare(other['raw'],q['raw'])
                if score['aligned_ncc'] is not None:
                    controls.append({'recorded_item':other['last_known_item'],'crop_sha256':other['crop_sha256'],**score})
            controls.sort(key=lambda r:(-r['aligned_ncc'],r['recorded_item'],r['crop_sha256']))
            same=compare(ref['raw'],q['raw'])
            result={'package_id':m['equipment_package_id'],'match_id':m['match_id'],'episode':q['episode_index'],
                    'hero_id':q['hero_id'],'slot_id':q['slot_id'],'role':q['role'],'recorded_item':ref['last_known_item'],
                    'reference_evidence':ref['evidence'],'query_evidence':q['evidence'],
                    'reference_crop_sha256':ref['crop_sha256'],'query_crop_sha256':q['crop_sha256'],
                    'reference_dimensions':ref.get('client_rect',[None,None,None,None])[2:],
                    'query_dimensions':q.get('client_rect',[None,None,None,None])[2:],
                    'similarity':same,'different_recorded_item_controls':len(controls),'best_controls':controls[:3],
                    'margin_to_best_control':same['aligned_ncc']-controls[0]['aligned_ncc']
                        if controls and same['aligned_ncc'] is not None else None}
            results.append(result);previews.append((result,ref['raw'],q['raw']))
    if any(path.read_bytes()!=raw for path,raw in inputs):raise ValueError('item_stability_input_changed')
    output.mkdir(parents=True)
    import cv2
    report={'schema_version':1,'kind':'prepared_item_crop_stability_probe','results':results,'unavailable':unavailable,
            'input_sha256':{str(p.resolve()):sha(raw) for p,raw in inputs},'opencv_version':cv2.__version__,
            'package_count':len(groups),'unknown_role_crops':len(results),
            'episode_count':len({(r['package_id'],r['episode']) for r in results}),
            'evidence_units':duplication_summary(results),
            'accuracy_verified':False,'original_sources_reverified_here':False,
            'limits':['Only prepared SHA-bound crops rechecked here; original recording admission occurred during crop preparation.',
                      'Scores compare recorded references, not independently verified item labels.',
                      'First/last roles may be the same frame; rows are not independent trials.',
                      'Controls are other recorded item crops within the same package, not a complete false-positive evaluation.',
                      'Fixed normalization/registration is an offline probe, no runtime threshold or valuation changes.']}
    (output/'probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    page=['<!doctype html><meta charset="utf-8"><title>装备中断图像稳定性</title>',
          '<style>body{font:16px system-ui;margin:24px;background:#20252b;color:#eee}td,th{padding:12px;border-bottom:1px solid #65717b}img{width:174px;image-rendering:pixelated}table{border-collapse:collapse}</style>',
          '<h1>装备中断：原图对照与局部配准</h1><p>前帧记录的物品不是独立真值；相似度不是识别置信度，不自动修正金额。只核对准备裁片，不重新读取原始游戏记录。</p>',
          '<p>'+escape(f"角色行{report['evidence_units']['query_role_rows']}；不同源行{report['evidence_units']['distinct_source_rows']}；"
                        f"不同裁片字节{report['evidence_units']['distinct_crop_bytes']}；"
                        f"不同参考/目标字节对{report['evidence_units']['distinct_reference_query_byte_pairs']}。均不是独立试验数。")+'</p>',
          '<table><tr><th>比赛/英雄/物品/行</th><th>上次已知</th><th>未知帧</th><th>图像相关性</th></tr>']
    for r,a,b in previews:
        label=f"{r['match_id']} / {r['hero_id']} / {r['recorded_item']} / {r['role']} / L{r['query_evidence']['line']}"
        values=json.dumps({'本参考':r['similarity'],'其他记录图标最高':r['best_controls'][:1]},ensure_ascii=False)
        page.append('<tr><td>'+escape(label)+'</td><td><img src="data:image/png;base64,'+base64.b64encode(a).decode()+'"></td><td><img src="data:image/png;base64,'+base64.b64encode(b).decode()+'"></td><td>'+escape(values)+'</td></tr>')
    page.append('</table><p>同一帧的首末角色不可当两个独立样本；不以这些定向样本计算准确率。</p>')
    (output/'index.html').write_text(''.join(page),encoding='utf-8')
    return report
