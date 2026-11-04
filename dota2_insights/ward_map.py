"""Historical ward placements bound to a candidate map, not certified live vision."""
from dataclasses import asdict
import math
import re

from .virtual_map import MapIdentity, VirtualMap

PHASES = ('全部', '赛前', '0–10分钟', '10–20分钟', '20–30分钟', '30分钟后', '时间未知')


def _relation(layer, model):
    source = layer.get('source_map_identity')
    if source is None:
        return 'unknown'
    source = MapIdentity(**source)
    if source.map_name != model.identity.map_name or source.map_asset_sha256 != model.identity.map_asset_sha256:
        return 'mismatch'
    return 'exact_identity' if source == model.identity else 'same_map_bytes_other_context'


def bind(layer, model, revision=None):
    if not isinstance(model, VirtualMap):
        raise ValueError('ward_target_model_required')
    if layer.get('map_binding') is not None:
        status(layer, model)  # Never silently rebind a cached layer to a new map.
    revision_id = None
    if revision is not None:
        from .map_updates import validate
        validate(revision)
        if revision['identity'] != asdict(model.identity):
            raise ValueError('ward_target_revision_mismatch')
        revision_id = revision['revision_id']
    relation = _relation(layer, model)
    return {'schema_version': 1, 'target_identity': asdict(model.identity), 'target_model_id': model.model_id,
        'target_revision_id': revision_id, 'source_map_relation': relation,
        'preview_allowed': relation != 'mismatch', 'projection_verified': False,
        'origin_translation_verified': False, 'ward_legality_verified': False,
        'production_ready': False, 'meaning': 'historical_placements_not_live_vision_or_current_wards'}


def status(layer, model):
    binding = layer.get('map_binding')
    if binding is None:
        return {'source_map_relation': 'legacy_unbound', 'preview_allowed': True, 'production_ready': False}
    if (type(binding.get('schema_version')) is not int or binding['schema_version'] != 1
            or binding.get('target_identity') != asdict(model.identity) or binding.get('target_model_id') != model.model_id):
        raise ValueError('ward_layer_target_map_changed')
    if any(binding.get(k) is not False for k in ('projection_verified', 'origin_translation_verified',
            'ward_legality_verified', 'production_ready')):
        raise ValueError('ward_preview_cannot_claim_certification')
    identifier = binding.get('target_revision_id')
    if identifier is not None and (not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{64}', identifier)):
        raise ValueError('ward_revision_id_invalid')
    relation = _relation(layer, model)
    if binding.get('source_map_relation') != relation or binding.get('preview_allowed') is not (relation != 'mismatch'):
        raise ValueError('ward_source_map_relation_changed')
    return binding


def phase_of(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        return '时间未知'
    if value < 0:
        return '赛前'
    if value < 600:
        return '0–10分钟'
    if value < 1200:
        return '10–20分钟'
    if value < 1800:
        return '20–30分钟'
    return '30分钟后'


def visible_points(layer, model, phase='全部', *, allow_unverified=False):
    if phase not in PHASES:
        raise ValueError('ward_phase_invalid')
    context = status(layer, model)
    if not allow_unverified or not context['preview_allowed']:
        return []
    return [p for p in layer['points'] if phase == '全部' or phase_of(p.get('placed_game_time_s')) == phase]


def check_installation(layer, model, snapshot):
    from .map_updates import validate
    validate(snapshot)
    context = status(layer, model)
    same = asdict(model.identity) == snapshot['identity']
    return {'source_map_relation': context['source_map_relation'], 'target_matches_installation': same,
        'historical_preview_allowed': context['preview_allowed'],
        'reuse_for_current_game_allowed': False, 'requires_new_binding': not same,
        'projection_verified': False, 'production_ready': False}
