"""Match every retained color candidate, preserving all rejection/ambiguity state."""
from .minimap_component_probe import probe
from .minimap_template_match import match_patch


def evaluate_frame(image_bgr, bank):
    observation = probe(image_bgr)
    results = []
    for index, candidate in enumerate(observation['candidates']):
        x, y, width, height = candidate['box']
        if not 4 <= min(width, height) <= max(width, height) <= 128:
            result = {'state': 'unavailable', 'reason': 'native_candidate_outside_patch_limit',
                'resource_family_candidate': None, 'ranked_families': [], 'hero_id': None,
                'semantic_identity_verified': False, 'online_changes_applied': False}
        else:
            result = match_patch(image_bgr[y:y + height, x:x + width], bank)
        results.append({'candidate_index': index, 'candidate': candidate, 'result': result})
    return {'candidates': results, 'candidate_count_before_limit': observation['candidate_count_before_limit'],
        'candidate_limit_reached': observation['candidate_limit_reached'],
        'scope': 'all_retained_color_candidates_not_just_holes', 'hero_count': None,
        'identity_verified': False, 'online_changes_applied': False}
