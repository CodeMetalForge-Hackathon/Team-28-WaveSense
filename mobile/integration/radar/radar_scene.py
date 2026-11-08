"""Measured range/angle return telemetry; does not classify humans.

Single TX / four RX. Geometry and complex factory correction follow ADI TinyRadTool
(dcea3878b73af183fa4404fc457da61380c29933, RfGet and ModBpa). No MIMO mode change.
Angle data requires finite, nonzero factory coefficients and remains unregistered
until mounting/left-right orientation are checked physically.
"""
import time
import numpy as np


class RadarScene:
    NFFT = 512
    def __init__(self, ranges, wavelength, calibration=None, range_resolution=.6):
        if not np.isfinite(wavelength) or wavelength<=0: raise ValueError("Invalid wavelength")
        self.ranges = np.asarray(ranges, dtype=float)
        if self.ranges.shape != (257,) or not np.isfinite(self.ranges).all() or not np.all(np.diff(self.ranges)>0):
            raise ValueError('Expected monotonic 512-point range axis')
        self.indices = np.flatnonzero((self.ranges>=.3)&(self.ranges<=12))
        self.angles = np.arange(-60.,61.,3.)
        self.resolution = float(range_resolution)
        self.calibration = None
        if calibration is not None:
            c = np.asarray(calibration,dtype=complex)
            if c.shape==(4,) and np.isfinite(c).all() and np.all((np.abs(c)>.01)&(np.abs(c)<100)):
                self.calibration=c
        positions=np.arange(4)*.006217
        self.steering=np.exp(-2j*np.pi*np.sin(np.deg2rad(self.angles))[:,None]*positions[None,:]/wavelength)
        self.last = -float('inf')
        self.result = None

    def feed(self,t,frame):
        if not np.isfinite(t):raise ValueError("Invalid capture time")
        if 0<=t-self.last<.05:return
        data=np.asarray(frame)
        if data.shape!=(256,4) or not np.isfinite(data).all():raise ValueError('Expected 256 x 4 ADC samples')
        self.last=t
        # ADI's BPA omits the frame counter and ten settling samples.
        x=data[10:].astype(float);x-=x.mean(axis=0)
        win=np.hanning(len(x));spectrum=np.fft.rfft(x*win[:,None],n=self.NFFT,axis=0)/win.sum()
        selected=spectrum[self.indices]
        power=np.mean(np.abs(selected)**2,axis=1)
        peak=max(float(power.max()),1e-12)
        profile=np.maximum(-60,10*np.log10(np.maximum(power,1e-12)/peak)) if power.max()>1e-12 else np.full_like(power,-60)
        out={'schema':1,'timestamp_ms':round(t*1000),'ranges_m':np.round(self.ranges[self.indices],3).tolist(),
             'profile_db':np.round(profile,1).tolist(),'range_resolution_m':round(self.resolution,3),
             'angle_available':self.calibration is not None,'mounting_verified':False,
             'human_confirmed':False,'rx_count':4,'mimo':False}
        if self.calibration is not None:
            beams=np.abs(np.sum((selected*self.calibration)[:,:,None]*self.steering.T[None,:,:],axis=1))**2
            norm=max(float(beams.max()),1e-12)
            levels=np.clip((10*np.log10(np.maximum(beams,1e-12)/norm)+40)/40,0,1) if beams.max()>1e-12 else np.zeros_like(beams)
            out.update(angles_deg=self.angles.tolist(),angle_power_u8=np.rint(levels*255).astype('uint8').tolist())
        self.result=out

    def snapshot(self,now=None,target_range=None):
        now=time.time() if now is None else now
        if not self.result or not 0<=now-self.result['timestamp_ms']/1000<1:return None
        out=dict(self.result)
        if self.calibration is not None and target_range is not None and np.isfinite(target_range):
            distances=np.asarray(out['ranges_m']);idx=int(np.argmin(np.abs(distances-target_range)))
            if abs(distances[idx]-target_range)<=self.resolution/2:
                row=out['angle_power_u8'][idx]
                if max(row)>0 and max(row)-float(np.median(row))>=32:out.update(target_range_m=round(float(target_range),2),target_angle_deg=float(self.angles[int(np.argmax(row))]))
        return out
