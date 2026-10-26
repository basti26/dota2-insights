"""Bounded contiguous minimap windows; image links never become hero events."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(root=ROOT, *, roots=None, latest=3, max_runs=2, max_images=24):
    import numpy as np
    from PIL import Image, ImageDraw
    from dota2_insights import minimap_pair_probe as algorithm
    from dota2_insights.history import checked_output
    from dota2_insights.match_journals import _read
    from tools.prepare_minimap_review import prepare
    root = Path(root).resolve()
    prepared = prepare(root, roots=roots, latest=latest, max_runs=max_runs,
                       max_images=max_images, selection='continuous_windows')
    source = Path(prepared['output'])
    raw_batch = _read(source / 'batch.json', 16 * 1024**2)
    batch = json.loads(raw_batch)
    output = checked_output(root, root / 'diagnostics/minimap-sequence-probe' /
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True, exist_ok=False)
    reports = []
    for group in batch['results']:
        entries = group['result']['entries']
        for window in group['result']['selection_windows']:
            frames, missing, previews = [], [], []
            for entry in entries:
                if window['window_id'] not in entry['window_ids']:
                    continue
                if entry['status'] != 'prepared_for_visual_review':
                    missing.append(entry['sample_ordinal'])
                    continue
                path = (source / group['directory'] / entry['image']).resolve()
                if not path.is_relative_to(source):
                    raise ValueError('sequence_probe_image_outside_review')
                raw = _read(path, 8 * 1024**2)
                if hashlib.sha256(raw).hexdigest() != entry['declared_image_sha256']:
                    raise ValueError('sequence_probe_prepared_image_changed')
                with Image.open(BytesIO(raw)) as encoded:
                    if encoded.mode != 'RGB' or list(encoded.size) != entry['dimensions']:
                        raise ValueError('sequence_probe_prepared_geometry_changed')
                    image = encoded.copy()
                frames.append((entry, np.asarray(image)[:, :, ::-1].copy()))
                preview = Image.new('RGB', (384, 420), '#20252b')
                draw = ImageDraw.Draw(preview)
                draw.text((6, 5), f"sample {entry['sample_ordinal']} / seq {entry['sequence']}", fill='white')
                draw.text((6, 22), str(entry['captured_at_utc'])[:34], fill='white')
                image.thumbnail((372, 372), Image.Resampling.NEAREST)
                preview.paste(image, (6, 42))
                previews.append(preview)
            pairs = []
            pair_previews = []
            for (before, old), (after, new) in zip(frames, frames[1:]):
                comparison = algorithm.compare(old, new, before, after)
                annotated = Image.fromarray(new[:, :, ::-1])
                draw = ImageDraw.Draw(annotated)
                for link in comparison['links']:
                    x, y = link['current_center']
                    color = '#ffb020' if link['pixel_motion'] == 'larger_displacement' else '#70d8ff'
                    draw.line((*link['previous_center'], x, y), fill=color, width=1)
                    draw.ellipse((x - 2, y - 2, x + 2, y + 2), outline=color, width=1)
                pair_previews.append(annotated)
                pairs.append({'previous_sample_ordinal': before['sample_ordinal'], 'current_sample_ordinal': after['sample_ordinal'],
                    'previous_source_evidence': before['source_evidence'], 'current_source_evidence': after['source_evidence'],
                    'annotated_image': f'pair-{len(pairs):02d}.png', 'comparison': comparison})
            directory = output / f'window-{len(reports):03d}'
            directory.mkdir()
            for pair, preview in zip(pairs, pair_previews):
                preview.save(directory / pair['annotated_image'])
            if previews:
                sheet = Image.new('RGB', (384 * min(4, len(previews)), 420 * ((len(previews) + 3) // 4)), '#20252b')
                for i, preview in enumerate(previews):
                    sheet.paste(preview, ((i % 4) * 384, (i // 4) * 420))
                sheet.save(directory / 'contact.png')
                previews[0].save(directory / 'preview.gif', save_all=True, append_images=previews[1:],
                                 duration=500, loop=0)
            report = {'window': window, 'source_review_directory': group['directory'],
                'frames_prepared': len(frames), 'unavailable_sample_ordinals': missing,
                'preview_timing': 'fixed_500ms_per_frame_not_realtime_playback',
                'pairs': pairs, 'hero_identity_verified': False, 'hero_disappearance_inferred': False}
            (directory / 'window.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            reports.append({**report, 'directory': directory.name})
    if _read(source / 'batch.json', 16 * 1024**2) != raw_batch:
        raise ValueError('sequence_probe_input_batch_changed')
    pairs = [p['comparison'] for report in reports for p in report['pairs']]
    compared = [p for p in pairs if p['state'] == 'compared']
    links = [link for pair in compared for link in pair['links']]
    summary = {'windows': len(reports), 'window_frame_observations': sum(r['frames_prepared'] for r in reports),
        'pairs_compared': len(compared), 'pairs_unavailable': len(pairs) - len(compared),
        'unavailable_reasons': dict(Counter(p['reason'] for p in pairs if p['state'] != 'compared')),
        'mutual_unique_component_links': len(links),
        'small_displacement_links': sum(link['pixel_motion'] == 'small_displacement' for link in links),
        'larger_displacement_links': sum(link['pixel_motion'] == 'larger_displacement' for link in links),
        'links_with_square_overlap': sum(link['square_overlap_either_frame'] for link in links),
        'links_with_text_overlap': sum(link['text_overlap_either_frame'] for link in links)}
    result = {'schema_version': 1, 'kind': 'minimap_sequence_probe', 'prepared_review': prepared,
        'algorithm_source_sha256': hashlib.sha256(Path(algorithm.__file__).read_bytes()).hexdigest(),
        'batch_sha256': hashlib.sha256(raw_batch).hexdigest(), 'summary': summary, 'windows': reports,
        'hero_count': None, 'prediction_accuracy_verified': False, 'online_changes_applied': False}
    lines = ['# 小地图连续样本探索', '', '候选对应只描述图像变化，不是英雄轨迹或消失。',
        'GIF固定每帧500ms，仅供看图；原采集UTC和比较门禁保留在window.json。', '', str(summary), '']
    for i, report in enumerate(reports):
        lines += [f"## 窗口 {i + 1}", '', f"原样本范围：{report['window']['first_sample_ordinal']}–{report['window']['last_sample_ordinal']}。",
            f"![连续原图]({report['directory']}/contact.png)", '',
            f"[固定速度预览]({report['directory']}/preview.gif)", '']
        for pair in report['pairs']:
            lines.append(f"- [原样本{pair['previous_sample_ordinal']}→{pair['current_sample_ordinal']}的图像对应]"
                         f"({report['directory']}/{pair['annotated_image']})：{pair['comparison']['state']} / {pair['comparison']['reason']}")
        lines += ['', '对应图浅蓝为小位移、橙色为较大位移；均不是英雄或敌我标签。', '']
    (output / 'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'output': str(output), **summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latest', type=int, default=3)
    parser.add_argument('--max-runs', type=int, default=2)
    parser.add_argument('--max-images', type=int, default=24)
    args = parser.parse_args()
    print(json.dumps(run(latest=args.latest, max_runs=args.max_runs, max_images=args.max_images), ensure_ascii=False))


if __name__ == '__main__':
    main()
