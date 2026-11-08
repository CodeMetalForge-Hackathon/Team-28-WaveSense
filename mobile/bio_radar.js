/* Bio Radar: radar candidates drawn over the camera when the operator turns it on.
   The switch only shows or hides the overlay. The radar keeps running, because restarting it
   costs a relearn of about 25 s and its start-up surge has dropped the USB bus before.
   Radar measures range and at most one azimuth, so height and body shape here are assumed.
   A blob is a place to check, not a confirmed person; no blob is not an all-clear. */
globalThis.BioRadar=(()=>{
 // Not measured yet. flip and yawDeg are set from a person standing at known positions.
 const CALIBRATION={hfovDeg:70,yawDeg:0,flip:false,personHeightM:1.7};
 const STORAGE_KEY='wavesense.bioRadar',STALE_MS=1200,MAX_BLOBS=3,TICK_MS=40;
 const BEARING_ERROR_DEG=8,MIN_RANGE_M=.2,MAX_RANGE_M=12;
 const finite=Number.isFinite,clamp=(v,a,b)=>Math.max(a,Math.min(b,v)),rad=d=>d*Math.PI/180;
 let enabled=false,current=null,received=0,sprites=null,shown=[];
 try{enabled=localStorage.getItem(STORAGE_KEY)==='1'}catch{}

 function visibleArea(w,h,view){
  const a=view?.aspect;
  if(view?.fit!=='slice'||!finite(a)||a<=0)return{left:0,right:w,top:0,bottom:h};
  const visibleW=Math.min(w,h*a),visibleH=Math.min(h,w/a);
  return{left:(w-visibleW)/2,right:(w+visibleW)/2,top:(h-visibleH)/2,bottom:(h+visibleH)/2};
 }
 function blobFor(track,w,h,area,focal){
  const r=track?.range_m;
  if(!finite(r)||r<MIN_RANGE_M||r>MAX_RANGE_M)return null;
  const areaW=area.right-area.left,areaH=area.bottom-area.top,cy=(area.top+area.bottom)/2,b=track.bearing_deg;
  if(!finite(b))return{kind:'unknown',cx:(area.left+area.right)/2,cy,rx:areaW*.48,ry:areaH*.2,range_m:r,edge:null};
  const azimuth=clamp((CALIBRATION.flip?-b:b)+CALIBRATION.yawDeg,-85,85),x=w/2+focal*Math.tan(rad(azimuth));
  const personH=focal*CALIBRATION.personHeightM/r,ry=clamp(personH/2,areaH*.14,areaH*.36);
  const rx=Math.max(ry*.45,focal*Math.tan(rad(BEARING_ERROR_DEG)));
  const edge=x<area.left?'left':x>area.right?'right':null;
  return{kind:'located',cx:clamp(x,area.left,area.right),cy,rx,ry,range_m:r,edge};
 }
 function plan(s,view){
  const radar=s?.radar,evidence=s?.evidence,camera=s?.camera;
  if(!radar?.online)return{state:'offline',blobs:[]};
  if(evidence?.state==='paused')return{state:'paused',blobs:[]};
  if(!evidence?.radar_candidate)return{state:evidence?.state==='clear'?'clear':'learning',blobs:[]};
  if(!camera?.online)return{state:'no-camera',blobs:[]};
  const res=camera.resolution,w=Array.isArray(res)&&res[0]>0?res[0]:640,h=Array.isArray(res)&&res[1]>0?res[1]:480;
  const area=visibleArea(w,h,view),focal=(w/2)/Math.tan(rad(CALIBRATION.hfovDeg/2));
  const blobs=(Array.isArray(radar.tracks)?radar.tracks:[]).slice(0,MAX_BLOBS).map(t=>blobFor(t,w,h,area,focal)).filter(Boolean);
  return{state:blobs.length?'candidate':'learning',width:w,height:h,area,blobs};
 }

 // Magma ramp from a deep magenta rim to a pale core, so it reads on dark and bright scenes.
 const RAMP=[[0,[176,36,92]],[.3,[232,84,42]],[.65,[255,150,60]],[1,[255,204,120]]];
 function ramp(t){for(let i=1;i<RAMP.length;i++){const[p,a]=RAMP[i-1],[q,b]=RAMP[i];if(t<=q){const k=(t-p)/(q-p);return a.map((v,c)=>v+(b[c]-v)*k)}}return RAMP[RAMP.length-1][1]}
 function smooth(a,b,v){const t=clamp((v-a)/(b-a),0,1);return t*t*(3-2*t)}
 const gauss=(a,b)=>Math.exp(-(a*a+b*b));
 function paintSprite(W,H,field){
  const c=document.createElement('canvas');c.width=W;c.height=H;
  const g=c.getContext('2d'),image=g.createImageData(W,H);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){
   const f=field((x+.5)/W*2-1,(y+.5)/H*2-1),t=clamp((f-.16)/.84,0,1),rim=Math.exp(-(((f-.22)/.05)**2)),[r,gr,b]=ramp(t),i=(y*W+x)*4;
   image.data[i]=clamp(r+rim*40,0,255);image.data[i+1]=clamp(gr+rim*46,0,255);image.data[i+2]=b;
   image.data[i+3]=255*smooth(.16,.26,f)*Math.min(.72,.28+.26*t+rim*.42);
  }
  g.putImageData(image,0,0);return c.toDataURL();
 }
 function loadSprites(){sprites=sprites||{
  // A soft upright mass: the radar measures no pose, so it has no limbs to imply one.
  located:paintSprite(120,240,(u,v)=>Math.max(gauss(u/.36,(v+.66)/.21),gauss(u/.56,(v-.06)/.62))),
  unknown:paintSprite(240,90,(u,v)=>gauss(u/.92,v/.62))};return sprites}

 function words(b){const m='~'+b.range_m.toFixed(1)+' m';return b.kind==='unknown'?['Direction unknown',m]:['Possible person',(b.edge==='left'?'◀ ':'')+m+(b.edge==='right'?' ▶':'')]}
 const stateText={offline:'No signal',paused:'Paused','no-camera':'No camera',learning:'Learning',clear:'Scanning'};
 function paintButton(root,p){
  let button=root.querySelector('.bio-toggle');
  if(!button){
   button=document.createElement('button');button.className='bio-toggle';button.type='button';
   button.innerHTML='<b>Bio Radar</b><small class="bio-state"></small><small class="bio-note"></small>';
   button.onclick=()=>{enabled=!enabled;try{localStorage.setItem(STORAGE_KEY,enabled?'1':'0')}catch{}paint()};
   root.append(button);
  }
  const candidate=p.blobs[0],state=!enabled?'Off':candidate?'~'+candidate.range_m.toFixed(1)+' m':stateText[p.state]||'Scanning';
  button.setAttribute('aria-pressed',String(enabled));
  button.setAttribute('aria-label','Bio Radar overlay '+(enabled?'on':'off')+'. '+state);
  button.querySelector('.bio-state').textContent=state;
  button.querySelector('.bio-note').textContent=enabled&&p.state==='clear'?'not an all-clear':enabled&&candidate?'verify':'';
 }
 function ease(from,to){return from===undefined?to:from+(to-from)*.11}
 function svgNode(name,parent){const n=document.createElementNS('http://www.w3.org/2000/svg',name);parent.append(n);return n}
 function paintShell(shell,now){
  let svg=shell.querySelector('.bio-overlay');
  const mini=!!shell.closest('.mini-swap'),view={fit:mini?'meet':'slice',aspect:shell.clientWidth/shell.clientHeight};
  const fresh=enabled&&current&&now-received<=STALE_MS,p=fresh?plan(current,view):{blobs:[]};
  shown.forEach(s=>{s.seen=false});
  p.blobs.forEach((b,i)=>{const s=shown[i]||(shown[i]={o:0});Object.assign(s,{seen:true,kind:b.kind,edge:b.edge,range_m:b.range_m,cx:ease(s.cx,b.cx),cy:ease(s.cy,b.cy),rx:ease(s.rx,b.rx),ry:ease(s.ry,b.ry),area:p.area,width:p.width,height:p.height})});
  shown.forEach(s=>{s.o+=((s.seen?1:0)-s.o)*(s.seen?.14:.2)});
  shown=shown.filter(s=>s.seen||s.o>.02);
  if(!shown.length){svg?.remove();return}
  if(!svg){svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('bio-overlay');svg.setAttribute('aria-hidden','true');shell.append(svg)}
  svg.setAttribute('preserveAspectRatio',mini?'xMidYMid meet':'xMidYMid slice');
  svg.setAttribute('viewBox','0 0 '+shown[0].width+' '+shown[0].height);
  const art=loadSprites(),reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,pulse=reduced?.9:.86+.14*Math.sin(now/350);
  while(svg.children.length>shown.length*2)svg.lastChild.remove();
  while(svg.children.length<shown.length*2){svgNode('image',svg);const t=svgNode('text',svg);t.classList.add('bio-label');svgNode('tspan',t);svgNode('tspan',t).setAttribute('dy','1.2em')}
  shown.forEach((s,i)=>{
   const image=svg.children[i*2],text=svg.children[i*2+1],href=art[s.kind];
   if(image.getAttribute('href')!==href)image.setAttribute('href',href);
   image.setAttribute('preserveAspectRatio','none');
   image.setAttribute('x',s.cx-s.rx);image.setAttribute('y',s.cy-s.ry);image.setAttribute('width',2*s.rx);image.setAttribute('height',2*s.ry);
   image.setAttribute('opacity',(s.o*pulse*(s.kind==='unknown'?.5:1)).toFixed(3));
   // On the blob's own core: the corners of the view belong to the map, button and sticks.
   const[first,second]=mini?['','']:words(s),x=clamp(s.cx,s.area.left+58,s.area.right-58);
   text.setAttribute('y',s.cy);
   [first,second].forEach((line,n)=>{const span=text.children[n];span.textContent=line;span.setAttribute('x',x)});
   text.setAttribute('opacity',s.o.toFixed(3));
  });
 }
 function paint(){
  if(typeof document==='undefined')return;
  const root=document.querySelector('.phone');if(!root)return;
  const now=performance.now(),shell=root.querySelector('.feed-shell[data-sensor="camera"]');
  paintButton(root,enabled&&current&&now-received<=STALE_MS?plan(current,{}):enabled?{state:'offline',blobs:[]}:{blobs:[]});
  if(shell)paintShell(shell,now);
 }
 function update(root,s){if(s!==current){current=s;received=performance.now()}paint()}
 window.setInterval(paint,TICK_MS);
 return{plan,update,CALIBRATION};
})();
