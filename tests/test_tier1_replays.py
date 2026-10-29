from contextlib import nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dota2_insights import tier1_replays as tier1
from dota2_insights import replay_library_store as library


class LocalStore(library.Store):
    def locked(self, name='catalog'):
        return nullcontext()


class Tier1Tests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.store=LocalStore(Path(t.name))
        self.store.config={'max_replays': 100, 'tier1_leagues':[{'league_id': 123, 'name': 'Example Event',
            'tier': 1, 'liquipedia_url': 'https://liquipedia.net/dota2/Example'}]}
        self.now=2_000_000

    def row(self, mid='9876543210', end=None):
        return {'match_id':int(mid),'leagueid':123,'league_name':'Example Event',
                'start_time':(end or self.now-1000)-2000,'duration':2000}

    def collect(self, rows):
        def request(url):
            if url.endswith('/proMatches'): return json.dumps(rows).encode()
            if '/matches/' in url:
                mid=url.rsplit('/',1)[1]
                return json.dumps({'match_id':int(mid),'leagueid':123,
                    'replay_url':f'http://replay123.valve.net/570/{mid}_1.dem.bz2'}).encode()
            raise AssertionError(url)
        def downloaded(store,mid,url,details,**kwargs):
            self.assertEqual(kwargs,{'source':'tier1'})
            store.update(mid,download_status='verified')
        with patch.object(library,'request',side_effect=request), patch.object(library,'download',side_effect=downloaded), patch.object(tier1.time,'time',return_value=self.now):
            return tier1.collect_latest(self.store)

    def test_first_poll_only_latest_then_all_new_not_old_backfill(self):
        rows=[self.row('9876543200',self.now-2000),self.row()]
        first=self.collect(rows)
        self.assertEqual([r['match_id'] for r in first['results']],['9876543210'])
        rows += [self.row('9876543211',self.now-900),self.row('9876543212',self.now-800)]
        second=self.collect(rows)
        self.assertEqual({r['match_id'] for r in second['results']},{'9876543211','9876543212'})
        self.assertNotIn('9876543200',self.store.catalog()['matches'])
        self.assertFalse(self.collect(rows)['results'])

    def test_other_tiers_names_qualifiers_old_and_unfinished_not_eligible(self):
        leagues=tier1.approved_leagues(self.store.config)
        rows=[self.row() for _ in range(5)]
        rows[0]['leagueid']=456
        rows[1]['league_name']='Example Event Qualifiers'
        rows[2]['start_time']=self.now-100000
        rows[3]['duration']=0
        rows[4]['start_time']=self.now
        self.assertEqual(tier1.candidates(rows,leagues,self.now),[])

    def test_tier1_not_charged_to_full_d2pt_quota(self):
        for i in range(100):
            self.store.update(str(9000000000+i),sources=['d2pt'],download_status='verified')
        self.assertEqual(self.store.capacity(),(100,100))
        self.assertEqual(self.collect([self.row()])['results'][0]['status'],'verified')
        self.assertEqual(self.store.capacity(),(100,100))
        self.store.update('9000000000',sources=['d2pt','tier1'])
        self.assertEqual(self.store.capacity(),(99,100))

    def test_missing_replay_rechecked_in_background_after_ten_minutes(self):
        rows=json.dumps([self.row()]).encode()
        def request(url):
            return rows if url.endswith('/proMatches') else b'[]' if '/replays?' in url else b'{"match_id":9876543210,"leagueid":123}'
        with patch.object(library,'request',side_effect=request),patch.object(tier1.time,'time',return_value=self.now):
            self.assertEqual(tier1.collect_latest(self.store)['results'][0]['status'],'waiting_metadata')
            self.assertEqual(tier1.collect_latest(self.store)['results'],[])
        with patch.object(library,'request',side_effect=request),patch.object(tier1.time,'time',return_value=self.now+601):
            self.assertEqual(tier1.collect_latest(self.store)['results'][0]['status'],'waiting_metadata')
            self.assertEqual(tier1.collect_latest(self.store)['results'],[])

    def test_transient_http_failure_retries_with_backoff_then_succeeds(self):
        import urllib.error
        rows=json.dumps([self.row()]).encode()
        error=urllib.error.HTTPError('https://api.opendota.com/',403,'forbidden',None,None)
        with patch.object(library,'request',side_effect=[rows,error]),patch.object(tier1.time,'time',return_value=self.now):
            result=tier1.collect_latest(self.store)
            self.assertEqual(result['results'][0]['status'],'waiting_retry')
        self.assertEqual(self.collect([self.row()])['results'],[])
        self.now+=601
        self.assertEqual(self.collect([self.row()])['results'][0]['status'],'verified')
        row=self.store.catalog()['matches']['9876543210']
        self.assertIsNone(row.get('reason'))
        self.assertIsNone(row.get('http_status'))
        self.assertIsNone(row.get('retry_after_epoch'))
        self.assertEqual(row['network_retries'],1)

    def test_completed_analysis_clears_current_failure_without_removing_original_work(self):
        import base64,hashlib
        identifier='9876543210';raw=b'[]'
        payload={'manifest':{'match_id':identifier,'source_sha256':'a'*64,
            'complete':True,'stop_reason':'replay_eof','parse_error':None,'ward_count':0,
            'files':{'wards.json':{'sha256':hashlib.sha256(raw).hexdigest()}}},
            'wards_base64':base64.b64encode(raw).decode()}
        self.store.update(identifier,sources=['tier1'],download_status='verified',sha256='a'*64,
            analysis_status='partial_or_failed',reason='parser_incomplete',http_status=404,
            retry_after_epoch=123,analysis_failure={'parse_error':'BufferReadError'})
        original=self.store.root/'work'/'previous-failure.json'
        original.parent.mkdir();original.write_bytes(b'{"complete":false}')
        with patch.object(library,'ward_page',return_value='test'),patch.object(library,'summary_page',return_value='test'):
            self.store.accept_analysis(identifier,payload)
        row=self.store.catalog()['matches'][identifier]
        self.assertEqual(row['analysis_status'],'complete')
        for key in ('reason','http_status','retry_after_epoch','analysis_failure'):
            self.assertIsNone(row.get(key))
        self.assertEqual(original.read_bytes(),b'{"complete":false}')

    def test_rejected_analysis_keeps_current_failure(self):
        import base64
        identifier='9876543210'
        self.store.update(identifier,sources=['tier1'],download_status='verified',sha256='a'*64,
            analysis_status='partial_or_failed',reason='parser_incomplete',
            analysis_failure={'parse_error':'BufferReadError'})
        payload={'manifest':{'match_id':identifier,'source_sha256':'a'*64,'complete':False},
                 'wards_base64':base64.b64encode(b'[]').decode()}
        with self.assertRaisesRegex(ValueError,'analysis_identity_or_completion_mismatch'):
            self.store.accept_analysis(identifier,payload)
        row=self.store.catalog()['matches'][identifier]
        self.assertEqual(row['reason'],'parser_incomplete')
        self.assertEqual(row['analysis_failure']['parse_error'],'BufferReadError')
        self.assertEqual(row['analysis_status'],'partial_or_failed')

    def test_known_latest_map_can_retry_when_discovery_api_is_unavailable(self):
        import urllib.error
        candidate=tier1.candidates([self.row()],tier1.approved_leagues(self.store.config),self.now)[0]
        self.store.update('9876543210',sources=['tier1'],sampling=candidate,
            download_status='manual_required',reason='HTTPError',http_status=503)
        library.atomic(self.store.root/'tier1-tracking.json',{'baseline_end_time':candidate['estimated_end_time'],
            'baseline_match_id':candidate['match_id']})
        def request(url):
            if url.endswith('/proMatches'):raise urllib.error.HTTPError(url,503,'unavailable',None,None)
            return b'{"match_id":9876543210,"leagueid":123,"replay_url":"http://replay123.valve.net/570/9876543210_1.dem.bz2"}'
        with patch.object(library,'request',side_effect=request),patch.object(library,'download') as download,patch.object(tier1.time,'time',return_value=self.now):
            r=tier1.collect_latest(self.store)
            download.assert_called_once()
            self.assertEqual(r['results'][0]['status'],'verified')
            self.assertEqual(r['source_error']['http_status'],503)

    def test_downloader_runs_while_analyzer_is_still_busy(self):
        import sys,threading,types
        stop=threading.Event();parsing=threading.Event();downloaded=threading.Event()
        self.store.config.update(local_analysis=True,tier1_enabled=True)
        def process(_store):
            parsing.set();stop.wait(3);return {'status':'queue_empty'}
        def collect(_store):
            downloaded.set();return {'results':[]}
        with patch.dict(sys.modules,{'pi_ward_worker':types.SimpleNamespace(process_one=process)}),patch.object(tier1,'collect_latest',side_effect=collect):
            threads=library.start_background_jobs(self.store,stop=stop,startup_delay_s=0)
            try:
                self.assertTrue(parsing.wait(2))
                self.assertTrue(downloaded.wait(2))
                self.assertEqual(len(threads),2)
            finally:
                stop.set()
                for thread in threads:thread.join(timeout=3)
            self.assertTrue(all(not thread.is_alive() for thread in threads))

    def test_metadata_mismatch_never_downloads_and_http_errors_not_success(self):
        with patch.object(library,'request',side_effect=[json.dumps([self.row()]).encode(),b'{"match_id":9876543210,"leagueid":999}']),patch.object(library,'download') as download,patch.object(tier1.time,'time',return_value=self.now):
            self.assertEqual(tier1.collect_latest(self.store)['results'][0]['status'],'manual_required')
            download.assert_not_called()
        with patch.object(library,'request',side_effect=OSError('offline')):
            with self.assertRaises(OSError): tier1.collect_latest(self.store)

    def test_unlimited_only_removes_configured_budget_not_disk_reserve(self):
        from collections import namedtuple
        usage=namedtuple('usage','total used free')
        self.store.config['storage_limit_gib']=0
        with patch.object(library.shutil,'disk_usage',return_value=usage(100*1024**3,0,100*1024**3)):
            with self.assertRaisesRegex(ValueError,'storage_reserve'): self.store.reserve(1024)
            self.store.reserve(1024,unlimited=True)
        with patch.object(library.shutil,'disk_usage',return_value=usage(100*1024**3,0,3*1024**3)):
            with self.assertRaisesRegex(ValueError,'storage_reserve'): self.store.reserve(1024,unlimited=True)

    def test_no_league_evidence_cannot_treat_all_professional_as_tier1(self):
        with self.assertRaisesRegex(ValueError,'no_confirmed'):tier1.approved_leagues({})
        self.store.config['tier1_leagues'][0]['tier']=2
        with self.assertRaisesRegex(ValueError,'evidence_required'):tier1.approved_leagues(self.store.config)
