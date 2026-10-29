"""Review the currently selected closed recording packages without game/network access."""
from datetime import datetime, timezone
import json
from pathlib import Path
from pathlib import PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def generate(root=ROOT, *, roots=None):
    from dota2_insights.closed_journals import read_package
    from dota2_insights.history import history_roots, checked_output
    from dota2_insights.record_content_review import summarize, render_markdown, item_catalogue
    from dota2_insights.performance_review import review as review_performance, render_markdown as render_performance
    from dota2_insights.audio_integrity_review import review as review_audio, render_markdown as render_audio
    from dota2_insights.draft_diagnostics import render_markdown as render_bp_diagnostics
    from dota2_insights.draft_advice_audit import render_markdown as render_advice_audit
    from dota2_insights.gsi_evidence_census import render_markdown as render_gsi_census
    root = Path(root).resolve()
    roots = roots if roots is not None else history_roots(root/'config/history.local.json')
    folder = root/'diagnostics/helper-handoff'
    index_path = folder/'index.json'
    if index_path.stat().st_size > 2*1024**2:
        raise ValueError('content_index_size_limit')
    index = json.loads(index_path.read_text(encoding='utf-8'))
    if (index.get('kind') != 'helper_handoff_index' or not isinstance(index.get('runs'),list)
            or len(index['runs']) > 25):
        raise ValueError('content_index_invalid')
    reports, skipped, performance_reports, audio_reports = [], [], [], []
    catalogue, catalogue_source = item_catalogue(root)
    legacy_gsi_scopes = set()
    legacy_observation_scopes = set()
    legacy_bp_sources = set()
    legacy_minimap_sources = set()
    legacy_audio_waveforms = set()
    legacy_audio_capture_files = set()
    legacy_performance_sources = set()
    legacy_performance_paths = set()
    for run in index['runs']:
        pid = run.get('package_id')
        if run['state'] not in ('ready','ready_with_gaps'):
            skipped.append({'state':run['state'],'reason':'handoff_not_ready'})
            continue
        if not isinstance(pid,str) or not re.fullmatch('[a-f0-9]{64}',pid):
            raise ValueError('content_package_id_invalid')
        package = read_package(folder/'packages'/(pid+'.json'))
        if package['launcher_run_id'] != run['launcher_run_id']:
            raise ValueError('content_index_package_mismatch')
        reports.append(summarize(package, roots, catalogue=catalogue, catalogue_source=catalogue_source))
        performance_reports.append(review_performance(package, roots))
        for game in performance_reports[-1]['matches']:
            csv_ref, context_ref = game['csv_source'], game['context_source']
            legacy_performance_sources.add((game['capture_run_id'], game['match_id'], game['game_session_id'],
                game['record_session_id'], csv_ref['sha256'], context_ref['sha256']))
            legacy_performance_paths.add((csv_ref['root_key'], csv_ref['path'], csv_ref['sha256']))
        audio_reports.append(review_audio(package, roots))
        for capture in audio_reports[-1]['captures']:
            for segment in capture['segments']:
                ref=segment['source']
                legacy_audio_waveforms.add((ref['root_key'],ref['path'],ref['sha256']))
                legacy_audio_capture_files.add((capture['capture_run_id'],PurePosixPath(ref['path']).name,ref['sha256']))
        for component in package.get('components', []):
            if component.get('kind') == 'minimap' and component.get('state') == 'closed_verified':
                for journal in component.get('journals', []):
                    ref = journal['source']
                    legacy_minimap_sources.add((ref['root_key'], ref['path']))
            if component.get('kind') == 'bp' and component.get('state') == 'closed_verified':
                for journal in component.get('journals', []):
                    ref = journal['source']
                    legacy_bp_sources.add((ref['root_key'], ref['path']))
            if component.get('kind') == 'own_gsi' and component.get('state') == 'closed_verified':
                for journal in component.get('journals', []):
                    ref = journal['source']
                    for match in package['matches']:
                        legacy_gsi_scopes.add((ref['root_key'], ref['path'], match['match_id'],
                                               match['game_session_id'], match['record_session_id']))
            if component.get('kind') == 'economy_context' and component.get('state') == 'closed_verified':
                for journal in component.get('journals', []):
                    ref = journal['source']
                    if PurePosixPath(ref['path']).name == 'observations.jsonl':
                        for match in package['matches']:
                            legacy_observation_scopes.add((ref['root_key'], ref['path'], match['match_id'],
                                match['game_session_id'], match['record_session_id']))
    from dota2_insights.match_journal_index import read_index, resolve_reference
    from dota2_insights.match_journal_review import review as review_native, render_markdown as render_native
    native_path = root/'diagnostics/native-match-handoff/index.json'
    native_reports, native_skipped, native_entries = [], [], []
    native_state = 'missing_not_treated_as_zero'
    if native_path.exists():
        native_index = read_index(native_path)
        native_entries = native_index['entries']
        native_state = 'read_and_sources_revalidated'
        for entry in native_index['entries']:
            if entry['state'] != 'ready_with_gaps':
                native_skipped.append({'state': entry['state'], 'reason': 'native_handoff_not_ready'})
                continue
            result = review_native(resolve_reference(entry, roots), expected_package_id=entry['package_id'])
            if any(result[key] != entry.get(key) for key in ('match_id', 'game_session_id', 'record_session_id', 'run_id')):
                raise ValueError('content_native_index_identity_conflict')
            primary = entry['source'].split('/closed-matches/')[0] + '/samples.jsonl'
            result.update(root_key=entry['root_key'], source=entry['source'], package_id=entry['package_id'],
                also_in_closed_launcher_scope=(entry['root_key'], primary, result['match_id'],
                    result['game_session_id'], result['record_session_id']) in legacy_gsi_scopes)
            native_reports.append(result)
    from dota2_insights.observation_index import read_index as read_equipment_index, resolve_reference as resolve_equipment
    from dota2_insights.observation_handoff import review as review_equipment, render_markdown as render_equipment
    equipment_path = root/'diagnostics/equipment-handoff/index.json'
    equipment_reports, equipment_skipped = [], []
    equipment_state = 'missing_not_treated_as_zero'
    if equipment_path.exists():
        equipment_index = read_equipment_index(equipment_path)
        equipment_state = 'read_and_sources_revalidated'
        for entry in equipment_index['entries']:
            if entry['state'] != 'ready_with_gaps':
                equipment_skipped.append({'state': entry['state'], 'reason': 'equipment_handoff_not_ready'})
                continue
            source_root, session = resolve_equipment(entry, roots)
            result = review_equipment(source_root, session, expected_package_id=entry['package_id'],
                                      source_reference=entry, catalogue=catalogue, catalogue_source=catalogue_source)
            identity = ('match_id', 'game_session_id', 'record_session_id')
            if any(result[key] != entry.get(key) for key in identity):
                raise ValueError('content_equipment_index_identity_conflict')
            prefix = PurePosixPath(entry['source']).parts[:-4]
            journal = PurePosixPath(*prefix, 'matches', 'match_' + result['match_id'], session, 'observations.jsonl').as_posix()
            result.update(root_key=entry['root_key'], source=entry['source'], package_id=entry['package_id'],
                also_in_closed_launcher_scope=(entry['root_key'], journal, result['match_id'],
                    result['game_session_id'], session) in legacy_observation_scopes,
                linked_native_gsi_packages=[gsi['package_id'] for gsi in native_reports
                    if gsi['root_key'] == entry['root_key'] and all(gsi[key] == result[key] for key in identity)])
            equipment_reports.append(result)
    from dota2_insights.draft_phase_index import read_index as read_bp_index, resolve_reference as resolve_bp
    from dota2_insights.draft_phase_handoff import review as review_bp, render_markdown as render_bp
    bp_path = root/'diagnostics/draft-phase-handoff/index.json'
    bp_reports, bp_skipped = [], []
    bp_state = 'missing_not_treated_as_zero'
    if bp_path.exists():
        bp_index = read_bp_index(bp_path)
        bp_state = 'read_and_sources_revalidated'
        for entry in bp_index['entries']:
            if entry['state'] != 'ready_with_gaps':
                bp_skipped.append({'state': entry['state'], 'reason': 'bp_phase_handoff_not_ready'})
                continue
            source_root, sid = resolve_bp(entry, roots)
            result = review_bp(source_root, sid, expected_package_id=entry['package_id'],
                               expected_binding_package_id=entry['binding_package_id'])
            if any(result[key] != entry.get(key) for key in
                   ('session_id', 'match_id_at_cutoff', 'effective_match_id', 'binding_package_id', 'ending_phase')):
                raise ValueError('content_bp_index_identity_conflict')
            mid = result['effective_match_id']
            result.update(root_key=entry['root_key'], source=entry['source'], package_id=entry['package_id'],
                streams_also_in_closed_launcher_scope=[name for name in result['stream_rows']
                    if (entry['root_key'], PurePosixPath(entry['source']).with_name(name).as_posix()) in legacy_bp_sources],
                linked_native_gsi_packages=[gsi['package_id'] for gsi in native_reports
                    if mid is not None and gsi['match_id'] == mid],
                linked_equipment_packages=[item['package_id'] for item in equipment_reports
                    if mid is not None and item['match_id'] == mid],
                capture_association_basis='explicit_match_id_only_not_capture_session_identity')
            bp_reports.append(result)
    from dota2_insights.minimap_index import read_index as read_minimap_index, resolve_reference as resolve_minimap
    from dota2_insights.minimap_handoff import review as review_minimap, render_markdown as render_minimap
    minimap_path = root/'diagnostics/minimap-run-handoff/index.json'
    minimap_reports, minimap_skipped = [], []
    minimap_state = 'missing_not_treated_as_zero'
    if minimap_path.exists():
        minimap_index = read_minimap_index(minimap_path)
        minimap_state = 'read_and_sources_revalidated'
        for entry in minimap_index['entries']:
            if entry['state'] != 'ready_with_gaps':
                minimap_skipped.append({'state': entry['state'], 'reason': 'minimap_run_handoff_not_ready'})
                continue
            source_root, run_id = resolve_minimap(entry, roots)
            result = review_minimap(source_root, run_id, expected_package_id=entry['package_id'])
            if any(result[key] != entry.get(key) for key in
                   ('run_id', 'chain_id', 'match_id', 'started_at_utc', 'ended_at_utc', 'close_reason')):
                raise ValueError('content_minimap_index_identity_conflict')
            prefix = PurePosixPath(entry['source']).parts[:-4]
            result.update(root_key=entry['root_key'], source=entry['source'], package_id=entry['package_id'],
                streams_also_in_closed_launcher_scope=[name for name in result['stream_rows']
                    if (entry['root_key'], PurePosixPath(*prefix, 'minimap-learning', 'run_' + run_id, name).as_posix())
                        in legacy_minimap_sources])
            for scope in result.get('explicit_context_scopes', []):
                scope['linked_native_gsi_packages'] = [gsi['package_id'] for gsi in native_reports
                    if all(gsi[key] == scope[key] for key in ('match_id','game_session_id','record_session_id'))]
                scope['association_basis'] = 'explicit_relay_context_match_and_both_sessions_not_time_inference'
            from dota2_insights.minimap_gsi_alignment import prepare as prepare_alignment, summarize as summarize_alignment
            alignment = prepare_alignment(entry, native_entries, roots)
            result['gsi_source_alignment'] = summarize_alignment(alignment, native_index_state=native_state)
            minimap_reports.append(result)
    from dota2_insights.audio_match_index import read_index as read_audio_index, resolve_reference as resolve_audio
    from dota2_insights.audio_match_handoff import review as review_audio_match, render_markdown as render_audio_match
    audio_path=root/'diagnostics/audio-match-handoff/index.json'
    audio_match_reports,audio_match_skipped=[],[]
    audio_match_state='missing_not_treated_as_zero'
    if audio_path.exists():
        audio_index=read_audio_index(audio_path)
        audio_match_state='read_and_sources_revalidated'
        for entry in audio_index['entries']:
            if entry['state']!='ready_with_gaps':
                audio_match_skipped.append({'state':entry['state'],'reason':'audio_match_handoff_not_ready',
                    'root_key':entry['root_key'],'source':entry['source']})
                continue
            source_folder=resolve_audio(entry,roots)
            result=review_audio_match(source_folder,expected_package_id=entry['package_id'],
                native_entries=native_entries,roots=roots,native_index_state=native_state)
            if any(result[key]!=entry.get(key) for key in
                   ('match_id','game_session_id','record_session_id','audio_capture_run_id','recording_integrity')):
                raise ValueError('content_audio_match_index_identity_conflict')
            capture=PurePosixPath(entry['source']).parent.parent
            wavs=result.pop('waveform_sha256_by_file')
            path_overlap=sum((entry['root_key'],(capture/name).as_posix(),sha) in legacy_audio_waveforms
                             for name,sha in wavs.items())
            overlap=sum((result['audio_capture_run_id'],name,sha) in legacy_audio_capture_files
                        for name,sha in wavs.items())
            result.update(root_key=entry['root_key'],source=entry['source'],
                waveforms_also_in_closed_launcher_scope=overlap,
                legacy_same_path_overlap_count=path_overlap,
                legacy_overlap_basis='same_declared_capture_run_file_name_and_verified_waveform_sha_not_time_or_audio_similarity',
                counts_must_not_be_added_to_legacy=overlap>0,
                linked_native_gsi_packages=[gsi['package_id'] for gsi in native_reports
                    if result['match_id'] is not None and all(gsi[key]==result[key] for key in
                        ('match_id','game_session_id','record_session_id'))],
                native_gsi_association_basis='explicit_public_match_and_both_runtime_sessions_not_per_source_id_join')
            audio_match_reports.append(result)
    from dota2_insights.performance_index import read_index as read_performance_index, resolve_reference as resolve_performance, MAX_READ_BYTES
    from dota2_insights.performance_handoff import review as review_performance_match, render_markdown as render_performance_match
    performance_path = root/'diagnostics/performance-match-handoff/index.json'
    performance_match_reports, performance_match_skipped = [], []
    performance_match_state = 'missing_not_treated_as_zero'
    if performance_path.exists():
        performance_index = read_performance_index(performance_path)
        performance_match_state = 'read_and_sources_revalidated'
        reserved = 0
        for entry in performance_index['entries']:
            if entry['state'] != 'ready_with_gaps':
                performance_match_skipped.append({'state':entry['state'], 'reason':'performance_match_handoff_not_ready',
                    'error_type':entry.get('error_type'), 'root_key':entry['root_key'], 'source':entry['source']})
                continue
            cost = entry.get('source_bytes')
            if type(cost) is not int or cost < 0 or reserved+cost > MAX_READ_BYTES:
                raise ValueError('content_performance_source_budget')
            reserved += cost
            source_folder = resolve_performance(entry, roots)
            result = review_performance_match(source_folder, entry['package_id'], source_budget=cost)
            game = result['matches'][0]
            if (result['capture_run_id'] != entry.get('capture_run_id')
                    or any(game[key] != entry.get(key) for key in ('match_id','game_session_id','record_session_id'))
                    or sum(s['bytes'] for s in result['source_files'].values()) != cost):
                raise ValueError('content_performance_index_identity_conflict')
            csv_sha = result['source_files']['frames.csv']['sha256']
            context_sha = result['source_files']['context.jsonl']['sha256']
            identity = (result['capture_run_id'], game['match_id'], game['game_session_id'], game['record_session_id'], csv_sha, context_sha)
            csv_path = (PurePosixPath(entry['source']).parent/'frames.csv').as_posix()
            overlap = identity in legacy_performance_sources
            result.update(root_key=entry['root_key'], source=entry['source'], package_id=entry['package_id'],
                also_in_closed_launcher_scope=overlap, counts_must_not_be_added_to_legacy=overlap,
                legacy_same_path_overlap=(entry['root_key'],csv_path,csv_sha) in legacy_performance_paths,
                legacy_overlap_basis='same_capture_public_match_and_both_sessions_verified_CSV_and_context_SHA_not_time',
                linked_native_gsi_packages=[gsi['package_id'] for gsi in native_reports
                    if game['match_id'] is not None and all(gsi[key] == game[key] for key in
                        ('match_id','game_session_id','record_session_id'))])
            performance_match_reports.append(result)
    from dota2_insights.ward_camera_review import discover_and_review as review_ward_camera, render_markdown as render_ward_camera
    ward_camera = review_ward_camera(roots, latest=5)
    # Only write once all selected sources have passed their after-analysis check.
    output = checked_output(root, root/'diagnostics/record-content'/
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    output.mkdir(parents=True,exist_ok=False)
    (output/'report.json').write_text(json.dumps({'reports':reports,'skipped':skipped,
        'performance_reports': performance_reports, 'audio_integrity_reports': audio_reports,
        'native_reports':native_reports, 'native_skipped':native_skipped, 'native_index_state':native_state,
        'equipment_reports':equipment_reports, 'equipment_skipped':equipment_skipped, 'equipment_index_state':equipment_state,
        'bp_phase_reports':bp_reports, 'bp_phase_skipped':bp_skipped, 'bp_phase_index_state':bp_state,
        'minimap_run_reports':minimap_reports, 'minimap_run_skipped':minimap_skipped, 'minimap_run_index_state':minimap_state,
        'audio_match_reports':audio_match_reports,'audio_match_skipped':audio_match_skipped,'audio_match_index_state':audio_match_state,
        'performance_match_reports':performance_match_reports,'performance_match_skipped':performance_match_skipped,
        'performance_match_index_state':performance_match_state, 'ward_camera':ward_camera},
        ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    (output/'REPORT.md').write_text(render_markdown(reports)
        + render_native(native_reports, native_skipped, index_state=native_state)
        + render_equipment(equipment_reports, equipment_skipped, index_state=equipment_state)
        + render_bp(bp_reports, bp_skipped, index_state=bp_state)
        + render_minimap(minimap_reports, minimap_skipped, index_state=minimap_state)
        + '\n' + render_performance(performance_reports) + render_audio(audio_reports)
        + render_audio_match(audio_match_reports,audio_match_skipped,index_state=audio_match_state)
        + render_performance_match(performance_match_reports,performance_match_skipped,index_state=performance_match_state)
        + render_bp_diagnostics(bp_reports) + render_advice_audit(bp_reports)
        + render_gsi_census(native_reports) + render_ward_camera(ward_camera),encoding='utf-8')
    return {'report':str(output/'REPORT.md'),'runs_reviewed':len(reports),
            'ward_camera_runs_reviewed':len(ward_camera['reports']),
            'performance_game_scopes_reviewed': sum(len(r['matches']) for r in performance_reports),
            'performance_match_scopes_reviewed':len(performance_match_reports),
            'performance_match_skipped':performance_match_skipped, 'performance_match_index_state':performance_match_state,
            'audio_segments_verified': sum(c['segments_verified'] for r in audio_reports for c in r['captures']),
            'native_matches_reviewed': len(native_reports), 'skipped':skipped, 'native_skipped':native_skipped,
            'native_index_state':native_state, 'equipment_sessions_reviewed':len(equipment_reports),
            'equipment_skipped':equipment_skipped, 'equipment_index_state':equipment_state,
            'bp_phase_sessions_reviewed':len(bp_reports), 'bp_phase_skipped':bp_skipped, 'bp_phase_index_state':bp_state,
            'minimap_runs_reviewed':len(minimap_reports), 'minimap_run_skipped':minimap_skipped,
            'minimap_run_index_state':minimap_state,
            'audio_match_scopes_reviewed':len(audio_match_reports),'audio_match_skipped':audio_match_skipped,
            'audio_match_index_state':audio_match_state,
            'audio_match_segments_verified':sum(row['segments_verified'] for row in audio_match_reports)}


def main():
    print(json.dumps(generate(),ensure_ascii=False))


if __name__ == '__main__':
    main()
