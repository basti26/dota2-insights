"""Latest-only Tier 1 collection; separate from the bounded D2PT sample library."""
import json
import re
import time
import urllib.error
from urllib.parse import urlsplit


def approved_leagues(config):
    result = {}
    for league in config.get('tier1_leagues', []):
        identifier = league.get('league_id')
        url = urlsplit(league.get('liquipedia_url', ''))
        if (type(identifier) is not int or identifier <= 0 or league.get('tier') != 1
                or url.scheme != 'https' or url.hostname != 'liquipedia.net'
                or not url.path.startswith('/dota2/') or not league.get('name')):
            raise ValueError('tier1_league_evidence_required')
        result[identifier] = league
    if not result:
        raise ValueError('tier1_no_confirmed_leagues')
    return result


def candidates(rows, leagues, now):
    """Require exact league ID and name, not a premium/sponsored label or pro player."""
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError('tier1_match_list_schema')
    found = {}
    for row in rows:
        lid = row.get('leagueid')
        league = leagues.get(lid) if type(lid) is int else None
        if league is None:
            continue
        names = [league['name'], *league.get('aliases', [])]
        if str(row.get('league_name', '')).strip().casefold() not in {n.strip().casefold() for n in names}:
            continue
        identifier = str(row.get('match_id', ''))
        start, duration = row.get('start_time'), row.get('duration')
        if (not re.fullmatch('[0-9]{8,12}', identifier) or type(start) is not int
                or type(duration) is not int or not 0 < duration <= 12*3600):
            continue
        end = start + duration
        if not now - 86400 <= end <= now - 600:
            continue
        found[identifier] = {'match_id': identifier, 'league_id': lid, 'league_name': league['name'],
            'liquipedia_url': league['liquipedia_url'], 'tier': 1, 'start_time': start,
            'duration': duration, 'estimated_end_time': end,
            'radiant_name': row.get('radiant_name'), 'dire_name': row.get('dire_name')}
    return sorted(found.values(), key=lambda r: (r['estimated_end_time'], int(r['match_id'])), reverse=True)


def collect_latest(store):
    from dota2_insights.replay_library_store import request, download, atomic, utc, mid
    from dota2_insights.replay_holdout import refuse_locked_match
    leagues = approved_leagues(store.config)
    with store.locked('collector'):
        now = time.time()
        state_path = store.root/'tier1-tracking.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else None
        existing = store.catalog()['matches']
        source_error = None
        try:
            rows = json.loads(request('https://api.opendota.com/api/proMatches'))
            available = candidates(rows, leagues, now)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            available = []
            source_error = {'error_type':type(exc).__name__,'http_status':getattr(exc,'code',None)}
        # Previously discovered new maps remain retryable even after falling out of the latest list.
        known = []
        for record in existing.values():
            if 'tier1' not in record.get('sources', []):
                continue
            if record.get('download_status') not in ('waiting_metadata','waiting_retry','manual_required'):
                continue
            sample = record.get('sampling') or {}
            end = sample.get('estimated_end_time')
            if type(end) not in (int,float) or end < now-86400:
                continue
            known.append({'match_id':record['match_id'],'leagueid':sample.get('league_id'),
                'league_name':sample.get('league_name'),'start_time':sample.get('start_time'),
                'duration':sample.get('duration'),'radiant_name':sample.get('radiant_name'),
                'dire_name':sample.get('dire_name')})
        available = list({r['match_id']:r for r in candidates(known,leagues,now)+available}.values())
        available.sort(key=lambda r:(r['estimated_end_time'],int(r['match_id'])),reverse=True)
        if not available:
            return {'status': 'source_query_failed' if source_error else 'no_recent_confirmed_tier1',
                    'source_error':source_error, 'results': [], 'historical_backfill': False}
        if state is None:
            # Enrol only the newest completed map. Subsequent polls collect newer maps.
            state = {'schema_version': 1, 'enabled_at_utc': utc(),
                     'baseline_end_time': available[0]['estimated_end_time'],
                     'baseline_match_id': available[0]['match_id'], 'historical_backfill': False}
            atomic(state_path, state)
        baseline = (state['baseline_end_time'], int(state['baseline_match_id']))
        pending = []
        for candidate in available:
            identifier = candidate['match_id']
            if (candidate['estimated_end_time'], int(identifier)) < baseline:
                continue
            prior = existing.get(identifier)
            if prior:
                legacy_http_failure = (prior.get('download_status') == 'manual_required'
                    and prior.get('reason') == 'HTTPError' and prior.get('http_status') in (403,404,408,425,429,500,502,503,504))
                if ('tier1' not in prior.get('sources', []) or
                        not (prior.get('download_status') in ('waiting_metadata','waiting_retry') or legacy_http_failure)
                        or prior.get('retry_after_epoch', 0) > now):
                    continue
            pending.append(candidate)
        results = []
        # Per-cycle work bound, not a daily or lifetime quota; remaining maps stay eligible.
        for candidate in reversed(pending[-3:]):
            identifier = mid(candidate['match_id'])
            refuse_locked_match(identifier)
            attempts = (existing.get(identifier) or {}).get('metadata_attempts', 0) + 1
            store.update(identifier, sources=['tier1'], sampling=candidate, download_status='attempting',
                         metadata_attempts=attempts, analysis_status='waiting_for_replay')
            try:
                data = json.loads(request(f'https://api.opendota.com/api/matches/{identifier}'))
                if mid(data.get('match_id')) != identifier or data.get('leagueid') != candidate['league_id']:
                    raise ValueError('tier1_metadata_identity_mismatch')
                url = data.get('replay_url')
                if not url:
                    location = json.loads(request(f'https://api.opendota.com/api/replays?match_id={identifier}'))
                    refs = [r for r in location if str(r.get('match_id')) == identifier] if isinstance(location, list) else []
                    if len(refs) == 1 and type(refs[0].get('cluster')) is int and type(refs[0].get('replay_salt')) is int:
                        url = f"http://replay{refs[0]['cluster']}.valve.net/570/{identifier}_{refs[0]['replay_salt']}.dem.bz2"
                if not url:
                    status = 'waiting_metadata'
                    store.update(identifier, download_status=status, retry_after_epoch=now+600,
                                 reason='public_replay_address_unavailable')
                    results.append({'match_id': identifier, 'status': status})
                    continue
                download(store, identifier, url, candidate, source='tier1')
                # This attempt completed; counters/old receipts remain historical.
                store.update(identifier, analysis_status='pending', reason=None,
                             http_status=None, retry_after_epoch=None)
                results.append({'match_id': identifier, 'status': 'verified'})
            except Exception as exc:
                code = getattr(exc,'code',None)
                transient = (isinstance(exc,(TimeoutError,ConnectionError,urllib.error.URLError)) and
                    (not isinstance(exc,urllib.error.HTTPError) or code in (403,404,408,425,429,500,502,503,504)))
                retries = (existing.get(identifier) or {}).get('network_retries',0)+1
                status = 'waiting_retry' if transient else 'manual_required'
                store.update(identifier, download_status=status, network_retries=retries,
                             retry_after_epoch=now+min(3600,600*2**min(retries-1,3)),
                             reason=type(exc).__name__, http_status=code)
                results.append({'match_id': identifier, 'status': status, 'error_type': type(exc).__name__})
        return {'status': 'finished', 'results': results, 'historical_backfill': False,
                'source_error':source_error,
                'quota_scope': 'tier1_unlimited_excluded_from_d2pt', 'remaining_eligible': max(0,len(pending)-3)}
