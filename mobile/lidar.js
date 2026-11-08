/* Shared live SLAM canvas: robot-forward is always up; map and trail move beneath it. */
globalThis.LidarView=(()=>{
 const valid=Number.isFinite,rad=a=>a*Math.PI/180;
 let mode='map',follow=true,pan={x:0,y:0},fitted=false;
 let receivedAt=0,state=null,range=8,image=null,loadedRevision=null,loadingRevision=null;
 const slots=[['front','↑'],['left','←'],['right','→'],['back','↓']];
 function distances(s){const p=s?.proximity;if(!s?.online||!p||p.stale)return null;return Object.fromEntries(slots.map(([key])=>{const sector=p.sectors?.[key],mm=sector?.nearest_mm;return[key,{mm:valid(mm)?mm:null,level:sector?.level||'unknown'}]}))}
 function worldPoint(pose,point,mmPerPx){const a=rad(pose[2]+point[0]);return[pose[0]+Math.cos(a)*point[1]/mmPerPx,pose[1]-Math.sin(a)*point[1]/mmPerPx]}
 function points(s){return(s?.points||[]).filter(p=>p.length>=2&&valid(p[0])&&valid(p[1])&&p[1]>0&&p[1]<=12000)}
 function fitRange(s){const d=points(s).map(p=>p[1]).sort((a,b)=>a-b);return d.length?Math.max(4,Math.min(24,d[Math.floor(d.length*.9)]/1000*2.4)):8}
 function render(){return '<div class="lidar-panel"><canvas class="lidar-canvas" role="img" aria-label="Live local map, robot heading up"></canvas><span class="map-state">Mapping</span><div class="lidar-readings"></div><span class="map-scale"></span><span class="map-note">Distances from sensor</span></div>'}
 function readings(shell,s){const d=distances(s),container=shell.querySelector('.lidar-readings');if(!container)return;container.innerHTML=slots.map(([key,arrow])=>{const v=d?.[key],text=v?.mm!=null?(v.mm/1000).toFixed(2)+' m':'—';return '<span class="range-reading '+key+' '+(v?.level==='close'?'close':v?.level==='near'?'near':'')+'" title="'+key+' distance from LiDAR centre"><i>'+arrow+'</i>'+text+'</span>'}).join('')}
 function draw(canvas,s){const box=canvas.getBoundingClientRect();if(!box.width||!box.height)return;const ratio=Math.min(window.devicePixelRatio||1,2),w=box.width,h=box.height;canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);const c=canvas.getContext('2d');if(!c)return;c.scale(ratio,ratio);c.fillStyle='#080808';c.fillRect(0,0,w,h);
  const compact=!!canvas.closest('.sensor-preview'),dx=compact?0:pan.x,dy=compact?0:pan.y;
  const m=s?.mapping,pose=m?.pose_px,canMap=!!(mode==='map'&&m?.available&&pose?.length===3&&pose.every(valid)&&m.mm_per_px>0&&image),fresh=!!(s?.online&&m?.online),pxPerMm=Math.min(w,h)/(range*1000);
  c.save();c.translate(w/2+dx,h/2+dy);
  if(canMap){const scale=pxPerMm*m.mm_per_px;c.scale(scale,scale);c.rotate(rad(pose[2]-90));c.translate(-pose[0],-pose[1]);c.imageSmoothingEnabled=false;c.drawImage(image,0,0,m.width,m.height);
   c.lineWidth=.6/scale;c.strokeStyle='#ffffff0c';c.beginPath();const grid=1000/m.mm_per_px;for(let p=0;p<=Math.max(m.width,m.height);p+=grid){c.moveTo(p,0);c.lineTo(p,m.height);c.moveTo(0,p);c.lineTo(m.width,p)}c.stroke();
   c.strokeStyle='#aaa';c.lineWidth=1.25/scale;c.setLineDash([2/scale,4/scale]);c.beginPath();(m.trail_px||[]).filter(p=>p.length===2&&p.every(valid)).forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.stroke();c.setLineDash([]);
   if(fresh){c.fillStyle='#fff';for(const point of points(s)){const p=worldPoint(pose,point,m.mm_per_px);c.beginPath();c.arc(p[0],p[1],1.4/scale,0,Math.PI*2);c.fill()}}
  }else{c.strokeStyle='#292929';c.lineWidth=.7;for(let mm=1000;mm<=range*500;mm+=1000){c.beginPath();c.arc(0,0,mm*pxPerMm,0,Math.PI*2);c.stroke()}if(s?.online){c.fillStyle='#eee';for(const[b,d]of points(s)){c.beginPath();c.arc(-Math.sin(rad(b))*d*pxPerMm,-Math.cos(rad(b))*d*pxPerMm,1.5,0,Math.PI*2);c.fill()}}}
  c.restore();c.save();c.translate(w/2+dx,h/2+dy);c.strokeStyle=fresh||(!m?.available&&s?.online)?'#fff':'#777';c.fillStyle='#111';c.lineWidth=1.5;c.beginPath();c.arc(0,0,13,0,Math.PI*2);c.fill();c.stroke();c.fillStyle=c.strokeStyle;c.beginPath();c.moveTo(0,-8);c.lineTo(-5,5);c.lineTo(0,2);c.lineTo(5,5);c.closePath();c.fill();c.restore();
  const shell=canvas.parentElement,tag=shell.querySelector('.map-state');tag.textContent=!s?.online?'Scan lost':m?.available?(m.online?(mode==='map'?'Local estimate':'Live scan'):'Tracking lost'):'Live scan';shell.classList.toggle('map-stale',!s?.online||!!m?.available&&!m.online);
  const metres=range<=4?.5:range<=16?1:2,bar=shell.querySelector('.map-scale');bar.style.setProperty('--bar',metres*1000*pxPerMm+'px');bar.textContent=metres+' m';shell.querySelector('.map-note').textContent=s?.proximity?.direction_verified===false?'Sensor distances · L/R unverified':'Distances from sensor';readings(shell,s);
 }
 function drawAll(){document.querySelectorAll('.map-tools [data-action]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.action===mode||(b.dataset.action==='follow'&&follow))));const stale=state&&Date.now()-receivedAt+(state.age_ms||0)>750;const current=stale?{...state,online:false,proximity:{stale:true},mapping:{...state.mapping,online:false}}:state;document.querySelectorAll('.lidar-canvas').forEach(c=>draw(c,current));document.querySelectorAll('.proximity-hud').forEach(h=>readings(h,current))}
 function update(root,s){if(state!==s)receivedAt=Date.now();state=s;const m=s?.mapping;if(m?.available&&m.revision!==loadedRevision&&m.revision!==loadingRevision){const rev=m.revision;loadingRevision=rev;const next=new Image();next.onload=()=>{if(loadingRevision!==rev)return;image=next;loadedRevision=rev;loadingRevision=null;drawAll()};next.onerror=()=>{if(loadingRevision===rev)loadingRevision=null};next.src='/api/lidar-map.png?rev='+encodeURIComponent(rev)}
  if(s?.online&&!fitted&&points(s).length){range=fitRange(s);fitted=true}
  root.querySelectorAll('.lidar-panel').forEach(shell=>{if(!shell.closest('.sensor-preview')&&!shell.querySelector('.map-tools')){
    const tools=document.createElement('div');tools.className='map-tools';tools.innerHTML='<button data-action="map" aria-label="Accumulated room map">Map</button><button data-action="scan" aria-label="Current obstacle scan">Scan</button><button data-action="follow" aria-label="Recenter on robot">⌖</button><button data-action="out" aria-label="Zoom map out">−</button><button data-action="in" aria-label="Zoom map in">+</button>';
    tools.querySelectorAll('button').forEach(b=>b.onclick=e=>{e.preventDefault();e.stopPropagation();const action=b.dataset.action;if(action==='map'||action==='scan')mode=action;else if(action==='follow'){follow=true;pan={x:0,y:0};range=fitRange(state)}else range=Math.max(2,Math.min(32,range*(action==='in'?.5:2)));drawAll()});shell.appendChild(tools);
    const canvas=shell.querySelector('canvas'),fingers=new Map();let lastDistance=0;
    canvas.addEventListener('pointerdown',e=>{e.preventDefault();fingers.set(e.pointerId,{x:e.clientX,y:e.clientY});canvas.setPointerCapture(e.pointerId);lastDistance=0});
    canvas.addEventListener('pointermove',e=>{const previous=fingers.get(e.pointerId);if(!previous)return;e.preventDefault();fingers.set(e.pointerId,{x:e.clientX,y:e.clientY});if(fingers.size===1){pan.x+=e.clientX-previous.x;pan.y+=e.clientY-previous.y;follow=false}else{const[a,b]=[...fingers.values()];const distance=Math.hypot(a.x-b.x,a.y-b.y);if(lastDistance>0&&distance>5)range=Math.max(2,Math.min(32,range*lastDistance/distance));lastDistance=distance}drawAll()});
    const release=e=>{fingers.delete(e.pointerId);lastDistance=0};canvas.addEventListener('pointerup',release);canvas.addEventListener('pointercancel',release);canvas.addEventListener('lostpointercapture',release);
  }});
  const tag=root.dataset.view==='lidar'?root.querySelector('.viewport .demo-tag'):null;if(tag)tag.textContent=s?.online?'LIVE':'RECONNECTING';drawAll();
 }
 window.addEventListener('resize',drawAll);window.setInterval(()=>{if(state&&Date.now()-receivedAt+(state.age_ms||0)>750)drawAll()},250);
 return{render,update,worldPoint,distances};
})();
