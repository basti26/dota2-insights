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
