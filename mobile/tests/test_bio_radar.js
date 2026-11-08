const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('node:assert/strict');
global.window={setInterval:()=>{}};
global.localStorage={getItem:()=>null,setItem:()=>{}};
for(const file of ['../person_overlay.js','../bio_radar.js'])vm.runInThisContext(fs.readFileSync(path.join(__dirname,file),'utf8'));
const camera={online:true,resolution:[640,480]};
const candidate={state:'candidate',radar_candidate:true};
const vision=(...boxes)=>({source:'camera',online:true,age_ms:50,width:640,height:480,boxes:boxes.map(box=>({box}))});
const status=(tracks,over={})=>({radar:{online:true,state:'candidate',tracks},evidence:candidate,camera,vision:vision(),...over});
const planFor=(track,view,over)=>BioRadar.plan(status([track],over),view);
const one=(range_m,bearing_deg,view)=>planFor({range_m,bearing_deg},view).items[0];
const centre=item=>(item.box[0]+item.box[2])/2,height=item=>item.box[3]-item.box[1];
const person=[210,70,400,470],small=[20,200,60,300],right=[500,100,620,400];

// The camera detector wins: a seen person gets the detector's exact box, not a second marker.
const seen=planFor({range_m:1.3,bearing_deg:-7},{},{vision:vision(person)});
assert.equal(seen.items.length,1);assert.equal(seen.items[0].kind,'confirmed');assert.deepEqual(seen.items[0].box,person);
assert.deepEqual(planFor({range_m:1.3,bearing_deg:-7},{},{vision:vision(small,person)}).items[0].box,person);
assert.equal(planFor({range_m:1.3,bearing_deg:null},{},{vision:vision(person)}).items[0].kind,'confirmed');

// Stale detections are not used to confirm anything.
assert.equal(BioRadar.plan(status([{range_m:1.3,bearing_deg:-7}],{vision:{...vision(person),age_ms:900}})).items[0].kind,'radar');

// A detector box that has just expired must not spawn a separate radar marker.
const gap=BioRadar.plan(status([{range_m:1.3,bearing_deg:-7}],{vision:vision()}),{},0,true);
assert.equal(gap.items.length,0);assert.equal(gap.heldRange,1.3);assert.equal(gap.state,'candidate');

// Once the mounting is verified, the bearing decides which box, or none.
BioRadar.CALIBRATION.verified=true;
assert.deepEqual(planFor({range_m:2,bearing_deg:25},{},{vision:vision(person,right)}).items[0].box,right);
const elsewhere=planFor({range_m:2,bearing_deg:-40},{},{vision:vision(right)});
assert.equal(elsewhere.items[0].kind,'radar');
BioRadar.CALIBRATION.verified=false;

// Unseen by the camera: an orange box from the bearing, person-shaped and inside the frame.
assert.equal(one(2,0).kind,'radar');assert.equal(centre(one(2,0)),320);
const focal=320/Math.tan(35*Math.PI/180);
assert.ok(Math.abs(centre(one(2,20))-(320+focal*Math.tan(20*Math.PI/180)))<1e-6);
assert.ok(centre(one(2,20))>320&&centre(one(2,-20))<320);
assert.ok(height(one(1.5,0))>height(one(3,0)));
assert.ok(height(one(.4,0))<=480*.72+1e-9);
assert.ok(height(one(11,0))>=480*.28-1e-9);

// A bearing outside the view stays fully inside it and says which way.
assert.equal(one(2,55).edge,'right');assert.ok(one(2,55).box[2]<=640+1e-9);
assert.equal(one(2,-55).edge,'left');assert.ok(one(2,-55).box[0]>=-1e-9);
const tall={fit:'slice',aspect:.6},wide={fit:'slice',aspect:2};
assert.equal(one(2,30,tall).edge,'right');assert.ok(one(2,30,tall).box[2]<=320+480*.6/2+1e-9);
assert.ok(one(2,0,wide).box[1]>=80-1e-9&&one(2,0,wide).box[3]<=400+1e-9);

// No direction and no camera person: nothing is drawn, only the distance is reported.
for(const b of [null,undefined,NaN]){const p=planFor({range_m:2,bearing_deg:b});assert.equal(p.items.length,0);assert.equal(p.unknownRange,2);assert.equal(p.state,'candidate')}

// Nothing is drawn unless the radar is live and has a candidate right now.
assert.deepEqual(BioRadar.plan(null,{}),{state:'offline',items:[],unknownRange:null});
const t=[{range_m:2,bearing_deg:0}];
assert.equal(BioRadar.plan(status(t,{radar:{online:false,tracks:[]}})).state,'offline');
assert.equal(BioRadar.plan(status(t,{evidence:{state:'paused'}})).state,'paused');
assert.equal(BioRadar.plan(status([],{evidence:{state:'clear',radar_candidate:false}})).state,'clear');
assert.equal(BioRadar.plan(status([],{evidence:{state:'not_judging',radar_candidate:false}})).state,'learning');
assert.equal(BioRadar.plan(status(t,{camera:{online:false}})).state,'no-camera');
assert.equal(BioRadar.plan(status(t,{evidence:{state:'clear',radar_candidate:false}})).items.length,0);

// A person the camera sees with no radar candidate gets no radar marking.
assert.equal(BioRadar.plan(status([],{evidence:{state:'clear',radar_candidate:false},vision:vision(person)})).items.length,0);

// Malformed tracks are dropped; the list is bounded.
assert.equal(BioRadar.plan(status([{range_m:NaN,bearing_deg:0},{range_m:-1,bearing_deg:0},{range_m:99,bearing_deg:0},null])).items.length,0);
assert.equal(BioRadar.plan(status(Array(9).fill({range_m:2,bearing_deg:0}))).items.length,3);

// Mounting corrections.
BioRadar.CALIBRATION.flip=true;assert.ok(centre(one(2,20))<320);BioRadar.CALIBRATION.flip=false;
BioRadar.CALIBRATION.yawDeg=10;assert.equal(centre(one(2,-10)),320);BioRadar.CALIBRATION.yawDeg=0;
console.log('PASS: Bio Radar uses camera boxes first, radar-only placement, unknown direction, gating and bounds');
