"""Adjacent same-hero/slot transitions for offline local-reference counterexamples."""
from collections import Counter
from html import escape
import json
from pathlib import Path
import re

from .equipment_continuity import _time, _number
from .item_crop_stability import compare
from .item_review_evidence import slot_crop
from .observation_handoff import open_rows
from .observation_index import resolve_reference, journal_reference
from .postgame_store import sha


class Transitions:
    def __init__(self,catalogue,*,limit=24,gap=180):
        if type(limit) is not int or not 1<=limit<=60 or type(gap) not in (int,float) or not 0<gap<=600:
            raise ValueError('item_transition_budget')
        self.catalogue,self.limit,self.gap=catalogue or {},limit,gap
        self.heroes={};self.cases=[];self.counts=Counter();self.seen=set();self.kept=Counter()

    def add(self,row,evidence):
        hero=(row.get('attempt') or {}).get('hero') or {};hid=hero.get('hero_id')
        if not isinstance(hid,str) or not re.fullmatch(r'npc_dota_hero_[a-z0-9_]{1,60}',hid):return
        stamp=_time(row.get('captured_at_utc'));old=self.heroes.get(hid)
        if stamp is None or old and stamp<=old['time']:
            self.heroes.pop(hid,None);self.counts['invalid_or_nonincreasing_time_resets']+=1;return
        if old and (stamp-old['time']).total_seconds()>self.gap:
            old=None;self.counts['observation_gap_resets']+=1
        if hid not in self.heroes and len(self.heroes)>=64:
            self.counts['hero_budget_omissions']+=1;return
        slots=hero.get('slots')
        if not isinstance(slots,list):self.heroes.pop(hid,None);return
        unique=Counter(s.get('slot_id') for s in slots if isinstance(s,dict) and isinstance(s.get('slot_id'),str))
        current={}
        for s in slots:
            if not isinstance(s,dict):continue
            sid=s.get('slot_id')
            if not isinstance(sid,str) or not re.fullmatch(r'(main|backpack)_[1-9]',sid) or unique[sid]!=1:continue
            status=s.get('status');item=s.get('item_id')
            if status=='known' and isinstance(item,str):item=item.removeprefix('item_')
            elif status=='empty':item=None
            else:continue  # Unknown/missing/duplicate slots break the reference.
            sample={'status':status,'item_id':item,'captured_at_utc':row.get('captured_at_utc'),
                    'evidence':evidence}
            prior=old['slots'].get(sid) if old else None
            if prior is not None:
                kind=('recorded_empty' if status=='empty' else 'recorded_same_item' if item==prior['item_id'] else 'recorded_item_changed')
                self.counts[kind+'_pairs']+=1
                key=hid,sid,prior['item_id'],item,kind
                if key not in self.seen:
                    self.seen.add(key);self.counts[kind+'_distinct_cases']+=1
                    if len(self.seen)>5000:raise ValueError('item_transition_distinct_budget')
                    if self.kept[kind]<self.limit:
                        self.cases.append({'kind':kind,'hero_id':hid,'slot_id':sid,
                            'previous_recorded_item':prior['item_id'],'current_recorded_item':item,
                            'before':prior,'after':sample});self.kept[kind]+=1
                    else:self.counts[kind+'_omitted_by_limit']+=1
            cost=_number(self.catalogue.get(item,{}).get('cost')) if item else None
            if status=='known' and cost is not None and cost>=2000:
                current[sid]=sample
        self.heroes[hid]={'time':stamp,'slots':current}


def prepare(entry,roots,catalogue,output,*,limit=24):
    output=Path(output).resolve()
    if output.exists() or any(output.is_relative_to(Path(r).resolve()) for r in roots.values()):
        raise ValueError('item_transition_new_nonhistory_output')
    root,session=resolve_reference(entry,roots);tracker=Transitions(catalogue,limit=limit)
    with open_rows(root,session,expected_package_id=entry['package_id'],with_evidence=True) as (manifest,rows):
        ref=journal_reference(entry,manifest)
        for _,row,evidence in rows:tracker.add(row,{**evidence,'source':ref})
    wanted={}
    for i,case in enumerate(tracker.cases):
        for role in ('before','after'):
            sample=case[role];wanted.setdefault(sample['evidence']['line'],[]).append((i,case,role,sample))
    output.mkdir(parents=True);(output/'crops').mkdir();results=[]
    with open_rows(root,session,expected_package_id=entry['package_id'],with_evidence=True) as (_,rows):
        for number,row,evidence in rows:
            for index,case,role,sample in wanted.pop(number,[]):
                if evidence['line_sha256']!=sample['evidence']['line_sha256']:
                    raise ValueError('item_transition_line_changed')
                h=(row.get('attempt') or {}).get('hero') or {}
                if h.get('hero_id')!=case['hero_id']:raise ValueError('item_transition_hero_changed')
                slots=[s for s in h.get('slots',[]) if s.get('slot_id')==case['slot_id']]
                if len(slots)!=1 or slots[0]['status']!=sample['status']:
                    raise ValueError('item_transition_slot_changed')
                actual=slots[0].get('item_id')
                if isinstance(actual,str):actual=actual.removeprefix('item_')
                if actual!=sample['item_id']:raise ValueError('item_transition_item_changed')
                result={'case_index':index,'role':role,**sample}
                try:
                    crop,binding=slot_crop(roots[ref['root_key']],ref,row,case['slot_id'])
                    relative=f'crops/{index:03d}-{role}.png';crop.save(output/relative)
                    result.update(status='prepared_for_visual_review',recorded_slot_status=sample['status'],
                                  image=relative,crop_sha256=sha((output/relative).read_bytes()),**binding)
                except (ValueError,OSError) as exc:
                    result.update(status='unavailable',recorded_slot_status=sample['status'],reason=str(exc))
                results.append(result)
    if wanted:raise ValueError('item_transition_rows_missing')
    by_role={(r['case_index'],r['role']):r for r in results}
    for i,case in enumerate(tracker.cases):
        a,b=(by_role[i,role] for role in ('before','after'))
        if a['status']==b['status']=='prepared_for_visual_review':
            case['similarity']=compare((output/a['image']).read_bytes(),(output/b['image']).read_bytes())
        else:case['similarity']=None
    report={'schema_version':1,'kind':'recorded_item_transition_evidence','match_id':manifest['match_id'],
            'package_id':entry['package_id'],'counts':dict(tracker.counts),'cases':tracker.cases,'entries':results,
            'limit_per_kind':limit,'maximum_gap_s':tracker.gap,'source_verified_before_and_after':True,
            'source_rows':manifest['payload']['rows'] if manifest['payload'] is not None else 0,
            'channel_has_observations':manifest['payload'] is not None and manifest['payload']['rows']>0,
            'catalogue_available':bool(catalogue),
            'independent_truth':False,'accuracy_verified':False,
            'limits':['Earliest distinct recorded transition selected before looking at image scores.',
                      'Known/empty labels are recording evidence, not independent item truth.',
                      'Only adjacent valid same-hero/slot observations; unknowns and gaps break chains.',
                      'Historical price >=2000 selects references, not a current-patch valuation.',
                      'Image correlation never changes acceptance, inventory or value.']}
    (output/'evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    parts=['<!doctype html><meta charset="utf-8"><title>同格物品变化反例</title>',
        '<style>body{font:16px system-ui;margin:24px;background:#20252b;color:#eee}td,th{padding:12px;border-bottom:1px solid #60717c}img{width:174px;image-rendering:pixelated}</style>',
        '<h1>同格物品变化与不变对照</h1><p>记录标签不是独立真值；用于检查历史图标参考会不会混淆换装/移格，不作为识别准确率。</p><table>']
    for i,case in enumerate(tracker.cases):
        text=f"{i} · {case['hero_id']} · {case['slot_id']} · {case['kind']} · {case['previous_recorded_item']} → {case['current_recorded_item']}"
        parts.append('<tr><td>'+escape(text)+'</td>')
        for role in ('before','after'):
            e=by_role[i,role];parts.append('<td>')
            if e['status']=='prepared_for_visual_review':parts.append('<img src="'+e['image']+'">')
            else:parts.append(escape(e.get('reason','unavailable')))
            parts.append('<p>'+escape(str(e['evidence']['line']))+'</p></td>')
        parts.append('<td>'+escape(json.dumps(case['similarity'],ensure_ascii=False))+'</td></tr>')
    parts.append('</table>');(output/'index.html').write_text(''.join(parts),encoding='utf-8')
    return report
