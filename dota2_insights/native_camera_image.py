"""Bind explicit offline calibration annotations to unchanged image bytes.

An image digest proves which pixels were supplied, never their landmark labels
or correspondence to the non-atomic camera query.
"""
from contextlib import contextmanager
import hashlib
from io import BytesIO
from pathlib import Path, PurePosixPath
import re


MAX_IMAGE_BYTES = 16 * 1024 * 1024


def _read(path):
    with path.open('rb') as stream:
        raw=stream.read(MAX_IMAGE_BYTES+1)
    if not raw or len(raw)>MAX_IMAGE_BYTES:
        raise ValueError('calibration_image_size_limit')
    return raw


def _check_path(parent, path):
    if not path.resolve().is_relative_to(parent) or not path.is_file():
        raise ValueError('calibration_image_outside_input')
    for candidate in (path,*path.parents):
        if candidate==parent:break
        if candidate.is_symlink() or candidate.is_junction():
            raise ValueError('calibration_image_link_refused')


@contextmanager
def open_image_reference(parent, reference, viewport, *, required=False):
    if reference is None:
        if required:raise ValueError('calibration_image_reference_required')
        yield {'source_image_binding_verified':False}
        return
    if not isinstance(reference,dict) or set(reference)!={'file','sha256'}:
        raise ValueError('calibration_image_reference_invalid')
    name,digest=reference['file'],reference['sha256']
    if (not isinstance(name,str) or not 1<=len(name)<=200 or '\\' in name or ':' in name
            or not isinstance(digest,str) or re.fullmatch('[a-f0-9]{64}',digest) is None):
        raise ValueError('calibration_image_reference_invalid')
    relative=PurePosixPath(name)
    if (relative.is_absolute() or '..' in relative.parts or relative.as_posix()!=name
            or relative.suffix.lower() not in {'.png','.jpg','.jpeg'}):
        raise ValueError('calibration_image_reference_invalid')
    parent=Path(parent).resolve(strict=True);path=parent.joinpath(*relative.parts)
    _check_path(parent,path)
    raw=_read(path)
    if hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('calibration_image_sha_mismatch')
    from PIL import Image
    with Image.open(BytesIO(raw)) as image:
        if (image.format not in {'PNG','JPEG'} or not isinstance(viewport,(list,tuple))
                or len(viewport)!=2 or any(type(v) is not int for v in viewport)
                or not 640<=viewport[0]<=4096 or not 480<=viewport[1]<=2160
                or image.size!=tuple(viewport)):
            raise ValueError('calibration_image_viewport_mismatch')
        image.load()
    try:
        yield {'source_image_binding_verified':True,'source_image_sha256':digest,
               'source_image_bytes':len(raw),'native_image_size':list(viewport),
               'pixel_annotation_truth_verified':False}
    finally:
        _check_path(parent,path)
        if _read(path)!=raw:
            raise ValueError('calibration_image_changed')
