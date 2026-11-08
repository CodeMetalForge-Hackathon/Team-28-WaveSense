const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
global.window={setInterval:()=>{}};
global.localStorage={getItem:()=>null,setItem:()=>{}};
vm.runInThisContext(fs.readFileSync(require('path').join(__dirname,'../bio_radar.js'),'utf8'));
const camera={online:true,resolution:[640,480]};
const candidate={state:'candidate',radar_candidate:true};
const status=(tracks,over={})=>({radar:{online:true,state:'candidate',tracks},evidence:candidate,camera,...over});
const one=(range_m,bearing_deg,view)=>BioRadar.plan(status([{range_m,bearing_deg}]),view).blobs[0];

// Straight ahead lands on the image centre; bearings move it off centre in proportion to tan().
assert.equal(one(2,0).cx,320);assert.equal(one(2,0).kind,'located');
const focal=320/Math.tan(35*Math.PI/180);
assert.ok(Math.abs(one(2,20).cx-(320+focal*Math.tan(20*Math.PI/180)))<1e-6);
assert.ok(one(2,20).cx>320&&one(2,-20).cx<320);

// Nearer people are drawn larger, and the blob never exceeds the visible frame.
assert.ok(one(1.5,0).ry>one(4,0).ry);
assert.ok(one(.4,0).ry<=480*.36+1e-9);

// A bearing beyond the image is pinned to that edge and says so.
assert.deepEqual([one(2,55).cx,one(2,55).edge],[640,'right']);
assert.deepEqual([one(2,-55).cx,one(2,-55).edge],[0,'left']);

// Cropped main view: an in-frame bearing outside the crop goes to the visible edge, not off-screen.
const tall={fit:'slice',aspect:.6},wide={fit:'slice',aspect:2};
assert.equal(one(2,30,tall).edge,'right');
assert.equal(one(2,30,tall).cx,320+480*.6/2);
assert.equal(one(2,0,wide).cy,240);
assert.ok(one(2,0,wide).ry<=320*.36+1e-9);

// No direction is shown as a wide band, never a guessed position.
for(const b of [null,undefined,NaN])assert.equal(one(2,b).kind,'unknown');

// Nothing is drawn unless the radar is live and has a candidate right now.
assert.deepEqual(BioRadar.plan(null,{}),{state:'offline',blobs:[]});
assert.equal(BioRadar.plan(status([{range_m:2,bearing_deg:0}],{radar:{online:false,tracks:[]}})).state,'offline');
assert.equal(BioRadar.plan(status([{range_m:2,bearing_deg:0}],{evidence:{state:'paused'}})).state,'paused');
assert.equal(BioRadar.plan(status([],{evidence:{state:'clear',radar_candidate:false}})).state,'clear');
assert.equal(BioRadar.plan(status([],{evidence:{state:'not_judging',radar_candidate:false}})).state,'learning');
assert.equal(BioRadar.plan(status([{range_m:2,bearing_deg:0}],{camera:{online:false}})).state,'no-camera');
assert.equal(BioRadar.plan(status([{range_m:2,bearing_deg:0}],{evidence:{state:'clear',radar_candidate:false}})).blobs.length,0);

// Malformed tracks are dropped; the list is bounded.
assert.equal(BioRadar.plan(status([{range_m:NaN,bearing_deg:0},{range_m:-1,bearing_deg:0},{range_m:99,bearing_deg:0},null])).blobs.length,0);
assert.equal(BioRadar.plan(status(Array(9).fill({range_m:2,bearing_deg:0}))).blobs.length,3);

// Mounting corrections.
BioRadar.CALIBRATION.flip=true;assert.ok(one(2,20).cx<320);BioRadar.CALIBRATION.flip=false;
BioRadar.CALIBRATION.yawDeg=10;assert.ok(one(2,-10).cx===320);BioRadar.CALIBRATION.yawDeg=0;
console.log('PASS: Bio Radar projection, crop handling, unknown direction, gating and bounds');
