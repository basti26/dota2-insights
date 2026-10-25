"""Bounded, read-only Source 2 metadata probe; no entity or match replay."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import hashlib
import importlib.metadata
from pathlib import Path
import re
import struct
import time

SCHEMA='replay_bounded_metadata_probe_v1'
MAX_SOURCE=512*1024**2
PREFIX_BYTES=16*1024**2
FRAME_BYTES=8*1024**2
EXPANDED_BYTES=16*1024**2
FOOTER_BYTES=1024**2
MAX_FRAMES=128
MAX_INNER=20_000
SECONDS=20
GEM_VERSION='0.13.0'
BUILD_COMPONENT=re.compile(r'/dota_v([0-9]+)/')


class ProbeError(ValueError):
    pass


def varuint(stream):
    value=0
    for shift in range(0,35,7):
        raw=stream.read(1)
        if not raw:raise ProbeError('truncated_varuint32')
        value|=(raw[0]&127)<<shift
        if not raw[0]&128:
            if value>0xffffffff:raise ProbeError('overflow_varuint32')
            return value
    raise ProbeError('overflow_varuint32')


def payload(data,compressed,limit):
    if compressed:
        declared=varuint(BytesIO(data))
        if declared>limit:raise ProbeError('expanded_payload_budget')
        import snappy
        data=snappy.decompress(data)
        if len(data)!=declared:raise ProbeError('expanded_payload_size_mismatch')
    if len(data)>limit:raise ProbeError('payload_budget')
    return data


def inspect_source(source,expected_match_id,expected_sha256):
    if not re.fullmatch(r'[0-9]{1,12}',str(expected_match_id)) or not re.fullmatch('[a-f0-9]{64}',expected_sha256):
        raise ValueError('metadata_probe_expected_identity_required')
    start=time.monotonic()
    result={'schema_version':SCHEMA,'match_id':str(expected_match_id),'expected_source_sha256':expected_sha256,
        'observed_at_utc':datetime.now(timezone.utc).isoformat(),'status':'not_read','game_build':None,'game_mode':None,
        'source_sha256':None,'source_sha256_after_read':None,'source_stat_unchanged':False,
        'footer':None,'prefix':None,
        'limits':{'source_bytes':MAX_SOURCE,'prefix_bytes':PREFIX_BYTES,'encoded_frame_bytes':FRAME_BYTES,
                  'expanded_frame_bytes':EXPANDED_BYTES,'footer_bytes':FOOTER_BYTES,'outer_frames':MAX_FRAMES,
                  'inner_messages':MAX_INNER,'wall_seconds':SECONDS},
        'scope':'bounded_prefix_and_indexed_footer_only_full_source_hash_not_entity_parse_or_replay_integrity_proof',
        'raw_names_paths_accounts_chat_or_server_strings_exported':False}
    def deadline():
        if time.monotonic()-start>SECONDS:raise ProbeError('probe_wall_budget')
    def file_sha(f):
        f.seek(0);digest=hashlib.sha256();count=0
        for block in iter(lambda:f.read(1024**2),b''):
            count+=len(block);deadline()
            if count>MAX_SOURCE:raise ProbeError('source_byte_budget')
            digest.update(block)
        return digest.hexdigest()
    try:
        if importlib.metadata.version('gem-dota')!=GEM_VERSION:raise ProbeError('unexpected_gem_version')
        from gem.binary.reader import BitReader
        from gem.proto.demo_pb2 import CDemoFileInfo,CDemoPacket,CDemoFullPacket
        from gem.proto.netmessages_pb2 import CSVCMsg_ServerInfo,svc_ServerInfo
        result['gem_version']=GEM_VERSION
        with Path(source).open('rb') as f:
            stat=Path(source).stat()
            if not 16<=stat.st_size<=MAX_SOURCE:raise ProbeError('source_size_budget')
            result['source_bytes']=stat.st_size
            result['source_sha256']=file_sha(f)
            if result['source_sha256']!=expected_sha256:
                result['status']='rejected_source_sha256_mismatch'
                return result
            f.seek(0);header=f.read(16)
            if header[:8]!=b'PBDEMS2\0':raise ProbeError('source2_magic_required')
            offset=struct.unpack_from('<I',header,8)[0]
            if not 16<=offset<stat.st_size:raise ProbeError('indexed_footer_missing')
            f.seek(offset);command=varuint(f);tick=varuint(f);size=varuint(f)
            if command&~64!=2 or size>FOOTER_BYTES or f.tell()+size>stat.st_size:raise ProbeError('indexed_footer_envelope')
            data=f.read(size);data=payload(data,bool(command&64),FOOTER_BYTES)
            info=CDemoFileInfo();info.ParseFromString(data)
            dota=info.game_info.dota
            if str(dota.match_id)!=str(expected_match_id):raise ProbeError('indexed_footer_match_id_mismatch')
            mode=int(dota.game_mode) if dota.HasField('game_mode') and dota.game_mode>0 else None
            result['footer']={'offset':offset,'tick_raw':tick,'encoded_bytes':size,'decoded_payload_sha256':hashlib.sha256(data).hexdigest(),
                              'match_id':str(dota.match_id),'game_mode':mode}
            f.seek(16);examined=0;decoded=0;inner_count=0;server=[];builds=set();reason='outer_frame_limit'
            while examined<MAX_FRAMES:
                deadline()
                position=f.tell()
                if position>=min(stat.st_size,PREFIX_BYTES):reason='prefix_byte_limit';break
                cmd=varuint(f);outer_tick=varuint(f);size=varuint(f);kind=cmd&~64
                if f.tell()+size>stat.st_size:raise ProbeError('truncated_prefix_frame')
                if f.tell()+size>PREFIX_BYTES:reason='prefix_byte_limit';break
                examined+=1
                if kind not in (7,8,13):
                    f.seek(size,1)
                    continue
                if size>FRAME_BYTES:reason='encoded_frame_limit';break
                data=payload(f.read(size),bool(cmd&64),EXPANDED_BYTES)
                if kind==13:
                    frame=CDemoFullPacket();frame.ParseFromString(data)
                    packet_data=frame.packet.data if frame.HasField('packet') else b''
                else:
                    frame=CDemoPacket();frame.ParseFromString(data);packet_data=frame.data
                decoded+=1
                reader=BitReader(packet_data)
                while reader.rem_bits()>=8:
                    deadline();inner_count+=1
                    if inner_count>MAX_INNER:raise ProbeError('inner_message_budget')
                    type_id=reader.read_ubit_var();n=reader.read_varuint32()
                    if n>FRAME_BYTES or n*8>reader.rem_bits():raise ProbeError('inner_payload_budget_or_truncation')
                    message=reader.read_bytes(n)
                    if type_id!=svc_ServerInfo:continue
                    if len(server)>=8:raise ProbeError('server_info_record_budget')
                    entry=CSVCMsg_ServerInfo();entry.ParseFromString(message)
                    component=BUILD_COMPONENT.search(entry.game_dir)
                    build=int(component.group(1)) if component and int(component.group(1))>0 else None
                    server.append({'outer_offset':position,'outer_tick_raw':outer_tick,'inner_type_id':type_id,
                        'payload_sha256':hashlib.sha256(message).hexdigest(),'game_build':build,
                        'build_component':'dota_v'+str(build) if build is not None else None})
                    if build is not None:builds.add(build)
                if len(builds)>1:raise ProbeError('contradictory_prefix_builds')
                if builds:reason='first_build_packet_completed';break
            result['prefix']={'outer_frames_examined':examined,'packet_envelopes_decoded':decoded,'inner_messages_examined':inner_count,
                'prefix_end_offset':f.tell(),'stop_reason':reason,'server_info_records':server}
            result['source_sha256_after_read']=file_sha(f)
            after=Path(source).stat()
            result['source_stat_unchanged']=(stat.st_size,stat.st_mtime_ns)==(after.st_size,after.st_mtime_ns)
            if not result['source_stat_unchanged'] or result['source_sha256_after_read']!=expected_sha256:
                raise ProbeError('source_changed_during_probe')
            if not builds and mode is None:
                raise ProbeError('no_reported_metadata_fields_within_limits')
            result['game_build']=next(iter(builds)) if builds else None
            result['game_mode']=mode
            result['status']='verified_bounded_metadata' if builds and mode is not None else 'verified_partial_metadata'
    except Exception as exc:
        result['status']='rejected_metadata_probe'
        result['error_code']=str(exc) if isinstance(exc,ProbeError) else type(exc).__name__
        result['game_build']=None;result['game_mode']=None
    finally:
        result['elapsed_s']=round(time.monotonic()-start,6)
    return result
