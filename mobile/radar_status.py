"""Explicit radar handoff contract. USB detection alone is never a person detection."""
import json
import math
from pathlib import Path
import time


def read_radar(path, now=None):
    now = time.time() if now is None else now
    detected = False
    for p in Path('/sys/bus/usb/devices').glob('*/idVendor'):
        try:
            if p.read_text().strip()=='064b' and (p.parent/'idProduct').read_text().strip()=='7823':detected=True
        except OSError:pass
    offline = {'online':False,'usb_detected':detected,'reason':'Radar driver feed unavailable','source':'radar','tracks':[]}
    try:
        p = Path(path)
        if p.stat().st_size>100000:raise ValueError('Radar payload too large')
        s = json.loads(p.read_text())
        if not isinstance(s,dict):raise ValueError('Invalid radar envelope')
        if not isinstance(s.get('tracks',[]),list):raise ValueError('Invalid radar tracks')
        age=now-float(s['timestamp_ms'])/1000
        if s.get('schema')!=1 or not 0<=age<2 or not s.get('online'):return dict(offline,reason='Radar data stale or offline')
        tracks=[]
        for t in s.get('tracks',[])[:32]:
            if not isinstance(t,dict):continue
            r=float(t['range_m']);a=t.get('bearing_deg');a=float(a) if a is not None else None
            if not math.isfinite(r) or not 0<=r<=100 or (a is not None and(not math.isfinite(a)or abs(a)>180)):continue
            tracks.append({'range_m':r,'bearing_deg':a,'candidate':True})
        return {'online':True,'usb_detected':detected,'source':'radar','age_ms':round(age*1000),
                'state':s.get('state') if s.get('state') in ('candidate','motion','clear','not_judging') else 'not_judging',
                'tracks':tracks,'spatial_alignment_verified':False,'through_wall_validated':False}
    except (OSError,ValueError,KeyError,TypeError):return offline
