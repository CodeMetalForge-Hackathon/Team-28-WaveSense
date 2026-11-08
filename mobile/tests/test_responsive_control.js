const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(require('path').join(__dirname,'../control.js'),'utf8'));
vm.runInThisContext(fs.readFileSync(require('path').join(__dirname,'../drive.js'),'utf8'));
const settle=()=>new Promise(r=>setImmediate(r));
(async()=>{
 let held=true,calls=[],pending=[];
 const c=new RobotControl({minInterval:0,hasIntent:()=>held,post:(url,data)=>{calls.push({url,data});if(url==='/api/arm')return Promise.resolve({lease:'a'});if(url==='/api/drive'&&(data.vx||data.vy||data.wz))return new Promise(resolve=>pending.push(resolve));return Promise.resolve({})}});
 c.setAvailable(true);c.input({vx:.2,vy:0,wz:0});await settle();
 c.input({vx:-.2,vy:0,wz:0});assert.equal(pending.length,2,'reversal waited for old movement acknowledgement');
 for(let i=0;i<20;i++)c.input({vx:-.1-i*.005,vy:0,wz:0});
 assert.equal(pending.length,2,'unbounded movement requests');
 const latest=c.vector.vx;pending[0]({});await settle();
 assert.equal(calls.at(-1).data.vx,latest,'queued an obsolete stick position');
 const beforeStop=calls.length;c.input({vx:0,vy:0,wz:0});
 assert(calls.slice(beforeStop).some(x=>x.url==='/api/drive'&&x.data.vx===0),'neutral waited for saturated movement transport');
 held=false;c.release();held=true;c.input({vx:0,vy:.1,wz:0});await settle();
 assert(c.armed);const currentEpoch=c.epoch;pending[1]({});await settle();assert.equal(c.epoch,currentEpoch);assert(c.inflight>=0&&c.inflight<=2,'old response corrupted new lease capacity');
 c.release();for(const resolve of pending)resolve({});await settle();
 // Coalesce changed input during rate limiting, then flush without waiting for heartbeat.
 let now=0,timers=[],sent=[];
 const d=new RobotControl({now:()=>now,schedule:(fn,ms)=>{const t={fn,ms};timers.push(t);return t},cancel:t=>{t.cancelled=true},hasIntent:()=>true,post:(url,data)=>{sent.push({url,data});return Promise.resolve({lease:'b'})}});
 d.setAvailable(true);d.input({vx:.1,vy:0,wz:0});await settle();now=5;d.input({vx:.2,vy:0,wz:0});assert.equal(timers.length,1);assert.equal(timers[0].ms,15);now=20;timers[0].fn();await settle();assert.equal(sent.at(-1).data.vx,.2);d.release();
 // Slider bounds diagonal + rotation mix, and zero always produces neutral.
 for(const percent of [0,5,25,50,100]){
  const cap=DriveMath.speedLimit(percent),v=DriveMath.motion({x:.707,y:-.707},{x:1,y:0},cap,false);
  assert(Math.abs(v.vx)+Math.abs(v.vy)+Math.abs(v.wz)<=cap+1e-9,'combined command exceeded speed slider cap');
 }
 assert.equal(DriveMath.speedLimit(-10),0);assert.equal(DriveMath.speedLimit(200),.35);
 assert.deepEqual(DriveMath.motion({x:1,y:1},{x:1},0,false),{vx:-0,vy:-0,wz:-0});
 assert(DriveMath.vector(.07,0).x>0,'small deliberate finger input ignored');assert.equal(DriveMath.vector(.02,0).x,0);
 let payloads=[];const meta=new RobotControl({minInterval:0,hasIntent:()=>true,post:(url,data)=>{payloads.push({url,data});return Promise.resolve({lease:'speed'})}});meta.setAvailable(true);meta.input({vx:.175,vy:0,wz:0,max_speed:50});await settle();assert.equal(payloads.find(x=>x.url==='/api/drive').data.max_speed,50);meta.input({vx:0,vy:0,wz:0,max_speed:0});assert.equal(payloads.at(-1).data.max_speed,0);meta.release();
 let contact=[];const center=new RobotControl({hasIntent:()=>true,post:(url,data)=>{contact.push({url,data});return Promise.resolve({lease:'center'})}});center.setAvailable(true);center.input({vx:0,vy:0,wz:0,max_speed:100});await settle();assert(contact.some(x=>x.url==='/api/arm'),'center contact did not prepare zero-speed control');assert(!contact.some(x=>x.url==='/api/drive'&&(x.data.vx||x.data.vy||x.data.wz)),'center contact caused motion');center.release();
 console.log('PASS: immediate reverse, latest-input coalescing, bounded requests, priority stop, fresh leases and combined speed cap');
})().catch(e=>{console.error(e);process.exitCode=1});
