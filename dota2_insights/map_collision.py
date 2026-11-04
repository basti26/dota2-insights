"""Strict subset reader for a static Source2Viewer 20 PHYS text export.

Decoded positions stay in physics-model local coordinates. Height intersections
are collision candidates, never certified walkable ground or ward legality.
"""
from dataclasses import dataclass
import math
import re

import numpy as np

MAX_TEXT_BYTES = 64 * 1024**2


@dataclass(frozen=True)
class CollisionMesh:
    ordinal: int
    attribute_index: int
    surface_index: int
    vertices: np.ndarray
    triangles: np.ndarray
    declared_min: tuple
    declared_max: tuple


def _finish_mesh(value, ordinal):
    if set(value) != {'attribute', 'surface', 'min', 'max', 'vertices', 'triangles'}:
        raise ValueError('collision_mesh_fields_missing_or_unsupported')
    vertices, triangles = value['vertices'], value['triangles']
    if not vertices or not triangles or len(vertices) % 12 or len(triangles) % 12:
        raise ValueError('collision_buffer_layout_invalid')
    v = np.frombuffer(vertices, dtype='<f4').reshape(-1, 3).copy()
    t = np.frombuffer(triangles, dtype='<u4').reshape(-1, 3).copy()
    if len(v) > 1000000 or len(t) > 2000000 or not np.isfinite(v).all() or np.max(np.abs(v)) > 65536:
        raise ValueError('collision_vertex_budget_or_coordinates')
    if t.max() >= len(v):
        raise ValueError('collision_triangle_index_outside_vertices')
    lower, upper = np.asarray(value['min']), np.asarray(value['max'])
    if (lower.shape != (3,) or upper.shape != (3,) or not np.isfinite(lower).all()
            or not np.isfinite(upper).all() or np.any(lower >= upper)
            or np.any(v.min(axis=0) < lower - .1) or np.any(v.max(axis=0) > upper + .1)):
        raise ValueError('collision_vertices_disagree_with_declared_bounds')
    v.setflags(write=False); t.setflags(write=False)
    return CollisionMesh(ordinal, value['attribute'], value['surface'], v, t, tuple(lower), tuple(upper))


def extract(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_TEXT_BYTES:
        raise ValueError('collision_text_budget')
    if ('--- Data for block "PHYS" ---' not in text or '<!-- kv3 encoding:text:' not in text
            or not re.search(r'^\tm_bindPose = \[\s*\]$', text, re.M)
            or not re.search(r'^\tm_boneNames = \[\s*\]$', text, re.M)):
        raise ValueError('unsupported_or_rigged_physics_export')
    # Only the explicitly observed single static part with no other shape
    # types is supported. Do not silently discard transforms/convex hulls.
    if len(re.findall(r'^\t{3}m_rnShape =\s*$', text, re.M)) != 1:
        raise ValueError('collision_static_single_part_required')
    for name in ('m_spheres', 'm_capsules', 'm_hulls', 'm_compounds'):
        matches = re.findall(r'^\t{4}' + name + r' = (.*)$', text, re.M)
        if len(matches) != 1 or not re.fullmatch(r'\[\s*\]', matches[0]):
            raise ValueError('collision_nonmesh_shapes_not_supported')
    meshes, current, buffer, expecting = [], None, None, None
    attribute, surface = None, None
    for line in text.splitlines():
        if current is None:
            match = re.fullmatch(r'\t{6}m_n(CollisionAttribute|SurfaceProperty)Index = ([0-9]+)', line)
            if match:
                if match[1] == 'CollisionAttribute': attribute = int(match[2])
                else: surface = int(match[2])
            elif line == '\t' * 6 + 'm_Mesh = ' or line == '\t' * 6 + 'm_Mesh =':
                if attribute is None or surface is None or len(meshes) >= 64:
                    raise ValueError('collision_mesh_context_missing')
                current = {'attribute': attribute, 'surface': surface}
                attribute, surface = None, None
            continue
        if expecting:
            if line != '\t' * 7 + '#[':
                raise ValueError('collision_binary_buffer_expected')
            buffer, expecting = expecting, None
            current[buffer] = bytearray()
            continue
        if buffer:
            if line == '\t' * 7 + ']':
                current[buffer] = bytes(current[buffer]); buffer = None
            else:
                if not re.fullmatch(r'\t{8}(?:[0-9A-F]{2})(?: [0-9A-F]{2})*', line):
                    raise ValueError('collision_hex_buffer_invalid')
                current[buffer].extend(bytes.fromhex(line.strip()))
                if len(current[buffer]) > 24 * 1024**2:
                    raise ValueError('collision_binary_buffer_budget')
            continue
        if line == '\t' * 6 + '}':
            meshes.append(_finish_mesh(current, len(meshes))); current = None
            continue
        bounds = re.fullmatch(r'\t{7}m_v(Min|Max) = \[ ([^\]]+) \]', line)
        array = re.fullmatch(r'\t{7}m_(Vertices|Triangles) =\s*', line)
        if bounds:
            key = bounds[1].lower()
            if key in current: raise ValueError('collision_duplicate_bounds')
            try: current[key] = tuple(float(n.strip()) for n in bounds[2].split(','))
            except ValueError as exc: raise ValueError('collision_bounds_invalid') from exc
        elif array:
            key = array[1].lower()
            if key in current: raise ValueError('collision_duplicate_buffer')
            expecting = key
    if current is not None or buffer or expecting or not meshes:
        raise ValueError('collision_mesh_export_incomplete')
    if len(meshes) != len(re.findall(r'^\t{6}m_Mesh =\s*$', text, re.M)):
        raise ValueError('collision_unparsed_meshes')
    attribute_start = re.findall(r'^\tm_collisionAttributes =\s*$', text, re.M)
    if len(attribute_start) != 1:
        raise ValueError('collision_attribute_table_missing')
    tail = text[text.index('\tm_collisionAttributes ='):]
    attributes = []
    for block in re.findall(r'^\t{2}\{\r?\n(.*?)^\t{2}\}', tail, re.M | re.S):
        group = re.findall(r'^\t{3}m_CollisionGroupString = "([^"\r\n]+)"$', block, re.M)
        if len(group) != 1: raise ValueError('collision_attribute_group_invalid')
        row = {'collision_group': group[0]}
        for field, label in [('m_InteractAsStrings', 'interact_as'), ('m_InteractWithStrings', 'interact_with'),
                             ('m_InteractExcludeStrings', 'interact_exclude')]:
            value = re.search(r'^\t{3}' + field + r' =\s*\[([^\]]*)\]', block, re.M)
            if value is None: raise ValueError('collision_attribute_strings_missing')
            names = re.findall(r'"([^"\r\n]+)"', value[1])
            if re.sub(r'"[^"\r\n]+"|[\s,]', '', value[1]):
                raise ValueError('collision_attribute_strings_invalid')
            row[label] = names
        attributes.append(row)
    if not attributes or any(m.attribute_index >= len(attributes) for m in meshes):
        raise ValueError('collision_attribute_reference_invalid')
    return meshes, attributes


def summarize(meshes, attributes):
    rows = []
    for mesh in meshes:
        v, t = mesh.vertices, mesh.triangles
        cross = np.cross(v[t[:, 1]].astype(float) - v[t[:, 0]], v[t[:, 2]].astype(float) - v[t[:, 0]])
        area2 = np.linalg.norm(cross, axis=1)
        rows.append({'mesh': mesh.ordinal, 'attribute_index': mesh.attribute_index,
            'collision_attribute': attributes[mesh.attribute_index], 'surface_property_index': mesh.surface_index,
            'vertices': len(v), 'triangles': len(t), 'observed_min': v.min(axis=0).tolist(),
            'observed_max': v.max(axis=0).tolist(), 'declared_min': list(mesh.declared_min),
            'declared_max': list(mesh.declared_max), 'degenerate_triangles': int((area2 < 1e-8).sum()),
            'vertical_or_xy_degenerate_triangles': int((np.abs(cross[:, 2]) < 1e-8).sum())})
    return {'schema_version': 1, 'kind': 'decoded_static_collision_mesh_candidates',
        'coordinate_basis': 'physics_model_local_xyz', 'meshes': rows,
        'vertices': sum(r['vertices'] for r in rows), 'triangles': sum(r['triangles'] for r in rows),
        'world_placement_verified': False, 'walkable_ground_verified': False, 'ward_legality_verified': False}


def vertical_hits(meshes, x, y, *, attribute_indices=None, maximum_results=64):
    """All distinct static collision heights at XY; do not choose a ground layer."""
    if (type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x)
            or not math.isfinite(y) or type(maximum_results) is not int or not 1 <= maximum_results <= 256):
        raise ValueError('collision_query_invalid')
    results = []
    for mesh in meshes:
        if attribute_indices is not None and mesh.attribute_index not in attribute_indices:
            continue
        vertices = mesh.vertices[mesh.triangles].astype(np.float64)
        keep = ((vertices[:, :, 0].min(axis=1) <= x + 1e-6) & (vertices[:, :, 0].max(axis=1) >= x - 1e-6)
            & (vertices[:, :, 1].min(axis=1) <= y + 1e-6) & (vertices[:, :, 1].max(axis=1) >= y - 1e-6))
        indices = np.flatnonzero(keep); triangle = vertices[keep]
        if not len(triangle): continue
        p, q, r = triangle[:, 0], triangle[:, 1], triangle[:, 2]
        denominator = (q[:, 1]-r[:, 1])*(p[:, 0]-r[:, 0])+(r[:, 0]-q[:, 0])*(p[:, 1]-r[:, 1])
        nonvertical = np.abs(denominator) > 1e-8
        p,q,r,indices,denominator = p[nonvertical],q[nonvertical],r[nonvertical],indices[nonvertical],denominator[nonvertical]
        a = ((q[:, 1]-r[:, 1])*(x-r[:, 0])+(r[:, 0]-q[:, 0])*(y-r[:, 1]))/denominator
        b = ((r[:, 1]-p[:, 1])*(x-r[:, 0])+(p[:, 0]-r[:, 0])*(y-r[:, 1]))/denominator
        c = 1-a-b
        inside = (a >= -1e-6) & (b >= -1e-6) & (c >= -1e-6)
        z = a*p[:, 2]+b*q[:, 2]+c*r[:, 2]
        groups = {}
        for index, height in zip(indices[inside], z[inside]):
            groups.setdefault(round(float(height), 4), []).append(int(index))
        for height, triangles in groups.items():
            results.append({'height': height, 'mesh': mesh.ordinal, 'attribute_index': mesh.attribute_index,
                'triangle': triangles[0], 'triangle_hits_at_height': len(triangles)})
    results.sort(key=lambda r: (r['height'], r['mesh']))
    return {'x': x, 'y': y, 'hits': results[:maximum_results], 'omitted_distinct_hits': max(0, len(results)-maximum_results),
        'height_quantization_world_units': .0001, 'ground_height': None,
        'meaning': 'static_model_vertical_intersections_not_walkability_or_live_world_state'}
