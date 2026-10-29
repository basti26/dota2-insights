"""Recompute admitted continuity events and prepare their original slot images."""
from pathlib import Path
from html import escape

from .equipment_continuity import EquipmentContinuity
from .item_review_evidence import slot_crop, _finish
from .observation_index import resolve_reference, journal_reference
from .observation_handoff import open_rows


def prepare(entry, roots, catalogue, output):
    from PIL import Image, ImageDraw
    from .map_holdout import sha
    output = Path(output).resolve()
    if any(output.is_relative_to(Path(root).resolve()) for root in roots.values()):
        raise ValueError('continuity_output_overlaps_history')
    root, session = resolve_reference(entry, roots)
    tracker = EquipmentContinuity(catalogue)
    # Recompute instead of trusting editable event labels in a generated report.
    with open_rows(root, session, expected_package_id=entry['package_id'], with_evidence=True) as (manifest, rows):
        ref = journal_reference(entry, manifest)
        for _, row, evidence in rows:
            tracker.add(row, {**evidence, 'source': ref})
    continuity = tracker.result()
    wanted = {}
    for index, event in enumerate(continuity['episodes']):
        for role in ('known', 'first_unknown', 'last_unknown', 'next_known'):
            sample = event[role]
            if sample is not None:
                wanted.setdefault(sample['evidence']['line'], []).append((index, event, role, sample))
    output.mkdir(parents=True, exist_ok=False)
    (output / 'crops').mkdir()
    results, tiles = [], []
    with open_rows(root, session, expected_package_id=entry['package_id'], with_evidence=True) as (manifest, rows):
        for number, row, evidence in rows:
            prepared = {}
            for index, event, role, sample in wanted.pop(number, []):
                if evidence['line_sha256'] != sample['evidence']['line_sha256']:
                    raise ValueError('continuity_source_line_changed')
                hero = (row.get('attempt') or {}).get('hero') or {}
                if hero.get('hero_id') != event['hero_id']:
                    raise ValueError('continuity_hero_changed')
                result = {'episode_index': index, 'hero_id': event['hero_id'], 'slot_id': event['slot_id'],
                          'last_known_item': event['last_known_item'], 'role': role, **sample}
                try:
                    # Same row/slot can fulfil first/last roles; decode once, retain both roles.
                    if event['slot_id'] not in prepared:
                        prepared[event['slot_id']] = slot_crop(roots[ref['root_key']], ref, row, event['slot_id'])
                    crop, binding = prepared[event['slot_id']]
                    filename = f'crops/{index:03d}-{role}.png'
                    crop.save(output / filename)
                    result.update(status='prepared_for_visual_review', recorded_slot_status=sample['status'],
                                  image=filename, crop_sha256=sha(output / filename), **binding)
                    tile = Image.new('RGB', (360, 220), '#20252b')
                    draw = ImageDraw.Draw(tile)
                    draw.text((8, 6), f"{index} {manifest['match_id']} L{number} {role}", fill='white')
                    draw.text((8, 26), event['hero_id'].removeprefix('npc_dota_hero_') + ' ' + event['slot_id'], fill='white')
                    draw.text((8, 46), f"recorded: {sample['status']} / {sample['item_id'] or sample['candidate']}", fill='#ffc85a')
                    enlarged = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.NEAREST)
                    enlarged.thumbnail((336, 140))
                    tile.paste(enlarged, (8, 70))
                    tiles.append(tile)
                except (ValueError, OSError) as exc:
                    result.update(status='unavailable', recorded_slot_status=sample['status'], reason=str(exc))
                results.append(result)
    if wanted:
        raise ValueError('continuity_missing_rows')
    # Source exit revalidation has succeeded before publishing either page or receipt.
    _page(output, manifest['match_id'], continuity, results)
    return _finish(output, results, tiles, kind='equipment_continuity_evidence',
                   equipment_package_id=entry['package_id'], match_id=manifest['match_id'],
                   record_session_id=session, continuity=continuity)


def _page(output, match_id, continuity, results):
    roles = ('known', 'first_unknown', 'last_unknown', 'next_known')
    headings = ('上次识别到', '首次未知', '末次未知', '下次识别到')
    cells = {(r['episode_index'], r['role']): r for r in results}
    parts = ['<!doctype html><meta charset="utf-8"><title>大件识别中断复核</title>',
             '<style>body{background:#20252b;color:#eee;font:16px sans-serif;padding:24px}'
             'table{border-collapse:collapse;margin-bottom:24px}td,th{border:1px solid #667;padding:12px;vertical-align:top;width:22%}'
             'img{image-rendering:pixelated;max-width:180px}small{display:block;margin-top:8px}</style>',
             '<h1>大件识别中断复核 · ' + escape(str(match_id)) + '</h1>',
             '<p>仅展示原记录与原图，不认定真实装备、冷却原因或漏算金额。金额为日志选择字段，屏幕实际显示未核验。</p>',
             '<p>计数：' + escape(str(continuity['counts'])) + '</p>']
    if not continuity['episodes']:
        parts.append('<p>限定规则内没有可展示事件；不代表装备识别正确或整局有记录。</p>')
    for index, event in enumerate(continuity['episodes']):
        title = f"{index + 1}. {event['hero_id']} / {event['slot_id']} / 上次记录 {event['last_known_item']}"
        parts += ['<h2>' + escape(title) + '</h2><table><tr>',
                  ''.join('<th>' + h + '</th>' for h in headings), '</tr><tr>']
        for role in roles:
            r = cells.get((index, role))
            if r is None:
                parts.append('<td>限定窗口内未记录</td>')
                continue
            parts.append('<td>')
            if r['status'] == 'prepared_for_visual_review':
                parts.append('<img src="' + r['image'] + '" width="174">')
            else:
                parts.append('图片不可用：' + escape(r['reason']))
            parts.append('<small>' + escape(f"行 {r['evidence']['line']} · {r['captured_at_utc']} · {r['recorded_slot_status']} · {r['item_id'] or r['candidate']}") + '</small>')
            if r['economy_selection'] is not None:
                s = r['economy_selection']
                parts.append('<small>' + escape(f"日志选取值 {s['selected_value']}；当前原观测 {s['current_observed_value']}；来源时间 {s['selected_captured_at_utc']}") + '</small>')
            parts.append('</td>')
        parts.append('</tr></table>')
    (output / 'index.html').write_text(''.join(parts), encoding='utf-8')
