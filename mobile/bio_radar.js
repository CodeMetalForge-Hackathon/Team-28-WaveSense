/* Bio Radar: radar candidates drawn in the camera person detector's box language.
   A candidate the camera also sees is drawn on the detector's own box. One the camera cannot see
   gets an orange box of its own, placed from the radar's bearing, with an assumed person size.
   The switch only shows or hides this. The radar keeps running, because restarting it costs a
   relearn of about 25 s and its start-up surge has dropped the USB bus before.
   A box is a place to check, not a confirmed person; no box is not an all-clear. */
globalThis.BioRadar=(()=>{
 // Not measured yet. Until verified, a radar return is not matched to a camera box by angle.
 const CALIBRATION={hfovDeg:70,yawDeg:0,flip:false,verified:false,personHeightM:1.7};
 const STORAGE_KEY='wavesense.bioRadar',STALE_MS=1200,MAX_ITEMS=3,TICK_MS=50,MATCH_DEG=18,PERSON_HOLD_MS=1500;
 const MIN_RANGE_M=.2,MAX_RANGE_M=12,PERSON_ASPECT=.42,ORANGE='#ff8a3c';
 const HEAT='<defs><radialGradient id="bio-heat"><stop offset="0" stop-color="#ffb45a" stop-opacity=".42"/><stop offset=".55" stop-color="#f0602f" stop-opacity=".3"/><stop offset=".85" stop-color="#aa2a64" stop-opacity=".14"/><stop offset="1" stop-color="#aa2a64" stop-opacity="0"/></radialGradient></defs>';
 const finite=Number.isFinite,clamp=(v,a,b)=>Math.max(a,Math.min(b,v)),rad=d=>d*Math.PI/180;
 let enabled=false,current=null,received=0,eased=[],personSeenAt=-Infinity;
 try{enabled=localStorage.getItem(STORAGE_KEY)==='1'}catch{}

 function visibleArea(w,h,view){
  const a=view?.aspect;
  if(view?.fit!=='slice'||!finite(a)||a<=0)return{left:0,right:w,top:0,bottom:h};
  const visibleW=Math.min(w,h*a),visibleH=Math.min(h,w/a);
  return{left:(w-visibleW)/2,right:(w+visibleW)/2,top:(h-visibleH)/2,bottom:(h+visibleH)/2};
 }
 const azimuth=b=>clamp((CALIBRATION.flip?-b:b)+CALIBRATION.yawDeg,-85,85);
 function matchBox(track,boxes,used,w,focal){
  const free=boxes.map((box,i)=>({box,i})).filter(x=>!used.has(x.i));
  if(!free.length)return null;
  if(!CALIBRATION.verified||!finite(track.bearing_deg)){
   const area=({box:[a,b,c,d]})=>(c-a)*(d-b);
   return free.reduce((best,x)=>area(x)>area(best)?x:best);
  }
  const off=({box})=>Math.abs(Math.atan(((box[0]+box[2])/2-w/2)/focal)*180/Math.PI-azimuth(track.bearing_deg));
  const nearest=free.reduce((best,x)=>off(x)<off(best)?x:best);
  return off(nearest)<=MATCH_DEG?nearest:null;
 }
 function radarBox(range,bearing,w,area,focal){
  const areaH=area.bottom-area.top,height=clamp(focal*CALIBRATION.personHeightM/range,areaH*.28,areaH*.72),width=height*PERSON_ASPECT;
  const x=w/2+focal*Math.tan(rad(azimuth(bearing))),edge=x<area.left?'left':x>area.right?'right':null;
  const cx=clamp(x,area.left+width/2,area.right-width/2),cy=(area.top+area.bottom)/2;
  return{box:[cx-width/2,cy-height/2,cx+width/2,cy+height/2],edge};
 }
 function plan(s,view,elapsed=0,personJustSeen=false){
  const radar=s?.radar,evidence=s?.evidence,camera=s?.camera,none=state=>({state,items:[],unknownRange:null});
  if(!radar?.online)return none('offline');
  if(evidence?.state==='paused')return none('paused');
  if(!evidence?.radar_candidate)return none(evidence?.state==='clear'?'clear':'learning');
  if(!camera?.online)return none('no-camera');
  const res=camera.resolution,w=Array.isArray(res)&&res[0]>0?res[0]:640,h=Array.isArray(res)&&res[1]>0?res[1]:480;
  const area=visibleArea(w,h,view),focal=(w/2)/Math.tan(rad(CALIBRATION.hfovDeg/2));
  const boxes=globalThis.PersonOverlay?.plan(s.vision,camera,elapsed)?.boxes||[],used=new Set(),items=[];
  let unknownRange=null,heldRange=null;
  for(const t of (Array.isArray(radar.tracks)?radar.tracks:[]).slice(0,MAX_ITEMS)){
   const range=t?.range_m;
   if(!finite(range)||range<MIN_RANGE_M||range>MAX_RANGE_M)continue;
   const hit=matchBox(t,boxes,used,w,focal);
   if(hit){used.add(hit.i);items.push({kind:'confirmed',box:hit.box,range_m:range})}
   // The detector's boxes expire within half a second; a gap must not spawn a second marker.
   else if(!boxes.length&&personJustSeen)heldRange=heldRange??range;
   else if(finite(t.bearing_deg))items.push({kind:'radar',range_m:range,...radarBox(range,t.bearing_deg,w,area,focal)});
   else if(unknownRange===null)unknownRange=range;
  }
  return{state:items.length||unknownRange!==null||heldRange!==null?'candidate':'learning',width:w,height:h,area,items,unknownRange,heldRange};
 }

 // The radar updates once a second; only its own boxes glide. Camera boxes stay exact.
 function settle(items){
  let n=0;
  const out=items.map(item=>{
   if(item.kind!=='radar')return item;
   const last=eased[n],box=last?item.box.map((v,i)=>last[i]+(v-last[i])*.18):item.box;
   eased[n++]=box;
   return{...item,box};
  });
  eased.length=n;
  return out;
 }
 function corners([x1,y1,x2,y2]){
  const k=Math.min(24,(x2-x1)/4,(y2-y1)/4);
  return [[x1,y1,1,1],[x2,y1,-1,1],[x1,y2,1,-1],[x2,y2,-1,-1]].map(([x,y,sx,sy])=>`M${x+sx*k} ${y}H${x}V${y+sy*k}`).join('');
 }
 function caption(item,area){
  const m='~'+item.range_m.toFixed(1)+' m';
  if(item.kind==='confirmed')return{text:'RADAR '+m,y:Math.max(area.top+18,item.box[1]+18)};
  return{text:(item.edge==='left'?'◀ ':'')+'RADAR ? '+m+(item.edge==='right'?' ▶':''),y:Math.max(area.top+13,item.box[1]-5)};
 }
 function markup(item,area,mini){
  const[x1,y1,x2,y2]=item.box,glow=`<ellipse cx="${(x1+x2)/2}" cy="${(y1+y2)/2}" rx="${(x2-x1)/2}" ry="${(y2-y1)/2}" fill="url(#bio-heat)"/>`;
  const bracket=`<path d="${corners(item.box)}" fill="none" stroke="${ORANGE}" stroke-width="2" vector-effect="non-scaling-stroke"/>`;
  if(mini)return glow+bracket;
  const{text,y}=caption(item,area);
  return glow+bracket+`<text x="${Math.max(area.left+6,x1+6)}" y="${y}" fill="${ORANGE}" stroke="#111" stroke-width="3" paint-order="stroke" font-family="sans-serif" font-size="13">${text}</text>`;
 }

 const stateText={offline:'No signal',paused:'Paused','no-camera':'No camera',learning:'Learning',clear:'Scanning'};
 function paintButton(root,p){
  let button=root.querySelector('.bio-toggle');
  if(!button){
   button=document.createElement('button');button.className='bio-toggle';button.type='button';
   button.innerHTML='<b>Bio Radar</b><small class="bio-state"></small><small class="bio-note"></small>';
   button.onclick=()=>{enabled=!enabled;try{localStorage.setItem(STORAGE_KEY,enabled?'1':'0')}catch{}paint()};
   root.append(button);
  }
  const range=p.items[0]?.range_m??p.heldRange??p.unknownRange,state=!enabled?'Off':finite(range)?'~'+range.toFixed(1)+' m':stateText[p.state]||'Scanning';
  button.setAttribute('aria-pressed',String(enabled));
  button.setAttribute('aria-label','Bio Radar overlay '+(enabled?'on':'off')+'. '+state);
  button.querySelector('.bio-state').textContent=state;
  button.querySelector('.bio-note').textContent=!enabled?'':p.state==='clear'?'not an all-clear':p.items.length||finite(p.heldRange)?'verify':finite(range)?'direction unknown':'';
 }
 function paintShell(shell,now){
  let svg=shell.querySelector('.bio-overlay');
  const mini=!!shell.closest('.mini-swap'),view={fit:mini?'meet':'slice',aspect:shell.clientWidth/shell.clientHeight};
  const p=enabled&&current&&now-received<=STALE_MS?plan(current,view,now-received,now-personSeenAt<PERSON_HOLD_MS):null;
  if(!p||!p.items.length){eased.length=0;svg?.remove();return}
  if(!svg){svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('bio-overlay');svg.setAttribute('aria-hidden','true');shell.append(svg)}
  svg.setAttribute('preserveAspectRatio',mini?'xMidYMid meet':'xMidYMid slice');
  svg.setAttribute('viewBox',`0 0 ${p.width} ${p.height}`);
  svg.innerHTML=HEAT+settle(p.items).map(item=>markup(item,p.area,mini)).join('');
 }
 function paint(){
  if(typeof document==='undefined')return;
  const root=document.querySelector('.phone');if(!root)return;
  const now=performance.now(),shell=root.querySelector('.feed-shell[data-sensor="camera"]');
  const fresh=enabled&&current&&now-received<=STALE_MS;
  if(current&&globalThis.PersonOverlay?.plan(current.vision,current.camera,now-received)?.boxes?.length)personSeenAt=now;
  paintButton(root,fresh?plan(current,{},now-received,now-personSeenAt<PERSON_HOLD_MS):{state:enabled?'offline':undefined,items:[],unknownRange:null});
  if(shell)paintShell(shell,now);
 }
 function update(root,s){if(s!==current){current=s;received=performance.now()}paint()}
 window.setInterval(paint,TICK_MS);
 return{plan,update,CALIBRATION};
})();
