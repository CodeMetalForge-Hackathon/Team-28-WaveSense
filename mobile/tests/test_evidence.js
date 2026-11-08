const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');global.window={setInterval:()=>{}};
vm.runInThisContext(fs.readFileSync(require('path').join(__dirname,'../evidence.js'),'utf8'));
const s={radar:{online:true},evidence:{radar_candidate:true,ranges_m:[1.5],visible_person:false}};
assert(SensorEvidence.presentation(s).detail.includes('direction unknown'));assert(!SensorEvidence.radarView(s).includes('person-box'));assert(SensorEvidence.radarView(s).includes('no position'));
s.evidence.visible_person=true;assert(SensorEvidence.presentation(s).detail.includes('association unknown'));s.evidence.state='paused';assert.equal(SensorEvidence.presentation(s).kind,'paused');s.radar.online=false;s.evidence.state='candidate';assert.equal(SensorEvidence.presentation(s).kind,'offline');console.log('PASS: range-only evidence, no phantom boxes, no unsupported camera-radar association');
