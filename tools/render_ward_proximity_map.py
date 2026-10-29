"""Render descriptive ward neighborhoods on the existing pinned illustrative map."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--analysis', type=Path, required=True)
    cli.add_argument('--map', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    args = cli.parse_args()
    raw = args.analysis.read_bytes()
    data = json.loads(raw)
    map_raw = args.map.read_bytes()
    map_sha = hashlib.sha256(map_raw).hexdigest()
    if map_sha != '9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764':
        raise ValueError('pinned_illustrative_map_required')
    if args.output.exists():
        raise ValueError('new_output_required')
    args.output.mkdir(parents=True)
    font_path = 'C:/Windows/Fonts/msyh.ttc'
    font = ImageFont.truetype(font_path, 21)
    small = ImageFont.truetype(font_path, 17)
    title = ImageFont.truetype(font_path, 30)
    base = Image.open(args.map).convert('RGB')
    width = 1200
    height = round(base.height/base.width*width)
    base = base.resize((width, height))
    group = next(g for g in data['radii'] if g['radius'] == 256)
    # Exactly the transform used by the existing pinned-map reports.
    # This is illustrative legacy-map registration, not live map certification.
    def xy(x, y):
        return (((8878-8356)/2+(x-7487)/18322*8356)/8878*width,
                (1-(y-7693)/18322)*8356/8878*width+100)
    outputs = []
    for kind, color, prefix, name in [('observer', '#39ed78', 'A', '假眼'),
                                      ('sentry', '#ff5252', 'S', '真眼')]:
        canvas = Image.new('RGB', (width, height+450), '#131c25')
        canvas.paste(base, (0, 100))
        draw = ImageDraw.Draw(canvas)
        draw.text((22, 10), f'{name}重复落点 · 最新3盘职业比赛', font=title, fill=color)
        draw.text((22, 57), '小点=每次放置；编号=256单位邻域；圈不是视野范围', font=font, fill='white')
        for row in data['rows']:
            if row['ward_type'] != kind:
                continue
            x, y = xy(row['x'], row['y'])
            draw.ellipse((x-4, y-4, x+4, y+4), fill=color, outline='black', width=2)
        selected = [h for h in group['hotspots'] if h['ward_type'] == kind and h['match_count'] >= 2][:6]
        for i, hotspot in enumerate(selected, 1):
            x, y = xy(hotspot['x'], hotspot['y'])
            radius = 256/18322*8356/8878*width
            draw.ellipse((x-radius, y-radius, x+radius, y+radius), outline='white', width=2)
            label = f'{prefix}{i}'
            # Displace labels consistently; center remains the original placement.
            lx, ly = x+14, y-27
            box = draw.textbbox((lx, ly), label, font=font)
            draw.line((x, y, lx, ly+10), fill='white', width=2)
            draw.rectangle((box[0]-4, box[1]-2, box[2]+4, box[3]+3), fill='#131c25')
            draw.text((lx, ly), label, font=font, fill=color)
            ends = hotspot['end_kinds']
            text = (f'{label}  {hotspot["placement_count"]}次 / {hotspot["match_count"]}盘'
                    f'    被击杀记录 {ends.get("killed",0)} · 到期 {ends.get("expired",0)}')
            draw.text((24, height+115+(i-1)*33), text, font=font, fill=color)
        draw.text((24, height+330), '底图为已有示意图；当前比赛地图版本及逐点落位尚未独立核验。', font=small, fill='#ffcf72')
        draw.text((24, height+357), '邻域可能重叠，不可加总；频率不等于有效性，结束类型来自录像解析。', font=small, fill='#c8d1da')
        draw.text((24, height+384), '比赛：9065794389 / 9065883621 / 9065986613', font=small, fill='#c8d1da')
        path = args.output/(kind+'.png')
        canvas.save(path)
        outputs.append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'labels': [{'label': prefix+str(i), **h} for i,h in enumerate(selected,1)]})
    if args.analysis.read_bytes() != raw or args.map.read_bytes() != map_raw:
        raise ValueError('source_changed')
    evidence = {'analysis_sha256': hashlib.sha256(raw).hexdigest(), 'map_sha256': map_sha,
                'registration': 'legacy_pinned_map_transform_same_as_ward_revisits',
                'current_map_certified': False, 'outputs': outputs}
    (args.output/'evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'outputs': [o['file'] for o in outputs]}))


if __name__ == '__main__':
    main()
