const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
global.window={setInterval:()=>{}};
vm.runInThisContext(fs.readFileSync(require('path').join(__dirname,'../person_overlay.js'),'utf8'));
const camera={online:true,resolution:[640,480]},v={source:'camera',online:true,age_ms:100,width:640,height:480,boxes:[{box:[10,20,100,200]}]};
assert.deepEqual(PersonOverlay.plan(v,camera).boxes,[[10,20,100,200]]);
assert.equal(PersonOverlay.plan(v,camera,350),null); // expiry includes time since receipt
assert.equal(PersonOverlay.plan({...v,online:false},camera),null);
assert.equal(PersonOverlay.plan(v,{...camera,online:false}),null);
assert.equal(PersonOverlay.plan(v,{...camera,resolution:[1920,1080]}),null);
assert.equal(PersonOverlay.plan({...v,source:'radar'},camera),null);
assert.equal(PersonOverlay.plan({...v,age_ms:null},camera),null);
assert.deepEqual(PersonOverlay.plan({...v,boxes:[{box:[NaN,0,20,40]},{box:[40,40,20,20]},{box:[-5,-8,700,500]}]},camera).boxes,[[0,0,640,480]]);
console.log('PASS: camera-only overlay bounds, letterbox coordinates, resolution changes and stale clearing');
