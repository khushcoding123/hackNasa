type Scenario={site:string;latitude:number;longitude:number;h_tx:number;h_rx:number;radio_profile:string;threshold?:number;range_m:number};
type Coverage={metadata:{state:string;message?:string;predictor:string;terrain_source?:string;resolution_m?:number;fallback_reasons:string[];threshold_db:number;display_transform:number[];tx_latitude:number;tx_longitude:number;valid_pixels:number;covered_pixels:number;display_stride:number};layers:Record<string,(number|null)[][]>};
type Inspection={latitude:number;longitude:number;state:string;margin_db:number|null;los:number|null;min_clearance_m?:number;obstruction_distance_m?:number;profile:{distance_m:number;terrain_m:number|null;ray_m:number}[]};
type Comparison={metadata:{model_seconds:number;physics_seconds:number;physics_cache_hit:boolean;display_transform:number[];tx_latitude:number;tx_longitude:number};scores:{valid_pixels:number;coverage_agreement:number;los_agreement:number;physics_covered_km2:number;physics_dead_km2:number;model_covered_km2:number;dead_zones_caught:number|null;false_covered_fraction:number|null;false_covered_km2:number;margin_mae_db:number};layers:{physics_class:(number|null)[][];ml_class:(number|null)[][];disagreement:(number|null)[][]}};
let generation=0,controller:AbortController|undefined,mlController:AbortController|undefined;
const R=1737400,DEG=Math.PI/180;
const ll=(x:number,y:number)=>[2*Math.atan(Math.hypot(x,y)/(2*R))/DEG-90,(Math.atan2(x,y)/DEG+360)%360];
export function surfaceCoverage(root:HTMLElement){
 controller?.abort();mlController?.abort();generation++;
 const number=(name:string,label:string,value:string,min:string,max:string)=>`<label>${label}<input name="${name}" type="number" step="any" min="${min}" max="${max}" value="${value}" ${name==='threshold'?'':'required'}></label>`;
 root.innerHTML=`<section class="panel"><h2>Surface coverage</h2><p>Simulated surface links on NASA LOLA terrain. Shift-click the map to move the transmitter; click to inspect a receiver. North is up in the polar grid.</p>
 <form id="surface-form"><div class="fields">
 <label>Site<select name="site"><option value="haworth">Haworth</option><option value="nobile-rim-1">Nobile Rim 1</option><option value="malapert">Malapert</option></select></label>
 ${number('latitude','TX latitude (°)','-86.8','-90','-85')}${number('longitude','TX longitude (° E)','337.2','-180','360')}${number('h_tx','TX height (m)','5','0','100')}${number('h_rx','RX height (m)','2','0','100')}
 <label>Radio profile<select name="radio_profile"><option value="moonmesh_2p4_ref">MOONMESH 2.4 GHz</option><option value="lunarrf_1p8">LunarRF 1.8 GHz</option></select></label>
 ${number('threshold','Threshold (dB; blank = profile)','','-200','200')}${number('range_m','Range (m)','8000','1','22040')}
 </div><button type="submit" class="primary">Calculate surface coverage</button></form>
 <p id="surface-state" role="status" aria-live="polite">Ready to calculate</p><p id="surface-provenance"></p>
 <fieldset id="surface-layers"><legend>Layers</legend>${['elevation','slope','los','margin','class','unknown'].map(k=>`<label style="display:inline-block;margin:6px"><input type="checkbox" value="${k}" ${['class','unknown'].includes(k)?'checked':''}> ${({los:'LOS',margin:'RF margin',class:'Covered / dead',unknown:'Unknown (purple hatch)'} as Record<string,string>)[k]??k}</label>`).join('')}</fieldset>
 <p>Legend: covered green · below margin orange · unknown purple hatch · LOS cyan clear / red blocked · RF margin red −20 to blue +20 dB relative to threshold. Elevation and slope use black to white over their displayed ranges. Enabled overlays combine in listed order.</p>
 <canvas id="surface-map" width="160" height="160" style="width:100%;image-rendering:pixelated;cursor:crosshair" aria-label="Polar coverage map: click to inspect, shift-click to move transmitter"></canvas>
 <button id="surface-export" disabled>Export native GeoTIFF layers + JSON</button><div id="surface-inspect" aria-live="polite"></div>
 <section id="surface-ml" style="margin-top:18px;padding:12px;border:1px dashed #b98cff;border-radius:8px"><h3>Experimental: compare with your ML model</h3>
 <p><strong>Not approved for planning.</strong> The final evaluation ships the physics map above. This runs the trained LightGBM model at the same transmitter and shows where it disagrees with physics. It only covers what the model was trained and tested on (Haworth, MOONMESH 2.4 GHz, TX 5–50 m, RX 2 m, range up to 8 km, and a transmitter whose whole 8 km range is on the prepared terrain), needs the trained model files on this computer (see shared-data/README.md), and takes about 30 seconds to 2 minutes at the full 8 km range (the first run after starting the app is the slowest). Use 8 km for scores comparable with the evaluation: short ranges have little dead area, so a few pixels swing the percentages. You can keep using the map while it runs.</p>
 <button id="surface-ml-run" disabled>Compare with ML model</button><p id="surface-ml-state" role="status" aria-live="polite">Calculate surface coverage first, then compare.</p>
 <div id="surface-ml-maps" style="display:none;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px">${[['physics','Physics (approved)'],['ml','Your ML model'],['diff','Where they disagree']].map(([k,t])=>`<figure style="margin:0"><figcaption>${t}</figcaption><canvas id="surface-ml-${k}" width="160" height="160" style="width:100%;image-rendering:pixelated" aria-label="${t} map"></canvas></figure>`).join('')}</div>
 <p>Physics and ML maps: covered green · dead orange · unknown purple hatch. Disagreement map: agree gray · <span style="color:#f04141">red = model says covered but physics says dead (the risky mistake)</span> · <span style="color:#3c8cff">blue = model says dead but physics says covered</span>.</p>
 <p id="surface-ml-scores"></p></section></section>`;
 const get=<T extends HTMLElement>(q:string)=>root.querySelector<T>(q)!;
 const form=get<HTMLFormElement>('#surface-form'),canvas=get<HTMLCanvasElement>('#surface-map'),state=get('#surface-state'),provenance=get('#surface-provenance'),detail=get('#surface-inspect'),download=get<HTMLButtonElement>('#surface-export');
 let data:Coverage|undefined,applied:Scenario|undefined,inspectVersion=0;
 const mlButton=get<HTMLButtonElement>('#surface-ml-run'),mlState=get('#surface-ml-state'),mlScores=get('#surface-ml-scores'),mlMaps=get('#surface-ml-maps');let mlVersion=0;
 const resetML=(text:string)=>{mlController?.abort();mlVersion++;mlButton.disabled=true;mlState.textContent=text;mlScores.textContent='';mlMaps.style.display='none';};
 const classColour=(v:number)=>v===1?[40,200,110]:[245,130,35],diffColour=(v:number)=>v===1?[240,65,65]:v===2?[60,140,255]:[70,70,78];
 function drawGrid(id:string,grid:(number|null)[][],colour:(v:number)=>number[],m:Comparison['metadata']){
  const c=get<HTMLCanvasElement>('#surface-ml-'+id),h=grid.length,w=h?grid[0].length:0;c.width=Math.max(1,w);c.height=Math.max(1,h);if(!w)return;
  const ctx=c.getContext('2d')!,img=ctx.createImageData(w,h);
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){const v=grid[y][x];img.data.set([...(v===null||v===255?((x+y)%7<2?[210,155,255]:[92,50,130]):colour(v)),255],4*(y*w+x));}
  ctx.putImageData(img,0,0);
  const t=m.display_transform,rho=2*R*Math.tan((90+m.tx_latitude)*DEG/2),px=(rho*Math.sin(m.tx_longitude*DEG)-t[2])/t[0],py=(rho*Math.cos(m.tx_longitude*DEG)-t[5])/t[4];
  ctx.strokeStyle='white';ctx.beginPath();ctx.moveTo(px-3,py);ctx.lineTo(px+3,py);ctx.moveTo(px,py-3);ctx.lineTo(px,py+3);ctx.stroke();
 }
 const body=():Scenario=>{const f=new FormData(form);return {site:String(f.get('site')),latitude:Number(f.get('latitude')),longitude:Number(f.get('longitude')),h_tx:Number(f.get('h_tx')),h_rx:Number(f.get('h_rx')),radio_profile:String(f.get('radio_profile')),threshold:f.get('threshold')===''?undefined:Number(f.get('threshold')),range_m:Number(f.get('range_m'))};};
 async function post(url:string,b:unknown,signal?:AbortSignal){
  const response=await fetch('/api/coverage'+url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b),signal});
  if(!response.ok){const error=await response.json();throw new Error(typeof error.detail==='string'?error.detail:JSON.stringify(error.detail));}return response;
 }
 function paint(){
  if(!data?.layers.valid||!applied)return;
  const a=data.layers,h=a.valid.length,w=a.valid[0].length;canvas.width=w;canvas.height=h;
  const ctx=canvas.getContext('2d')!,pixels=ctx.createImageData(w,h),enabled=new Set([...root.querySelectorAll<HTMLInputElement>('#surface-layers input:checked')].map(i=>i.value));
  const bounds=(k:string)=>{const v=a[k].flat().filter((x):x is number=>x!==null);return v.length?[Math.min(...v),Math.max(...v)]:[0,1];},elev=bounds('elevation'),slope=bounds('slope');
  for(let y=0;y<h;y++)for(let x=0;x<w;x++){
   let c=[25,25,30];const known=a.valid[y][x]===1;
   for(const k of ['elevation','slope','los','margin','class'])if(enabled.has(k)&&a[k][y][x]!==null){
    const v=a[k][y][x]!;
    if(k==='elevation'||k==='slope'){const b=k==='elevation'?elev:slope,g=40+200*(v-b[0])/Math.max(1,b[1]-b[0]);c=[g,g,g];}
    else if(known){const f=Math.max(0,Math.min(1,(v-data!.metadata.threshold_db+20)/40));const top=k==='class'?(v===1?[40,200,110]:[245,130,35]):k==='los'?(v===1?[30,210,230]:[240,65,65]):[255*(1-f),80,255*f];c=c.map((z,i)=>z*.2+top[i]*.8);}
   }
   if(!known&&enabled.has('unknown'))c=(x+y)%7<2?[210,155,255]:[92,50,130];pixels.data.set([...c,255],4*(y*w+x));
  }
  ctx.putImageData(pixels,0,0);
  const m=data.metadata,t=m.display_transform,rho=2*R*Math.tan((90+m.tx_latitude)*DEG/2),x=(rho*Math.sin(m.tx_longitude*DEG)-t[2])/t[0],y=(rho*Math.cos(m.tx_longitude*DEG)-t[5])/t[4];
  ctx.strokeStyle='white';ctx.beginPath();ctx.moveTo(x-3,y);ctx.lineTo(x+3,y);ctx.moveTo(x,y-3);ctx.lineTo(x,y+3);ctx.stroke();
 }
 async function run(){
  if(!form.reportValidity())return;controller?.abort();const current=++generation;controller=new AbortController();inspectVersion++;applied=undefined;data=undefined;download.disabled=true;detail.textContent='';canvas.getContext('2d')!.clearRect(0,0,canvas.width,canvas.height);resetML('Calculate surface coverage first, then compare.');
  state.textContent='Loading surface coverage…';provenance.textContent='';canvas.setAttribute('aria-busy','true');const requested=body();
  try{const fresh=await (await post('',requested,controller.signal)).json() as Coverage;if(current!==generation||!root.isConnected)return;
   data=fresh;applied=requested;const m=data.metadata;state.textContent=`${m.state.replaceAll('_',' ')} · ${m.message??`${m.valid_pixels} valid pixels; ${m.covered_pixels} covered`}`;
   provenance.textContent=`Simulated · active ${m.predictor} · ${m.terrain_source??'terrain unavailable'} · ${m.resolution_m??'—'} m native resolution. ${m.fallback_reasons.join(' ')}${data.layers.valid?` Display samples every ${m.display_stride} pixels; export retains native resolution.`:''}`;download.disabled=!data.layers.valid;paint();
   if(data.layers.valid){mlButton.disabled=false;mlState.textContent='Ready to compare this physics map with your ML model.';}
  }catch(e){if(current===generation&&!(e instanceof DOMException&&e.name==='AbortError'))state.textContent=`Coverage unavailable: ${String(e)}`;}
  finally{if(current===generation)canvas.setAttribute('aria-busy','false');}
 }
 form.onsubmit=e=>{e.preventDefault();void run();};
 form.oninput=()=>{controller?.abort();resetML('Inputs changed; calculate surface coverage, then compare.');generation++;inspectVersion++;applied=undefined;download.disabled=true;state.textContent='Inputs changed; calculate to update coverage.';detail.textContent='';canvas.setAttribute('aria-busy','false');canvas.getContext('2d')!.clearRect(0,0,canvas.width,canvas.height);};
 get<HTMLSelectElement>('[name="site"]').onchange=()=>{const coords=({haworth:[-86.8,337.2],'nobile-rim-1':[-85.4,37.3],malapert:[-85.964,357.681]} as Record<string,number[]>)[String(new FormData(form).get('site'))];get<HTMLInputElement>('[name="latitude"]').value=String(coords[0]);get<HTMLInputElement>('[name="longitude"]').value=String(coords[1]);};
 get('#surface-layers').onchange=paint;
 canvas.onclick=async e=>{
  if(!data?.layers.valid||!applied)return;const rect=canvas.getBoundingClientRect(),col=Math.min(canvas.width-1,Math.floor((e.clientX-rect.left)*canvas.width/rect.width)),row=Math.min(canvas.height-1,Math.floor((e.clientY-rect.top)*canvas.height/rect.height)),t=data.metadata.display_transform,x=t[2]+(col+.5)*t[0],y=t[5]+(row+.5)*t[4];
  if(e.shiftKey){const [lat,lon]=ll(x,y);get<HTMLInputElement>('[name="latitude"]').value=String(lat);get<HTMLInputElement>('[name="longitude"]').value=String(lon);void run();return;}
  const current=++inspectVersion,version=generation,scenario=applied;detail.textContent='Loading obstruction profile…';
  try{const i=await (await post('/inspect',{...scenario,rx_x_m:x,rx_y_m:y})).json() as Inspection;if(current!==inspectVersion||version!==generation||!root.isConnected)return;
   detail.textContent=`${i.latitude.toFixed(6)}°, ${i.longitude.toFixed(6)}° E · ${data.metadata.terrain_source} · TX ${scenario.h_tx} m / RX ${scenario.h_rx} m · ${i.state} · margin ${i.margin_db?.toFixed(2)??'unknown'} dB · LOS ${i.los===null?'unknown':i.los?'clear':'blocked'} · clearance ${i.min_clearance_m?.toFixed(2)??'unknown'} m at ${i.obstruction_distance_m?.toFixed(1)??'unknown'} m.`;
   if(i.profile.length){const values=i.profile.flatMap(p=>p.terrain_m===null?[p.ray_m]:[p.terrain_m,p.ray_m]),min=Math.min(...values),max=Math.max(...values),last=i.profile[i.profile.length-1].distance_m;
    const points=(key:'terrain_m'|'ray_m')=>i.profile.filter(p=>p[key]!==null).map(p=>`${20+460*p.distance_m/Math.max(1,last)},${180-150*(p[key]!-min)/Math.max(1,max-min)}`).join(' ');
    detail.insertAdjacentHTML('beforeend',`<svg viewBox="0 0 500 220" role="img" aria-label="Obstruction profile: orange terrain, cyan antenna ray"><polyline fill="none" stroke="#f58223" points="${points('terrain_m')}"/><polyline fill="none" stroke="#20d2e6" points="${points('ray_m')}"/><text x="20" y="205" fill="currentColor">0–${last.toFixed(0)} m distance · ${min.toFixed(0)}–${max.toFixed(0)} m elevation</text></svg><p>Terrain orange · antenna ray cyan. Spherical radial elevations.</p>`);
   }
  }catch(e){if(current===inspectVersion&&version===generation)detail.textContent=String(e);}
 };
 mlButton.onclick=async()=>{
  if(!applied||!data?.layers.valid)return;mlController?.abort();const current=++mlVersion,version=generation,scenario=applied,started=Date.now();mlController=new AbortController();
  mlButton.disabled=true;mlScores.textContent='';mlMaps.style.display='none';
  const tick=()=>{if(current===mlVersion)mlState.textContent=`Running your ML model… ${Math.round((Date.now()-started)/1000)} s (30 s to 2 min at 8 km; the first run is slowest)`;};tick();const timer=setInterval(tick,1000);
  const signal=mlController.signal,pause=(ms:number)=>new Promise<void>((done,fail)=>{const t=setTimeout(done,ms);signal.addEventListener('abort',()=>{clearTimeout(t);fail(new DOMException('Aborted','AbortError'));},{once:true});});
  try{const {job_id}=await (await post('/ml-compare/jobs',scenario,signal)).json() as {job_id:string};let r:Comparison|undefined;
   while(!r){await pause(2000);const response=await fetch('/api/coverage/ml-compare/jobs/'+job_id,{signal});const job=await response.json() as {status:string;detail?:unknown;result?:Comparison};
    if(!response.ok||job.status==='error')throw new Error(typeof job.detail==='string'?job.detail:JSON.stringify(job.detail));if(job.status==='done')r=job.result;}
   if(current!==mlVersion||version!==generation||!root.isConnected)return;
   const s=r.scores,m=r.metadata,pct=(v:number|null)=>v===null?'none in range':`${(100*v).toFixed(2)}%`;
   drawGrid('physics',r.layers.physics_class,classColour,m);drawGrid('ml',r.layers.ml_class,classColour,m);drawGrid('diff',r.layers.disagreement,diffColour,m);mlMaps.style.display='grid';
   mlState.textContent=`Experimental comparison done · model ${m.model_seconds.toFixed(1)} s vs physics ${m.physics_cache_hit?'(already calculated)':`${m.physics_seconds.toFixed(1)} s`}. Simulated; not approved for planning.`;
   mlScores.textContent=`Coverage agreement ${pct(s.coverage_agreement)} · LOS agreement ${pct(s.los_agreement)} · dead zones caught ${pct(s.dead_zones_caught)} of ${s.physics_dead_km2.toFixed(2)} km² dead area · model says covered where physics says dead: ${pct(s.false_covered_fraction)} of the dead area (${s.false_covered_km2.toFixed(3)} km²) · average margin error ${s.margin_mae_db.toFixed(2)} dB · covered area: physics ${s.physics_covered_km2.toFixed(2)} km², model ${s.model_covered_km2.toFixed(2)} km² · ${s.valid_pixels} pixels compared.`;
  }catch(e){if(current===mlVersion&&version===generation&&!(e instanceof DOMException&&e.name==='AbortError'))mlState.textContent=`ML comparison unavailable: ${e instanceof Error?e.message:String(e)}`;}
  finally{clearInterval(timer);if(current===mlVersion&&version===generation)mlButton.disabled=false;}
 };
 download.onclick=async()=>{if(!applied)return;const current=generation;download.disabled=true;try{const blob=await (await post('/export',applied)).blob();if(current!==generation)return;const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='surface-coverage.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){if(current===generation)state.textContent=String(e);}finally{if(current===generation)download.disabled=false;}};
}
