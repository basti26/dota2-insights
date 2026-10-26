"""Freeze full-image glyph detections before reading any projected landmarks."""
import math
from pathlib import Path

from .map_holdout import read_plan, sha, load, save, local, distribution
from .map_glyph_probe import detect_glyph,detect_building_silhouette

ICONS = {'minimap_lotuspool': ('lotus_pool',),
    'minimap_watcher': ('watch_tower',),
    'minimap_rune_bounty': ('bounty_spawn_origin', 'power_rune_spawn_origin'),
    'minimap_rune_unknown': ('bounty_spawn_origin', 'power_rune_spawn_origin')}
BUILDING_ICONS={'minimap_ancient':('ancient',),'minimap_racks90':('barracks',),'minimap_racks45':('barracks',)}
ALL_ICONS={**ICONS,**BUILDING_ICONS}


def icon_rect(definitions, name):
    import re
    match = re.search(r'"' + re.escape(name) + r'"\s*\{([^{}]+)\}', definitions)
    if match is None:
        raise ValueError('map_icon_definition_missing')
    fields = dict(re.findall(r'"([^"\n]+)"\s*"([^"\n]+)"', match[1]))
    if fields.get('file') != 'materials/vgui/hud/minimap_sheet.vmat':
        raise ValueError('map_icon_sheet_mismatch')
    rect = [int(fields[k]) for k in ('x', 'y', 'width', 'height')]
    if min(rect) < 0 or not 8 <= min(rect[2:]) <= max(rect[2:]) <= 256:
        raise ValueError('map_icon_rect_invalid')
    return rect


def observe(package, icon_root, output,*,profile='basic'):
    from PIL import Image
    import numpy as np
    from .history import checked_input
    root = Path(__file__).resolve().parents[1]
    package, icon_root = checked_input(root, package), checked_input(root, icon_root)
    output = Path(output)
    plan, _ = read_plan(package)
    definitions_path = icon_root / 'mod_textures.txt'
    sheet_path = icon_root / 'decoded/minimap_sheet_psd_b65d6dd9.png'
    sources = {str(p): sha(p) for p in (definitions_path, sheet_path, Path(__file__),
                                      Path(__file__).with_name('map_glyph_probe.py'))}
    definitions = definitions_path.read_text(encoding='utf-8')
    with Image.open(sheet_path) as im:
        sheet = im.convert('RGBA')
    templates = {}
    rects = {}
    if profile not in ('basic','buildings','buildings_tint'):raise ValueError('map_icon_profile_invalid')
    icons=ICONS if profile=='basic' else BUILDING_ICONS
    for name in icons:
        x, y, w, h = rects[name] = icon_rect(definitions, name)
        if x+w > sheet.width or y+h > sheet.height:
            raise ValueError('map_icon_outside_sheet')
        templates[name] = np.asarray(sheet.crop((x, y, x+w, y+h)))
    rows = []
    for frame in plan['frames']:
        with Image.open(local(package, frame['image'])) as im:
            image = np.asarray(im.convert('RGB'))
        for name, template in templates.items():
            rows.append({'case': frame['case'], 'frame_index': frame['frame_index'],
                'icon': name, 'image_sha256': frame['image_sha256'], 'crop_width': frame['size'][0],
                'detection': detect_building_silhouette(image,template) if profile=='buildings_tint' else
                    detect_glyph(image, template,**({'minimum_center_distance':6,'per_scale_limit':32}
                    if profile=='buildings' else {}))})
    if any(sha(p) != digest for p, digest in sources.items()):
        raise ValueError('map_icon_source_changed')
    # Verify the images again before freezing, still without predictions.
    read_plan(package)
    result = {'schema_version': 1, 'kind': 'map_glyph_observations',
        'package_sha256': sha(package / 'package.json'), 'map_build': plan['map_build'],
        'sources': sources, 'icon_sheet_rects': rects, 'predictions_read': False, 'rows': rows,
        'icon_profile':profile,'icons':list(icons),
        'identity_confirmed': False, 'formal_p2_pass': False, 'map_certified': False}
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'observations.json', result)
    save(output / 'freeze.json', {'observations_sha256': sha(output / 'observations.json'),
        'package_sha256': result['package_sha256'], 'predictions_read': False})
    return result


def compare_set(observed, expected, *, crop_width, overflow=False):
    """Unordered compatibility, with no named identity assignment or refitting."""
    if (type(crop_width) not in (int, float) or not math.isfinite(crop_width)
            or not 64 <= crop_width <= 1024):
        raise ValueError('map_set_crop_width_invalid')
    for points in (observed, expected):
        if not isinstance(points, list) or len(points) > 16:
            raise ValueError('map_set_points_invalid')
        if any(not isinstance(p, list) or len(p) != 2 or any(
            type(v) not in (int, float) or not math.isfinite(v) for v in p) for p in points):
            raise ValueError('map_set_points_invalid')
    if overflow or not expected or len(observed) != len(expected):
        return {'status': 'unjudgeable_count_or_overflow', 'errors_reference_px': None}
    # Two best exact assignments via subset DP, bounded to 16 points. Never refit.
    distances=[[math.dist(p,q)*360/crop_width for q in expected] for p in observed]
    states={0:[(0.,())]}
    for i in range(len(observed)):
        next_states={}
        for mask,paths in states.items():
            for cost,path in paths:
                for j in range(len(expected)):
                    if mask & (1<<j):continue
                    key=mask | (1<<j)
                    entry=cost+distances[i][j]**2,path+(j,)
                    best=next_states.setdefault(key,[])
                    if entry not in best:
                        best.append(entry);best.sort();del best[2:]
        states=next_states
    choices=states[(1<<len(expected))-1]
    if len(choices) > 1 and abs(choices[0][0]-choices[1][0]) < 1e-9:
        return {'status': 'unjudgeable_assignment', 'errors_reference_px': None}
    errors = [distances[i][j] for i,j in enumerate(choices[0][1])]
    return {'status': 'setwise_compatible' if max(errors) <= 1.5 else 'setwise_mismatch',
        'errors_reference_px': errors}


def compare(package, batch, output):
    package, batch, output = Path(package), Path(batch), Path(output)
    plan, manifest = read_plan(package)
    freeze = load(batch / 'freeze.json')
    if (sha(batch / 'observations.json') != freeze['observations_sha256']
            or sha(package / 'package.json') != freeze['package_sha256']):
        raise ValueError('map_glyph_frozen_sources_changed')
    obs = load(batch / 'observations.json')
    if (obs['package_sha256'] != freeze['package_sha256'] or obs['predictions_read'] is not False
            or obs.get('map_build') != plan['map_build'] or freeze.get('predictions_read') is not False):
        raise ValueError('map_glyph_observation_binding')
    frames = {(f['case'], f['frame_index']): f for f in plan['frames']}
    icons=obs.get('icons',list(ICONS))
    profile=obs.get('icon_profile','basic')
    expected_icons=list(ICONS) if profile=='basic' else list(BUILDING_ICONS) if profile in ('buildings','buildings_tint') else None
    if not isinstance(icons,list) or icons!=expected_icons:
        raise ValueError('map_icon_profile_invalid')
    expected_keys = {(case, number, icon) for case, number in frames for icon in icons}
    seen = set()
    for row in obs['rows']:
        key = row['case'], row['frame_index'], row['icon']
        frame = frames.get(key[:2])
        if (key not in expected_keys or key in seen or frame is None
                or row['image_sha256'] != frame['image_sha256']
                or row['crop_width'] != frame['size'][0]):
            raise ValueError('map_glyph_full_frame_roster_required')
        seen.add(key)
    if seen != expected_keys:
        raise ValueError('map_glyph_full_frame_roster_required')
    predictions_path = package / 'reference/predictions.json'
    if sha(predictions_path) != manifest['files']['reference/predictions.json']:
        raise ValueError('map_glyph_predictions_changed')
    predictions = load(predictions_path)
    # The first access to predictions occurs only after the persisted observation hash check.
    if not isinstance(predictions, list):
        predictions = predictions['predictions']
    categories = {p['id']: p['category'] for p in plan['points']}
    result_rows = []
    for row in obs['rows']:
        points = [p['pixel_xy'] for p in predictions if p['case'] == row['case']
                  and categories[p['point_id']] in ALL_ICONS[row['icon']]]
        detection = row['detection']
        candidates=[p['pixel_xy'] for p in detection['candidates']]
        comparison=compare_set(candidates,points,crop_width=row['crop_width'],overflow=detection['overflow']) if len(candidates)<=16 else {
            'status':'unjudgeable_candidate_budget','errors_reference_px':None}
        result_rows.append({**row, 'expected_point_count': len(points),'comparison':comparison})
    groups=[]
    if icons==list(BUILDING_ICONS):
        for (case,frame_index),frame in frames.items():
            selected=[r for r in result_rows if r['case']==case and r['frame_index']==frame_index and r['icon'].startswith('minimap_racks')]
            raw=sorted([p for r in selected for p in r['detection']['candidates']],key=lambda p:-p['score'])
            merged=[]
            for p in raw:
                if not any(math.dist(p['pixel_xy'],q['pixel_xy'])<5 for q in merged):merged.append(p)
            points=[p['pixel_xy'] for p in predictions if p['case']==case and categories[p['point_id']]=='barracks']
            comparison=compare_set([p['pixel_xy'] for p in merged],points,crop_width=frame['size'][0],
                overflow=any(r['detection']['overflow'] for r in selected)) if len(merged)<=16 else {
                    'status':'unjudgeable_candidate_budget','errors_reference_px':None}
            groups.append({'case':case,'frame_index':frame_index,'category':'barracks',
                'observed_candidates':merged,'expected_point_count':len(points),'comparison':comparison,
                'identity_confirmed':False,'method':'union_of_official_racks45_racks90_full_crop_candidates'})
    result = {'schema_version': 1, 'kind': 'map_glyph_setwise_comparison',
        'observations_sha256': freeze['observations_sha256'], 'rows': result_rows,'groups':groups,
        'errors_reference_px': distribution([e for r in result_rows
            for e in r['comparison']['errors_reference_px'] or []]),
        'identity_confirmed': False, 'formal_p2_pass': False, 'map_certified': False,
        'claim': 'unordered_glyph_sets_only_not_named_entities_or_wards'}
    output.mkdir(parents=True, exist_ok=False)
    save(output / 'comparison.json', result)
    write_review(package, output, plan, result)
    return result


def write_review(package, output, plan, result):
    from collections import Counter
    from PIL import Image, ImageDraw
    icons=list(dict.fromkeys(r['icon'] for r in result['rows']))
    colors = dict(zip(ALL_ICONS, ('#00ffff', '#ff00ff', '#ffff00', '#ffffff','#ff9900','#00ff66','#ff4466')))
    frames = {(f['case'], f['frame_index']): f for f in plan['frames']}
    lines = ['# 地图图标核对', '',
        '图标搜索先冻结，再读坐标预测；全图搜索，不按预测点裁剪。',
        '候选得分不是概率。集合不符可能来自误匹配或漏检，不能直接归咎坐标映射。',
        '本批未确认命名实体身份，不认证地图、眼位、世界坐标或封野范围。', '',
        '| 场景 | 图标 | 3帧集合比较结果 |', '|---|---|---|']
    images = []
    for case in plan['cases']:
        rows = [r for r in result['rows'] if r['case'] == case]
        for icon in icons:
            counts = Counter(r['comparison']['status'] for r in rows if r['icon'] == icon)
            lines.append(f'| {case} | {icon} | {dict(counts)} |')
        frame = frames[(case, 31)]
        with Image.open(local(package, frame['image'])) as original:
            im = original.convert('RGB').resize((frame['size'][0]*3, frame['size'][1]*3))
        draw = ImageDraw.Draw(im)
        for row in rows:
            if row['frame_index'] != 31:
                continue
            for candidate in row['detection']['candidates']:
                x, y, w, h = candidate['rect']
                color = colors[row['icon']]
                draw.rectangle((x*3, y*3, (x+w)*3, (y+h)*3), outline=color, width=2)
                draw.text((x*3, max(0,y*3-12)), f"{row['icon'].removeprefix('minimap_')} {candidate['score']:.3f}", fill=color)
        im.save(output / (case + '-candidates.png'))
        images += ['', f'![{case}图像候选]({case}-candidates.png)', '']
    lines += images
    if result.get('groups'):
        lines+=['','## 合并的兵营候选集合','','|场景/帧|候选数|预期数|结果|','|---|---:|---:|---|']
        for g in result['groups']:
            lines.append(f"|{g['case']}/{g['frame_index']}|{len(g['observed_candidates'])}|{g['expected_point_count']}|{g['comparison']['status']}|")
    lines += ['', '框仅代表资源模板的检测候选，不是确认的建筑类型、实体身份或地标。资源图标名不能替代实际语义确认。', '']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
