"""Poll an existing bounded job and review completed captures without launching parses."""
import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dota2_insights.pi_context_expansion import request
from dota2_insights.postgame_capture_review import review
from dota2_insights.postgame_replay_context import load_capture
from dota2_insights.postgame_store import bounded, sha, mid as validate_mid, HEX


def write_new(path, value):
    with path.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, indent=2)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--plan', type=Path, required=True)
    cli.add_argument('--baseline', type=Path, required=True)
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--capture-directory', type=Path, help='Reuse already collected capture-MATCH_ID.json files.')
    cli.add_argument('--saved-job-state', type=Path,
                     help='Local review only, with an explicit capture directory; this is not a current liveness observation.')
    cli.add_argument('--reconcile-paused-metric-clocks', action='store_true',
                     help='Explicit loader policy for differences explained by recorded pauses; raw review remains unchanged.')
    cli.add_argument('--retain-missing-event-times', action='store_true',
                     help='Keep original null event times as unknown; never infer their time.')
    args = cli.parse_args()
    diagnostics = (ROOT / 'diagnostics').resolve()
    paths = [args.plan, args.baseline, args.output]
    if args.capture_directory is not None:
        paths.append(args.capture_directory)
    if args.saved_job_state is not None:
        paths.append(args.saved_job_state)
        if args.capture_directory is None:
            cli.error('--saved-job-state requires --capture-directory and never collects remotely.')
    if any(not p.resolve().is_relative_to(diagnostics) or p.resolve() == diagnostics for p in paths):
        cli.error('Use diagnostics paths.')
    plan_raw = bounded(args.plan, 256 * 1024)
    baseline_raw = bounded(args.baseline, 32 * 1024**2)
    plan, baseline = json.loads(plan_raw), json.loads(baseline_raw)
    if (plan.get('kind') != 'bounded_fixed_pi_context_expansion' or plan.get('schema_version') != 1
            or not 1 <= len(plan.get('selected', [])) <= 3 or plan.get('max_parses') != len(plan['selected'])):
        raise ValueError('collection_bounded_plan_required')
    if baseline.get('kind') != 'frozen_verified_ward_exports' or baseline.get('schema_version') != 1:
        raise ValueError('collection_verified_baseline_required')
    selected = {validate_mid(r['match_id']): r for r in plan['selected']}
    entries = {validate_mid(r['match_id']): r for r in baseline['matches']}
    if len(selected) != len(plan['selected']) or not set(selected) <= set(entries):
        raise ValueError('collection_selected_baseline_mismatch')
    if (len(entries) != len(baseline['matches']) or len(entries) > 256
            or any(not HEX.fullmatch(str(r.get(k, ''))) for r in selected.values()
                   for k in ('replay_sha256', 'wards_sha256', 'manifest_sha256'))):
        raise ValueError('collection_baseline_identity_or_fingerprint')
    names = ('pi_context_expansion.py', 'postgame_capture_review.py', 'postgame_replay_context.py',
             'replay_postgame_context.py', 'postgame_store.py')
    code = {n: (ROOT / 'dota2_insights' / n).read_bytes() for n in names}
    entry = Path(__file__).read_bytes()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    identity = {'schema_version': 1, 'kind': 'completed_capture_review_inputs',
                'plan_sha256': sha(plan_raw), 'baseline_sha256': sha(baseline_raw),
                'metric_clock_policy': 'reconcile_recorded_pauses' if args.reconcile_paused_metric_clocks else 'strict',
                'event_clock_policy': 'retain_missing' if args.retain_missing_event_times else 'strict',
                'source_code_sha256': {n: sha(b) for n, b in code.items()}, 'entrypoint_sha256': sha(entry)}
    saved_state_raw = bounded(args.saved_job_state, 8 * 1024**2) if args.saved_job_state else None
    identity['saved_job_state_sha256'] = sha(saved_state_raw) if saved_state_raw is not None else None
    binding = out / 'collection-inputs.json'
    if binding.exists():
        if json.loads(bounded(binding, 64 * 1024)) != identity:
            raise ValueError('collection_input_or_code_changed_use_new_review_directory')
    else:
        write_new(binding, identity)
        (out / 'collection-source-code').mkdir()
        for n, body in {**code, Path(__file__).name: entry}.items():
            (out / 'collection-source-code' / n).write_bytes(body)
    index = 1
    while ((out / ('collection-' + str(index).zfill(4) + '.json')).exists()
           or (out / ('job-poll-' + str(index).zfill(4) + '.json')).exists()):
        index += 1
    if saved_state_raw is not None:
        state = json.loads(saved_state_raw)
        observation = 'saved_job_state_not_current_liveness'
    else:
        initial_guard_passed = False
        try:
            from dota2_insights.pi_replay_library import Bridge
            Bridge().guard()
            initial_guard_passed = True
            state = request(args.plan, 'poll')
        except ValueError as exc:
            if str(exc) != 'game_running':
                raise
            receipt = {'schema_version': 1, 'kind': 'existing_job_completed_source_review',
                       'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'plan_sha256': sha(plan_raw),
                       'status': 'deferred_game_running', 'job_liveness': 'not_observed',
                       'remote_poll_dispatch': 'completion_unknown_after_initial_guard'
                       if initial_guard_passed else 'not_attempted_initial_game_guard',
                       'parse_launch_resume_or_retry_performed': False, 'facts_database_modified': False}
            write_new(out / ('collection-' + str(index).zfill(4) + '.json'), receipt)
            print(json.dumps(receipt))
            return
        observation = 'live_remote_poll'
    if state.get('plan_sha256') != sha(plan_raw):
        raise ValueError('collection_observed_job_plan_mismatch')
    write_new(out / ('job-poll-' + str(index).zfill(4) + '.json'), state)
    results = state.get('batch', {}).get('results', [])
    remote = {r['match_id']: r for r in results}
    if len(remote) != len(results) or not set(remote) <= set(selected):
        raise ValueError('collection_job_results_identity')
    cases = []
    policy = 'reconcile_recorded_pauses' if args.reconcile_paused_metric_clocks else 'strict'
    event_policy = 'retain_missing' if args.retain_missing_event_times else 'strict'
    for mid, wanted in selected.items():
        result = remote.get(mid)
        case = {'match_id': mid, 'metric_clock_policy': policy, 'event_clock_policy': event_policy}
        if result is None:
            case['status'] = 'not_complete_in_current_job_observation'
            cases.append(case)
            continue
        if result.get('status') != 'complete':
            case.update(status='incomplete_source_not_collected', remote_result_status=result.get('status'))
            cases.append(case)
            continue
        if result.get('source_sha256') != wanted['replay_sha256']:
            raise ValueError('collection_completed_result_source_changed')
        capture = (args.capture_directory.resolve() if args.capture_directory else out) / ('capture-' + mid + '.json')
        if not capture.exists():
            if args.capture_directory:
                case['status'] = 'completed_capture_missing_from_explicit_directory'
                cases.append(case)
                continue
            request(args.plan, 'collect', match_id=mid, output=capture)
            case['capture_origin'] = 'collected_from_existing_handle'
        else:
            case['capture_origin'] = 'reused_local_capture'
        raw = bounded(capture, 20 * 1024**2)
        frozen = json.loads(raw)
        manifest_raw = base64.b64decode(frozen['manifest_base64'], validate=True)
        manifest = json.loads(manifest_raw)
        if (frozen.get('expansion_plan_sha256') != sha(plan_raw) or manifest.get('match_id') != mid
                or manifest.get('source_sha256') != wanted['replay_sha256']):
            raise ValueError('collection_capture_plan_or_source_binding')
        capture_binding = out / ('capture-' + mid + '.binding.json')
        expected = {'plan_sha256': sha(plan_raw), 'match_id': mid, 'capture_sha256': sha(raw)}
        if capture_binding.exists():
            if json.loads(bounded(capture_binding, 4096)) != expected:
                raise ValueError('collection_local_capture_bytes_changed')
        else:
            write_new(capture_binding, expected)
        folder = out / ('review-' + mid)
        if folder.exists():
            checked = json.loads(bounded(folder / 'review.json', 2 * 1024**2))
            if checked.get('capture_sha256') != sha(raw) or checked.get('baseline_snapshot_sha256') != sha(baseline_raw):
                raise ValueError('collection_existing_review_input_mismatch')
        else:
            checked = review(capture, args.baseline, folder)
        case.update(capture_sha256=sha(raw), raw_review_status=checked['status'],
                    raw_issues=checked['issues'], full_capture=checked['full_capture'])
        if (not checked['full_capture'] or not checked['ward_comparison']['byte_identical']
                or not checked['ward_comparison']['clock_identical'] or not checked['coverage_recount_matches']):
            case['status'] = 'raw_source_review_not_accepted'
            cases.append(case)
            continue
        if plan.get('resolved_game_build_filter') is not None and checked['replay_metadata'].get('game_build') != plan['resolved_game_build_filter']:
            case['status'] = 'captured_build_conflicts_with_frozen_population'
            cases.append(case)
            continue
        allowed = all(
            (args.reconcile_paused_metric_clocks
             and r.get('issue') == 'saved_packet_time_differs_from_final_clock'
             and r.get('kind') == 'metric_packet_minus_final_clock_s')
            or (args.retain_missing_event_times and r.get('issue') == 'event_packet_time_missing')
            for r in checked['issues'])
        if checked['issues'] and not allowed:
            case['status'] = 'raw_review_issues_require_separate_decision'
            cases.append(case)
            continue
        ward_manifest = json.loads(base64.b64decode(entries[mid]['manifest_base64'], validate=True))
        try:
            events, packets, provenance = load_capture(capture, ward_manifest, metric_clock_policy=policy,
                                                       event_clock_policy=event_policy)
        except ValueError as exc:
            case['status'] = 'typed_loader_rejected'
            reason = str(exc)
            case['reason'] = reason if re.fullmatch('[a-z_]{1,100}', reason) else type(exc).__name__
            cases.append(case)
            continue
        case.update(status='source_ready_for_evidence_import_semantics_pending', events=len(events),
                    metric_packets=len(packets), replay_metadata=checked['replay_metadata'],
                    metric_clock_projection=provenance.get('metric_clock_projection'),
                    event_packet_time_missing=provenance.get('event_packet_time_missing'))
        cases.append(case)
    if (Path(__file__).read_bytes() != entry or any((ROOT / 'dota2_insights' / n).read_bytes() != b for n, b in code.items())
            or sha(bounded(args.plan, 256 * 1024)) != sha(plan_raw)
            or sha(bounded(args.baseline, 32 * 1024**2)) != sha(baseline_raw)):
        raise ValueError('collection_code_or_inputs_changed')
    receipt = {'schema_version': 1, 'kind': 'existing_job_completed_source_review',
               'observed_at_utc': datetime.now(timezone.utc).isoformat(), 'plan_sha256': sha(plan_raw),
               'state_observation': observation,
               'unit': state['unit'], 'unit_state': state['state'], 'metric_clock_policy': policy,
               'event_clock_policy': event_policy,
               'cases': cases, 'statuses': dict(Counter(r['status'] for r in cases)),
               'parse_launch_resume_or_retry_performed': False, 'facts_database_modified': False,
               'semantic_or_statistical_certification': False}
    write_new(out / ('collection-' + str(index).zfill(4) + '.json'), receipt)
    print(json.dumps({k: receipt[k] for k in ('unit', 'state_observation', 'unit_state', 'statuses', 'cases')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
