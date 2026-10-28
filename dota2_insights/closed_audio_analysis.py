"""Closed audio admission and final receipt; predictions precede GSI review."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

from .audio_integrity_review import review as verify_audio
from .closed_journals import ClosedJournals
from .replay_holdout import refuse_locked_match


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class AudioAnalysisInputs:
    def __init__(self, reader, component, capture):
        self.reader, self.capture = reader, capture
        self.after_verified = False
        self.refs = {Path(j['source']['path']).name: j['source'] for j in component['journals']}
        self.allowed_matches = {m['match_id'] for m in reader.package['matches']}
        self.reference_sha256 = self.refs.get('reference-gsi.jsonl', {}).get('sha256')
        self.audio_folder = (Path(reader.roots[capture['metadata_source']['root_key']]) /
                             capture['metadata_source']['path']).parent.resolve()
        self.requires_capture_summary = True
        self.reference_review_complete = self.reference_sha256 is None
        self._reference_freeze = None

    def authorize_reference_review(self, output):
        self._reference_freeze = (Path(output), prediction_gate(output, self))

    def _check_reference_gate(self):
        if self._reference_freeze is None:
            raise ValueError('audio_reference_requires_prediction_freeze')
        output, digest = self._reference_freeze
        if prediction_gate(output, self) != digest:
            raise ValueError('audio_reference_prediction_freeze_changed')

    def segment_rows(self):
        return [row for _, row, _ in self.reader.rows(self.refs['segments.jsonl'])]

    def reference_rows(self):
        self._check_reference_gate()
        from .audio_source_context import validate_reference_context
        ref = self.refs.get('reference-gsi.jsonl')
        if ref is None:
            return
        for _, row, _ in self.reader.rows(ref):
            context = row.get('context')
            if not isinstance(context, dict):
                raise ValueError('audio_reference_context_invalid')
            mid = context.get('match_id')
            if mid is not None:
                refuse_locked_match(mid)
                if mid not in self.allowed_matches:
                    raise ValueError('audio_reference_foreign_match')
            validate_reference_context(context)
            yield row
        self.reference_review_complete = True


def prediction_gate(output, inputs):
    """The semantic GSI reader cannot be released by an unbound marker file."""
    output = Path(output)
    from .match_journals import _json, _read
    raw = _read(output/'prediction-manifest.json', 4*1024**2)
    frozen = _json(raw)
    expected = [{'file':Path(s['source']['path']).name,'sha256':s['source']['sha256']}
                for s in inputs.capture['segments']]
    if (frozen.get('stage')!='prediction_frozen_before_reference'
            or frozen.get('gsi_read_during_inference') is not False
            or frozen.get('source_journal_sha256')!=inputs.refs['segments.jsonl']['sha256']
            or frozen.get('sources')!=expected
            or frozen.get('prediction_sha256')!=_sha(output/'predictions.jsonl')
            or frozen.get('scan_sha256')!=_sha(output/'scans.jsonl')):
        raise ValueError('audio_reference_prediction_freeze_invalid')
    return hashlib.sha256(raw).hexdigest()


class AudioMatchAnalysisInputs(AudioAnalysisInputs):
    """Expose only inference metadata; keep semantic GSI behind the freeze."""
    def __init__(self, folder, package):
        self.folder = Path(folder)
        self.audio_folder = self.folder.parent.parent.resolve()
        self.package = package
        self.package_id = package['package_id']
        self.refs = {s['file']:s for s in package['manifest']['files']}
        self.capture = {'capture_run_id':package['manifest']['audio_capture_run_id'],
            'segments':[{'source':{'path':row['audio_file'],'sha256':row['sha256']}}
                        for row in package['segments']]}
        self.reference_sha256 = self.refs['reference-gsi.jsonl']['sha256']
        self.requires_capture_summary = False
        self.after_verified = self.reference_review_complete = False
        self._reference_freeze = None
        self.active = True
        self.reader = self  # Same owner-lifetime protocol used by seal_receipt.

    def segment_rows(self):
        fields=('audio_file','sha256','audio_seconds','take_audio_start_offset_s','ended_by')
        return [{**{key:row.get(key) for key in fields},
                 'take_label':{'take_id':row['take_label']['take_id']}}
                for row in self.package['segments']]

    def reference_rows(self):
        self._check_reference_gate()
        from .audio_match_handoff import verify
        reviewed=verify(self.folder,expected_package_id=self.package_id)
        for row in reviewed['references']:
            yield row
        self.reference_review_complete=True


@contextmanager
def open_match_analysis_inputs(folder, *, expected_package_id=None):
    from .audio_match_handoff import verify
    before=verify(folder,expected_package_id=expected_package_id,defer_reference_review=True)
    if before['manifest']['recording_integrity']!='complete':
        raise ValueError('audio_analysis_requires_gap_free_handoff')
    inputs=AudioMatchAnalysisInputs(folder,before)
    try:
        yield inputs
        if not inputs.reference_review_complete:
            raise ValueError('audio_analysis_reference_review_incomplete')
        inputs._check_reference_gate()
        if verify(folder,expected_package_id=inputs.package_id,defer_reference_review=True)!=before:
            raise ValueError('audio_analysis_match_sources_changed')
        inputs.after_verified=True
    finally:
        inputs.active=False


@contextmanager
def open_analysis_inputs(package, roots, folder):
    folder = Path(folder).resolve()
    before = verify_audio(package, roots)
    matched = [c for c in before['captures'] if (Path(roots[c['metadata_source']['root_key']]) /
                  c['metadata_source']['path']).parent.resolve() == folder]
    if len(matched) != 1:
        raise ValueError('audio_analysis_requires_one_admitted_capture')
    capture = matched[0]
    components = [c for c in package['components'] if c['kind'] == 'audio'
                  and c['state'] == 'closed_verified' and c['metadata'] == capture['metadata_source']]
    if len(components) != 1:
        raise ValueError('audio_analysis_component_ambiguous')
    with ClosedJournals(package, roots) as reader:
        inputs = AudioAnalysisInputs(reader, components[0], capture)
        yield inputs
        if verify_audio(package, roots) != before:
            raise ValueError('audio_analysis_sources_changed')
    inputs.after_verified = True


def bank_fingerprint(folder):
    folder = Path(folder)
    from .minimap.offline_io import check_input_paths
    check_input_paths(folder, folder / 'bank.json', folder / 'features.npy')
    if (folder / 'bank.json').stat().st_size > 1024**2 or (folder / 'features.npy').stat().st_size > 4*1024**2:
        raise ValueError('audio_analysis_bank_budget')
    manifest = json.loads((folder / 'bank.json').read_text(encoding='utf-8'))
    features = _sha(folder / 'features.npy')
    if features != manifest.get('features_sha256'):
        raise ValueError('audio_analysis_bank_features_changed')
    return {'bank.json': _sha(folder / 'bank.json'), 'features.npy': features}


def seal_receipt(output, inputs, bank_before, bank_after, *, package_id):
    """Called only after source context exits successfully; exclusive final marker."""
    if not inputs.after_verified or inputs.reader.active or not inputs.reference_review_complete:
        raise ValueError('audio_analysis_after_verification_required')
    if bank_after != bank_before:
        raise ValueError('audio_analysis_bank_changed')
    output = Path(output)
    path = output / 'prediction-manifest.json'
    if path.stat().st_size > 4*1024**2:
        raise ValueError('audio_analysis_prediction_manifest_budget')
    frozen = json.loads(path.read_text(encoding='utf-8'))
    expected = [{'file': Path(s['source']['path']).name, 'sha256': s['source']['sha256']}
                for s in inputs.capture['segments']]
    if (frozen.get('stage') != 'prediction_frozen_before_reference'
            or frozen.get('gsi_read_during_inference') is not False
            or frozen.get('source_journal_sha256') != inputs.refs['segments.jsonl']['sha256']
            or frozen.get('bank_sha256') != bank_before['bank.json'] or frozen.get('sources') != expected
            or frozen.get('prediction_sha256') != _sha(output / 'predictions.jsonl')
            or frozen.get('scan_sha256') != _sha(output / 'scans.jsonl')):
        raise ValueError('audio_analysis_frozen_sources_conflict')
    post_path = output / 'post-gsi-review.json'
    if post_path.stat().st_size > 16*1024**2:
        raise ValueError('audio_analysis_post_reference_budget')
    post = json.loads(post_path.read_text(encoding='utf-8'))
    if (post.get('prediction_sha256') != frozen['prediction_sha256']
            or post.get('reference_sha256') != inputs.reference_sha256):
        raise ValueError('audio_analysis_post_reference_conflict')
    result = {'schema_version': 1, 'kind': 'closed_audio_analysis_receipt', 'complete': True,
        'source_package_id': package_id, 'capture_run_id': inputs.capture['capture_run_id'],
        'source_verified_before_and_after': True, 'waveforms_verified_before_and_after': True,
        'bank_hashes': bank_before, 'prediction_manifest_sha256': _sha(path),
        'post_gsi_review_sha256': _sha(post_path),
        'reference_journal_sha256': inputs.reference_sha256, 'gsi_used_for_inference': False,
        'skill_audibility_verified': False, 'accuracy_verified': False,
        'meaning': 'offline_pipeline_and_bytes_verified_not_cast_or_audibility_validation'}
    if isinstance(inputs, AudioMatchAnalysisInputs):
        if package_id != inputs.package_id:
            raise ValueError('audio_analysis_match_package_conflict')
        result.update(source_kind='native_audio_match_handoff',
            source_scope={k:inputs.package['manifest'][k] for k in ('match_id','game_session_id','record_session_id')},
            full_capture_shutdown_required=False,whole_match_coverage_verified=False,
            sample_exact_audio_GSI_timing_verified=False)
    from .minimap.offline_io import new_binary_output
    with new_binary_output(output / 'analysis-receipt.json') as stream:
        stream.write((json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))
    return result
