/* Same-origin operator transport: persistent media, serialized polling, no automatic re-arm. */
(()=>{
  const originalViews={...views},app=document.querySelector('.phone');
  const badge=document.querySelector('.badge'),fs=document.querySelector('.fullscreen');
  const analysisEnabled=true;
  let live=false,status=null,pollTimer=null,sessionPending=null;
  const control=new RobotControl({post,hasIntent:()=>activePointers.size>0||keys.size>0,onReset:()=>stopMotion(),onState:paintAvailability});
  const signatures={};
  const esc=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const offline=(label,reason='Connecting')=>'<div class="feed-offline"><span class="offline-symbol">⌁</span><span>'+label+'</span><small>'+esc(reason)+'</small></div>';
  function find(obj,key){if(!obj||typeof obj!=='object')return null;if(key in obj)return obj[key];for(const v of Object.values(obj)){if(v&&typeof v==='object'){const r=find(v,key);if(r!==null)return r}}return null}
  function media(kind){return '<div class="feed-shell reconnecting" data-sensor="'+kind+'"><img class="live-feed" alt="Live '+kind+' feed"><div class="feed-recover"><span>Connecting</span><small></small></div><span class="stream-age"></span></div>'}
  function wifi(){return SensorEvidence.radarView(status)}
  function lidar(){return status?.lidar?.mode!=='3d'?LidarView.render():window.CloudView?CloudView.render():'<div class="feed-offline">Loading 3D view</div>'}
  function mountMini(current){
    let mini=app.querySelector('.mini-swap');
    if(!mini){mini=document.createElement('button');mini.className='mini-swap';mini.onclick=()=>{stopMotion();const target=mini.dataset.target,viewport=app.querySelector('.viewport'),incoming=mini.querySelector('.feed-shell,.lidar-panel,.cloud-panel'),outgoing=viewport.querySelector('.feed-shell,.lidar-panel,.cloud-panel');if(incoming&&outgoing){viewport.insertBefore(incoming,outgoing);mini.append(outgoing);viewport.querySelector('.proximity-hud')?.remove();app.dataset.view=target;mini.dataset.target=target==='lidar'?'rgb':'lidar';mini.setAttribute('aria-label',target==='lidar'?'Swap camera into main view':'Swap LiDAR map into main view');mini.querySelector('.mini-icon').textContent=target==='lidar'?'◉':'⌖'}else setMain(app,target);refreshViews()};app.append(mini)}
    const target=current==='lidar'?'rgb':'lidar';
    if(mini.dataset.target!==target){mini.dataset.target=target;mini.setAttribute('aria-label',target==='lidar'?'Swap LiDAR map into main view':'Swap camera into main view');mini.innerHTML=views[target]()+'<span class="mini-icon" aria-hidden="true">'+(target==='lidar'?'⌖':'◉')+'</span><span class="mini-expand" aria-hidden="true">⤢</span>'}
    bindMedia();
  }
  function bindMedia(){app.querySelectorAll('.feed-shell').forEach(shell=>{
    const kind=shell.dataset.sensor,img=shell.querySelector('img'),overlay=shell.querySelector('.feed-recover');const s=status?.[kind],online=!!s?.online;
    if(!img.dataset.bound){img.dataset.bound='true';img.onload=()=>{shell.classList.add('has-frame');img.dataset.failures='0';if(status?.[kind]?.online){shell.classList.remove('reconnecting');overlay.hidden=true}};img.onerror=()=>{const failures=Math.min(4,Number(img.dataset.failures||0)+1);img.dataset.failures=String(failures);img.dataset.retryAt=String(Date.now()+Math.min(4000,500*2**failures));shell.classList.add('reconnecting');overlay.hidden=false;overlay.querySelector('span').textContent='Reconnecting'};}
    if(img.naturalWidth>0)shell.classList.add('has-frame');
    const desired=kind;
    if(img.dataset.stream&&img.dataset.stream!==desired){img.removeAttribute('src');shell.classList.remove('has-frame')}img.dataset.stream=desired;
    const retryAt=Number(img.dataset.retryAt||0);
    if(online&&!document.hidden&&(!img.getAttribute('src')||(retryAt&&Date.now()>=retryAt))){img.dataset.retryAt='0';img.src='/api/'+kind+'.mjpg?connection='+Date.now();}
    const waiting=!online||!shell.classList.contains('has-frame')||!!Number(img.dataset.retryAt||0);
    shell.classList.toggle('reconnecting',waiting);overlay.hidden=!waiting;
    overlay.querySelector('span').textContent=online?'Connecting':'Reconnecting';overlay.querySelector('small').textContent=online?'':s?.reason||'Waiting for robot';
    shell.querySelector('.stream-age').textContent=online&&kind==='thermal'&&Number.isFinite(s.max_c_uncalibrated)?'~'+Math.round(s.max_c_uncalibrated)+' °C (uncal.)':'';
  })}
  function refreshViews(force=false){const current=app.dataset.view||'rgb',mediaView=current==='rgb'||current==='ir';const next={wifi:JSON.stringify([status?.radar,status?.evidence]),lidar:JSON.stringify(status?.lidar)};
    if(force||(!mediaView&&current!=='lidar'&&next[current]!==signatures[current]))setMain(app,current);
    Object.assign(signatures,next);bindMedia();
    const viewport=app.querySelector('.viewport');if(current==='rgb'&&!viewport.querySelector('.proximity-hud'))viewport.insertAdjacentHTML('beforeend','<div class="proximity-hud"><div class="lidar-readings"></div></div>');
    const hud=viewport.querySelector(".proximity-hud");if(hud)hud.hidden=!status?.lidar?.online||!status?.lidar?.proximity||status.lidar.proximity.stale;
    mountMini(current);
    SensorEvidence.update(app,status);
    PersonOverlay.update(status,analysisEnabled);
    if(status?.lidar?.mode!=='3d')LidarView.update(app,status?.lidar);else window.CloudView?.bind(app,status?.lidar);const sensor=current==='rgb'?'camera':current==='ir'?'thermal':current;const tag=app.querySelector('.viewport .demo-tag');if(tag){const source=sensor==='wifi'?status?.radar:status?.[sensor];const rate=sensor==='camera'?source?.stream_fps:sensor==='thermal'?source?.stream_fps:sensor==='lidar'?source?.fps:null;tag.textContent=source?.online?(Number.isFinite(rate)?Math.round(rate)+(sensor==='lidar'?' Hz':' FPS'):'●'):'○'}
  }
  function paintAvailability(){if(!live)return;const state=control.state();command.textContent=state.error?'Connection lost':status?.control?.enabled===false?'View only':state.armed?'Driving':state.available?'Ready':'Connecting';}
  async function request(path,{data,timeout=2500}={}){const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);try{return await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined,signal:controller.signal,cache:'no-store',credentials:'same-origin',keepalive:!!data})}finally{clearTimeout(timer)}}
  async function session(){if(!sessionPending)sessionPending=request('/api/session').then(r=>{if(!r.ok)throw Object.assign(new Error('Session unavailable'),{status:r.status})}).finally(()=>sessionPending=null);return sessionPending}
  async function post(path,data){const r=await request(path,{data});if(!r.ok){const body=await r.json().catch(()=>({}));throw Object.assign(new Error(body.error||'Control unavailable'),{code:body.code})}return r.json()}
  window.addEventListener('wavesense:drive',e=>{if(live)control.input(e.detail)});
  setInterval(()=>{if(live)control.tick()},75);
  ['blur','pagehide'].forEach(event=>window.addEventListener(event,()=>{if(live)control.release()}));
  document.addEventListener('visibilitychange',()=>{if(document.hidden){if(live)control.release();clearTimeout(pollTimer)}else if(live)poll()});
  async function poll(){clearTimeout(pollTimer);if(document.hidden)return;const generation=control.epoch;try{let r=await request('/api/status');if(r.status===401){await session();r=await request('/api/status')}if(!r.ok)throw new Error();status=await r.json();control.observe(status.control,generation);control.setAvailable(!!status.camera.online&&!!status.control.motor_access&&!!status.control.enabled);refreshViews();paintAvailability()}catch{if(live){status=null;control.setAvailable(false);refreshViews();paintAvailability()}}finally{if(live&&!document.hidden)pollTimer=setTimeout(poll,analysisEnabled?100:300)}}
  async function init(){app.querySelector('.sensor-tray')?.remove();const local=['localhost','127.0.0.1',''].includes(location.hostname);live=true;app.dataset.live='true';badge.textContent='ROBOT';views.rgb=()=>media('camera');views.ir=()=>media('thermal');views.wifi=wifi;views.lidar=lidar;stopMotion();refreshViews(true);try{await session();await poll()}catch(e){if(e.status===404&&local){live=false;delete app.dataset.live;Object.assign(views,originalViews);badge.textContent='DEMO';stopMotion();setMain(app,'rgb');app.querySelectorAll('.sensor-preview').forEach(b=>{const key=b.closest('.sensor-card').dataset.sensor;b.innerHTML=views[key]()+'<span class="sensor-name">'+sensorNames[key]+'</span>'});return}paintAvailability();pollTimer=setTimeout(poll,1000)}}
  // Sensor switches mount a new stream once; polling never remounts a healthy camera.
  window.addEventListener('wavesense:cloud-ready',()=>refreshViews(true));
  const observer=new MutationObserver(()=>{bindMedia();if(status?.lidar?.mode!=='3d')LidarView.update(app,status?.lidar);else window.CloudView?.bind(app,status?.lidar)});observer.observe(app.querySelector('.viewport'),{childList:true});
  init();
})();
