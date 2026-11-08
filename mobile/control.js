/* Latest input wins. Two bounded moving requests; neutral never waits behind them. */
globalThis.RobotControl=class RobotControl{
 constructor({post,hasIntent,onState=()=>{},onReset=()=>{},now=()=>performance.now(),schedule=setTimeout,cancel=clearTimeout,minInterval=20}){
  Object.assign(this,{post,hasIntent,onState,onReset,now,schedule,cancel,minInterval});
  this.available=false;this.armed=false;this.lease=null;this.seq=0;this.epoch=0;
  this.inflight=0;this.busy=false;this.pending=null;this.timer=null;
  this.revision=0;this.sentRevision=-1;this.sentAt=-Infinity;
  this.vector={vx:0,vy:0,wz:0,max_speed:100};this.error=null;
 }
 state(){return{available:this.available,armed:this.armed,error:this.error}}
 notify(){this.onState(this.state())}
 neutral(){return Math.abs(this.vector.vx)+Math.abs(this.vector.vy)+Math.abs(this.vector.wz)<1e-7}
 clearTimer(){if(this.timer!==null){this.cancel(this.timer);this.timer=null}}
 setAvailable(ready){if(ready&&!this.available){this.vector={vx:0,vy:0,wz:0,max_speed:100};this.onReset()}if(!ready&&this.available){this.available=false;this.release('Connection lost');this.onReset()}else{this.available=ready;this.notify()}}
 input(v){
  const previous=this.vector,wasNeutral=this.neutral();this.vector={vx:v.vx,vy:v.vy,wz:v.wz,max_speed:Number.isFinite(v.max_speed)?v.max_speed:100};++this.revision;this.error=null;
  if(!this.available)return;
  if(this.neutral()){this.clearTimer();if(this.armed&&!wasNeutral)this.zeroNow();if(!this.hasIntent())this.release();else if(!this.armed)this.arm();return}
  if(!this.hasIntent())return;
  const reverse=['vx','vy','wz'].some(k=>previous[k]*this.vector[k]<0);
  const slowing=['vx','vy','wz'].reduce((n,k)=>n+Math.abs(this.vector[k])-Math.abs(previous[k]),0)<-1e-7;
  if(!this.armed)this.arm();else{if(['vx','vy','wz'].some(k=>Math.abs(previous[k])>1e-7&&Math.abs(this.vector[k])<1e-7))this.zeroNow();this.flush(reverse||slowing)}
 }
 async arm(){
  if(this.pending||this.armed||!this.available||!this.hasIntent())return;
  const generation=++this.epoch;this.error=null;
  this.pending=(async()=>{try{
   const result=await this.post('/api/arm',{protocol:4});
   if(generation!==this.epoch||!this.hasIntent()||!this.available){this.post('/api/release',{lease:result.lease}).catch(()=>{});return}
   this.lease=result.lease;this.seq=0;this.armed=true;this.notify();this.flush(true);
  }catch(e){if(generation===this.epoch){this.error=e.message;this.armed=false;this.lease=null;this.onReset();this.notify()}}finally{this.pending=null}})();
  return this.pending;
 }
 tick(){if(!this.available||!this.hasIntent())return;if(this.armed)this.flush();else if(!this.neutral())this.arm()}
 async zeroNow(){
  if(!this.armed)return;
  this.clearTimer();const generation=this.epoch,payload={vx:0,vy:0,wz:0,max_speed:this.vector.max_speed,seq:++this.seq,lease:this.lease};
  this.sentRevision=this.neutral()?this.revision:-1;this.sentAt=this.now();
  try{await this.post('/api/drive',payload)}catch(e){if(e.code!=='stale_command'&&generation===this.epoch)this.fail(e)}
 }
 async flush(urgent=false){
  if(this.inflight>=2||!this.armed||!this.available||!this.hasIntent())return;
  const delay=this.minInterval-(this.now()-this.sentAt);
  if(!urgent&&delay>0){if(this.timer===null)this.timer=this.schedule(()=>{this.timer=null;this.flush()},delay);return}
  this.clearTimer();++this.inflight;this.busy=true;
  const generation=this.epoch,payload={...this.vector,seq:++this.seq,lease:this.lease};
  this.sentRevision=this.revision;this.sentAt=this.now();
  try{await this.post('/api/drive',payload)}catch(e){if(e.code!=='stale_command'&&generation===this.epoch)this.fail(e)}finally{
   if(generation===this.epoch){--this.inflight;this.busy=this.inflight>0;if(this.revision!==this.sentRevision)this.flush()}
  }
 }
 fail(e){this.error=e.message;this.release();this.onReset();this.notify()}
 release(reason=null){
  const owned=this.armed||!!this.pending,lease=this.lease;this.clearTimer();++this.epoch;
  this.armed=false;this.lease=null;this.inflight=0;this.busy=false;this.vector={vx:0,vy:0,wz:0,max_speed:100};
  if(reason)this.error=reason;if(owned)this.post('/api/release',{lease}).catch(()=>{});this.notify();
 }
 observe(control,requestEpoch){if(requestEpoch===this.epoch&&this.armed&&!control.mine){this.release('Control ended');this.onReset()}}
};
