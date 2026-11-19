"""Find frames with independent pre/post GSI support for a ground control."""

from bisect import bisect_right
from collections import defaultdict


MAX_BRACKET_GAP_S = 2.0


class GroundPoseReview:
    def __init__(self):
        self.poses = defaultdict(dict)
        self.frames = []

    def add(self, row, captured, provider):
        context = row['context']
        pose = context.get('own_hero_pose')
        if not isinstance(pose,dict) or pose.get('alive') is not True:
            return
        world = pose.get('xpos'),pose.get('ypos')
        hero = pose.get('hero_id')
        if (pose.get('coordinate_basis')!='gsi_centered_world_xy'
                or not isinstance(hero,str)
                or any(type(v) not in (int,float) or not -16384<=v<=16384 for v in world)):
            return
        scope = tuple(context.get(k) for k in ('match_id','game_session_id','record_session_id'))+(hero,)
        source = context.get('gsi_source_id')
        if not isinstance(source,str) or not source:
            return
        times = self.poses[scope]
        current = source,world
        # Provider timestamps may have only second granularity. A collision
        # containing different positions cannot serve as either bound.
        earlier = times.setdefault(provider,current)
        if earlier is not None and earlier[1]!=world:
            times[provider] = None
        image = row.get('image')
        anchor = row.get('ground_control_screen_anchor') or row.get('self_screen_anchor')
        if image is None or not isinstance(anchor,dict):
            return
        pixel = anchor.get('x'),anchor.get('y')
        if any(type(value) not in (int,float) or type(limit) is not int or not 0<=value<limit
               for value,limit in zip(pixel,(row.get('width'),row.get('height')))):
            return
        measured = anchor.get('ground_ring_center_measured') is True
        self.frames.append((scope,captured,world,image['file'],pixel,measured,(row['width'],row['height'])))

    def summary(self):
        timelines = {scope:sorted(samples) for scope,samples in self.poses.items()}
        pairs = []
        for scope,captured,world,file,pixel,measured,viewport in self.frames:
            times = timelines[scope]
            i = bisect_right(times,captured)
            if i==0 or i==len(times):
                continue
            before,after = times[i-1:i+1]
            if not (0<=captured-before<=MAX_BRACKET_GAP_S and 0<after-captured<=MAX_BRACKET_GAP_S):
                continue
            previous,next_pose = self.poses[scope][before],self.poses[scope][after]
            if previous is None or next_pose is None or previous[0]==next_pose[0]:
                continue
            if previous[1]!=world or next_pose[1]!=world:
                continue
            pairs.append({'image':file,'world_xy':list(world),'screen_xy_candidate':list(pixel),'viewport':viewport,
                          'before_s':round(captured-before,6),'after_s':round(after-captured,6),
                          'ground_ring_measured':measured})
        return {'image_pose_candidates':len(self.frames),'bracketed_same_position':pairs,
                'distinct_bracketed_world_xy':len({tuple(p['world_xy']) for p in pairs}),
                'bracketed_measured_ring_count':sum(p['ground_ring_measured'] for p in pairs),
                'world_to_image_alignment_verified':False}
