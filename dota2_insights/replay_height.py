"""Keep raw Z at its source observation; entity origins are not ground truth."""
import math
from copy import deepcopy

FIELDS=('CBodyComponent.m_cellZ','CBodyComponent.m_vecZ')


def height_observation(entity):
    cell,vec=(entity.get(name) for name in FIELDS)
    missing=cell is None or vec is None
    cell_ok=type(cell) is int and 0<=cell<=65535
    vec_ok=type(vec) in (int,float) and math.isfinite(vec) and abs(vec)<=65536
    good=cell_ok and vec_ok
    return {'version':'replay_raw_height_v1',
            'status':'recorded' if good else 'missing_components' if missing else 'invalid_components',
            'components':{FIELDS[0]:cell if cell_ok else None,FIELDS[1]:float(vec) if vec_ok else None},
            'raw_z':float(cell*128+vec) if good else None,
            'coordinate_basis':'source2_cell128_plus_vec_raw_z',
            'centered_transform_verified':False,'ground_height_verified':False}


class WardHeightCapture:
    """Associate by the exact new WardEvent object in its native entity callback."""
    def __init__(self,extractor):
        if not isinstance(getattr(extractor,'ward_events',None),list) or not callable(getattr(extractor,'_on_entity',None)):
            raise ValueError('ward_height_pinned_callback_layout_required')
        self.observations={};self.limited=False;self.ambiguous_callbacks=0
        original=extractor._on_entity
        def entity_callback(entity,operation):
            before=len(extractor.ward_events)
            original(entity,operation)
            added=extractor.ward_events[before:]
            if not added:return
            if len(added)!=1:
                self.ambiguous_callbacks+=1
                return
            if len(self.observations)>=8192:
                self.limited=True
                return
            self.observations[id(added[0])]={'height_observation':height_observation(entity),
                'height_entity_index':entity.get_index(),'height_entity_serial':entity.get_serial(),
                'height_association':'same_native_placement_event_and_entity_callback'}
        extractor._on_entity=entity_callback

    def for_event(self,event):
        return deepcopy(self.observations.get(id(event),{'height_observation':None,
            'height_entity_index':None,'height_entity_serial':None,'height_association':'unavailable_not_joined_by_time_or_xy'}))

    def summary(self):
        return {'version':'native_ward_height_capture_v1','captured_event_objects':len(self.observations),
                'limited':self.limited,'ambiguous_callbacks':self.ambiguous_callbacks,
                'centered_transform_verified':False,'ground_height_verified':False}
