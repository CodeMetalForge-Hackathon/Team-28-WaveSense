/* Finger displacement and maximum command scaling, shared by both sticks. */
globalThis.DriveMath=Object.freeze({
 vector(x,y){const length=Math.hypot(x,y);if(length<0.06)return{x:0,y:0};return{x:x/Math.max(1,length),y:y/Math.max(1,length)}},
 speedLimit(percent){const n=Number(percent);return .35*Math.max(0,Math.min(100,Number.isFinite(n)?n:50))/100},
 motion(move,turn,limit,latched){
  if(latched)return{vx:0,vy:0,wz:0};
  const vx=-move.y,vy=-move.x,wz=-turn.x;
  // Bound the combined wheel mix, including diagonal movement plus turning.
  const scale=Math.max(1,Math.abs(vx)+Math.abs(vy)+Math.abs(wz));
  return{vx:vx*limit/scale,vy:vy*limit/scale,wz:wz*limit/scale};
 },
 command(v,limit,latched){return latched?{linear:0,angular:0}:{linear:-v.y*limit,angular:-v.x*limit}}
});
