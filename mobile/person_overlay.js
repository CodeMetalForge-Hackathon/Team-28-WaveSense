/* Camera-only detections over the full-rate video. Never projects radar returns. */
globalThis.PersonOverlay=(()=>{
 const ttl=450;
 let packet=null,received=0,enabled=false;
 function plan(v,camera,elapsed=0){
  const age=v?.age_ms+elapsed,w=v?.width,h=v?.height;
  if(v?.source!=='camera'||!v?.online||!camera?.online||!Number.isFinite(v.age_ms)||!Number.isFinite(age)||age<0||age>=ttl||!Number.isInteger(w)||!Number.isInteger(h)||w<1||h<1||w>8192||h>8192)return null;
  if(camera.resolution&&(camera.resolution[0]!==w||camera.resolution[1]!==h))return null;
  const boxes=[];
  for(const d of Array.isArray(v.boxes)?v.boxes.slice(0,16):[]){
   if(!Array.isArray(d.box)||d.box.length!==4||!d.box.every(Number.isFinite))continue;
   const [a,b,c,e]=d.box,x1=Math.max(0,Math.min(w,a)),y1=Math.max(0,Math.min(h,b)),x2=Math.max(0,Math.min(w,c)),y2=Math.max(0,Math.min(h,e));
   if(x2>x1&&y2>y1)boxes.push([x1,y1,x2,y2]);
  }
  return {width:w,height:h,boxes};
 }
 function paint(){
  document.querySelectorAll('.feed-shell[data-sensor="camera"]').forEach(shell=>{
   let svg=shell.querySelector('.person-overlay');
   const p=enabled&&packet?plan(packet.vision,packet.camera,performance.now()-received):null;
   if(!p||!p.boxes.length){svg?.remove();return}
   if(!svg){svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('person-overlay');svg.setAttribute('aria-label','Recent camera person detections; positions may briefly lag video');svg.setAttribute('preserveAspectRatio','xMidYMid meet');shell.append(svg)}
   svg.setAttribute('viewBox',`0 0 ${p.width} ${p.height}`);
   svg.innerHTML=p.boxes.map(([x1,y1,x2,y2])=>{const k=Math.min(24,(x2-x1)/4,(y2-y1)/4);const path=[[x1,y1,1,1],[x2,y1,-1,1],[x1,y2,1,-1],[x2,y2,-1,-1]].map(([x,y,sx,sy])=>`M${x+sx*k} ${y}H${x}V${y+sy*k}`).join('');return `<path d="${path}" fill="none" stroke="white" stroke-width="2" vector-effect="non-scaling-stroke"/><text x="${x1+3}" y="${Math.max(13,y1-5)}" fill="white" stroke="#111" stroke-width="3" paint-order="stroke" font-family="sans-serif" font-size="13">CAM person</text>`}).join('');
  });
 }
 function update(s,on){if(packet!==s){packet=s;received=performance.now()}enabled=on;paint()}
 window.setInterval(paint,50);
 return {plan,update};
})();
