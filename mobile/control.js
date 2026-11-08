/* Control transport is independent of video rendering. Neutral bypasses an in-flight move. */
globalThis.RobotControl=class RobotControl{
 constructor({post,hasIntent,onState=()=>{},onReset=()=>{}}){this.post=post;this.hasIntent=hasIntent;this.onState=onState;this.onReset=onReset;this.available=false;this.armed=false;this.lease=null;this.seq=0;this.epoch=0;this.busy=false;this.pending=null;this.vector={vx:0,vy:0,wz:0};this.error=null}
 state(){return{available:this.available,armed:this.armed,error:this.error}}
 notify(){this.onState(this.state())}
 neutral(){return Math.abs(this.vector.vx)+Math.abs(this.vector.vy)+Math.abs(this.vector.wz)<1e-7}
 setAvailable(ready){if(ready&&!this.available){this.vector={vx:0,vy:0,wz:0};this.onReset()}if(!ready&&this.available){this.available=false;this.release('Connection lost');this.onReset()}else{this.available=ready;this.notify()}}
 input(v){this.vector={vx:v.vx,vy:v.vy,wz:v.wz};this.error=null;if(!this.available)return;if(this.neutral()){if(this.armed)this.zeroNow();if(!this.hasIntent())this.release();return}if(!this.hasIntent())return;if(!this.armed)this.arm();else this.flush()}
 async arm(){if(this.pending||this.armed||!this.available||!this.hasIntent())return;const generation=++this.epoch;this.error=null;
  this.pending=(async()=>{try{const result=await this.post('/api/arm',{protocol:3});if(generation!==this.epoch||!this.hasIntent()||!this.available){this.post('/api/release',{lease:result.lease}).catch(()=>{});return}this.lease=result.lease;this.seq=0;this.armed=true;this.notify();this.flush()}catch(e){if(generation===this.epoch){this.error=e.message;this.armed=false;this.lease=null;this.onReset();this.notify()}}finally{this.pending=null}})();return this.pending
 }
 tick(){if(!this.available||!this.hasIntent())return;if(this.armed)this.flush();else if(!this.neutral())this.arm()}
 async zeroNow(){if(!this.armed)return;const generation=this.epoch,payload={vx:0,vy:0,wz:0,seq:++this.seq,lease:this.lease};try{await this.post('/api/drive',payload)}catch(e){if(e.code!=='stale_command'&&generation===this.epoch)this.fail(e)}}
 async flush(){if(this.busy||!this.armed||!this.available)return;this.busy=true;const generation=this.epoch,payload={...this.vector,seq:++this.seq,lease:this.lease};try{await this.post('/api/drive',payload)}catch(e){if(e.code!=='stale_command'&&generation===this.epoch)this.fail(e)}finally{this.busy=false}}
 fail(e){this.error=e.message;this.release();this.onReset();this.notify()}
 release(reason=null){const owned=this.armed||!!this.pending,lease=this.lease;++this.epoch;this.armed=false;this.lease=null;this.vector={vx:0,vy:0,wz:0};if(reason)this.error=reason;if(owned)this.post('/api/release',{lease}).catch(()=>{});this.notify()}
 observe(control,requestEpoch){if(requestEpoch===this.epoch&&this.armed&&!control.mine){this.release('Control ended');this.onReset()}}
};
