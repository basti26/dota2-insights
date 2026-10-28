"""Offline audio-only baseline, frozen before optional GSI corroboration."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read_rows(path, limit):
    if path.stat().st_size > limit:
        raise ValueError('audio_journal_limit')
    return [json.loads(row) for row in path.read_text(encoding='utf-8').splitlines() if row.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--bank', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--waveform-check', action='store_true')
    parser.add_argument('--engine', choices=('baseline','mixture'), default='baseline')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--handoff-package', type=Path,
                        help='SHA-named closed package produced by sync_helper_records.py')
    source.add_argument('--audio-match-package', type=Path,
                        help='Writer-closed native scope directory; whole capture may continue')
    parser.add_argument('--expected-package-id', help='Pin native manifest SHA from the audio-match-handoff index')
    args = parser.parse_args()
    from dota2_insights.history import history_roots, checked_input, checked_output
    from dota2_insights.closed_journals import read_package
    from dota2_insights.closed_audio_analysis import (open_analysis_inputs, open_match_analysis_inputs,
                                                     bank_fingerprint, seal_receipt)
    bank = checked_input(ROOT, args.bank)
    checked_input(ROOT, args.run)
    output = checked_output(ROOT, args.output)
    if output.exists():
        raise ValueError('new_local_diagnostics_output_required')
    before = bank_fingerprint(bank)
    if args.audio_match_package is not None:
        scope = checked_input(ROOT, args.audio_match_package)
        with open_match_analysis_inputs(scope, expected_package_id=args.expected_package_id) as inputs:
            analyze(args, inputs)
        package_id = inputs.package_id
    else:
        if args.expected_package_id is not None:
            raise ValueError('native_expected_package_id_requires_native_audio_scope')
        package_path = checked_input(ROOT, args.handoff_package)
        package = read_package(package_path)
        roots = history_roots(ROOT / 'config/history.local.json')
        with open_analysis_inputs(package, roots, args.run) as inputs:
            analyze(args, inputs)
        package_id = package_path.stem
    receipt = seal_receipt(output, inputs, before, bank_fingerprint(bank), package_id=package_id)
    print(json.dumps({'complete': receipt['complete'], 'receipt': str(output / 'analysis-receipt.json')}), flush=True)


def analyze(args, inputs):
    if os.name == 'nt':
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    import numpy as np
    from dota2_insights.ability_sound_matcher import AbilitySoundMatcher, mono8k
    folder, output = inputs.audio_folder, args.output.resolve()
    if args.run.resolve() != folder:
        raise ValueError('audio_analysis_capture_folder_conflict')
    from dota2_insights.history import checked_input, checked_output
    checked_input(ROOT, folder)
    checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_local_diagnostics_output_required')
    if inputs.requires_capture_summary and not (folder/'summary.json').exists():
        raise ValueError('closed_recording_summary_required')
    records = inputs.segment_rows()
    if not 1 <= len(records) <= 5000:
        raise ValueError('audio_segment_count_limit')
    if args.engine == 'mixture':
        if args.waveform_check:
            raise ValueError('mixture_engine_has_no_waveform_hard_gate')
        from dota2_insights.mixture_sound_matcher import MixtureSoundMatcher
        matcher = MixtureSoundMatcher(args.bank.resolve())
    else:
        matcher = AbilitySoundMatcher(args.bank.resolve(), waveform_check=args.waveform_check)
    output.mkdir(parents=True)
    started = time.perf_counter()
    reasons, skills = Counter(), Counter()
    predictions, sources = [], []
    previous = None
    clip_count = 0
    audio_seconds = 0.
    last_scan = -1
    with (output/'predictions.jsonl').open('x', encoding='utf-8') as accepted, (output/'scans.jsonl').open('x', encoding='utf-8') as scans:
        for number, record in enumerate(records, 1):
            filename = record['audio_file']
            path = (folder/filename).resolve()
            if (path.parent != folder or re.fullmatch(r'audio-[0-9]{4,8}\.wav', filename) is None
                    or path.stat().st_size > 4*1024**2 or sha(path) != record['sha256']):
                raise ValueError('audio_source_integrity_failed')
            sources.append({'file': filename, 'sha256': record['sha256']})
            label = record.get('take_label') or {}
            if (previous is None or previous.get('ended_by') != 'five_second_segment'
                    or abs(record['take_audio_start_offset_s'] - previous['take_audio_start_offset_s'] - previous['audio_seconds']) > 1e-6
                    or label.get('take_id') != (previous.get('take_label') or {}).get('take_id')):
                matcher.reset()
            base = record['take_audio_start_offset_s']
            with wave.open(str(path), 'rb') as stream:
                rate, channels, width = stream.getframerate(), stream.getnchannels(), stream.getsampwidth()
                if width != 2 or channels not in (1, 2) or not 8000 <= rate <= 192000:
                    raise ValueError('unsupported_audio_format')
                consumed = 0
                while raw := stream.readframes(rate//10):
                    samples = np.frombuffer(raw, dtype='<i2').reshape(-1, channels).astype(np.float32)/32768
                    consumed += len(samples)
                    candidate = matcher.feed(mono8k(samples, rate), offset_s=base+consumed/rate)
                    decision = matcher.last_decision
                    if decision and decision['scan_number'] != last_scan:
                        last_scan = decision['scan_number']
                        reasons[decision['reason']] += 1
                        scans.write(json.dumps({**decision, 'source_file': filename, 'take_id':label.get('take_id')}, allow_nan=False)+'\n')
                    if candidate:
                        skills[candidate['skill']] += 1
                        event = {**candidate, 'source_file': filename,
                                 'take_id':label.get('take_id'),
                                 'audio_offset_s': round(base+consumed/rate, 4)}
                        if clip_count < 50:
                            clip_count += 1
                            clip = output/f'candidate-{clip_count:04d}.wav'
                            with wave.open(str(clip), 'wb') as w:
                                w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
                                w.writeframes(np.clip(matcher.last_audio*32768,-32768,32767).astype('<i2').tobytes())
                            event.update(clip=clip.name, clip_sha256=sha(clip))
                        predictions.append(event)
                        accepted.write(json.dumps(event, allow_nan=False)+'\n')
                audio_seconds += consumed/rate
            previous = record
            if number % 100 == 0:
                print(json.dumps({'segments_done':number,'total_segments':len(records),'candidate_count':sum(skills.values())}), flush=True)
    # Commit the immutable prediction digest before opening the GSI sidecar.
    prediction_hash = sha(output/'predictions.jsonl')
    frozen = {'schema_version':1, 'inference_inputs':['PCM','fixed_source_bank'],
        'gsi_read_during_inference':False,'bank_sha256':matcher.sha256,
        'source_journal_sha256':inputs.refs['segments.jsonl']['sha256'], 'sources':sources,
        'prediction_sha256':prediction_hash,'scan_sha256':sha(output/'scans.jsonl'),
        'audio_seconds':audio_seconds,'elapsed_s':round(time.perf_counter()-started,3),
        'scan_reasons':dict(reasons),'candidate_skills':dict(skills),'clips_written':clip_count,
        'stage':'prediction_frozen_before_reference'}
    frozen['waveform_check'] = args.waveform_check
    frozen['engine'] = args.engine
    frozen['engine_policy'] = getattr(matcher, 'policy', None)
    frozen['matcher_source_sha256'] = sha(ROOT/'dota2_insights/ability_sound_matcher.py')
    frozen['runner_source_sha256'] = sha(Path(__file__))
    frozen['waveform_policy'] = {'version':'differential_pcm_ncc_v1', 'provisional_gate':.30,
        'lag_samples':1600,'pitch_min':.97,'pitch_max':1.03,'pitch_steps':61} if args.waveform_check else None
    (output/'prediction-manifest.json').write_text(json.dumps(frozen,indent=2)+'\n',encoding='utf-8')
    inputs.authorize_reference_review(output)
    edges = []
    prior = None
    if inputs.reference_sha256 is not None:
        for row in inputs.reference_rows():
            context = row['context']
            if prior:
                old = prior['context']
                gap = row['audio_offset_s']-prior['audio_offset_s']
                wall = (context.get('provider_timestamp') or 0)-(old.get('provider_timestamp') or 0)
                if (row.get('take_id') == prior.get('take_id')
                        and context.get('game_session_id') == old.get('game_session_id')
                        and context.get('hero_id') == old.get('hero_id') and context.get('match_id') == old.get('match_id')
                        and 0 <= gap <= 2 and 0 <= wall <= 2):
                    old_cd = {r['name']:r.get('cooldown') for r in old.get('abilities',[])}
                    for ability in context.get('abilities',[]):
                        cd = ability.get('cooldown')
                        if old_cd.get(ability['name']) == 0 and type(cd) in (int,float) and cd > 0:
                            skill = ability['name']
                            edges.append({'skill':skill,'audio_offset_s':row['audio_offset_s'],'take_id':row.get('take_id'),
                                'covered_by_bank':skill in matcher.skill_counts,
                                'nearby_audio_candidate':any(p['take_id']==row.get('take_id') and p['skill']==skill and abs(p['audio_offset_s']-row['audio_offset_s'])<=2 for p in predictions)})
            prior = row
    if sha(output/'predictions.jsonl') != prediction_hash:
        raise ValueError('prediction_changed_during_reference_review')
    corroboration = {'schema_version':1,'prediction_sha256':prediction_hash,'reference_sha256':inputs.reference_sha256,
        'own_sampled_cd_edges':edges,'own_edge_skills':dict(Counter(e['skill'] for e in edges)),
        'limits':['CD edges are sampled corroboration, not precise cast labels',
                  'own GSI cannot label enemy sounds; other heroes are not automatically false positives',
                  'multiple candidate windows may describe one continuous effect',
                  'no precision, recall, game impact or latency acceptance claimed']}
    (output/'post-gsi-review.json').write_text(json.dumps(corroboration,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'audio_seconds':round(audio_seconds,2),'elapsed_s':frozen['elapsed_s'],
        'candidates':sum(skills.values()),'skills':dict(skills),'own_edge_skills':corroboration['own_edge_skills']}), flush=True)


if __name__ == '__main__':
    main()
