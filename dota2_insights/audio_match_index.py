"""Bounded source/release discovery of closed audio scopes, no asset copying."""
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re

from .audio_match_handoff import verify
from .match_journal_index import history_bases, index_destination, write_index, read_index as _read_index


def resolve_reference(reference, roots):
    key, value = reference.get('root_key'), reference.get('source')
    if (key not in ('helper_history','helper_releases') or key not in roots
            or not isinstance(value,str) or len(value)>1000):
        raise ValueError('audio_match_index_reference_invalid')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or '\\' in value or path.as_posix()!=value:
        raise ValueError('audio_match_index_reference_invalid')
    parts = path.parts
    if key=='helper_releases':
        if (len(parts)!=7 or not parts[0].startswith(('foresight-','helper-','economy-'))
                or parts[1] not in ('Foresight','ForesightEconomy','Dota2Helper','Dota2Economy') or parts[2]!='diagnostics'):
            raise ValueError('audio_match_index_release_layout_invalid')
        parts = parts[3:]
    if (len(parts)!=4 or parts[0]!='match-audio' or parts[2]!='closed-matches'
            or not re.fullmatch(r'run_[a-f0-9]{32}',parts[1])
            or not re.fullmatch(r'scope_[a-f0-9]{64}',parts[3])):
        raise ValueError('audio_match_index_layout_invalid')
    root = Path(roots[key]).resolve(strict=True)
    target = root/Path(*path.parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError('audio_match_index_outside_root')
    for parent in (target,*target.parents):
        if parent.is_relative_to(root) and (parent.is_symlink() or parent.is_junction()):
            raise ValueError('audio_match_index_link_refused')
    return target


def discover(roots, *, latest=20):
    if type(latest) is not int or not 1<=latest<=128:
        raise ValueError('audio_match_index_scan_limit_invalid')
    candidates, seen = [],set()
    for key,root,base in history_bases(roots):
        for manifest in base.glob('match-audio/run_*/closed-matches/scope_*/manifest.json'):
            if len(candidates)>=10000:
                raise ValueError('audio_match_index_discovery_limit')
            lexical=str(manifest).casefold()
            if lexical not in seen:
                seen.add(lexical)
                candidates.append((manifest.stat().st_mtime_ns,{'root_key':key,
                    'source':manifest.parent.relative_to(root).as_posix()}))
    return [ref for _,ref in sorted(candidates,key=lambda r:(-r[0],r[1]['source']))[:latest]]


def sync(project, roots, *, latest=20):
    with index_destination(project,roots,'audio-match-handoff') as output:
        entries,scopes,duplicates,conflicts=[],{},0,0
        for ref in discover(roots,latest=latest):
            try:
                folder=resolve_reference(ref,roots)
                package=verify(folder)
                doc=package['manifest']
                identity=tuple(doc[k] for k in ('audio_capture_run_id','match_id','game_session_id','record_session_id'))
                entry={**ref,'state':'ready_with_gaps','package_id':package['package_id'],
                    **{k:doc[k] for k in ('audio_capture_run_id','match_id','game_session_id','record_session_id',
                                         'closed_at_utc','recording_integrity')},
                    'segments':len(package['segments']),'reference_rows':len(package['references']),
                    'other_channels':'not_closed_by_this_manifest'}
                if identity not in scopes:
                    scopes[identity]=len(entries);entries.append(entry)
                else:
                    position=scopes[identity];old=entries[position]
                    if old['state']=='blocked':
                        continue
                    if old['package_id']==entry['package_id']:
                        duplicates+=1
                    else:
                        conflicts+=1
                        entries[position]={**ref,'state':'blocked','audio_capture_run_id':doc['audio_capture_run_id'],
                            'error_type':'conflicting_audio_match_receipts'}
            except (OSError,ValueError,TypeError,KeyError) as exc:
                entries.append({**ref,'state':'blocked','error_type':type(exc).__name__})
        index={'schema_version':1,'kind':'audio_match_handoff_index',
            'indexed_at_utc':datetime.now(timezone.utc).isoformat(),'entries':entries,
            'duplicate_packages_omitted':duplicates,'conflicting_scope_packages':conflicts,
            'index_time_is_not_data_time':True}
        write_index(output,index)
        return index


def read_index(path):
    return _read_index(path,kind='audio_match_handoff_index')
