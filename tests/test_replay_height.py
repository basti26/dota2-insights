import unittest
from types import SimpleNamespace
from dota2_insights.replay_height import height_observation,FIELDS,WardHeightCapture


class ReplayHeightTests(unittest.TestCase):
    def sample(self,cell,vec):return height_observation(dict(zip(FIELDS,(cell,vec))))

    def test_raw_height_keeps_large_vector_without_silent_cell_normalization(self):
        r=self.sample(128,255.96875)
        self.assertEqual(r['raw_z'],16639.96875)
        self.assertEqual(r['components'][FIELDS[1]],255.96875)
        self.assertFalse(r['centered_transform_verified']);self.assertFalse(r['ground_height_verified'])

    def test_missing_is_unknown_and_zero_is_a_valid_record(self):
        self.assertEqual(self.sample(None,0)['status'],'missing_components')
        self.assertIsNone(self.sample(128,None)['raw_z'])
        self.assertEqual(self.sample(0,0)['raw_z'],0)

    def test_invalid_boolean_nonfinite_and_out_of_range_are_not_heights(self):
        for cell,vec in [(True,0),(128,False),(-1,0),(65536,0),(128,float('nan')),(128,float('inf')),(128,100000)]:
            r=self.sample(cell,vec)
            with self.subTest(cell=cell,vec=vec):
                self.assertEqual(r['status'],'invalid_components')
                self.assertIsNone(r['raw_z'])

    def test_ward_height_uses_exact_event_object_even_when_tick_and_xy_equal(self):
        class Extractor:
            def __init__(self):self.ward_events=[]
            def _on_entity(self,entity,operation):
                if operation=='place':self.ward_events.append(SimpleNamespace(tick=5,x=10,y=20))
        class Entity(dict):
            def get_index(self):return self['index']
            def get_serial(self):return self['serial']
        extractor=Extractor();capture=WardHeightCapture(extractor)
        for index,height in [(1,0),(2,128)]:
            entity=Entity({FIELDS[0]:128,FIELDS[1]:height,'index':index,'serial':index+10})
            extractor._on_entity(entity,'place')
        first,second=extractor.ward_events
        self.assertEqual(capture.for_event(first)['height_observation']['raw_z'],16384)
        self.assertEqual(capture.for_event(second)['height_observation']['raw_z'],16512)
        self.assertEqual(capture.for_event(second)['height_entity_serial'],12)
        # A newly constructed identical payload must not pick either recorded entity.
        self.assertIsNone(capture.for_event(SimpleNamespace(tick=5,x=10,y=20))['height_observation'])
        extractor._on_entity(entity,'update')
        self.assertEqual(capture.summary()['captured_event_objects'],2)

    def test_ambiguous_callback_does_not_assign_one_entity_to_multiple_events(self):
        extractor=SimpleNamespace(ward_events=[])
        def callback(entity,operation):extractor.ward_events.extend([object(),object()])
        extractor._on_entity=callback
        capture=WardHeightCapture(extractor)
        extractor._on_entity({},'place')
        self.assertEqual(capture.summary()['ambiguous_callbacks'],1)
        self.assertEqual(capture.summary()['captured_event_objects'],0)
