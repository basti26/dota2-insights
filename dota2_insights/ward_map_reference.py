"""Pinned illustrative map registration shared by descriptive exports."""
import hashlib
import io
from pathlib import Path

MAP_SHA256 = '9fb56fc131acc161fb904f6c7189c6f37c57705bac752ccf9a47e01b1b079764'


def load(path):
    from PIL import Image
    with Path(path).open('rb') as stream:
        raw = stream.read(12*1024**2+1)
    if hashlib.sha256(raw).hexdigest() != MAP_SHA256:
        raise ValueError('pinned_illustrative_map_required')
    image = Image.open(io.BytesIO(raw))
    if image.size != (8878, 8356):
        raise ValueError('pinned_map_dimensions_changed')
    return raw, image.convert('RGB')


def project(x, y, width=1600):
    # Legacy registration, not a certified mapping for the current match/map.
    return (((8878-8356)/2+(x-7487)/18322*8356)/8878*width,
            (1-(y-7693)/18322)*8356/8878*width)
