/* Range evidence and scene context stay separate until spatial registration is measured. */
globalThis.SensorEvidence=(()=>{
 let received=0,current=null,lastSample=0,history=[];
 const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const finite=Number.isFinite;
 function presentation(s){const e=s?.evidence,r=s?.radar;if(e?.state==='paused')return{kind:'paused',title:'Ⅱ',detail:'Wait after movement'};if(!r?.online)return{kind:'offline',title:'×',detail:'Radar offline'};if(e?.radar_candidate){const range=e.ranges_m?.length?'~'+e.ranges_m[0].toFixed(1)+' m':'—';return{kind:'candidate',title:range,detail:e.visible_person?'Camera person also visible · association unknown':'Camera unconfirmed · direction unknown'}}return{kind:'quiet',title:r.state==='clear'?'—':'⋯',detail:r.state==='clear'?'No candidate · not an all-clear':'Learning · keep robot still'}}
 function rangePlot(s){
  const ranges=s?.evidence?.ranges_m||[],r=ranges.find(finite),scene=s?.radar?.scene;
  const max=Math.max(6,finite(r)?Math.ceil((r+1)/2)*2:6);let line='';
  if(Array.isArray(scene?.ranges_m)&&Array.isArray(scene?.profile_db)&&scene.ranges_m.length===scene.profile_db.length){
   const points=scene.ranges_m.map((d,i)=>[d,scene.profile_db[i]]).filter(([d,v])=>finite(d)&&finite(v)&&d>=0&&d<=max).map(([d,v])=>(20+d/max*280)+','+(80-Math.max(0,Math.min(60,v+60))/60*60));
   if(points.length>1)line='<polyline points="'+points.join(' ')+'" fill="none" stroke="#aaa" stroke-width="1.5"/>';
  }
  const x=finite(r)?20+r/max*280:null;
  return '<svg viewBox="0 0 320 110" role="img" aria-label="Radar distance only; direction unknown"><path d="M20 80H300" stroke="#555"/>'+line+[0,.333,.667,1].map(f=>'<path d="M'+(20+280*f)+' 80v6" stroke="#777"/><text x="'+(20+280*f)+'" y="102" text-anchor="middle" fill="#aaa" font-size="10">'+(max*f).toFixed(f===0||f===1?0:1)+' m</text>').join('')+(x!==null?'<rect x="'+Math.max(20,x-.3/max*280)+'" y="12" width="'+(.6/max*280)+'" height="68" fill="#e2ba74" opacity=".2"/><path d="M'+x+' 12V80" stroke="#e2ba74" stroke-width="2"/>':'')+'</svg>';
 }
 function anglePlot(s){const scene=s?.radar?.scene;if(!scene?.angle_available||!Array.isArray(scene.angle_power_u8))return null;
  const angles=scene.angles_deg,ranges=scene.ranges_m,power=scene.angle_power_u8;if(!Array.isArray(angles)||!Array.isArray(ranges)||!angles.length||!ranges.length)return null;
  const min=angles[0],max=angles[angles.length-1],far=ranges[ranges.length-1];let cells='';
  for(let y=0;y<ranges.length;y++)for(let x=0;x<angles.length;x++){const v=power[y]?.[x];if(!finite(v)||v<1)continue;const c=Math.round(15+v*.8);cells+='<rect x="'+(20+x/angles.length*280)+'" y="'+(8+y/ranges.length*72)+'" width="'+(280/angles.length+.2)+'" height="'+(72/ranges.length+.2)+'" fill="rgb('+c+','+c+','+c+')"/>'}
  const a=scene.target_angle_deg,r=scene.target_range_m;if(finite(a)&&finite(r))cells+='<circle cx="'+(20+(a-min)/(max-min)*280)+'" cy="'+(8+r/far*72)+'" r="7" fill="none" stroke="#e2ba74" stroke-width="2"/>';
  return '<svg viewBox="0 0 320 110" role="img" aria-label="Measured radar returns in sensor angle and range; mounting unverified">'+cells+'<text x="20" y="101" fill="#aaa" font-size="10">'+min+'°</text><text x="160" y="101" text-anchor="middle" fill="#aaa" font-size="10">0°</text><text x="300" y="101" text-anchor="end" fill="#aaa" font-size="10">+'+max+'°</text><text x="3" y="78" fill="#aaa" font-size="9">'+Math.round(far)+'m</text></svg>';
 }
 function detail(s){const p=presentation(s),e=s?.evidence,v=s?.vision,l=s?.lidar,front=l?.online?l?.proximity?.sectors?.front?.nearest_mm:null;
  const people=v?.online&&Array.isArray(v.boxes)?v.boxes.length:null;
  const hist=history.map((x,i)=>'<rect x="'+(i*5)+'" y="'+(x===null?14:x?2:20)+'" width="4" height="'+(x===null?3:x?22:4)+'" fill="'+(x?'#e2ba74':'#555')+'"/>').join('');
  return '<div class="radar-detail-top"><span>◎</span><b>'+esc(p.title)+'</b><button aria-label="Close radar evidence">×</button></div><div class="radar-verdict">'+(p.kind==='candidate'?(s?.radar?.signal==='motion'?'Motion · possible person':s?.radar?.signal==='micro_motion'?'Micro-motion · possible person':'Possible person · unconfirmed'):esc(p.detail))+'</div>'+(anglePlot(s)||rangePlot(s))+'<div class="radar-limits">'+(s?.radar?.scene?.angle_available?'Sensor angle · unverified':'Direction unknown')+'</div><div class="radar-context"><span title="Camera person detections, independent of radar">◉ '+(people===null?'—':people)+'</span><span title="LiDAR front distance from scanner; mounting alignment unverified">⌖ '+(finite(front)?(front/1000).toFixed(1)+' m':'—')+'</span></div><svg class="radar-history" viewBox="0 0 300 26" aria-label="Last 60 seconds: amber indicates radar candidates, not confirmed people">'+hist+'</svg><small>60 s · '+(finite(s?.radar?.watch_range_m)?'0.3–'+s.radar.watch_range_m+' m · ':'')+'unconfirmed</small>';
 }
 function radarView(s){return '<div class="radar-scope '+presentation(s).kind+'">'+rangePlot(s)+'<small>Range only · no position or person count</small></div>'}
 function update(root,s){if(current!==s){current=s;received=performance.now()}
  if(performance.now()-lastSample>1000){lastSample=performance.now();history.push(s?.radar?.online&&!['paused','not_judging'].includes(s?.evidence?.state)?(s?.evidence?.radar_candidate?1:0):null);history=history.slice(-60)}
  root.querySelectorAll('.evidence-cue').forEach(x=>x.remove());
  let chip=root.querySelector('.radar-peek');
  if(!chip){chip=document.createElement('button');chip.className='radar-peek';chip.setAttribute('aria-label','Open radar evidence');chip.onclick=()=>{root.classList.toggle('radar-open');update(root,current)};root.append(chip)}
  const p=presentation(s);chip.className='radar-peek '+p.kind;chip.textContent='◎ '+p.title+(p.kind==='candidate'?' ?':'');chip.title=p.detail;chip.setAttribute('aria-expanded',String(root.classList.contains('radar-open')));
  let panel=root.querySelector('.radar-detail');
  if(root.classList.contains('radar-open')){if(!panel){panel=document.createElement('section');panel.className='radar-detail';panel.setAttribute('aria-label','Radar evidence');root.append(panel)}panel.innerHTML=detail(s);panel.querySelector('button').onclick=()=>{root.classList.remove('radar-open');panel.remove();chip.setAttribute('aria-expanded','false')}}else panel?.remove();
 }
 window.setInterval(()=>{if(current&&performance.now()-received>1200){current=null;const root=document.querySelector('.phone');if(root)update(root,null)}},250);
 return{presentation,radarView,update};
})();
