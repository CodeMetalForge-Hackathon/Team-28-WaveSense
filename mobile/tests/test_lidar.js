const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
global.window={addEventListener:()=>{},setInterval:()=>{}};
vm.runInThisContext(fs.readFileSync(require('path').join(__dirname,'../lidar.js'),'utf8'),{filename:'lidar.js'});

const p=LidarView.worldPoint([400,399,0],[0,1000],25);assert.deepEqual(p,[440,399]);
const left=LidarView.worldPoint([400,399,0],[90,1000],25);assert(Math.abs(left[0]-400)<1e-6);assert.equal(left[1],359);
const turned=LidarView.worldPoint([400,399,90],[0,1000],25);assert.equal(turned[1],359);
const live={online:true,proximity:{stale:false,sectors:{front:{nearest_mm:250,level:'close'}}}};
assert.equal(LidarView.distances(live).front.level,'close');live.online=false;assert.equal(LidarView.distances(live),null);live.online=true;live.proximity.stale=true;assert.equal(LidarView.distances(live),null);
assert(!LidarView.render().includes('<button'),'preview must not nest buttons');
console.log('PASS: world projection, sideways and rotated scans, stale ranges hidden, valid preview markup');
