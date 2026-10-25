"""Export post-inference listening windows; GSI is a review aid, not a label."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.analyze_match_audio import read_rows, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--analysis', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    folder, analysis, output = (p.resolve() for p in (args.run, args.analysis, args.output))
    from dota2_insights.history import checked_input, checked_output
    checked_input(ROOT, folder)
    checked_input(ROOT, analysis)
    checked_output(ROOT, output)
    if output.exists():
        raise ValueError('new_local_diagnostics_output_required')
    frozen = json.loads((analysis/'prediction-manifest.json').read_text())
    if (sha(analysis/'predictions.jsonl') != frozen['prediction_sha256']
            or sha(folder/'segments.jsonl') != frozen['source_journal_sha256']):
        raise ValueError('frozen_audio_evidence_mismatch')
    review = json.loads((analysis/'post-gsi-review.json').read_text())
    if review['reference_sha256'] != sha(folder/'reference-gsi.jsonl'):
        raise ValueError('gsi_reference_changed')
    # Require explicit take identity; older reports can be regenerated.
    edges = review['own_sampled_cd_edges']
    if any(not e.get('take_id') for e in edges):
        raise ValueError('regenerate_analysis_with_take_ids')
    records = read_rows(folder/'segments.jsonl', 16*1024**2)
    output.mkdir(parents=True)
    counts, clips = Counter(), []
    for edge in edges:
        if not edge['covered_by_bank'] or counts[edge['skill']] >= 3:
            continue
        start, end = max(0., edge['audio_offset_s']-2), edge['audio_offset_s']+3
        chunks, sources, covered, fmt = [], [], [], None
        for record in records:
            if (record.get('take_label') or {}).get('take_id') != edge['take_id']:
                continue
            base = record['take_audio_start_offset_s']
            left, right = max(start, base), min(end, base+record['audio_seconds'])
            if right <= left:
                continue
            path = (folder/record['audio_file']).resolve()
            if path.parent != folder or path.stat().st_size > 4*1024**2 or sha(path) != record['sha256']:
                raise ValueError('source_audio_mismatch')
            with wave.open(str(path), 'rb') as stream:
                actual = (stream.getnchannels(), stream.getsampwidth(), stream.getframerate())
                if fmt is not None and actual != fmt:
                    raise ValueError('mixed_audio_format')
                fmt = actual
                stream.setpos(round((left-base)*fmt[2]))
                chunks.append(stream.readframes(round((right-left)*fmt[2])))
            sources.append({'file':record['audio_file'],'sha256':record['sha256']})
            covered.append((left,right))
        # Do not silently concatenate across a pause or capture gap.
        if not covered or covered[0][0] > start+.001 or covered[-1][1] < end-.001 or any(abs(b[0]-a[1])>.001 for a,b in zip(covered,covered[1:])):
            continue
        name = f'review-{len(clips)+1:03d}.wav'
        with wave.open(str(output/name), 'wb') as stream:
            stream.setnchannels(fmt[0]); stream.setsampwidth(fmt[1]); stream.setframerate(fmt[2])
            stream.writeframes(b''.join(chunks))
        counts[edge['skill']] += 1
        clips.append({**edge,'clip':name,'clip_sha256':sha(output/name),'start_s':start,'end_s':end,
                      'sources':sources,'human_verdict':'unreviewed',
                      'basis':'post_prediction_sampled_CD_edge_not_verified_audible_cast'})
    report = {'schema_version':1,'purpose':'posthoc_listening_only_never_template_training_or_accuracy_labels',
              'prediction_sha256':frozen['prediction_sha256'],'clips':clips,
              'counts':dict(counts),'exporter_source_sha256':sha(Path(__file__))}
    (output/'review.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'clips':len(clips),'counts':dict(counts)}))


if __name__ == '__main__':
    main()
