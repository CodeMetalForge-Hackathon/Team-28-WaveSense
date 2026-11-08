"""Local, asynchronous visible-person detection. Annotates the exact inference frame.
Radar evidence is never used to invent an image location or human silhouette.
"""
import importlib.util
from collections import deque
import math
from pathlib import Path
import threading
import time


def restore_box(box, scale, padding, width, height):
    x1,y1,x2,y2=map(float,box)
    if not all(math.isfinite(v) for v in (x1,y1,x2,y2,scale)) or scale<=0:return None
    px,py=padding
    values=[max(0,min(width,(x1-px)/scale)),max(0,min(height,(y1-py)/scale)),
            max(0,min(width,(x2-px)/scale)),max(0,min(height,(y2-py)/scale))]
    return values if values[2]>values[0] and values[3]>values[1] else None


class PersonVision:
    def __init__(self, camera, root):
        self.camera,self.root=camera,Path(root)
        self.lock=threading.Lock();self.condition=threading.Condition(self.lock)
        self.frame_times=deque(maxlen=30)
        self.frame=None;self.updated=0;self.seq=0;self.meta={'reason':'Starting local person detector','boxes':[]}
        self.done=threading.Event()
        self.thread=threading.Thread(target=self.run,name='camera-person-detection',daemon=True)

    def start(self):self.thread.start();return self
    def close(self):self.done.set();self.thread.join(2)
    def details(self):
        with self.lock:
            age=(time.monotonic()-self.updated)*1000 if self.updated else None
            fresh=bool(age is not None and age<1000)
            times=self.frame_times
            fps=(len(times)-1)/(times[-1]-times[0]) if fresh and len(times)>1 else 0
            return dict(self.meta,fps=round(fps,1),online=fresh,age_ms=round(age) if age is not None else None,sequence=self.seq,
                        source='camera',boxes=self.meta.get('boxes',[]) if fresh else [])

    def run(self):
        try:
            import cv2
            spec=importlib.util.spec_from_file_location('opencv_nanodet',self.root/'vendor/nanodet/nanodet.py')
            mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            cv2.setNumThreads(2)
            model=mod.NanoDet(str(self.root/'models/person_nanodet.onnx'),prob_threshold=.5,
                             backend_id=cv2.dnn.DNN_BACKEND_OPENCV,target_id=cv2.dnn.DNN_TARGET_CPU)
        except Exception as e:
            with self.lock:self.meta={'reason':'Person detector unavailable: '+type(e).__name__,'boxes':[]}
            return
        previous=None
        while not self.done.is_set():
            started=time.monotonic()
            try:
                if self.camera.sensor is None:self.done.wait(.1);continue
                s=self.camera.sensor.snapshot()
                if s['status']!='ok' or s['last_seen']==previous or s['age_ms']>300:self.done.wait(.05);continue
                image=s['data'];h,w=image.shape[:2];scale=min(416/w,416/h)
                rw,rh=round(w*scale),round(h*scale);padx,pady=(416-rw)//2,(416-rh)//2
                small=cv2.resize(image,(rw,rh));letter=cv2.copyMakeBorder(small,pady,416-rh-pady,padx,416-rw-padx,cv2.BORDER_CONSTANT,value=(0,0,0))
                predictions=model.infer(letter);boxes=[]
                for row in predictions:
                    if int(row[5])!=0:continue  # COCO person, no identity/face recognition.
                    b=restore_box(row[:4],scale,(padx,pady),w,h)
                    if b and len(boxes)<16:boxes.append({'box':[round(v,1) for v in b],'score':round(float(row[4]),3),'label':'person'})
                annotated=image.copy()
                for detection in boxes:
                    x1,y1,x2,y2=map(int,detection['box']);k=max(8,min(24,(x2-x1)//4));color=(245,245,245)
                    for x,y,sx,sy in [(x1,y1,1,1),(x2,y1,-1,1),(x1,y2,1,-1),(x2,y2,-1,-1)]:
                        cv2.line(annotated,(x,y),(x+sx*k,y),color,2);cv2.line(annotated,(x,y),(x,y+sy*k),color,2)
                    cv2.rectangle(annotated,(x1,max(0,y1-19)),(min(w,x1+94),y1),(15,15,15),-1)
                    cv2.putText(annotated,'CAM person',(x1+3,max(12,y1-5)),cv2.FONT_HERSHEY_SIMPLEX,.4,color,1,cv2.LINE_AA)
                ok,jpg=cv2.imencode('.jpg',annotated,[cv2.IMWRITE_JPEG_QUALITY,75])
                if ok:
                    elapsed=(time.monotonic()-started)*1000
                    # Capture age includes inference time; slow processing cannot refresh old frames.
                    capture_age=max(0,(time.time()*1000-s['last_seen'])/1000)
                    with self.condition:
                        self.frame_times.append(time.monotonic())
                        self.frame=jpg.tobytes();self.updated=time.monotonic()-capture_age;self.seq+=1
                        self.meta={'reason':'Visible-person analysis','boxes':boxes,'width':w,'height':h,
                                   'capture_ms':s['last_seen'],'inference_ms':round(elapsed,1),'model':'OpenCV NanoDet','frame_matched':True}
                        self.condition.notify_all()
                previous=s['last_seen']
            except Exception as e:
                with self.lock:self.meta={'reason':'Analysis recovering: '+type(e).__name__,'boxes':[]}
                self.done.wait(.5)
            self.done.wait(max(0,.22-(time.monotonic()-started)))
