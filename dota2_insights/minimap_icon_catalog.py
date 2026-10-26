"""Bounded original-atlas sprite catalog; resource names do not certify objects."""
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re

from .match_journals import _read

SHEET = 'materials/vgui/hud/minimap_sheet.vmat'


def definitions(text, size):
    if not isinstance(text, str) or len(text) > 2 * 1024**2:
        raise ValueError('minimap_icon_definitions_limit')
    text = re.sub(r'//[^\n]*', '', text)
    entries, unsupported, seen = [], [], set()
    for name, body in re.findall(r'"([^"\n]+)"\s*\{([^{}]+)\}', text):
        pairs = re.findall(r'"([^"\n]+)"\s*"([^"\n]*)"', body)
        fields = dict(pairs)
        if fields.get('file') != SHEET:
            continue
        if len(pairs) != len(fields) or name in seen or not re.fullmatch(r'[a-zA-Z0-9_]{1,100}', name):
            raise ValueError('minimap_icon_duplicate_or_invalid_definition')
        seen.add(name)
        if len(seen) > 512:
            raise ValueError('minimap_icon_catalog_limit')
        values = [fields.get(key) for key in ('x', 'y', 'width', 'height')]
        if any(not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,5}', value) for value in values):
            unsupported.append({'name': name, 'reason': 'missing_or_invalid_rectangle'})
            continue
        x, y, width, height = map(int, values)
        if not 1 <= width <= 256 or not 1 <= height <= 256 or x + width > size[0] or y + height > size[1]:
            unsupported.append({'name': name, 'reason': 'rectangle_outside_atlas_or_limit'})
            continue
        entries.append({'name': name, 'rect': [x, y, width, height], 'material': SHEET})
    if not seen:
        raise ValueError('minimap_icon_sheet_definitions_missing')
    return sorted(entries, key=lambda entry: entry['name']), unsupported


def export(icon_root, output):
    from PIL import Image, ImageDraw
    icon_root, output = Path(icon_root).resolve(), Path(output).resolve()
    if output.is_relative_to(icon_root):
        raise ValueError('minimap_icon_output_overlaps_source')
    definitions_path = icon_root / 'mod_textures.txt'
    sheet_path = icon_root / 'decoded/minimap_sheet_psd_b65d6dd9.png'
    for path in (definitions_path, sheet_path, sheet_path.parent):
        if path.is_symlink() or path.is_junction():
            raise ValueError('minimap_icon_source_link_refused')
    raw_definitions = _read(definitions_path, 2 * 1024**2)
    raw_sheet = _read(sheet_path, 16 * 1024**2)
    with Image.open(BytesIO(raw_sheet)) as source:
        if source.format != 'PNG' or not 64 <= min(source.size) <= max(source.size) <= 2048 or source.mode != 'RGBA':
            raise ValueError('minimap_icon_original_rgba_atlas_required')
        sheet = source.copy()
    entries, unsupported = definitions(raw_definitions.decode('utf-8-sig'), sheet.size)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'icons').mkdir()
    tiles = []
    for entry in entries:
        x, y, w, h = entry['rect']
        icon = sheet.crop((x, y, x + w, y + h))
        filename = 'icons/' + entry['name'] + '.png'
        icon.save(output / filename)
        entry.update(file=filename, png_sha256=hashlib.sha256((output / filename).read_bytes()).hexdigest(),
            rgba_pixel_sha256=hashlib.sha256(icon.tobytes()).hexdigest(), size=list(icon.size),
            visible_alpha_pixels=sum(value > 16 for value in icon.getchannel('A').getdata()),
            semantic_identity_verified=False)
        tile = Image.new('RGB', (360, 160), '#30343b')
        draw = ImageDraw.Draw(tile)
        draw.text((6, 5), entry['name'], fill='white')
        draw.text((6, 23), f"rect {entry['rect']} / original RGBA", fill='#ffc85a')
        for i, tint in enumerate(((1, 1, 1), (.1, 1, .1), (1, .1, .1))):
            rgb = icon.convert('RGB')
            tinted = Image.merge('RGBA', tuple(rgb.getchannel(channel).point(lambda value, factor=factor: round(value * factor))
                for channel, factor in zip(('R', 'G', 'B'), tint)) + (icon.getchannel('A'),))
            tinted = tinted.resize((tinted.width * 2, tinted.height * 2), Image.Resampling.NEAREST)
            tinted.thumbnail((100, 100), Image.Resampling.NEAREST)
            tile.paste(tinted, (6 + i * 116, 48), tinted)
        tiles.append(tile)
    for start in range(0, len(tiles), 12):
        part = tiles[start:start + 12]
        canvas = Image.new('RGB', (1440, 160 * ((len(part) + 3) // 4)), '#30343b')
        for i, tile in enumerate(part):
            canvas.paste(tile, ((i % 4) * 360, (i // 4) * 160))
        canvas.save(output / f'sheet-{start // 12:02d}.png')
    if _read(definitions_path, 2 * 1024**2) != raw_definitions or _read(sheet_path, 16 * 1024**2) != raw_sheet:
        raise ValueError('minimap_icon_sources_changed')
    result = {'schema_version': 1, 'kind': 'minimap_original_icon_catalog',
        'definitions_sha256': hashlib.sha256(raw_definitions).hexdigest(),
        'atlas_png_sha256': hashlib.sha256(raw_sheet).hexdigest(), 'atlas_size': list(sheet.size),
        'entries': entries, 'unsupported_definitions': unsupported,
        'tint_previews': 'illustrations_only_not_certified_renderer_colors',
        'source_current_install_verified': False, 'semantic_identity_verified': False,
        'game_patch_verified': False, 'online_changes_applied': False}
    lines = ['# 小地图原始资源目录', '', '原图矩形和像素已绑定；资源名不等于画面身份。绿/红只是示意染色，不是经认证的实际渲染。',
             f"图标：{len(entries)}；不支持定义：{len(unsupported)}。", '']
    for i in range((len(tiles) + 11) // 12):
        lines += [f'![资源页 {i}](sheet-{i:02d}.png)', '']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'catalog.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
