/* Normalized two-axis joystick, independent of the transport adapter. */
globalThis.DriveMath=Object.freeze({
 vector(x,y){const length=Math.hypot(x,y);if(length<0.12)return {x:0,y:0};return {x:x/Math.max(1,length),y:y/Math.max(1,length)}},
 motion(move,turn,limit,latched){return latched?{vx:0,vy:0,wz:0}:{vx:-move.y*limit,vy:-move.x*limit,wz:-turn.x*limit}},
 command(v,limit,latched){return latched?{linear:0,angular:0}:{linear:-v.y*limit,angular:-v.x*limit}}
});
