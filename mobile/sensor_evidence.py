"""Honest camera/radar evidence composition: concurrence is not spatial association."""
import math


def compose_evidence(radar, vision, moving=False):
    active=bool(radar.get('online') and radar.get('state') in ('candidate','motion') and not moving)
    ranges=[]
    if active:
        for t in radar.get('tracks',[]):
            r=t.get('range_m')
            if isinstance(r,(float,int)) and not isinstance(r,bool) and math.isfinite(r) and 0<r<=100:ranges.append(r)
    visible=bool(vision.get('online') and vision.get('boxes'))
    return {'radar_candidate':active,'ranges_m':ranges[:4],'visible_person':visible,
            'state':'paused' if moving else 'candidate' if active else 'clear' if radar.get('online') and radar.get('state')=='clear' else 'not_judging',
            'camera_source':'camera','radar_source':'radar','spatially_associated':False,
            'bearing_known':False,'occlusion':'unknown','human_confirmed':False}
