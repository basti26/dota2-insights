"""Bounded original-pixel review for admitted minimap logs, never identity truth."""
from collections import Counter
from io import BytesIO
import hashlib
import json
from pathlib import Path
import re

from .helper_handoff import utc
from .match_journals import _read


def _image(folder, row):
    from PIL import Image
    name, expected = row.get('image'), row.get('image_sha256')
    if (not isinstance(name, str) or not re.fullmatch(r'frames/frame-[0-9]{6}\.png', name)
            or not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected)):
        raise ValueError('minimap_review_bound_image_missing')
    folder = Path(folder)
    path = folder / name
    for parent in (path, path.parent, folder):
        if parent.is_symlink() or parent.is_junction():
            raise ValueError('minimap_review_image_link_refused')
    if not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError('minimap_review_image_outside_run')
    raw = _read(path, 8 * 1024**2)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError('minimap_review_image_sha_mismatch')
    roi, client = row.get('roi'), row.get('client_size')
    if (not isinstance(roi, list) or len(roi) != 4 or any(type(v) is not int for v in roi)
            or not isinstance(client, list) or len(client) != 2 or any(type(v) is not int for v in client)
            or not 0 < client[0] <= 8192 or not 0 < client[1] <= 4320
            or min(roi[:2]) < 0 or not 64 <= roi[2] <= 1024 or not 64 <= roi[3] <= 1024
            or roi[0] + roi[2] > client[0] or roi[1] + roi[3] > client[1]):
        raise ValueError('minimap_review_geometry_unavailable')
    with Image.open(BytesIO(raw)) as encoded:
        if encoded.format != 'PNG' or encoded.size != tuple(roi[2:]) or encoded.mode != 'RGB':
            raise ValueError('minimap_review_png_geometry_or_format')
        image = encoded.copy()
    pixel_sha = row.get('pixel_sha256')
    if pixel_sha is not None:
        import numpy as np
        if (not isinstance(pixel_sha, str) or not re.fullmatch('[a-f0-9]{64}', pixel_sha)
                or hashlib.sha256(np.asarray(image)[:, :, ::-1].tobytes()).hexdigest() != pixel_sha):
            raise ValueError('minimap_review_pixel_sha_mismatch')
    return image, raw, path


def _prepare(rows_factory, image_folder, source, output, *, max_images, metadata, selection='spread_plus_largest_capture_gap'):
    from PIL import Image, ImageDraw
    if type(max_images) is not int or not 2 <= max_images <= 24:
        raise ValueError('minimap_review_image_limit_invalid')
    if selection not in ('spread_plus_largest_capture_gap', 'continuous_windows'):
        raise ValueError('minimap_review_selection_invalid')
    total = regressions = repeated_times = reused = layout_changes = missing_clock = 0
    previous = previous_layout = None
    max_gap, gap_pair = None, None
    statuses = Counter()
    for number, row, evidence in rows_factory():
        if row.get('kind') != 'sample':
            if isinstance(row.get('status'), str):
                statuses[row['status'][:80] if len(statuses) < 32 else 'other_status'] += 1
            continue
        total += 1
        try:
            observed = utc(row.get('captured_at_utc'))
        except (ValueError, TypeError):
            observed = None
            missing_clock += 1
        layout = (row.get('client_size'), row.get('roi'), row.get('layout_segment'))
        if previous_layout is not None and layout != previous_layout:
            layout_changes += 1
        previous_layout = layout
        if previous is not None:
            prior_number, prior_row, prior_time = previous
            reused += int(prior_row.get('image') == row.get('image') and row.get('image') is not None)
            if observed is not None and prior_time is not None:
                gap = (observed - prior_time).total_seconds()
                regressions += int(gap < 0)
                repeated_times += int(gap == 0)
                if gap >= 0 and (max_gap is None or gap > max_gap):
                    max_gap, gap_pair = gap, (total - 1, total)
        previous = number, row, observed
    # Spread across the run and include the largest observed capture gap.
    count = min(total, max_images)
    selected = {1, total} if total else set()
    if gap_pair is not None and max_images >= 4:
        selected.update(gap_pair)
    if count > 1:
        for i in range(count):
            if len(selected) >= count:
                break
            selected.add(1 + round(i * (total - 1) / (count - 1)))
    windows = []
    if selection == 'continuous_windows':
        selected = set()
        groups = min(3, max_images, total)
        seeds = [(max(1, round(total / 3)), 'first_third'), (max(1, round(2 * total / 3)), 'second_third'),
                 (gap_pair[0] if gap_pair else max(1, round(total / 2)), 'largest_gap_or_middle')]
        if groups < 3:
            seeds = [(1, 'first_sample'), (total, 'last_sample')][:groups]
        for window_id, (center, reason) in enumerate(seeds[:groups]):
            size = min(total, max_images // groups)
            start = max(1, min(center - size // 2, total - size + 1))
            end = start + size - 1
            selected.update(range(start, end + 1))
            windows.append({'window_id': window_id, 'center_sample_ordinal': center,
                'first_sample_ordinal': start, 'last_sample_ordinal': end, 'selection_reason': reason})
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'samples').mkdir()
    entries, tiles, verified = [], [], []
    ordinal = 0
    for number, row, evidence in rows_factory():
        if row.get('kind') != 'sample':
            continue
        ordinal += 1
        if ordinal not in selected:
            continue
        entry = {'sample_ordinal': ordinal, 'sequence': row.get('sequence'),
            'window_ids': [w['window_id'] for w in windows if w['first_sample_ordinal'] <= ordinal <= w['last_sample_ordinal']],
            'source_evidence': {**evidence, 'line': number, 'source': source},
            'captured_at_utc': row.get('captured_at_utc'), 'time_basis': row.get('time_basis'),
            'client_size': row.get('client_size'), 'roi': row.get('roi'), 'layout_segment': row.get('layout_segment'),
            'declared_image': row.get('image'), 'declared_image_sha256': row.get('image_sha256'),
            'declared_duplicate_pixels': row.get('duplicate_pixels')}
        try:
            image, raw, path = _image(image_folder, row)
            destination = f'samples/sample-{ordinal:06d}.png'
            (output / destination).write_bytes(raw)
            entry.update(status='prepared_for_visual_review', image=destination,
                image_bytes_sha_verified=True, pixel_sha_verified=row.get('pixel_sha256') is not None,
                dimensions=list(image.size))
            verified.append((path, hashlib.sha256(raw).hexdigest()))
            tile = Image.new('RGB', (360, 340), '#20252b')
            draw = ImageDraw.Draw(tile)
            draw.text((8, 6), f"sample {ordinal} / line {number}", fill='white')
            draw.text((8, 24), str(row.get('captured_at_utc'))[:40], fill='white')
            draw.text((8, 42), f"ROI {row.get('roi')}", fill='#ffc85a')
            image.thumbnail((336, 268), Image.Resampling.NEAREST)
            tile.paste(image, (8, 64))
            tiles.append(tile)
        except (OSError, ValueError) as exc:
            entry.update(status='unavailable', reason=str(exc) if isinstance(exc, ValueError) else 'image_unreadable')
        entries.append(entry)
    if ordinal != total or len(entries) != len(selected):
        raise ValueError('minimap_review_source_rows_changed')
    # Recheck prepared originals before publishing a success evidence manifest.
    for path, digest in verified:
        if hashlib.sha256(_read(path, 8 * 1024**2)).hexdigest() != digest:
            raise ValueError('minimap_review_prepared_image_changed')
    return {'schema_version': 1, **metadata, 'entries': entries,
        'sample_rows': total, 'selected_sample_count': len(entries), 'selection': selection, 'selection_windows': windows,
        'max_observed_capture_gap_s': max_gap, 'capture_clock_regressions': regressions,
        'repeated_capture_timestamps': repeated_times, 'missing_capture_timestamps': missing_clock,
        'adjacent_reused_image_references': reused, 'recorded_layout_changes': layout_changes,
        'capture_status_rows': dict(statuses), 'all_selected_images_verified': bool(entries) and len(verified) == len(entries),
        'source_verified_before_and_after': True, 'hero_identity_verified': False,
        'map_calibration_verified': False, 'whole_match_coverage_verified': False,
        'visual_verdicts_entered': False, 'match_association': 'unknown_no_time_inference',
        'meaning': 'capture_intervals_not_inferred_hero_disappearances_or_packet_loss'}, tiles


def _finish(output, result, tiles):
    from PIL import Image
    output = Path(output)
    if tiles:
        sheet = Image.new('RGB', (1080, 340 * ((len(tiles) + 2) // 3)), '#20252b')
        for i, tile in enumerate(tiles):
            sheet.paste(tile, ((i % 3) * 360, (i // 3) * 340))
        sheet.save(output / 'contact.png')
    lines = ['# 小地图图像抽样复核', '', '比赛归属未知；只核验抽中的图像与日志对应，不判断英雄或地图坐标。', '',
        f"样本行：{result['sample_rows']}；抽中：{result['selected_sample_count']}；原采集时钟最大间隔：{result['max_observed_capture_gap_s']}秒。",
        f"时钟倒退：{result['capture_clock_regressions']}；重复时钟：{result['repeated_capture_timestamps']}；相邻复用图：{result['adjacent_reused_image_references']}；布局记录变化：{result['recorded_layout_changes']}。",
        '采集间隔不等于丢帧、掉线或英雄消失；静止画面复用不等于程序卡住。', '']
    if tiles:
        lines.append('![抽样图](contact.png)')
    for entry in result['entries']:
        label = f"第{entry['sample_ordinal']}条 / 原行{entry['source_evidence']['line']}"
        if entry.get('image'):
            label = f"[{label}]({entry['image']})"
        lines.append(f"- {label}：{entry['status']} {entry.get('reason', '')}")
    (output / 'REVIEW.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (output / 'evidence.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return result


def _output(output, roots):
    output = Path(output).resolve()
    if any(output.is_relative_to(Path(root).resolve()) for root in roots.values()):
        raise ValueError('minimap_review_output_overlaps_history')
    return output


def prepare_native(entry, roots, output, *, max_images=12, selection='spread_plus_largest_capture_gap'):
    from .minimap_handoff import open_rows
    from .minimap_index import resolve_reference
    output = _output(output, roots)
    root, run = resolve_reference(entry, roots)
    with open_rows(root, run, expected_package_id=entry['package_id']) as (doc, session, _):
        if any(entry.get(key) != doc[key] for key in ('run_id', 'chain_id', 'match_id', 'started_at_utc', 'ended_at_utc', 'close_reason')):
            raise ValueError('minimap_review_index_identity_changed')
        # Each pass has its own verified context and fully exhausts the journal.
        def rows():
            with open_rows(root, run, expected_package_id=entry['package_id']) as (_, _, stream):
                for name, number, row, evidence in stream:
                    if name == 'samples.jsonl':
                        yield number, row, evidence
        spec = next(spec for spec in doc['streams'] if spec['file'] == 'samples.jsonl')
        prefix = Path(entry['source']).parts[:-4]
        relative = Path(*prefix, 'minimap-learning', 'run_' + run, 'samples.jsonl').as_posix()
        source = {'root_key': entry['root_key'], 'path': relative, 'bytes': spec['bytes'], 'sha256': spec['sha256']}
        result, tiles = _prepare(rows, root / 'minimap-learning' / ('run_' + run), source, output,
            max_images=max_images, selection=selection,
            metadata={'kind': 'native_minimap_image_review', 'run_id': run, 'package_id': entry['package_id']})
    return _finish(output, result, tiles)


def prepare_legacy(package, roots, reference, output, *, max_images=12, selection='spread_plus_largest_capture_gap'):
    from .closed_journals import ClosedJournals
    output = _output(output, roots)
    with ClosedJournals(package, roots) as reader:
        admitted = [ref for _, ref in reader.journals('minimap') if Path(ref['path']).name == 'samples.jsonl']
        if reference not in admitted:
            raise ValueError('minimap_review_journal_not_admitted')
        root = Path(roots[reference['root_key']]).resolve()
        folder = (root / reference['path']).parent
        result, tiles = _prepare(lambda: reader.rows(reference), folder, reference, output, max_images=max_images, selection=selection,
            metadata={'kind': 'legacy_minimap_image_review', 'launcher_run_id': package['launcher_run_id']})
    return _finish(output, result, tiles)
