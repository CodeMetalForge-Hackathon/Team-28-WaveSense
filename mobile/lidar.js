/* Shared live SLAM canvas: robot-forward is always up; map and trail move beneath it. */
globalThis.LidarView=(()=>{
 const valid=Number.isFinite,rad=a=>a*Math.PI/180;
 let receivedAt=0,state=null,range=8,image=null,loadedRevision=null,loadingRevision=null;
 const slots=[['front','↑'],['left','←'],['right','→'],['back','↓']];
 function distances(s){const p=s?.proximity;if(!s?.online||!p||p.stale)return null;return Object.fromEntries(slots.map(([key])=>{const sector=p.sectors?.[key],mm=sector?.nearest_mm;return[key,{mm:valid(mm)?mm:null,level:sector?.level||'unknown'}]}))}
 function worldPoint(pose,point,mmPerPx){const a=rad(pose[2]+point[0]);return[pose[0]+Math.cos(a)*point[1]/mmPerPx,pose[1]-Math.sin(a)*point[1]/mmPerPx]}
 function points(s){return(s?.points||[]).filter(p=>p.length>=2&&valid(p[0])&&valid(p[1])&&p[1]>0&&p[1]<=12000)}
 function render(){return '<div class="lidar-panel"><canvas class="lidar-canvas" role="img" aria-label="Live local map, robot heading up"></canvas><span class="map-state">Mapping</span><div class="lidar-readings"></div><span class="map-scale"></span><span class="map-note">Distances from sensor</span></div>'}
 function readings(shell,s){const d=distances(s),container=shell.querySelector('.lidar-readings');if(!container)return;container.innerHTML=slots.map(([key,arrow])=>{const v=d?.[key],text=v?.mm!=null?(v.mm/1000).toFixed(2)+' m':'—';return '<span class="range-reading '+key+' '+(v?.level==='close'?'close':v?.level==='near'?'near':'')+'" title="'+key+' distance from LiDAR centre"><i>'+arrow+'</i>'+text+'</span>'}).join('')}
 function draw(canvas,s){const box=canvas.getBoundingClientRect();if(!box.width||!box.height)return;const ratio=Math.min(window.devicePixelRatio||1,2),w=box.width,h=box.height;canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);const c=canvas.getContext('2d');if(!c)return;c.scale(ratio,ratio);c.fillStyle='#080808';c.fillRect(0,0,w,h);
  const m=s?.mapping,pose=m?.pose_px,canMap=!!(m?.available&&pose?.length===3&&pose.every(valid)&&m.mm_per_px>0&&image),fresh=!!(s?.online&&m?.online),pxPerMm=Math.min(w,h)/(range*1000);
  c.save();c.translate(w/2,h/2);
  if(canMap){const scale=pxPerMm*m.mm_per_px;c.scale(scale,scale);c.rotate(rad(pose[2]-90));c.translate(-pose[0],-pose[1]);c.imageSmoothingEnabled=false;c.drawImage(image,0,0,m.width,m.height);
   c.lineWidth=.6/scale;c.strokeStyle='#ffffff0c';c.beginPath();const grid=1000/m.mm_per_px;for(let p=0;p<=Math.max(m.width,m.height);p+=grid){c.moveTo(p,0);c.lineTo(p,m.height);c.moveTo(0,p);c.lineTo(m.width,p)}c.stroke();
   c.strokeStyle='#aaa';c.lineWidth=1.25/scale;c.setLineDash([2/scale,4/scale]);c.beginPath();(m.trail_px||[]).filter(p=>p.length===2&&p.every(valid)).forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.stroke();c.setLineDash([]);
   if(fresh){c.fillStyle='#fff';for(const point of points(s)){const p=worldPoint(pose,point,m.mm_per_px);c.beginPath();c.arc(p[0],p[1],1.4/scale,0,Math.PI*2);c.fill()}}
  }else{c.strokeStyle='#292929';c.lineWidth=.7;for(let mm=1000;mm<=range*500;mm+=1000){c.beginPath();c.arc(0,0,mm*pxPerMm,0,Math.PI*2);c.stroke()}if(s?.online){c.fillStyle='#eee';for(const[b,d]of points(s)){c.beginPath();c.arc(-Math.sin(rad(b))*d*pxPerMm,-Math.cos(rad(b))*d*pxPerMm,1.5,0,Math.PI*2);c.fill()}}}
  c.restore();c.save();c.translate(w/2,h/2);c.strokeStyle=fresh||(!m?.available&&s?.online)?'#fff':'#777';c.fillStyle='#111';c.lineWidth=1.5;c.beginPath();c.arc(0,0,13,0,Math.PI*2);c.fill();c.stroke();c.fillStyle=c.strokeStyle;c.beginPath();c.moveTo(0,-8);c.lineTo(-5,5);c.lineTo(0,2);c.lineTo(5,5);c.closePath();c.fill();c.restore();
  const shell=canvas.parentElement,tag=shell.querySelector('.map-state');tag.textContent=!s?.online?'Scan lost':m?.available?(m.online?'Local map':'Tracking lost'):'Live scan · mapping…';shell.classList.toggle('map-stale',!s?.online||!!m?.available&&!m.online);
  const metres=range<=4?.5:range<=16?1:2,bar=shell.querySelector('.map-scale');bar.style.setProperty('--bar',metres*1000*pxPerMm+'px');bar.textContent=metres+' m';shell.querySelector('.map-note').textContent=s?.proximity?.direction_verified===false?'Sensor distances · L/R unverified':'Distances from sensor';readings(shell,s);
 }
 function drawAll(){const stale=state&&Date.now()-receivedAt+(state.age_ms||0)>750;const current=stale?{...state,online:false,proximity:{stale:true},mapping:{...state.mapping,online:false}}:state;document.querySelectorAll('.lidar-canvas').forEach(c=>draw(c,current));document.querySelectorAll('.proximity-hud').forEach(h=>readings(h,current))}
 function update(root,s){if(state!==s)receivedAt=Date.now();state=s;const m=s?.mapping;if(m?.available&&m.revision!==loadedRevision&&m.revision!==loadingRevision){const rev=m.revision;loadingRevision=rev;const next=new Image();next.onload=()=>{if(loadingRevision!==rev)return;image=next;loadedRevision=rev;loadingRevision=null;drawAll()};next.onerror=()=>{if(loadingRevision===rev)loadingRevision=null};next.src='/api/lidar-map.png?rev='+encodeURIComponent(rev)}
  root.querySelectorAll('.lidar-panel').forEach(shell=>{if(!shell.closest('.sensor-preview')&&!shell.querySelector('.map-tools')){const tools=document.createElement('div');tools.className='map-tools';tools.innerHTML='<button aria-label="Zoom map out">−</button><button aria-label="Zoom map in">+</button>';tools.querySelectorAll('button').forEach((b,i)=>b.onclick=e=>{e.preventDefault();e.stopPropagation();range=Math.max(2,Math.min(32,range*(i?.5:2)));drawAll()});shell.appendChild(tools)}});
  const tag=root.dataset.view==='lidar'?root.querySelector('.viewport .demo-tag'):null;if(tag)tag.textContent=s?.online?'LIVE':'RECONNECTING';drawAll();
 }
 window.addEventListener('resize',drawAll);window.setInterval(()=>{if(state&&Date.now()-receivedAt+(state.age_ms||0)>750)drawAll()},250);
 return{render,update,worldPoint,distances};
})();
