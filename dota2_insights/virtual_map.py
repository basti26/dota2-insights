"""Version-bound, two-dimensional world geometry. All v1 models are candidates."""
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re


FRAMES = frozenset({'source2_map_centered', 'gem_cell128_plus_vec_raw', 'gsi_hero_world'})


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('finite_coordinate_required')
    return float(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class MapIdentity:
    map_name: str
    client_build: int
    map_asset_sha256: str
    patch: str | None = None

    def __post_init__(self):
        if (not isinstance(self.map_name, str) or not re.fullmatch(r'[a-z0-9_]{1,64}', self.map_name)
                or type(self.client_build) is not int or self.client_build <= 0
                or not isinstance(self.map_asset_sha256, str)
                or not re.fullmatch(r'[a-f0-9]{64}', self.map_asset_sha256)
                or (self.patch is not None and (not isinstance(self.patch, str) or not 1 <= len(self.patch) <= 32))):
            raise ValueError('map_identity_invalid')


@dataclass(frozen=True)
class WorldBounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self):
        for name in ('min_x', 'min_y', 'max_x', 'max_y'):
            object.__setattr__(self, name, number(getattr(self, name)))
        if self.min_x >= self.max_x or self.min_y >= self.max_y:
            raise ValueError('world_bounds_degenerate')
        if not math.isfinite(self.max_x - self.min_x) or not math.isfinite(self.max_y - self.min_y):
            raise ValueError('world_bounds_nonfinite_span')


@dataclass(frozen=True)
class WorldPoint:
    x: float
    y: float
    identity: MapIdentity
    frame: str
    z: float | None = None

    def __post_init__(self):
        object.__setattr__(self, 'x', number(self.x))
        object.__setattr__(self, 'y', number(self.y))
        if self.z is not None:
            object.__setattr__(self, 'z', number(self.z))
        if not isinstance(self.identity, MapIdentity) or self.frame not in FRAMES:
            raise ValueError('world_point_context_required')


@dataclass(frozen=True)
class NormalizedPoint:
    u: float
    v: float
    model_id: str

    def __post_init__(self):
        object.__setattr__(self, 'u', number(self.u))
        object.__setattr__(self, 'v', number(self.v))
        if not isinstance(self.model_id, str) or not re.fullmatch(r'[a-f0-9]{64}', self.model_id):
            raise ValueError('normalized_point_model_required')


@dataclass(frozen=True)
class TranslationCandidate:
    identity: MapIdentity
    source_frame: str
    target_frame: str
    dx: float
    dy: float
    provenance: str

    def __post_init__(self):
        if (not isinstance(self.identity, MapIdentity) or self.source_frame not in FRAMES
                or self.target_frame not in FRAMES or self.source_frame == self.target_frame
                or not isinstance(self.provenance, str) or not 1 <= len(self.provenance) <= 2048):
            raise ValueError('translation_provenance_required')
        object.__setattr__(self, 'dx', number(self.dx))
        object.__setattr__(self, 'dy', number(self.dy))

    def apply(self, point, *, allow_unverified=False):
        if not allow_unverified:
            raise ValueError('translation_not_independently_validated')
        if point.identity != self.identity or point.frame != self.source_frame:
            raise ValueError('translation_coordinate_context_mismatch')
        return WorldPoint(point.x + self.dx, point.y + self.dy, self.identity, self.target_frame, point.z)


@dataclass(frozen=True)
class VirtualMap:
    identity: MapIdentity
    bounds: WorldBounds
    coordinate_frame: str
    bounds_source_kind: str
    bounds_provenance: str
    validation_status: str = field(default='unverified', init=False)

    def __post_init__(self):
        if (not isinstance(self.identity, MapIdentity) or not isinstance(self.bounds, WorldBounds)
                or self.coordinate_frame not in FRAMES
                or self.bounds_source_kind not in {'reference', 'asset_extracted', 'independent_fit'}
                or not isinstance(self.bounds_provenance, str) or not 1 <= len(self.bounds_provenance) <= 2048):
            raise ValueError('virtual_map_context_invalid')

    def to_dict(self):
        return {'schema_version': 1, 'kind': 'virtual_world_map_candidate',
            'identity': asdict(self.identity), 'world_bounds': asdict(self.bounds),
            'coordinate_frame': self.coordinate_frame, 'normalized_y_direction': 'down',
            'bounds_source_kind': self.bounds_source_kind, 'bounds_provenance': self.bounds_provenance,
            'validation_status': self.validation_status}

    @property
    def model_id(self):
        return digest(self.to_dict())

    def require_compatible(self, identity):
        if identity != self.identity:
            raise ValueError('map_build_asset_or_variant_mismatch')

    def _gate(self, allow_unverified):
        if not allow_unverified:
            raise ValueError('virtual_map_requires_independent_landmark_validation')

    def world_to_norm(self, point, *, allow_unverified=False, allow_outside=False):
        self._gate(allow_unverified)
        self.require_compatible(point.identity)
        if point.frame != self.coordinate_frame:
            raise ValueError('world_coordinate_frame_mismatch')
        b = self.bounds
        u = (point.x - b.min_x) / (b.max_x - b.min_x)
        v = (b.max_y - point.y) / (b.max_y - b.min_y)
        if not allow_outside and not (0 <= u <= 1 and 0 <= v <= 1):
            raise ValueError('point_outside_declared_world_window')
        return NormalizedPoint(u, v, self.model_id)

    def norm_to_world(self, point, *, allow_unverified=False, allow_outside=False):
        self._gate(allow_unverified)
        if point.model_id != self.model_id:
            raise ValueError('normalized_coordinate_model_mismatch')
        if not allow_outside and not (0 <= point.u <= 1 and 0 <= point.v <= 1):
            raise ValueError('point_outside_normalized_window')
        b = self.bounds
        # Inverse recovers two dimensions only. Original marker Z stays in its
        # own WorldPoint; no ground elevation is reconstructed here.
        return WorldPoint(b.min_x + point.u * (b.max_x - b.min_x),
            b.max_y - point.v * (b.max_y - b.min_y), self.identity, self.coordinate_frame)

    @classmethod
    def from_dict(cls, value):
        fields = {'schema_version', 'kind', 'identity', 'world_bounds', 'coordinate_frame',
            'normalized_y_direction', 'bounds_source_kind', 'bounds_provenance', 'validation_status'}
        if (not isinstance(value, dict) or set(value) != fields or type(value.get('schema_version')) is not int
                or value.get('schema_version') != 1
                or value.get('kind') != 'virtual_world_map_candidate'
                or value.get('normalized_y_direction') != 'down'
                or value.get('validation_status') != 'unverified'):
            raise ValueError('unsupported_or_self_certified_map_model')
        return cls(MapIdentity(**value['identity']), WorldBounds(**value['world_bounds']),
            value['coordinate_frame'], value['bounds_source_kind'], value['bounds_provenance'])
