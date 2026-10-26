"""Offline facts and provisional UTC alignment, never inferred gameplay truth."""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

from .closed_journals import ClosedJournals, explicit_match
from .helper_handoff import utc
from .self_economy_audit import SelfEconomyAudit
from .item_review_priorities import ItemReviewPriorities

HERO = re.compile(r'npc_dota_hero_[a-z0-9_]{1,60}')
LIVE = {'DOTA_GAMERULES_STATE_PRE_GAME', 'DOTA_GAMERULES_STATE_GAME_IN_PROGRESS'}
TIME_FIELDS = ('captured_at_utc', 'observed_at_utc', 'sampled_at_utc', 'at_utc',
               'attempt_started_at_utc', 'presented_at_utc', 'written_at_utc')


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def stamp(value):
    try:
        return utc(value).timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


class AnchorIndex:
    """Bracketing receipt anchors only; a candidate association, not capture truth."""
    def __init__(self, anchors, max_gap_s=5):
        self.anchors = sorted(anchors)
        self.times = [r[0] for r in self.anchors]
        self.max_gap_s = max_gap_s

    def candidate(self, value):
        t = stamp(value)
        if t is None:
            return None, 'missing_or_invalid_utc'
        i = bisect_left(self.times, t)
        if i == 0 or i == len(self.times):
            return None, 'not_bracketed_by_live_gsi'
        before, after = self.anchors[i-1], self.anchors[i]
        if after[0]-before[0] > self.max_gap_s:
            return None, 'gsi_anchor_gap'
        # Repeated competing anchors can share a UTC timestamp.
        for endpoint in (before[0], after[0]):
            lo, hi = bisect_left(self.times, endpoint), bisect_right(self.times, endpoint)
            if len({a[1] for a in self.anchors[lo:hi]}) != 1:
                return None, 'ambiguous_gsi_anchor'
        if before[1] != after[1]:
            return None, 'cross_match_anchor_boundary'
        return before[1], 'utc_bracket_candidate_not_explicit_identity'


def percentiles(values):
    values = sorted(values)
    return {'count': len(values), 'median': statistics.median(values) if values else None,
            'p95': values[math.ceil(.95*len(values))-1] if values else None,
            'max': max(values) if values else None}


def item_catalogue(root):
    path = Path(root)/'data/items.json'
    metadata = Path(root)/'data/items.meta.json'
    raw, meta_raw = path.read_bytes(), metadata.read_bytes()
    if len(raw) > 2*1024**2 or len(meta_raw) > 256*1024:
        raise ValueError('review_catalogue_size_limit')
    digest = hashlib.sha256(raw).hexdigest()
    meta = json.loads(meta_raw)
    if digest != meta['cache_sha256']:
        raise ValueError('review_catalogue_fingerprint_changed')
    return json.loads(raw), {'file':'data/items.json','sha256':digest,
        'metadata_sha256':hashlib.sha256(meta_raw).hexdigest(),
        'basis':'retained_catalogue_cost_for_review_priority_not_current_price_certification'}


def summarize(package, roots, *, catalogue=None, catalogue_source=None):
    associations = {m['record_session_id']: m['match_id'] for m in package['matches']}
    if len(associations) != len(package['matches']):
        raise ValueError('record_duplicate_session_association')
    anchors, own = [], defaultdict(lambda: {'rows':0, 'available_rows':0,
        'heroes':Counter(), 'economy_status':Counter(), 'missing_reasons':Counter(),
        'arithmetic_disagreements':[], 'arithmetic_disagreement_count':0, 'arithmetic_comparable_rows':0,
        'decreases':[], 'large_decrease_count':0,
        'unavailable_rows':0, 'first_received_at_utc':None, 'last_received_at_utc':None})
    content, shared = {}, {}
    priorities = ItemReviewPriorities(catalogue)
    self_audits = defaultdict(SelfEconomyAudit)
    previous = {}
    with ClosedJournals(package, roots) as inputs:
        for component, ref in inputs.journals('own_gsi'):
            for _, row, evidence in inputs.rows(ref):
                mid = explicit_match(row)
                if mid is None:
                    continue
                result = own[mid]
                self_audits[mid].add(row, evidence)
                result['rows'] += 1
                received, provider = stamp(row.get('received_at_utc')), stamp(row.get('provider_at_utc'))
                if received is not None:
                    dt = row['received_at_utc']
                    if result['first_received_at_utc'] is None: result['first_received_at_utc'] = dt
                    result['last_received_at_utc'] = dt
                context = row.get('map_context') or {}
                if (context.get('game_state') in LIVE and received is not None and provider is not None
                        and 0 <= received-provider <= 5):
                    anchors.append((received, mid))
                economy = row.get('self_economy')
                if not isinstance(economy, dict):
                    result['unavailable_rows'] += 1
                    continue
                result['available_rows'] += 1
                hero = economy.get('hero_id')
                if isinstance(hero, str) and HERO.fullmatch(hero): result['heroes'][hero] += 1
                status = economy.get('economy_status')
                if status in ('partial', 'complete', 'unavailable', 'known', 'reported', 'estimated'):
                    result['economy_status'][status] += 1
                else:
                    result['economy_status']['unknown_status'] += 1
                for reason in economy.get('missing_reasons', []):
                    if isinstance(reason, str) and re.fullmatch('[a-zA-Z0-9_]{1,120}', reason):
                        result['missing_reasons'][reason] += 1
                parts = [number(economy.get(k)) for k in ('cash_value','item_value','upgrade_value')]
                amount = number(economy.get('amount'))
                if status != 'reported' and amount is not None and all(v is not None for v in parts):
                    result['arithmetic_comparable_rows'] += 1
                    if abs(sum(parts)-amount) > .01:
                        result['arithmetic_disagreement_count'] += 1
                        if len(result['arithmetic_disagreements']) < 20:
                            result['arithmetic_disagreements'].append({'amount':amount, 'component_sum':sum(parts), 'evidence':evidence})
                key = mid, hero
                prev = previous.get(key)
                if amount is not None and received is not None:
                    if prev and received > prev[0] and received-prev[0] <= 5 and amount-prev[1] <= -500:
                        result['large_decrease_count'] += 1
                        if len(result['decreases']) < 20:
                            result['decreases'].append({'previous_amount':prev[1], 'amount':amount,
                                'delta':amount-prev[1], 'received_at_utc':row.get('received_at_utc'),
                                'clock_time_s':row.get('clock_time_s'), 'economy_status':status,
                                'evidence':evidence, 'meaning':'gsi_derived_value_change_not_recognition_error'})
                    previous[key] = received, amount
        index = AnchorIndex(anchors)
        for component, ref in inputs.journals():
            kind = component['kind']
            if kind == 'own_gsi': continue
            source_key = ref['root_key'] + ':' + ref['path']
            totals = {'source':ref, 'kind':kind, 'rows':0, 'explicit_match_rows':Counter(),
                'session_bound_rows':Counter(), 'utc_candidate_rows':Counter(), 'unassigned_reasons':Counter(),
                'meaning':'UTC candidates are postgame association hints, not identity or causal features'}
            for _, row, evidence in inputs.rows(ref):
                totals['rows'] += 1
                mid = explicit_match(row)
                if mid is not None and component.get('declared_match_id') not in (None, mid):
                    raise ValueError('record_component_match_identity_conflict')
                if mid:
                    totals['explicit_match_rows'][mid] += 1
                else:
                    context = row.get('clock_context') or {}
                    sid = row.get('record_session_id', context.get('record_session_id'))
                    sid_mid = associations.get(sid)
                    if sid_mid is not None:
                        mid = sid_mid
                        totals['session_bound_rows'][mid] += 1
                    elif component.get('declared_match_id'):
                        mid = component['declared_match_id']
                        totals['session_bound_rows'][mid] += 1
                    else:
                        field = next((k for k in TIME_FIELDS if row.get(k) is not None), None)
                        if row.get('utc_interval_consistent') is False or row.get('probe_utc_consistent') is False:
                            candidate, reason = None, 'source_utc_inconsistent'
                        else:
                            candidate, reason = index.candidate(row.get(field) if field else None)
                        if candidate:
                            totals['utc_candidate_rows'][candidate] += 1
                            spans = totals.setdefault('utc_candidate_spans', [])
                            if (spans and spans[-1]['match_id'] == candidate and spans[-1]['time_field'] == field
                                    and spans[-1]['last_line'] == evidence['line']-1):
                                spans[-1]['last_line'] = evidence['line']
                                spans[-1]['last_source_utc'] = row[field]
                            elif len(spans) < 2000:
                                spans.append({'match_id':candidate,'first_line':evidence['line'],
                                    'last_line':evidence['line'],'time_field':field,
                                    'first_source_utc':row[field],'last_source_utc':row[field]})
                            else:
                                totals['candidate_span_limit_reached'] = True
                        else:
                            totals['unassigned_reasons'][reason] += 1
                # Only explicit/session-bound identities feed match facts below.
                if mid is None: continue
                m = content.setdefault(mid, {'observation_attempts':Counter(), 'heroes':{},
                    'recognition_ms':[], 'own_hero_visual_attempts':0,
                    'bp_event_phases':Counter(), 'bp_ui_phases':Counter(), 'bp_ui_selection_candidate_receipts':0})
                name = Path(ref['path']).name
                if kind == 'economy_context' and name == 'observations.jsonl':
                    priorities.add(mid, row, evidence)
                    attempt = row.get('attempt') or {}
                    accepted = attempt.get('accepted') is True
                    m['observation_attempts']['accepted' if accepted else 'not_accepted'] += 1
                    hero = attempt.get('hero') or {}
                    hid = hero.get('hero_id')
                    if isinstance(hid, str) and HERO.fullmatch(hid):
                        h = m['heroes'].setdefault(hid, {'attempts':0,'accepted':0,'known_slots':0,'unknown_slots':0})
                        h['attempts'] += 1
                        h['accepted'] += int(accepted)
                        for slot in hero.get('slots', []):
                            if slot.get('status') in ('known','empty'): h['known_slots'] += 1
                            else: h['unknown_slots'] += 1
                        if hid in own.get(mid, {}).get('heroes', {}): m['own_hero_visual_attempts'] += 1
                    ms = number((row.get('performance_ms') or {}).get('recognition'))
                    if ms is not None and ms >= 0: m['recognition_ms'].append(ms)
                if kind == 'bp':
                    snap = row.get('snapshot') if name == 'events.jsonl' else (row.get('view_input') or {}).get('snapshot')
                    if isinstance(snap,dict) and snap.get('phase') in ('waiting','ban','selection','strategy','in_game','finished'):
                        phase = snap['phase']
                        m['bp_event_phases' if name == 'events.jsonl' else 'bp_ui_phases'][phase] += 1
                        advice = (row.get('view_input') or {}).get('advice') or {}
                        if name == 'ui-presented.jsonl' and phase == 'selection' and advice.get('candidates'):
                            m['bp_ui_selection_candidate_receipts'] += 1
            shared[source_key] = totals
        for m in content.values():
            m['recognition_ms'] = percentiles(m['recognition_ms'])
    return {'schema_version':1, 'kind':'closed_record_content_review',
        'generated_at_utc':datetime.now(timezone.utc).isoformat(), 'launcher_run_id':package['launcher_run_id'],
        'matches':package['matches'], 'own_gsi':dict(own),
        'own_economy_audit': {mid: audit.result() for mid, audit in self_audits.items()}, 'match_facts':content,
        'item_review_priorities':priorities.result(),
        'priority_queue_limit_reached':priorities.at_capacity, 'catalogue_source':catalogue_source,
        'journal_alignment':list(shared.values()), 'source_verified_before_and_after':True,
        'limitations':['No replay, screenshot or audio truth inspected.',
            'GSI-derived decreases are not smoothed or classified as errors.',
            'Partial GSI valuation is not the same as full net worth.',
            'Slot known/accepted counts are not recognition accuracy.',
            'UTC candidate rows never become match facts or training features.',
            'UI receipts describe widget application, not user perception or recommendation quality.']}


def render_markdown(reports):
    lines = ['# 闭合记录内容复核', '',
        '只读分析已闭合日志；原始记录、本人GSI金额和历史显示不修改。',
        '没有打开录像、图片或音频核验，以下数量均不是识别准确率。', '']
    for report in reports:
        lines += [f"## {report['launcher_run_id']}", '']
        for match in report['matches']:
            mid = match['match_id']
            gsi, facts = report['own_gsi'].get(mid, {}), report['match_facts'].get(mid, {})
            display = report.get('own_economy_audit', {}).get(mid, {}).get('display_checks', {})
            lines += [f"### 比赛 {mid or '本地会话'}", '',
                f"结束证据：{match['match_end_evidence']}",
                f"本人GSI：{gsi.get('rows', '未知')}行；金额可用{gsi.get('available_rows', '未知')}行。",
                f"本人英雄：{', '.join(gsi.get('heroes', {})) or '未知'}",
                f"金额分项加总不一致：{gsi.get('arithmetic_disagreement_count', '未知')}（可比分项记录{gsi.get('arithmetic_comparable_rows', '未知')}）；相邻5秒内下降至少500：{gsi.get('large_decrease_count', '未知')}。下降不是识别错误判定。",
                f"GSI估值状态：{dict(gsi.get('economy_status', {}))}",
                f"同包显示核对：普通{display.get('normal_comparable_rows', '未知')}行、熊灵合计{display.get('bear_comparable_rows', '未知')}行；金额不符{display.get('amount_mismatches', '未知')}，熊灵来源不符{display.get('bear_provenance_issues', '未知')}。这里只核对记录字段，不代表游戏画面已核验。",
                f"显示不可比原因：{display.get('uncomparable_reasons', {})}",
                f"视觉观测尝试：{dict(facts.get('observation_attempts', {}))}；其中本人英雄尝试{facts.get('own_hero_visual_attempts', '未知')}。",
                f"识别耗时毫秒：{facts.get('recognition_ms', {})}",
                f"选人阶段含候选推荐的UI应用回执：{facts.get('bp_ui_selection_candidate_receipts', '未知')}（不代表推荐质量或用户已看到）。", '',
                '| 英雄 | 尝试 | accepted | 已知/空格日志数 | 未知格日志数 |', '|---|---:|---:|---:|---:|']
            for hero, h in facts.get('heroes', {}).items():
                lines.append(f"| {hero} | {h['attempts']} | {h['accepted']} | {h['known_slots']} | {h['unknown_slots']} |")
            lines += ['', '本人估值缺项：', '']
            for reason,count in sorted(gsi.get('missing_reasons', {}).items(), key=lambda p:(-p[1],p[0]))[:15]:
                lines.append(f'- {reason}: {count}')
            lines += ['']
        lines += ['### 共享资料的场次候选归属', '',
            'UTC候选仅供赛后定位：必须被同场有效GSI接收时间前后夹住，锚点间隔≤5秒；跨局、无锚点、时间异常均不归属。候选不进入上面的比赛事实。', '',
            '| 通道/文件 | 总行数 | 显式比赛号 | 显式会话/比赛目录 | UTC候选 | 未分配 |', '|---|---:|---|---|---|---|']
        for j in report['journal_alignment']:
            lines.append(f"| {j['kind']}/{Path(j['source']['path']).name} | {j['rows']} | {dict(j['explicit_match_rows'])} | {dict(j['session_bound_rows'])} | {dict(j['utc_candidate_rows'])} | {dict(j['unassigned_reasons'])} |")
        lines += ['', '### 大件复查优先队列', '',
            '价格仅用随仓库保留的目录排序，不能当当前补丁价格认证。候选名不是物品真值；重复观测合并计数不作为独立错误。分差阈值0.12只用于离线挑样，不修改在线识别。', '',
            '| 比赛 | 英雄 | 格 | 候选物品 | 目录价格 | 原因 | 重复观测 | 原日志行 |',
            '|---|---|---|---|---:|---|---:|---|']
        for p in report.get('item_review_priorities', []):
            lines.append(f"| {p['match_id']} | {p['hero_id']} | {p['slot_id']} | {p['candidate_item']} | {p['catalogue_cost']} | {p['reason']} | {p['observations']} | {p['first_evidence']['line']}–{p['last_evidence']['line']} |")
        lines += ['']
    return '\n'.join(lines)
