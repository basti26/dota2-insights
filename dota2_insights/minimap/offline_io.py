"""Bounded offline provenance helpers; never read a replay or sealed answers."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import uuid


def checked_match(match_id):
    if type(match_id) is int:
        match_id = str(match_id)
    if not isinstance(match_id, str) or not re.fullmatch(r'[0-9]{1,20}', match_id):
        raise ValueError('movement_match_id_invalid')
    # Reuse the recorder's public role/hash lock; no extra answer source.
    from dota2_insights.replay_holdout import refuse_locked_match
    refuse_locked_match(match_id)
    return match_id


def check_input_paths(*paths):
    # Known match names are refused before opening even candidate metadata.
    for path in paths:
        for part in Path(path).resolve().parts:
            for match in re.findall(r'(?<![0-9])[0-9]{1,20}(?![0-9])', part):
                checked_match(match)


def check_match_metadata(value):
    """Preflight known identity/path declarations before opening linked bodies."""
    pending, nodes = [(value, 0)], 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > 1000000 or depth > 64:
            raise ValueError('offline_match_metadata_limit')
        if isinstance(current, dict):
            for key, item in current.items():
                if key in ('match_id','parsed_match_id','effective_match_id') and item is not None:
                    checked_match(item)
                elif key in ('source_file','source_path','replay_file') and isinstance(item,str):
                    check_input_paths(item)
                elif key in ('match_ids','matches','heldout_matches') and isinstance(item,list):
                    for match in item:
                        if type(match) is int or isinstance(match,str) and match.isascii() and match.isdigit():
                            checked_match(match)
                if isinstance(item,(dict,list)):
                    pending.append((item,depth+1))
        elif isinstance(current,list):
            pending.extend((item,depth+1) for item in current if isinstance(item,(dict,list)))


@contextmanager
def new_binary_output(destination):
    """Publish a complete owned file atomically, without replacing any file."""
    destination = Path(destination)
    temporary = destination.with_name('.' + destination.name + '.' + uuid.uuid4().hex + '.part')
    owned = False
    try:
        with temporary.open('xb') as stream:
            owned = True
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
    finally:
        if owned:
            temporary.unlink(missing_ok=True)
