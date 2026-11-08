"""Bounded measured-return telemetry. It never certifies people or registration."""
import math

def clean_scene(scene,now):
    def number(x):return type(x) in (int,float) and math.isfinite(x)
    if not isinstance(scene,dict) or scene.get('schema')!=1:return None
    stamp=scene.get('timestamp_ms')
    if not number(stamp) or not 0<=now-stamp/1000<2:return None
    ranges=scene.get('ranges_m');profile=scene.get('profile_db')
    if not isinstance(ranges,list) or not isinstance(profile,list) or not 1<=len(ranges)<=128 or len(profile)!=len(ranges):return None
    if not all(number(x) and 0<=x<=24 for x in ranges) or any(b<=a for a,b in zip(ranges,ranges[1:])):return None
    if not all(number(x) and -100<=x<=0 for x in profile):return None
    result={'schema':1,'timestamp_ms':stamp,'ranges_m':ranges,'profile_db':profile,
            'angle_available':False,'mounting_verified':False,'human_confirmed':False}
    resolution=scene.get('range_resolution_m')
    if number(resolution) and .01<=resolution<=10:result['range_resolution_m']=resolution
    angles=scene.get('angles_deg');power=scene.get('angle_power_u8')
    if scene.get('angle_available') is True and isinstance(angles,list) and 2<=len(angles)<=128 and all(number(x) and -90<=x<=90 for x in angles) and all(b>a for a,b in zip(angles,angles[1:])) and isinstance(power,list) and len(power)==len(ranges):
        if all(isinstance(row,list) and len(row)==len(angles) and all(type(x) is int and 0<=x<=255 for x in row) for row in power):
            result.update(angle_available=True,angles_deg=angles,angle_power_u8=power)
            a=scene.get('target_angle_deg');r=scene.get('target_range_m')
            if number(a) and min(angles)<=a<=max(angles) and number(r) and min(ranges)<=r<=max(ranges):
                result.update(target_angle_deg=a,target_range_m=r)
    return result
