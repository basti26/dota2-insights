"""Audit evidence recorded at recommendation time, without today's cache or final roster."""
from collections import Counter
import math


def _positive_integer(value):
    return type(value) is int and value > 0


def audit(snapshot, advice, *, candidate_limit=3, minimum_relation_samples=100):
    if not 1 <= candidate_limit <= 10 or not 1 <= minimum_relation_samples:
        raise ValueError('draft_advice_audit_budget')
    side = snapshot.get('own_side')
    public = []
    for slot in snapshot.get('slots') or []:
        if (isinstance(slot, dict) and slot.get('public') is True and not slot.get('conflict')
                and isinstance(slot.get('hero_id'), str) and slot['hero_id']):
            public.append((slot.get('slot'), slot['hero_id']))
    hero_counts = Counter(hero for _, hero in public)
    usable = side in ('left', 'right') and all(n == 1 for n in hero_counts.values())
    expected = set()
    for slot, hero in public:
        if not isinstance(slot, str) or slot not in {s + str(i) for s in ('left', 'right') for i in range(1, 6)}:
            usable = False
            continue
        expected.add(('with' if slot.startswith(side or '?') else 'against', hero))
    rules = advice.get('capability_rules')
    rules = rules if isinstance(rules, dict) else {}
    candidates = advice.get('candidates')
    candidates = candidates if isinstance(candidates, list) else []
    cases = []
    for candidate in candidates[:candidate_limit]:
        if not isinstance(candidate, dict):
            cases.append({'candidate_record_invalid': True})
            continue
        relations = candidate.get('relations')
        relations = relations if isinstance(relations, list) else []
        keyed = {}
        unexpected = 0
        for relation in relations:
            if not isinstance(relation, dict):
                unexpected += 1
                continue
            key = relation.get('kind'), relation.get('other_id')
            if not all(isinstance(v, str) for v in key):
                unexpected += 1
                continue
            keyed.setdefault(key, []).append(relation)
            if key not in expected:
                unexpected += 1
        covered, missing, small = [], [], []
        if usable:
            for key in sorted(expected):
                matches = keyed.get(key, [])
                if len(matches) != 1:
                    missing.append({'kind': key[0], 'other_id': key[1],
                                    'reason': 'duplicate_relation' if matches else 'absent'})
                    continue
                relation = matches[0]
                count, delta = relation.get('match_count'), relation.get('delta')
                valid_delta = type(delta) in (int, float) and math.isfinite(delta)
                if not _positive_integer(count) or not valid_delta:
                    missing.append({'kind': key[0], 'other_id': key[1], 'reason': 'invalid_samples_or_delta'})
                    continue
                covered.append({'kind': key[0], 'other_id': key[1], 'match_count': count, 'delta': delta})
                if count < minimum_relation_samples:
                    small.append({'kind': key[0], 'other_id': key[1], 'match_count': count})
        cases.append({'hero_id': candidate.get('hero_id'),
            'candidate_already_public': candidate.get('hero_id') in hero_counts,
            'baseline_samples': candidate.get('baseline_samples') if _positive_integer(candidate.get('baseline_samples')) else None,
            'expected_relations': len(expected) if usable else None,
            'covered_relations': covered if usable else None,
            'missing_or_invalid_relations': missing if usable else None,
            'small_sample_relations': small if usable else None,
            'unexpected_relation_rows': unexpected,
            'recorded_unknown_relations': candidate.get('unknown_relations')})
    return {'cache_status_recorded': advice.get('cache_status'), 'bundle_id_recorded': advice.get('bundle_id'),
            'bundle_sha256_recorded': advice.get('bundle_sha256'),
            'statistics_scope_recorded': advice.get('scope'),
            'rules_status_recorded': rules.get('status'), 'rules_sha256_recorded': rules.get('sha256'),
            'rules_game_version_recorded': rules.get('applies_to_game_version'),
            'public_roster': [{'slot': slot, 'hero_id': hero} for slot, hero in public],
            'relation_scope_available': usable, 'candidate_count': len(candidates),
            'audited_candidate_limit': candidate_limit, 'minimum_relation_samples': minimum_relation_samples,
            'candidates': cases, 'quality_verified': False,
            'meaning': 'recorded_advice_evidence_only_not_independent_cache_patch_or_score_validation'}


def render_markdown(reports):
    lines = ['\n## BP推荐数据依据', '',
        '只核查推荐当时记录的缓存状态、规则状态和公开阵容关系；不读取后续阵容，不以现在的缓存代替历史依据。',
        '每阶段保留首条含候选事件，检查前三名；覆盖仅表示记录有有效样本与数值，不认证统计真实或推荐质量。', '']
    for report in reports:
        seen = set()
        for event in (report.get('diagnostics') or {}).get('events', []):
            health = event.get('advice_data_health')
            if event.get('phase') != 'selection' or not health or not health['candidate_count']:
                continue
            stage = event.get('stage')
            if stage in seen or len(seen) >= 6:
                continue
            seen.add(stage)
            lines += [f"### {report.get('effective_match_id') or report['session_id']} / {stage} / 行 {event['line']}", '',
                f"当时缓存状态：{health['cache_status_recorded'] or '未知'}；规则状态：{health['rules_status_recorded'] or '未知'}；规则适用版本：{health['rules_game_version_recorded'] or '未知'}。", '',
                '| 候选 | 候选英雄基线样本 | 有效关系/应有关系 | 缺失或无效关系 | 小样本关系 |',
                '|---|---:|---|---|---|']
            for candidate in health['candidates']:
                if candidate.get('candidate_record_invalid'):
                    lines.append('| 候选记录无效 | 未知 | 未知 | 未知 | 未知 |')
                    continue
                covered = candidate['covered_relations']
                missing = candidate['missing_or_invalid_relations']
                small = candidate['small_sample_relations']
                lines.append(f"| {candidate['hero_id']} | {candidate['baseline_samples'] if candidate['baseline_samples'] is not None else '未知'} | "
                    f"{str(len(covered)) + '/' + str(candidate['expected_relations']) if covered is not None else '未知（阵营/公开阵容不足）'} | "
                    f"{len(missing) if missing is not None else '未知'} | {len(small) if small is not None else '未知'} |")
            lines.append('')
    return '\n'.join(lines) + '\n'
