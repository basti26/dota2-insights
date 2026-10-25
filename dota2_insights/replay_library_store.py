"""Small stdlib-only replay archive for a Pi; no Steam login or live game access."""
from __future__ import annotations

import argparse
import base64
import bz2
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import struct
import subprocess
import sys
import time
import threading
import urllib.parse
import urllib.request
import uuid

MAX_DEM = 1024**3
MAX_WIRE = 512*1024**2
UA = 'dota2-helper-replay-library/0.1'


def utc():
    return datetime.now(timezone.utc).isoformat()


def mid(value):
    value = str(value)
    if re.fullmatch(r'[1-9][0-9]{0,19}', value) is None:
        raise ValueError('invalid_match_id')
    return value


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_bytes(path, raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def varint(stream):
    result = 0
    for shift in range(0, 70, 7):
        byte = stream.read(1)
        if not byte:
            raise ValueError('truncated_varint')
        result |= (byte[0]&127) << shift
        if byte[0] < 128:
            return result
    raise ValueError('oversized_varint')


def unsnappy(data):
    """Decode the bounded indexed footer, never a full replay packet stream."""
    from io import BytesIO
    stream = BytesIO(data)
    size = varint(stream)
    if size > 1024**2:
        raise ValueError('footer_expansion_limit')
    out = bytearray()
    while len(out) < size:
        tag = stream.read(1)
        if not tag:
            raise ValueError('truncated_snappy')
        tag = tag[0]
        kind, length = tag&3, tag>>2
        if kind == 0:
            if length >= 60:
                count = length-59
                raw = stream.read(count)
                if len(raw) != count:
                    raise ValueError('truncated_literal')
                length = int.from_bytes(raw,'little')
            length += 1
            raw = stream.read(length)
            if len(raw) != length or len(out)+length > size:
                raise ValueError('invalid_literal')
            out.extend(raw)
        else:
            count = {1:1,2:2,3:4}[kind]
            raw = stream.read(count)
            if len(raw) != count:
                raise ValueError('truncated_copy')
            if kind == 1:
                length, offset = 4+((tag>>2)&7), ((tag&224)<<3)|raw[0]
            else:
                length, offset = 1+(tag>>2), int.from_bytes(raw,'little')
            if not 0 < offset <= len(out) or len(out)+length > size:
                raise ValueError('invalid_copy')
            for _ in range(length):
                out.append(out[-offset])
    return bytes(out)


def protobuf_field(data, expected):
    from io import BytesIO
    stream = BytesIO(data)
    while stream.tell() < len(data):
        tag = varint(stream)
        number, wire = tag>>3, tag&7
        if wire == 0:
            value = varint(stream)
        elif wire == 2:
            size = varint(stream)
            if size > 1024**2:
                raise ValueError('protobuf_field_limit')
            value = stream.read(size)
            if len(value) != size:
                raise ValueError('truncated_protobuf_field')
        elif wire in (1,5):
            size = 8 if wire == 1 else 4
            raw = stream.read(size)
            if len(raw) != size:
                raise ValueError('truncated_fixed_field')
            value = int.from_bytes(raw,'little')
        else:
            raise ValueError('unsupported_protobuf_wire')
        if number == expected:
            return value
    raise ValueError('missing_footer_field')


def footer_id(path):
    """CDemoFileInfo.game_info(4).dota(4).match_id(1), uint64 preserved."""
    with Path(path).open('rb') as stream:
        header = stream.read(16)
        if len(header) != 16 or header[:8] != b'PBDEMS2\0':
            raise ValueError('invalid_dem_header')
        offset = struct.unpack_from('<I', header, 8)[0]
        if not 16 <= offset < Path(path).stat().st_size:
            raise ValueError('invalid_footer_offset')
        stream.seek(offset)
        command = varint(stream)
        varint(stream)
        size = varint(stream)
        if command & ~64 != 2 or size > 1024**2:
            raise ValueError('invalid_footer_packet')
        data = stream.read(size)
        if len(data) != size:
            raise ValueError('truncated_footer')
        if command & 64:
            data = unsnappy(data)
        return mid(protobuf_field(protobuf_field(protobuf_field(data,4),4),1))


def verify(path, expected):
    path = Path(path)
    before = path.stat()
    if not 16 <= before.st_size <= MAX_DEM or footer_id(path) != mid(expected):
        raise ValueError('replay_identity_mismatch')
    digest = sha(path)
    after = path.stat()
    if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
        raise ValueError('replay_changed')
    return {'match_id': mid(expected), 'bytes': after.st_size, 'sha256': digest,
            'verification': 'source2_magic_indexed_uint64_footer_and_sha256'}


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        config = self.root/'config.json'
        self.config = json.loads(config.read_text()) if config.exists() else {}
        self.replays=Path(self.config.get('replay_directory',self.root/'replays')).resolve()
        self.analysis=Path(self.config.get('analysis_directory',self.root/'analysis')).resolve()
        for folder in (self.replays,self.analysis,self.root/'receipts',self.root/'incoming'):
            folder.mkdir(parents=True,exist_ok=True)

    def capacity(self):
        limit=max(1,min(100,int(self.config.get('max_replays',100))))
        catalog=self.catalog()['matches']
        # Personal replays are retained separately and never consume the pro quota.
        verified=sum(r.get('download_status')=='verified' and 'd2pt' in r.get('sources',[])
                     and 'own' not in r.get('sources',[]) for r in catalog.values())
        return verified,limit

    @contextmanager
    def locked(self, name='catalog'):
        import fcntl
        with (self.root/(name+'.lock')).open('a') as stream:
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
            yield

    def catalog(self):
        path = self.root/'catalog.json'
        return json.loads(path.read_text()) if path.exists() else {'schema_version':1,'matches':{},'updated_at_utc':utc()}

    def update(self, identifier, **fields):
        identifier = mid(identifier)
        with self.locked():
            data = self.catalog()
            row = data['matches'].setdefault(identifier,{'match_id':identifier,'sources':[]})
            row.update(fields)
            data['updated_at_utc'] = utc()
            atomic(self.root/'catalog.json',data)

    def reserve(self, expected_bytes):
        budget = int(self.config.get('storage_limit_gib',12))*1024**3
        used = sum(p.stat().st_size for p in self.replays.glob('*.dem'))
        free = shutil.disk_usage(self.root).free
        if used+expected_bytes > budget or free-expected_bytes < 4*1024**3:
            raise ValueError('archive_storage_reserve')

    def commit(self, temporary, identifier, source, expected_sha=None, details=None):
        value = verify(temporary,identifier)
        if expected_sha and value['sha256'] != expected_sha:
            raise ValueError('transfer_digest_mismatch')
        with self.locked():
            target = self.replays/(mid(identifier)+'.dem')
            data = self.catalog()
            row = data['matches'].setdefault(mid(identifier),{'match_id':mid(identifier),'sources':[]})
            if target.exists():
                if sha(target) != value['sha256']:
                    raise ValueError('existing_replay_conflict')
                temporary.unlink()
            else:
                if source=='d2pt' and self.capacity()[0]>=self.capacity()[1]:raise ValueError('replay_limit_reached')
                self.reserve(value['bytes'])
                os.replace(temporary,target)
            row.update(value,download_status='verified',analysis_status=row.get('analysis_status','pending'),
                       archived_at_utc=utc())
            row['sources'] = sorted(set(row.get('sources',[])+[source]))
            if details:
                row['sampling'] = details
            data['updated_at_utc'] = utc()
            atomic(self.root/'catalog.json',data)
        return value

    def receive(self, identifier, source, size, expected_sha):
        if source not in ('own','d2pt') or not 16 <= size <= MAX_DEM or not re.fullmatch('[0-9a-f]{64}',expected_sha):
            raise ValueError('invalid_transfer')
        self.reserve(size)
        temporary = self.root/'incoming'/(mid(identifier)+'.'+uuid.uuid4().hex+'.part')
        try:
            remaining = size
            with temporary.open('xb') as out:
                while remaining:
                    block = sys.stdin.buffer.read(min(1024**2,remaining))
                    if not block:
                        raise ValueError('transfer_incomplete')
                    out.write(block);remaining -= len(block)
            return self.commit(temporary,identifier,source,expected_sha)
        finally:
            temporary.unlink(missing_ok=True)

    def receive_analysis(self, identifier):
        raw = sys.stdin.buffer.read(8*1024**2+1)
        if len(raw) > 8*1024**2:
            raise ValueError('analysis_payload_limit')
        payload = json.loads(raw)
        return self.accept_analysis(identifier,payload)

    def accept_analysis(self,identifier,payload):
        manifest = payload['manifest']
        ward_bytes=base64.b64decode(payload['wards_base64'],validate=True)
        wards=json.loads(ward_bytes)
        row = self.catalog()['matches'].get(mid(identifier))
        if (not row or row.get('download_status')!='verified' or manifest.get('match_id') != mid(identifier)
                or manifest.get('source_sha256') != row.get('sha256') or manifest.get('complete') is not True
                or manifest.get('stop_reason') != 'replay_eof' or manifest.get('parse_error')
                or not isinstance(wards,list) or len(wards)>5000):
            raise ValueError('analysis_identity_or_completion_mismatch')
        json.dumps(wards,allow_nan=False)  # Reject NaN/Infinity accepted by json.loads.
        if (manifest.get('ward_count')!=len(wards) or
            manifest.get('files',{}).get('wards.json',{}).get('sha256')!=hashlib.sha256(ward_bytes).hexdigest()):
            raise ValueError('analysis_digest_mismatch')
        if any(w.get('match_id') != mid(identifier) or w.get('ward_type') not in ('observer','sentry') for w in wards):
            raise ValueError('invalid_ward_rows')
        if any(w.get(key) is not None and type(w[key]) not in (int,float)
               for w in wards for key in ('x','y','placed_game_time_s','ended_game_time_s','duration_s')):
            raise ValueError('invalid_ward_numeric_fields')
        context_bytes=None
        if manifest.get('postgame_context') is not None:
            from dota2_insights.replay_postgame_context import validate_context
            encoded=payload.get('postgame_context_base64')
            if not isinstance(encoded,str) or len(encoded)>16*1024**2:
                raise ValueError('postgame_context_payload_required_or_oversized')
            context_bytes=base64.b64decode(encoded,validate=True)
            detail=manifest.get('files',{}).get('postgame_context.json',{})
            if len(context_bytes)>12*1024**2 or detail.get('bytes')!=len(context_bytes) or detail.get('sha256')!=hashlib.sha256(context_bytes).hexdigest():
                raise ValueError('postgame_context_digest_mismatch')
            context=validate_context(json.loads(context_bytes),mid(identifier),row['sha256'])
            if manifest['postgame_context'].get('producer_sha256')!=context['producer_sha256'] or manifest['postgame_context'].get('complete') is not True:
                raise ValueError('postgame_context_manifest_mismatch')
        elif 'postgame_context_base64' in payload:
            raise ValueError('postgame_context_without_manifest')
        folder = self.analysis/mid(identifier)
        atomic_bytes(folder/'wards.json',ward_bytes)
        if context_bytes is not None:atomic_bytes(folder/'postgame_context.json',context_bytes)
        atomic(folder/'manifest.json',manifest)
        summary = dict(Counter(f"{w.get('team')}:{w['ward_type']}" for w in wards))
        valid=[w for w in wards if not w.get('after_match_end')]
        fast=sum(w.get('end_kind')=='killed' and w.get('duration_s') is not None and 0<=w['duration_s']<=60 for w in valid)
        self.update(identifier,analysis_status='complete',ward_count=len(wards),ward_counts=summary,
                    analyzed_at_utc=utc(),analysis_sha256=sha(folder/'wards.json'),
                    postgame_context_status='complete' if context_bytes is not None else 'not_requested',
                    postgame_context_sha256=hashlib.sha256(context_bytes).hexdigest() if context_bytes is not None else None,
                    postgame_context_event_count=len(context['events']) if context_bytes is not None else None,
                    postgame_context_metric_packet_count=len(context['metric_packets']) if context_bytes is not None else None,
                    quick_killed_unverified_team_count=fast,
                    unknown_end_count=sum(w.get('end_kind')=='unobserved' for w in valid))
        atomic_bytes(folder/'眼位报告.html',ward_page(self,mid(identifier)).encode('utf-8'))
        atomic_bytes(self.analysis/'汇总.html',summary_page(self).encode('utf-8'))
        return {'match_id':mid(identifier),'analysis_status':'complete','ward_count':len(wards)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):
        raise ValueError('unexpected_redirect')


def request(url, limit=4*1024**2):
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with client.open(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=15) as response:
        raw = response.read(limit+1)
    if len(raw)>limit:
        raise ValueError('response_size_limit')
    return raw


def valve_url(url, identifier):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme not in ('http','https') or parsed.username or parsed.password
            or parsed.port not in (None,80,443) or parsed.query or parsed.fragment
            or not re.fullmatch(r'replay[0-9]{1,4}\.(valve\.net|dota2\.com\.cn)',parsed.hostname or '')
            or not re.fullmatch(r'/570/'+mid(identifier)+r'_[0-9]{1,10}\.dem\.bz2',parsed.path)):
        raise ValueError('unapproved_replay_url')
    return url


def download(store, identifier, url, details):
    identifier=mid(identifier);valve_url(url,identifier)
    store.reserve(MAX_DEM+MAX_WIRE)
    wire=store.root/'incoming'/(identifier+'.'+uuid.uuid4().hex+'.compressed.part')
    temporary=wire.with_name(wire.name+'.dem')
    began=time.monotonic()
    process=None
    try:
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        received=0
        with client.open(urllib.request.Request(url,headers={'User-Agent':UA}),timeout=15) as response,wire.open('xb')as out:
            length=response.headers.get('Content-Length')
            length=int(length) if length and length.isdigit() else None
            if length and length>MAX_WIRE:raise ValueError('compressed_size_limit')
            while True:
                block=response.read(64*1024)
                if not block:break
                received+=len(block)
                if received>MAX_WIRE or time.monotonic()-began>240:raise ValueError('download_resource_limit')
                out.write(block)
                # Keep a daily background download from saturating the home uplink.
                limit=max(.25,min(4,float(store.config.get('download_mib_per_second',1))))*1024**2
                delay=received/limit-(time.monotonic()-began)
                if delay>0:time.sleep(min(delay,1))
        if length is not None and received!=length:raise ValueError('truncated_download')
        with wire.open('rb')as stream: magic=stream.read(4)
        if magic[:3]==b'BZh':
            decoded=bz2.open(wire,'rb')
        elif magic==b'\x28\xb5\x2f\xfd' and shutil.which('zstd'):
            process=subprocess.Popen(['zstd','-q','-d','--memory=128MB','-c',str(wire)],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            decoded=process.stdout
        else:raise ValueError('compression_decoder_unavailable')
        total=0
        with decoded,temporary.open('xb')as out:
            while True:
                block=decoded.read(1024**2)
                if not block:break
                total+=len(block)
                if total>MAX_DEM or time.monotonic()-began>360:raise ValueError('expanded_resource_limit')
                out.write(block)
        if process is not None and process.wait(timeout=5)!=0:raise ValueError('zstd_decode_failed')
        result=store.commit(temporary,identifier,'d2pt',details=details)
        atomic(store.root/'receipts'/f'{identifier}-download.json',
               {**result,'source':'dota2protracker_public_list_then_opendota_valve','wire_bytes':received,'at_utc':utc()})
        return result
    finally:
        if process is not None and process.poll() is None:
            process.kill();process.wait()
        wire.unlink(missing_ok=True);temporary.unlink(missing_ok=True)


def collect(store, count, seed=None, prepared=None):
    """Bounded random sample; one address lookup/download attempt per match."""
    count=max(1,min(3,count))
    with store.locked('collector'):
        current,limit=store.capacity()
        if current>=limit:return {'status':'replay_limit_reached','verified':current,'limit':limit}
        count=min(count,limit-current)
        existing=store.catalog()['matches']
        today=datetime.now(timezone.utc).date().isoformat()
        attempts=sum(r.get('sampled_at_utc','').startswith(today) for r in existing.values())
        budget=max(0,int(store.config.get('pro_daily_limit',3))-attempts)
        if not budget:return {'status':'daily_limit','attempts':attempts}
        if prepared is None:
            robots=request('https://dota2protracker.com/robots.txt',64*1024).decode('utf-8')
            import urllib.robotparser
            rules=urllib.robotparser.RobotFileParser();rules.parse(robots.splitlines())
            if not rules.can_fetch(UA,'https://dota2protracker.com/matches'):
                raise ValueError('source_robots_disallow')
            time.sleep(2)
            raw=request('https://dota2protracker.com/matches')
            text=raw.decode('utf-8')
            candidates=[]
            for match in re.finditer(r'\{match_id:([0-9]{8,12}),activate_time:([0-9]{10}),mmr:([0-9]{3,5}),from_upload:([01]),duration:([0-9]+)',text):
                identifier,stamp,mmr,uploaded,duration=map(int,match.groups())
                if mmr>=int(store.config.get('min_mmr',7000)) and duration>=600:
                    candidates.append({'match_id':str(identifier),'mmr':mmr,'d2pt_activate_time':stamp,
                        'duration':duration,'from_upload':uploaded,'source_list_sha256':hashlib.sha256(raw).hexdigest()})
            if not candidates:raise ValueError('source_schema_changed_or_empty')
        else:
            candidates=[dict(r) for r in prepared['selected']]
            for candidate in candidates:
                if 'start_time' in candidate:
                    candidate['d2pt_activate_time']=candidate.pop('start_time')
            seed=prepared['seed']
        candidates=list({mid(r['match_id']):r for r in candidates
            if int(r.get('mmr',0))>=int(store.config.get('min_mmr',7000))
            and int(r.get('duration',0))>=600 and mid(r['match_id']) not in existing}.values())
        seed=secrets.randbits(64) if seed is None else seed
        if prepared is None:random.Random(seed).shuffle(candidates)
        selected=candidates[:min(count,budget)]
        atomic(store.root/'receipts'/('sample-'+uuid.uuid4().hex+'.json'),
               {'at_utc':utc(),'seed':seed,'eligible_pool_count':len(candidates),'selected':selected,
                'source':'https://dota2protracker.com/matches','source_usage':'reference_metadata_not_AI_training'})
        results=[]
        for candidate in selected:
            identifier=mid(candidate['match_id'])
            store.update(identifier,sources=['d2pt'],sampled_at_utc=utc(),sampling={**candidate,'seed':seed},
                         download_status='attempting',analysis_status='waiting_for_replay')
            try:
                data=json.loads(request(f'https://api.opendota.com/api/matches/{identifier}'))
                if mid(data.get('match_id'))!=identifier:raise ValueError('metadata_match_mismatch')
                url=data.get('replay_url')
                if not url:raise ValueError('public_replay_address_unavailable')
                download(store,identifier,url,{**candidate,'seed':seed})
                store.update(identifier,analysis_status='pending')
                results.append({'match_id':identifier,'status':'verified'})
            except Exception as exc:
                reason=str(exc) if isinstance(exc,ValueError) and re.fullmatch('[a-z_]{1,80}',str(exc)) else type(exc).__name__
                store.update(identifier,download_status='manual_required',reason=reason,
                             http_status=getattr(exc,'code',None),attempted_at_utc=utc())
                results.append({'match_id':identifier,'status':'manual_required','reason':reason})
            time.sleep(2)
        return {'status':'finished','results':results}


def public_catalog(store):
    data=store.catalog()
    fields={'match_id','sources','bytes','download_status','analysis_status','ward_count','ward_counts',
            'archived_at_utc','analyzed_at_utc','sampled_at_utc','reason','http_status',
            'quick_killed_unverified_team_count','unknown_end_count','postgame_context_status',
            'postgame_context_sha256','postgame_context_event_count','postgame_context_metric_packet_count'}
    rows=[]
    for row in data['matches'].values():
        out={k:v for k,v in row.items() if k in fields}
        out['mmr']=(row.get('sampling')or{}).get('mmr')
        rows.append(out)
    current,limit=store.capacity()
    worker={}
    try:
        status=json.loads((store.root/'worker_status.json').read_text())
        worker={k:status[k] for k in ('match_id','status','elapsed_seconds','rss_bytes','progress','at_utc') if k in status}
    except (OSError,ValueError):pass
    return {'updated_at_utc':data['updated_at_utc'],'matches':sorted(rows,key=lambda r:int(r['match_id']),reverse=True),
            'replay_count':current,'replay_limit':limit,'downloads_stopped':current>=limit,
            'total_replay_count':sum(r.get('download_status')=='verified' for r in rows),
            'own_replay_count':sum(r.get('download_status')=='verified' and 'own' in r.get('sources',[]) for r in rows),
            'analysis_location':'pi' if store.config.get('local_analysis') else 'windows',
            'postgame_context_enabled':store.config.get('postgame_context') is True,
            'worker':worker,
            'disk_free_bytes':shutil.disk_usage(store.root).free,'scope':'offline_replay_analysis_not_live_ward_detection'}


def ward_summary(store):
    matches=[]
    for identifier,record in store.catalog()['matches'].items():
        if record.get('analysis_status')!='complete':continue
        folder=store.analysis/mid(identifier)
        manifest=json.loads((folder/'manifest.json').read_text())
        if (manifest.get('source_sha256')!=record.get('sha256') or
            sha(folder/'wards.json')!=manifest.get('files',{}).get('wards.json',{}).get('sha256')):
            continue
        rows=[w for w in json.loads((folder/'wards.json').read_text()) if not w.get('after_match_end')]
        counts=Counter(w['ward_type'] for w in rows)
        duration=manifest.get('match_end_game_time_s')
        quick=[w for w in rows if w.get('end_kind')=='killed' and w.get('duration_s') is not None and 0<=w['duration_s']<=60]
        matches.append({'match_id':identifier,'source':'own' if 'own' in record['sources'] else 'd2pt',
            'mmr':(record.get('sampling')or{}).get('mmr'),'game_end_seconds':duration,
            'observer':counts['observer'],'sentry':counts['sentry'],
            'quick_killed_unverified_team':len(quick),
            'unknown_end':sum(w.get('end_kind')=='unobserved' for w in rows),
            'observer_per_10_minutes':round(counts['observer']*600/duration,2) if duration and duration>0 else None,
            'phase_placements':dict(Counter('pregame' if w['placed_game_time_s']<0 else
                '0_10' if w['placed_game_time_s']<600 else '10_20' if w['placed_game_time_s']<1200 else '20_plus'
                for w in rows if w.get('placed_game_time_s') is not None)),
            'quick_killed_events':[{'placed_game_time_s':w.get('placed_game_time_s'),
                'duration_s':w['duration_s'],'ward_type':w['ward_type'],'team':w.get('team'),
                'placer':w.get('placer'),'killer':w.get('killer'),'x':w.get('x'),'y':w.get('y')} for w in quick]})
    return {'schema_version':1,'created_at_utc':utc(),'matches':matches,
            'limitations':['Both teams combined; pregame included, after-match placements excluded.',
                'Quick kills have not been assigned an enemy/ally relation.',
                'Counts and density are descriptive, not ward effectiveness or a representative MMR comparison.']}


def summary_page(store):
    data=ward_summary(store)
    result=['<!doctype html><meta charset="utf-8"><title>眼位汇总</title><style>body{background:#172127;color:#e4edf2;font:16px sans-serif;margin:30px}td,th{padding:10px;border-bottom:1px solid #40505b}a{color:#8bd6b8}</style>',
        '<a href="/">返回资料库</a><h1>眼位汇总</h1><p>双方合计，含赛前、排除赛后。短时间被击杀尚未核对敌我，不代表反眼成功率；样本不能代表整个分段水平。</p>',
        '<table><tr><th>比赛</th><th>来源</th><th>假眼</th><th>真眼</th><th>每10分钟假眼</th><th>60秒内被击杀</th><th>结束未知</th></tr>']
    for row in sorted(data['matches'],key=lambda r:int(r['match_id']),reverse=True):
        result.append('<tr><td><a href="'+row['match_id']+'/眼位报告.html">'+row['match_id']+'</a></td>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in [
            '本人' if row['source']=='own' else 'D2PT '+str(row['mmr']),row['observer'],row['sentry'],row['observer_per_10_minutes'],row['quick_killed_unverified_team'],row['unknown_end']])+'</tr>')
    return '\n'.join(result+['</table>'])


PAGE='''<!doctype html><meta charset="utf-8"><title>Dota录像资料库</title>
<style>body{background:#172127;color:#e4edf2;font:16px sans-serif;max-width:1100px;margin:24px auto;padding:12px}a{color:#8bd6b8}td,th{padding:10px;text-align:left;border-bottom:1px solid #35434b}table{width:100%;border-collapse:collapse}.note{color:#b4c1ca}</style>
<h1>Dota录像资料库</h1><p class="note">本人录像与Dota2ProTracker随机高分局。眼位为赛后全知分析，未接入实战提示。</p><p><a href="/ward-summary">查看眼位汇总</a></p><p id="state">读取状态…</p><table><thead><tr><th>比赛</th><th>来源</th><th>分数</th><th>录像</th><th>眼位</th></tr></thead><tbody id="rows"></tbody></table>
<script>async function refresh(){try{const d=await(await fetch('/api/catalog',{cache:'no-store'})).json();document.getElementById('state').textContent='高分局 '+d.replay_count+'/'+d.replay_limit+' · 本人 '+d.own_replay_count+' 盘（不占额度） · '+(d.downloads_stopped?'下载已停止':'继续收集')+' · '+(d.worker&&d.worker.status==='analyzing'?'Pi正在分析 '+d.worker.match_id+'，已用 '+Math.floor(d.worker.elapsed_seconds/60)+' 分钟':'等待下一项')+' · 空闲 '+(d.disk_free_bytes/1073741824).toFixed(1)+' GiB';const host=document.getElementById('rows');host.replaceChildren();for(const r of d.matches){const tr=document.createElement('tr');for(const value of [r.match_id,(r.sources||[]).includes('own')?'本人':'D2PT随机',r.mmr||'—',r.download_status+(r.reason?' · '+r.reason:'')]){const td=document.createElement('td');td.textContent=value;tr.append(td)}const td=document.createElement('td');if(r.analysis_status==='complete'){const a=document.createElement('a');a.href='/match/'+r.match_id;a.textContent=r.ward_count+'条 · 查看';td.append(a)}else td.textContent=r.analysis_status||'等待';tr.append(td);host.append(tr)}}catch(e){document.getElementById('state').textContent='暂时无法读取状态'}}refresh();setInterval(refresh,10000)</script>'''


def ward_page(store, identifier):
    rows=json.loads((store.analysis/identifier/'wards.json').read_text())
    mapped=(store.analysis/'map.jpg').is_file()
    height=760*8356/8878 if mapped else 760
    parts=[f'<!doctype html><meta charset="utf-8"><title>眼位 {identifier}</title><style>body{{font:15px sans-serif;background:#172127;color:#e4edf2;margin:24px}}a{{color:#8bd6b8}}table{{border-collapse:collapse}}td,th{{padding:7px;border-bottom:1px solid #40505b}}.map-scroll{{width:min(1200px,95vw);max-height:78vh;overflow:auto;cursor:grab}}svg{{display:block;background:#22303a;width:100%;max-width:none}}.ward{{cursor:pointer;stroke-width:2}}tr:target{{background:#47595e}}</style>',
           '<a href="../汇总.html">返回汇总</a>',f'<h1>{identifier} · 眼位</h1>',
           '<p>绿=天辉，红=夜魇；圆=假眼，十字=真眼。悬停查看时间；点击标记定位下方记录。</p>',
           '<p>'+('7.41真实地形底图，采用固定版解析器的地标坐标标定；本项目尚未逐点核验，不能解释为精确视野。' if mapped else '原始世界坐标；暂未加载地形底图。')+'</p>',
           '<p><label><input id="all" type="checkbox" checked>全部放置点</label>　<label>时间 <input id="clock" type="range" min="-120" max="7200" value="0" step="1"></label> <span id="stamp"></span></p>',
           '<p><label>缩放 <input id="zoom" type="range" min="1" max="4" step="0.25" value="1"></label> 放大后可拖动地图。<span id="selection"></span></p>',
           '<p>取消“全部放置点”按时间查看；半透明点代表结束时间未知，不能证明当时仍存活。</p>',
           f'<div class="map-scroll"><svg id="ward-map" viewBox="0 0 760 {height:.3f}">']
    if mapped:
        parts.append(f'<image href="../map.jpg" x="0" y="0" width="760" height="{height:.3f}"/>')
    else:
        for value in range(8000,28001,4000):
            pos=40+(value-8000)/20000*680
            parts.append(f'<path d="M{pos} 40V720 M40 {pos}H720" stroke="#3b505c"/>')
    ordered=sorted(rows,key=lambda r:r.get('placed_game_time_s') if r.get('placed_game_time_s') is not None else -1e9)
    for index,row in enumerate(ordered):
        x,y=row.get('x'),row.get('y')
        if type(x) not in (int,float) or type(y) not in (int,float):continue
        if mapped:
            # Pinned gem.catalog.map.world_to_map_image, full uncropped 8878x8356 image.
            x=((8878-8356)/2+(x-7487)/18322*8356)/8878*760
            y=(1-(y-7693)/18322)*8356/8878*760
        else:x,y=40+(x-8000)/20000*680,720-(y-8000)/20000*680
        color={2:'#65d792',3:'#ed8181'}.get(row.get('team'),'#adb5bd')
        tip=html.escape(f"{row.get('placed_game_time_s')}s {row.get('placer')} {row.get('end_kind')}")
        shape=f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" stroke="#101a18"' if row['ward_type']=='observer' else f'<path d="M{x-4:.1f} {y:.1f}h8 M{x:.1f} {y-4:.1f}v8" stroke="{color}"'
        placed=row.get('placed_game_time_s');ended=row.get('ended_game_time_s')
        parts.append(shape+f' class="ward" data-row="ward-{index}" data-start="{placed if placed is not None else ""}" data-end="{ended if ended is not None else ""}" fill="{color}"><title>{tip}</title></'+('circle>' if row['ward_type']=='observer' else 'path>'))
    fast=[r for r in rows if not r.get('after_match_end') and r.get('end_kind')=='killed'
          and r.get('duration_s') is not None and 0<=r['duration_s']<=60]
    parts.append(f'</svg></div><p>记录{len(rows)}条；其中{len(fast)}条在60秒内被击杀。此处尚未核对击杀者阵营，不直接称为成功反眼。</p><table><tr><th>时间</th><th>阵营</th><th>类型</th><th>放置英雄</th><th>存活秒</th><th>结束</th><th>击杀者</th></tr>')
    for index,row in enumerate(ordered):
        seconds=row.get('placed_game_time_s')
        stamp=('—' if seconds is None else ('-'if seconds<0 else '')+f'{int(abs(seconds))//60}:{int(abs(seconds))%60:02d}')
        values=[stamp,{2:'天辉',3:'夜魇'}.get(row.get('team'),'未知'),'假眼'if row['ward_type']=='observer'else'真眼',row.get('placer'),
                round(row['duration_s'],1)if row.get('duration_s')is not None else'未知',{'killed':'被击杀','expired':'到期','unobserved':'未知'}.get(row.get('end_kind'),'未知'),row.get('killer')or'—']
        parts.append(f'<tr id="ward-{index}">'+''.join('<td>'+html.escape(str(v))+'</td>'for v in values)+'</tr>')
    parts.append('''</table><p>未知结束保留为未知；没有计算地形遮挡或眼位有效率。</p>
<script>const clock=document.getElementById('clock'),all=document.getElementById('all'),points=[...document.querySelectorAll('.ward')];const finite=points.flatMap(p=>[p.dataset.start,p.dataset.end]).filter(v=>v!=='').map(Number).filter(Number.isFinite);clock.max=Math.ceil(Math.max(0,...finite));clock.min=Math.floor(Math.min(-120,...finite));function render(){const t=Number(clock.value);document.getElementById('stamp').textContent=(t<0?'-':'')+Math.floor(Math.abs(t)/60)+':'+String(Math.abs(t)%60).padStart(2,'0');for(const p of points){const start=p.dataset.start,end=p.dataset.end;const visible=all.checked||(start!==''&&Number(start)<=t&&(end===''||Number(end)>t));p.style.display=visible?'':'none';p.style.opacity=!all.checked&&end===''?'.35':'1'}}clock.oninput=render;all.onchange=render;render();
const svg=document.getElementById('ward-map'),box=document.querySelector('.map-scroll');document.getElementById('zoom').oninput=e=>svg.style.width=(Number(e.target.value)*100)+'%';let drag=null,moved=false;box.onpointerdown=e=>{drag={x:e.clientX,y:e.clientY,l:box.scrollLeft,t:box.scrollTop};moved=false};window.addEventListener('pointermove',e=>{if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>5)moved=true;box.scrollLeft=drag.l-dx;box.scrollTop=drag.t-dy});window.addEventListener('pointerup',()=>drag=null);svg.onclick=e=>{if(moved)return;const p=e.target.closest('.ward');if(p){location.hash=p.dataset.row;document.getElementById('selection').textContent=p.querySelector('title').textContent}};
</script>''')
    return '\n'.join(parts)


def serve(store):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path=urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            try:
                if path=='/':body=PAGE.encode();content='text/html; charset=utf-8'
                elif path=='/api/catalog':body=json.dumps(public_catalog(store),ensure_ascii=False).encode();content='application/json; charset=utf-8'
                elif path=='/api/ward-summary':body=json.dumps(ward_summary(store),ensure_ascii=False).encode();content='application/json; charset=utf-8'
                elif path in ('/ward-summary','/汇总.html'):body=summary_page(store).encode();content='text/html; charset=utf-8'
                elif path=='/map.jpg':
                    body=(store.analysis/'map.jpg').read_bytes();content='image/jpeg'
                elif re.fullmatch(r'/match/[1-9][0-9]{0,19}',path):body=ward_page(store,path.rsplit('/',1)[1]).encode();content='text/html; charset=utf-8'
                elif re.fullmatch(r'/[1-9][0-9]{0,19}/眼位报告\.html',path):body=ward_page(store,path.split('/')[1]).encode();content='text/html; charset=utf-8'
                else:self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type',content);self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','public, max-age=86400' if path=='/map.jpg' else 'no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
            except (OSError,ValueError,KeyError):self.send_error(404)
        def log_message(self,*_args):pass
    host=store.config.get('bind_host','127.0.0.1')
    import ipaddress
    address=ipaddress.ip_address(host)
    if address.is_unspecified or not (address.is_private or address.is_loopback):raise ValueError('explicit_private_bind_required')
    ThreadingHTTPServer((host,int(store.config.get('port',8877))),Handler).serve_forever()


def run(store):
    """User-owned boot daemon; one process, daily bounded collection, read-only HTTP."""
    with store.locked('daemon'):
        for identifier,row in store.catalog()['matches'].items():
            if row.get('analysis_status')=='analyzing':
                store.update(identifier,analysis_status='partial_or_failed',reason='analysis_interrupted')
            if row.get('download_status')=='attempting':
                store.update(identifier,download_status='manual_required',reason='download_interrupted')
        def collector():
            time.sleep(30)
            while True:
                try:
                    if store.config.get('local_analysis'):
                        from pi_ward_worker import process_one
                        result=process_one(store)
                        if result.get('status')=='processed':continue
                    result=collect(store,1 if store.config.get('local_analysis') else int(store.config.get('pro_daily_limit',3)))
                except Exception as exc:
                    result={'error':type(exc).__name__}
                atomic(store.root/'collector_status.json',{'at_utc':utc(),'result':result})
                time.sleep(60 if store.config.get('local_analysis') and result.get('results') else 1800)
        threading.Thread(target=collector,daemon=True).start()
        while True:
            try:serve(store)
            except OSError:time.sleep(30)  # Address may not exist immediately at boot.


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--root',type=Path,required=True)
    commands=cli.add_subparsers(dest='command',required=True)
    commands.add_parser('catalog');commands.add_parser('serve');commands.add_parser('run')
    c=commands.add_parser('collect');c.add_argument('--count',type=int,default=3);c.add_argument('--prepared',type=Path)
    r=commands.add_parser('receive');r.add_argument('match_id');r.add_argument('--source',choices=('own','d2pt'),required=True);r.add_argument('--bytes',type=int,required=True);r.add_argument('--sha256',required=True)
    a=commands.add_parser('analysis');a.add_argument('match_id')
    a=commands.add_parser('export');a.add_argument('match_id')
    a=commands.add_parser('analysis-failed');a.add_argument('match_id')
    args=cli.parse_args();store=Store(args.root)
    if args.command=='catalog':
        result=store.catalog();current,limit=store.capacity()
        result['runtime']={'local_analysis':bool(store.config.get('local_analysis')),
            'postgame_context_enabled':store.config.get('postgame_context') is True,
            'replay_count':current,'replay_limit':limit,'downloads_stopped':current>=limit,
            'quota_scope':'d2pt_excluding_own'}
    elif args.command=='serve':serve(store);return
    elif args.command=='run':run(store);return
    elif args.command=='collect':result=collect(store,args.count,prepared=json.loads(args.prepared.read_text())if args.prepared else None)
    elif args.command=='receive':result=store.receive(args.match_id,args.source,args.bytes,args.sha256)
    elif args.command=='analysis':result=store.receive_analysis(args.match_id)
    elif args.command=='analysis-failed':
        store.update(args.match_id,analysis_status='partial_or_failed',reason='offline_parser_incomplete')
        result={'match_id':mid(args.match_id),'analysis_status':'partial_or_failed'}
    elif args.command=='export':
        identifier=mid(args.match_id)
        row=store.catalog()['matches'].get(identifier)
        if not row or row.get('download_status')!='verified':raise ValueError('replay_not_verified')
        with (store.replays/(identifier+'.dem')).open('rb')as stream:shutil.copyfileobj(stream,sys.stdout.buffer,1024**2)
        return
    else:raise ValueError('unknown_command')
    print(json.dumps(result,ensure_ascii=True),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'error':type(exc).__name__,'reason':str(exc)if isinstance(exc,ValueError)and re.fullmatch('[a-z_]{1,80}',str(exc))else None}),file=sys.stderr)
        raise SystemExit(1)
