"""Bounded timing and exact-cache diagnostics from already admitted rows."""
import math
import re

STAGES=('anchor','hud','hero_ocr','items','upgrades','wallet','frame_total')
PERFORMANCE=('capture','queue_wait','recognition')
CACHES=('hero_ocr','items','numeric_ocr','slot_png')


def count(value):
    return type(value) is int and 0<=value<=10**12


class CacheDeltas:
    def __init__(self, name):
        self.name=name;self.generations={};self.active=None;self.last=None;self.last_generation=None
        self.missing=self.invalid=self.unscoped=self.excluded=self.baselines=0

    def gap(self):
        if self.active is not None:self.generations[self.active]['last']=None
        self.active=None

    def add(self, value):
        if value is None:self.missing+=1;self.gap();return
        expected='identical_pixels_shape_dtype_only' if self.name=='slot_png' else 'identical_pixels_only'
        if (not isinstance(value,dict) or value.get('basis')!=expected
                or any(not count(value.get(k)) for k in ('hits','misses','entries','limit'))
                or value['entries']>value['limit']):
            self.invalid+=1;self.gap();return
        if self.name=='slot_png' and (value.get('scope')!='slot_png_encoding_not_recognition_values_or_timestamps'
                or any(not count(value.get(k)) for k in ('retained_bytes','byte_limit'))
                or value['retained_bytes']>value['byte_limit']):
            self.invalid+=1;self.gap();return
        self.last={k:value[k] for k in ('hits','misses','entries','limit')}
        generation=value.get('generation_id')
        if not isinstance(generation,str) or re.fullmatch('[a-f0-9]{32}',generation) is None:
            self.unscoped+=1;self.gap();return
        if self.last_generation is not None and self.last_generation!=generation:
            previous_generation=self.generations.get(self.last_generation)
            if previous_generation is not None:previous_generation['retired']=True
            self.gap()
        self.last_generation=generation
        if generation not in self.generations:
            if len(self.generations)>=32:self.excluded+=1;self.gap();return
            self.generations[generation]={'last':None,'signature':(expected,value['limit'],value.get('byte_limit')),
                'hits':0,'misses':0,'intervals':0,'valid':True,'retired':False}
        entry=self.generations[generation];self.active=generation
        signature=(expected,value['limit'],value.get('byte_limit'))
        if entry['retired'] or signature!=entry['signature']:entry['valid']=False
        current=(value['hits'],value['misses']);previous=entry['last']
        if previous is None:self.baselines+=1
        elif current[0]<previous[0] or current[1]<previous[1]:entry['valid']=False
        elif entry['valid']:
            entry['hits']+=current[0]-previous[0];entry['misses']+=current[1]-previous[1]
            entry['intervals']+=1
        entry['last']=current

    def result(self):
        valid=[g for g in self.generations.values() if g['valid']]
        hits=sum(g['hits'] for g in valid);misses=sum(g['misses'] for g in valid)
        return {'observed_delta_hits':hits,'observed_delta_misses':misses,
            'observed_delta_hit_fraction':hits/(hits+misses) if hits+misses else None,
            'valid_adjacent_intervals':sum(g['intervals'] for g in valid),
            'generation_count':len(self.generations),
            'invalid_generation_count':sum(not g['valid'] for g in self.generations.values()),
            'baseline_samples':self.baselines,'missing_samples':self.missing,'invalid_samples':self.invalid,
            'samples_without_generation':self.unscoped,'capacity_excluded_samples':self.excluded,
            'last_cumulative_snapshot':self.last,
            'scope':'observed_valid_adjacent_same_generation_snapshots_not_whole_match'}


class RuntimeProfile:
    def __init__(self, *, sample_limit=30000):
        if type(sample_limit) is not int or not 1<=sample_limit<=30000:
            raise ValueError('runtime_sample_limit')
        self.limit=sample_limit;self.rows=0
        self.timings={f'{group}.{key}':{'values':[],'missing':0,'invalid':0,'excluded':0}
            for group,keys in (('stage',STAGES),('performance',PERFORMANCE)) for key in keys}
        self.caches={name:CacheDeltas(name) for name in CACHES}

    def add(self, row):
        self.rows+=1
        attempt=row.get('attempt') if isinstance(row.get('attempt'),dict) else {}
        for group,keys,raw in (('stage',STAGES,attempt.get('stage_ms')),
                              ('performance',PERFORMANCE,row.get('performance_ms'))):
            raw=raw if isinstance(raw,dict) else {}
            for key in keys:
                series=self.timings[f'{group}.{key}'];value=raw.get(key)
                if value is None:series['missing']+=1
                elif type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=3600000:
                    series['invalid']+=1
                elif len(series['values'])>=self.limit:series['excluded']+=1
                else:series['values'].append(float(value))
        caches=attempt.get('cache');caches=caches if isinstance(caches,dict) else {}
        for name,accumulator in self.caches.items():accumulator.add(caches.get(name))

    def result(self):
        timings={}
        for name,series in self.timings.items():
            values=sorted(series['values']);n=len(values)
            timings[name]={'samples':n,'missing':series['missing'],'invalid':series['invalid'],
                'capacity_excluded':series['excluded'],
                'median_ms':round((values[(n-1)//2]+values[n//2])/2,3) if n else None,
                'p95_ms':round(values[math.ceil(n*.95)-1],3) if n else None,
                'maximum_ms':round(values[-1],3) if n else None}
        return {'version':'admitted_observation_runtime_v1','rows':self.rows,
            'sample_limit_per_metric':self.limit,'timings':timings,
            'caches':{name:value.result() for name,value in self.caches.items()},
            'percentile_method':'nearest_rank','stage_times_additive':False,
            'game_fps_or_cpu_effect_verified':False,'whole_match_coverage_verified':False}


def render_markdown(profile):
    if not isinstance(profile,dict):return []
    lines=['装备处理耗时（毫秒，中位数 / P95；仅本闭合来源中有记录的样本）：']
    for name,row in profile['timings'].items():
        if row['samples']:
            lines.append(f"- {name}：{row['median_ms']} / {row['p95_ms']}；{row['samples']}条，容量排除{row['capacity_excluded']}条。")
    for name,row in profile['caches'].items():
        fraction=row['observed_delta_hit_fraction']
        label=f'{fraction:.1%}' if fraction is not None else '未知'
        lines.append(f"- {name}缓存：相邻同代次区间命中占比 {label}；有效区间{row['valid_adjacent_intervals']}，缺代次{row['samples_without_generation']}条。")
    lines+=['首条累计值只作基线；缺字段不补零，计数回退或代次矛盾不参与差值。阶段耗时不相加，不代表游戏FPS或整局覆盖。','']
    return lines
