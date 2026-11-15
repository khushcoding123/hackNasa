import {MoonView} from "./moon.js";
import {map, timeline, horizon, powerTimeline} from "./charts.js";
import type {Result, Comparison, Candidate, Sensitivity, Metrics, AreaResult} from "./types.js";

const $=<T extends HTMLElement=HTMLElement>(q:string)=>document.querySelector<T>(q)!;
const form=$<HTMLFormElement>("#mission"), outlet=$("#view");
let result:Result, active="landing", cursor=0, zoom=0, mapZoom=1, layer="elevation";
let selected="", compared:Comparison[]|undefined, sensitivity:Sensitivity[]|undefined;
let disabled=new Set<string>(), busy=false, version=0, lastBody="";
let candidateSort="recovered", pending="";
let moon:MoonView|undefined;
let stale=false, configured=false, shownStale=false;
let area:[number,number][]|undefined, areaKey="", areaDirty=false, layout:AreaResult|undefined, layoutKey="", layoutFormat="";
const steps=["landing","configure","layout","overview","communication","resilience"];
const MOON_R=1737400, DEG=Math.PI/180;
const toXY=(lat:number,lon:number)=>{const rho=2*MOON_R*Math.tan((90+lat)*DEG/2);return [rho*Math.sin(lon*DEG),rho*Math.cos(lon*DEG)];};
const toLatLon=(x:number,y:number):[number,number]=>[2*Math.atan(Math.hypot(x,y)/(2*MOON_R))/DEG-90,((Math.atan2(x,y)/DEG)%360+360)%360];
let sites:Record<string,[string,number,number]>={};
const views:Record<string,[string,string]>={landing:["Start with the Moon.","01 · Explore the surface. Then draw the area you want to analyse: a square or any shape."],configure:["Tell us about your mission.","02 · Draw an area, choose dates and communication goals, then calculate."],layout:["Plan the network.","03 · The best layout of towers and links inside the area you drew, compared with simpler formats."],overview:["Your mission, in three dimensions.","04 · Inspect the lander and calculated relay network. Explore the local terrain, or switch to Whole Moon for context."],sites:["Choose your horizon.","Compare landing regions over the same mission period and assumptions."],communication:["Understand the silence.","Trace each communication blackout to terrain, geometry, and RF conditions."],relays:["Bridge the distance.","Explore the smallest screened relay configuration and its physical constraints."],resilience:["Plan for the unexpected.","Disable a relay and inspect how the remaining paths perform."],sensitivity:["Question the assumptions.","Change one assumption at a time and compare the modeled outcome."],data:["The science behind the signal.","Inspect sources, model assumptions, and the limits of these planning estimates."]};
function dirty(){
 // Compare only editable fields; API configuration may include additional model defaults.
 const same=(a:unknown,b:unknown):boolean=>a!==null&&typeof a==="object"?Object.entries(a).every(([k,v])=>same(v,(b as Record<string,unknown>)?.[k])):a===b;
 const edited=(!!result&&!same(values(),result.config))||(!!result&&areaDirty);stale=edited;
 $("#dirty").textContent=edited?"Changes not applied":result?"Scenario applied":!area?"Draw an area first":configured?"Ready to calculate":"Preparing scenario";
 $("#dirty").classList.toggle("stale",edited);updateNavigation();
}
form.addEventListener("input",dirty);
form.addEventListener("change",dirty);

// ----- Workspace shell: the Moon fills the screen; mission content lives in a collapsible side panel (bottom sheet on phones). -----
function setPanel(open:boolean){
 document.body.classList.toggle("panel-collapsed",!open);
 $("#panel-tab").setAttribute("aria-expanded",String(open));$("#panel").setAttribute("aria-hidden",String(!open));
 ($("#panel") as HTMLElement & {inert:boolean}).inert=!open;
}
$("#panel-tab").onclick=()=>setPanel(document.body.classList.contains("panel-collapsed"));
$("#panel-close").onclick=()=>{setPanel(false);$("#panel-tab").focus();};
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!document.body.classList.contains("panel-collapsed")&&matchMedia("(max-width:767px)").matches&&(e.target as HTMLElement).closest?.("#panel"))setPanel(false);});
function updateNavigation(){
 document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(b=>{
  const available=["landing","configure"].includes(b.dataset.view!)||(!!result&&!stale);
  b.disabled=!available;b.title=available?"":"Complete mission setup and calculate a network first.";
  b.classList.toggle("completed",!!result&&!stale&&steps.indexOf(b.dataset.view!)<steps.indexOf(active));
 });
 for(const id of ["json","csv","report"])$<HTMLButtonElement>("#"+id).disabled=!result||stale;
 $(".export-menu").hidden=!result||stale||active==="landing";
 if(moon&&shownStale!==stale){shownStale=stale;moon.setMission(stale?undefined:result);}
 $("#start-mission").hidden=active!=="landing";
 $<HTMLButtonElement>('button[type="submit"]').disabled=busy||!configured||!area;
}
$("#start-mission").onclick=()=>navigate("configure");
function navigate(view:string,focus=true){
 if(!["landing","configure"].includes(view)&&(!result||stale))view="configure";
 active=view;
 document.querySelectorAll("[data-view][aria-current]").forEach(b=>b.removeAttribute("aria-current"));
 document.querySelectorAll<HTMLButtonElement>(`[data-view="${view}"]`).forEach(b=>b.setAttribute("aria-current","step"));

 $("#title").innerHTML=views[view][0];$("#purpose").textContent=views[view][1];
 const index=steps.indexOf(view);$("#chapter").textContent=index<0?"OPTIONAL / DEEPER ANALYSIS":`STEP 0${index+1} OF 0${steps.length}`;
 form.hidden=view!=="configure";updateNavigation();
 $("#panel-tab-label").textContent=view==="landing"?"Guide":"Mission";
 setPanel(view!=="landing");
 render();if(focus&&view!=="landing")$("#title").focus();
}
function flow(){
 const index=steps.indexOf(active);
 const previous=index>0?steps[index-1]:"overview";
 const next=({landing:"configure",layout:"overview",overview:"communication",communication:"resilience"} as Record<string,string>)[active]??"";
 $("#flow-actions").innerHTML=`<div><span class="eyebrow">${index<0?"OPTIONAL ANALYSIS":`STEP ${index+1} / ${steps.length}`}</span><p>${active==="landing"?"First, explore. Then set the mission parameters.":active==="configure"?"Your network is calculated only when you submit this form.":active==="resilience"?"Ready to share? Export your applied mission from the header.":"Your applied mission stays with you at every step."}</p></div><div class="flow-buttons">${index!==0?`<button data-go="${previous}">← ${index<0?"Back to the Earth link":"Back"}</button>`:""}${next?`<button class="primary" data-go="${next}">${next==="configure"?"Set up my mission":next==="overview"?"Next: the Earth link":next==="communication"?"Next: understand blackouts":"Next: test resilience"} →</button>`:active==="resilience"?'<button data-go="configure">Revise mission parameters</button>':""}</div>`;
 $("#flow-actions").querySelectorAll<HTMLButtonElement>('[data-go]').forEach(b=>b.onclick=()=>navigate(b.dataset.go!));
}

const n=(x:number|null|undefined,d=1)=>x==null?"—":x.toFixed(d);
const pct=(x:number)=>n(x)+"%";
const hours=(x:number)=>n(x)+" h";
const utc=(x:string)=>x.slice(0,16).replace("T"," ")+" UTC";
const escape=(s:unknown)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]!));
const status=(s:string,error=false)=>{const node=$("#status");node.textContent=s;node.classList.toggle("error",error);};
const panel=(title:string,html:string)=>`<section class="panel"><h2>${title}</h2>${html}</section>`;
const row=(cells:string[])=>"<tr>"+cells.map(c=>"<td>"+c+"</td>").join("")+"</tr>";
const table=(columns:string[],rows:string[],cls="")=>`<div class="table-wrap" role="region" aria-label="Scrollable results table" tabindex="0"><table class="${cls}"><thead><tr>${columns.map(c=>'<th scope="col">'+c+"</th>").join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
const readout=(entries:[string,string][])=>`<dl>${entries.map(([label,value])=>`<dt>${label}</dt><dd>${value}</dd>`).join("")}</dl>`;
const metric=(label:string,value:string,caption:string,cls="")=>`<section class="panel metric"><small>${label}</small><strong class="${cls}">${value}</strong><small>${caption}</small></section>`;

const fields:[string,string,number,number,number,string][]=[
 ["availability","Minimum coverage (%)",0,100,.1,"Required percentage of mission intervals with a modeled communication path."],
 ["blackout_hours","Max blackout (h)",0,1440,.5,"Longest permissible continuous loss of communication."],
 ["darkness_hours","Max darkness (h)",0,1440,.5,"Longest permissible continuous darkness at the lander."],
 ["max_relays","Maximum relays",0,4,1,"Maximum masts in the restricted candidate search."],
 ["deployment_km","Deployment radius (km)",1,8,.1,"Straight-line radius around the lander."],
 ["slope_limit","Maximum relay slope (°)",.1,45,.1,"Maximum local DEM slope at a candidate mast."],
 ["observer_m","Lander mast (m)",.1,100,.1,"Height above the local DEM surface."],
 ["mast_m","Relay mast (m)",.1,100,.1,"Height above the local DEM surface."],
 ["dem_bias_m","Terrain elevation bias (m)",-30,30,.1,"Terrain height adjustment for horizon sensitivity estimates."],
 ["clearance","Terrain clearance (°)",0,10,.01,"Required angular margin for the Earth and Sun center."],
 ["battery_hours","Battery endurance (h)",0,720,.5,"Usable battery capacity expressed as hours at constant load."],
 ["initial_charge_pct","Initial battery charge (%)",0,100,1,"Percentage of usable battery capacity at mission start."],
 ["charge_ratio","Net charging rate",.1,20,.1,"Hours of battery reserve added per hour of modeled sunlight."],
 ["latitude","Custom latitude (°)",-90,-85,.000001,"Optional latitude within the selected terrain crop."],
 ["longitude","Custom longitude (° E)",-180,360,.000001,"Optional longitude within the selected terrain crop."],
];
const rf:[string,string,number,number,number,string][]=[
 ["ghz","Frequency (GHz)",.001,100,.001,"Carrier frequency."],["watts","Transmitter (W)",.01,1e5,.01,"Radiated profile uses transmitter power and antenna gain."],
 ["tx_gain","TX gain (dBi)",-30,120,.1,"Assumed transmitting antenna gain."],["rx_gain","RX gain (dBi)",-30,120,.1,"Assumed receiving antenna gain."],
 ["sensitivity","Sensitivity (dBm)",-200,0,.1,"Receiver threshold at reference data rate of 128 kbps."],
 ["loss","Losses (dB)",0,100,.1,"Additional modeled losses."],["margin","Required margin (dB)",0,60,.1,"Minimum margin over receiver threshold."],
 ["rate_kbps","Data rate (kbps)",.001,1e7,.001,"Rate scales the reference receiver threshold."],
];
const input=([key,label,min,max,step,help]:typeof fields[number],prefix="")=>`<label title="${help}">${label}<input type="number" min="${min}" max="${max}" step="${step}" name="${prefix+key}" ${key==="latitude"||key==="longitude"?"":"required"}></label>`;
$("#expert").innerHTML=`<fieldset><legend>Mission and deployment constraints</legend><div class="fields">${fields.filter(f=>!["availability","blackout_hours","max_relays","deployment_km"].includes(f[0])).map(f=>input(f)).join("")}<label title="Require compliance after any selected relay fails"><span><input type="checkbox" name="resilient"> Require N−1</span></label></div></fieldset>`+
 [["earth_radio","Direct Earth and relay-to-Earth RF"],["surface_radio","Lander-to-relay RF"]].map(([key,label])=>`<fieldset><legend>${label}</legend><div class="fields">${rf.map(f=>input(f,key+".")).join("")}</div></fieldset>`).join("");

$("#basic-constraints").innerHTML=fields.filter(f=>["availability","blackout_hours","max_relays","deployment_km"].includes(f[0])).map(f=>input(f)).join("");

async function api<T>(url:string,body?:unknown):Promise<T>{
 let response:Response;
 try{response=await fetch("/api/"+url,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});}
 catch{throw new Error("Cannot reach the MOONMESH server. Check that it is running.");}
 const data=await response.json().catch(()=>({detail:`Server error ${response.status}.`}));
 if(!response.ok){
  const detail=data.detail;
  const message=typeof detail==="string"?detail:Array.isArray(detail)?detail.map((d:{loc:(string|number)[];msg:string})=>{
   const field=d.loc.slice(1).join("."), text=d.msg.replace(/^Value error, /,"");
   return field?`${field}: ${text}`:text;
  }).join(" "):"";
  throw new Error(message||"Request failed.");
 }
 return data;
}
const meets=(m:Metrics)=>m.availability>=Number(result.config.availability)&&m.longest<=Number(result.config.blackout_hours);

function values(){
 const next:Record<string,unknown>={earth_radio:{},surface_radio:{}};
 for(const [key,value] of new FormData(form)){
   const [parent,child]=key.split(".");
   if(child)(next[parent] as Record<string,number>)[child]=Number(value);
   else next[key]=["site","start","end"].includes(key)?value:value===""?null:Number(value);
 }
 next.resilient=$<HTMLInputElement>('input[name="resilient"]').checked;
 return next;
}

async function run(){
 if(!configured||busy||!area||!form.reportValidity())return;
 busy=true;
 document.body.classList.add("computing");$("#view").setAttribute("aria-busy","true");
 $<HTMLButtonElement>('button[type="submit"]').disabled=true;
 status("Computing terrain, ephemerides and candidate relay paths…");
 try{
   const fresh=layoutKey!==JSON.stringify(area);
   if(fresh){
    status("Choosing relay sites and comparing layouts inside your area… about 20 seconds, up to a minute or two for very large areas.");
    layout=await api<AreaResult>("ora/area",{polygon:area});layoutKey=JSON.stringify(area);layoutFormat=layout.recommended;
    const region=$<HTMLSelectElement>('[name="site"]');if(!region.querySelector(`option[value="${layout.site.key}"]`))region.add(new Option(layout.site.name,layout.site.key));
    region.value=layout.site.key;for(const name of ["latitude","longitude"])$<HTMLInputElement>(`[name="${name}"]`).value="";
    sites[layout.site.key]=[layout.site.name,layout.site.lat,layout.site.lon];
    status("Computing terrain, ephemerides and candidate relay paths…");
   }
   const requested=values(), key=JSON.stringify(requested);
   const next=await api<Result>("mission",requested);
   result=next; version++;
   disabled.clear(); cursor=0; zoom=result.times.length; mapZoom=1;
   selected=result.recommended[0]??""; if(key!==lastBody){compared=undefined;sensitivity=undefined;}lastBody=key;
   for(const id of ["json","csv","report"]) $<HTMLButtonElement>("#"+id).disabled=false;
   $(".export-options p").textContent="Exports use the applied scenario and current relay failure state.";
   $("#subtitle").textContent=`${result.site.name} · ${result.config.start} to ${result.config.end} UTC`;
   status(`${result.times.length.toLocaleString()} samples · ${result.config.minutes} min intervals · ${result.candidates.length} relay candidates evaluated`);
   areaDirty=false;shownStale=false;dirty();moon?.setMission(result);
   if(fresh&&layout){moon?.setLayout(layout,layoutFormat);moon?.flyToSite(layout.site.lat,layout.site.lon,Math.max(30000,layout.site.radius_m*5+16000));}
   navigate(fresh?"layout":"overview");
   if(!fresh)moon?.flyToSite(result.site.lat,result.site.lon);
 }catch(error){status((result?"Previous results shown. ":"")+(error as Error).message,true);if(!result)outlet.innerHTML=`<section class="panel initial-state"><h2>Mission could not be computed</h2><p class="muted">Review the settings and use Run mission to retry. The error is shown above.</p></section>`;}
 finally{busy=false;document.body.classList.remove("computing");$("#view").setAttribute("aria-busy","false");$<HTMLButtonElement>('button[type="submit"]').disabled=false;}
}

form.onsubmit=e=>{e.preventDefault();void run();};
document.addEventListener("click",event=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>("button[data-view]");
 if(!button||button.disabled)return;
 button.closest("details")?.removeAttribute("open");
 navigate(button.dataset.view!);
});

function live(){
 return result.failure_states[[...disabled].sort().join(",")||"nominal"];
}

function mapPanel(){
 return panel("Lunar terrain",`<div class="controls"><label>Layer <select id="layer"><option>elevation</option><option>slope</option><option>confidence</option></select></label><button id="map-zoom">Zoom map (${mapZoom}×)</button></div>
 <canvas id="map" class="map" aria-label="South polar terrain map with lander, relay and celestial directions"></canvas>
 <div class="legend"><span>Lander</span><span style="--key:var(--green)">Selected relay</span><span style="--key:var(--red)">Disabled</span></div>
 <label class="caption">Inspect candidate <select id="map-candidate"><option value="">Choose a relay</option>${result.candidates.map(c=>`<option value="${c.id}">${c.id}${result.recommended.includes(c.id)?" · selected":""}</option>`).join("")}</select></label><p class="caption">South polar stereographic · Grid north and distances shown. Layer uses modeled terrain coverage.</p>`);
}

function timelinePanel(){
 return panel("Mission timeline",`<div class="controls"><button id="zoom">Zoom (${Math.round(result.times.length/zoom)}×)</button><span id="time" class="muted"></span></div>
 <canvas class="timeline" id="timeline" aria-label="Availability and blackout tracks"></canvas>
 <input type="range" id="cursor" min="0" max="${result.times.length-1}" value="${cursor}" aria-label="Mission timestamp">
 <div class="legend"><span style="--key:#779ebd">Flat horizon</span><span>Terrain visible</span><span style="--key:var(--green)">Direct RF / combined</span><span style="--key:var(--amber)">Sunlight</span><span style="--key:#bba9ff">Relay path</span><span style="--key:var(--red)">Blackout</span><span class="unknown">Uncertain</span></div><p class="caption">Use arrow keys on the time slider to inspect individual samples. Times are UTC.</p><div id="timeline-readout" class="timeline-readout"></div><details class="track-help"><summary>What the tracks mean</summary><dl>
 <dt>Flat horizon</dt><dd>Earth is above the horizon of a smooth Moon with no terrain.</dd>
 <dt>Terrain visible</dt><dd>Earth clears the real terrain horizon plus the clearance allowance. A site on high ground can see past the flat horizon, so this can be on while Flat horizon is off.</dd>
 <dt>Direct RF</dt><dd>Earth is terrain-visible and the estimated radio margin meets the requirement.</dd>
 <dt>Sunlight</dt><dd>The Sun's center clears the terrain horizon plus the clearance allowance.</dd>
 <dt>Relay path</dt><dd>At least one active relay links the lander to Earth.</dd>
 <dt>Combined</dt><dd>Direct or relay communication is available.</dd>
 <dt>Direct blackout</dt><dd>No direct link to Earth.</dd>
 <dt><span class="swatch uncertain"></span>Brown</dt><dd>Uncertain: terrain data is incomplete in that direction.</dd>
 </dl></details>`);
}

const horizonPanel=()=>panel("360° terrain horizon",`<canvas id="horizon" class="horizon" aria-label="Horizon elevation versus azimuth"></canvas><p class="caption">Azimuth (°) horizontally · Elevation (°) vertically. Solid cyan: terrain; dashed: clearance allowance. Green Earth and amber Sun markers. The line beneath Earth shows its clearance (green clear, red blocked); gaps indicate unknown terrain.</p><div id="point"></div>`);
const planState=()=>!result.feasible?"Best evaluated configuration · requirements unmet":result.recommended.length?"Minimum among screened candidates · requirements met":"Direct path meets the requirements · no relays needed";
const bars=(label:string,value:number,after=false)=>`<div class="muted">${label} · ${pct(value)}</div><div class="chart-bar ${after?"after":""}"><span style="width:${value}%"></span></div>`;
function outageTable(){
 const rows=result.outages.map(item=>row([`<button data-time="${item.first}" aria-label="Inspect blackout starting ${utc(item.start)}">${utc(item.start)}</button>`,utc(item.end),hours(item.hours),escape(item.causes.join(", "))]));
 return panel("Direct-link blackouts", rows.length?table(["Start","End","Duration","Cause"],rows):"<p class='muted'>No direct blackouts in the sampled period.</p>");
}
function powerImage(c:Candidate){
 const element=document.createElement("canvas");
 powerTimeline(c,Number(result.config.battery_hours),-1,element);
 return element.toDataURL();
}
function relayDetail(c:Candidate,includeTimeline=true){
 const reason=c.los===null?"Terrain coverage is incomplete.":!c.los?"Terrain obstructs the lander-to-relay path.":c.surface_margin<Number((result.config.surface_radio as Record<string,number>).margin)?"Surface RF margin is below the requirement.":c.recovered>0?`This candidate covers ${pct(c.recovered)} of the mission during direct-link outages.`:"This candidate recovers zero direct-link outage intervals.";
 return panel("Relay "+c.id,`<p class="callout">${reason}</p>`+readout([
 ["Coordinates",`${n(c.lat,5)}°, ${n(c.lon,5)}° E`],["Deployment estimate",n(c.distance,2)+" km"],["Elevation",n(c.elevation)+" m"],
 ["Local slope",n(c.slope)+"°"],["Surface margin",n(c.surface_margin)+" dB"],["Relay-to-Earth feasibility",pct(c.earth.availability)],
 ["Sunlight",pct(c.solar.availability)],["Powered",pct(c.power.availability)],["Full relay path",pct(c.coverage.availability)],["Uncertain Earth samples",String(c.unknown_samples)],
 ["Minimum Earth-link margin",n(c.minimum_earth_margin)+" dB"],["Power outages",String(c.power.intervals)],["Battery capacity",hours(Number(result.config.battery_hours))],["Minimum battery reserve",hours(c.minimum_reserve)],
 ])+(includeTimeline?`<p class="muted">Sunlight and usable power over the mission</p><div id="power-readout"></div><canvas id="power-timeline" class="power-timeline" aria-label="Relay sunlight, power and battery reserve"></canvas>`:`<img src="${powerImage(c)}" alt="Relay ${c.id} sunlight, power and battery reserve">`));
}
function planPanel(){
 const field=(key:string)=>form.elements.namedItem(key) as HTMLInputElement;
 const limit=(key:string,label:string,min:number,max:number,step:number)=>`<label>${label}<input type="number" data-sync="${key}" min="${min}" max="${max}" step="${step}" value="${escape(field(key).value)}" required></label>`;
 return panel("Restricted candidate search",`<p class="callout ${result.feasible?"":"amber"}">${planState()}</p>`+readout([
 ["Selected",result.recommended.join(", ")||"Direct path only"],["Usable candidates",`${result.eligible} of ${result.candidates.length} screened`],
 ["Required coverage",pct(Number(result.config.availability))],["Maximum blackout",hours(Number(result.config.blackout_hours))],
 ["Requirement",result.config.resilient?"Survive any single relay failure (N−1)":"Nominal coverage"],
 ])+`<h3 class="limits-title">Optimization limits</h3><div class="limits">${limit("max_relays","Maximum relays",0,4,1)}${limit("availability","Minimum coverage (%)",0,100,.1)}${limit("blackout_hours","Max blackout (h)",0,1440,.5)}<label class="check"><input type="checkbox" data-sync="resilient" ${field("resilient").checked?"checked":""}> Require N−1</label></div>
 <p><button id="optimize" class="primary">Optimize relay network</button></p><p id="optimize-note" class="muted" role="status"></p>`);
}
function siteRows(){
 if(!compared)return '<p class="muted">Compare all three regions over the current period and assumptions.</p>';
 return table(["Region","Sunlight","Longest darkness","Direct to Earth","Longest direct blackout","With relays","Relays","Limits met","DEM coverage"],
 compared.map(c=>row([`<button data-site="${Object.entries(sites).find(([,v])=>v[0]===c.site.name)?.[0]??""}">${escape(c.site.name)}${c.site.name===result.site.name?" · current":""}</button><small class="coords">${n(c.site.lat,3)}°, ${n(c.site.lon,3)}° E</small>`,pct(c.solar.availability),hours(c.solar.longest),pct(c.direct.availability),hours(c.direct.longest),
 `<strong>${pct(c.network.availability)}</strong>`,escape(c.recommended.join(", ")||"None"),c.feasible?'<span class="green">Met</span>':'<span class="amber">Unmet</span>',pct(c.site.terrain_coverage)])),"compare");
}
function configuration(){
 const names:Record<string,string>={site:"Landing region",start:"Start (UTC)",end:"End (UTC)",minutes:"Sampling (min)",resilient:"Require N−1",dem_bias_m:"Terrain bias (m)",
  earth_radio:"Direct Earth and relay-to-Earth RF",surface_radio:"Lander-to-relay RF",...Object.fromEntries(fields.map(([key,label])=>[key,label]))};
 const radio=(r:Record<string,number>)=>`${r.ghz} GHz · ${r.watts} W · TX ${r.tx_gain} dBi · RX ${r.rx_gain} dBi · ${r.sensitivity} dBm sensitivity · ${r.loss} dB losses · ${r.margin} dB required margin · ${r.rate_kbps} kbps`;
 return readout(Object.entries(result.config).filter(([,v])=>v!==null).map(([key,v])=>[names[key]??key.replaceAll("_"," "),
  escape(key==="site"?result.site.name:typeof v==="boolean"?(v?"Yes":"No"):v&&typeof v==="object"?radio(v as Record<string,number>):String(v))]));
}
function render(){
 document.body.dataset.step=active;flow();form.hidden=active!=="configure";
 if(active==="landing"){
  outlet.innerHTML=`<section class="panel introduction"><p class="eyebrow">A CONNECTION STARTS HERE</p><h2>From the surface to a working plan.</h2><p>Search, zoom and click the Moon to read coordinates, elevation and nearby features. Then <strong>draw the area you care about</strong>, a square or any shape of any size anywhere on the Moon, and MOONMESH plans the towers and links inside it.</p><div class="draw-actions"><button class="primary" data-draw-start="rect">▭ Draw a square</button><button data-draw-start="free">✎ Draw freeform</button><button data-pole-go>⌖ Go to the pole</button></div><p>Terrain can hide Earth from a lunar lander. Relay towers may help restore contact. MOONMESH shows when, where, and under which assumptions.</p><ol class="journey-list"><li><strong>Draw an area</strong><span>Any size, anywhere on the Moon.</span></li><li><strong>Plan the layout</strong><span>Towers, gateways and links, with tree, loop and mesh compared.</span></li><li><strong>Understand and test it</strong><span>The Earth link, blackouts and relay failures.</span></li></ol><button class="primary" data-go="configure">Set up my mission →</button><p class="caption">${result&&!stale?"Your applied mission is ready in Step 3.":"No towers are placed before you calculate a mission."}</p></section>`;
  connect();return;
 }
 if(active==="configure"){
  outlet.innerHTML=panel("Your area",area?`<p class="callout">${area.length} corner${area.length===1?"":"s"} drawn. Calculate to plan the network inside it.</p><div class="draw-actions"><button data-draw-start="rect">Redraw as a square</button><button data-draw-start="free">Redraw freeform</button></div>`:`<p class="callout amber">Draw the area to analyse first. Click a button below, then drag on the Moon.</p><div class="draw-actions"><button class="primary" data-draw-start="rect">▭ Draw a square</button><button data-draw-start="free">✎ Draw freeform</button><button data-pole-go>⌖ Go to the pole</button></div>`)+panel("What happens next?",`<p>Choose <strong>Calculate network</strong> above. We plan towers and links inside your area, then evaluate terrain, sunlight and direct communication for your mission period. Step 03 opens with the layout.</p><p class="muted">${stale?"You changed the mission parameters. Calculate again to update the network; analysis steps are locked until then.":"Start with the provided research scenario, or change the parameters. No mission runs automatically."}</p>`);connect();return;
 }
 if(!result)return;
 const net=live(), r=result;
 const charts=`<div class="split">${mapPanel()}${horizonPanel()}</div>`;
 const context=readout([["Position",`${n(r.site.lat,5)}°, ${n(r.site.lon,5)}° E`],["Elevation",n(r.site.elevation)+" m"],
 ["Mean / max slope",n(r.site.mean_slope)+"° / "+n(r.site.max_slope)+"°"],["DEM grid spacing",n(r.site.resolution,0)+" m"],["Local DEM coverage",pct(r.site.terrain_coverage)]]);
 const kpis=`<div class="metrics">${metric("DIRECT TO EARTH",pct(r.direct.availability),`${r.direct.intervals} blackout intervals`)}${metric("SUNLIGHT",pct(r.solar.availability),hours(r.solar.longest)+" longest darkness")}${metric("LONGEST DIRECT BLACKOUT",hours(r.direct.longest),hours(r.direct.total)+" offline in total")}${metric("WITH ACTIVE RELAYS",pct(net.metrics.availability),hours(net.metrics.longest)+" longest blackout",net.meets?"green":"amber")}${metric("WORST SINGLE FAILURE",pct(r.worst.availability),hours(r.worst.longest)+" longest blackout",meets(r.worst)?"green":"amber")}</div>`;
 const comparison=bars("Direct-to-Earth",r.direct.availability)+bars("Active network",net.metrics.availability,true)+`<p class="callout ${net.meets?"":"amber"}">${net.meets?"Active network meets communication requirements.":"Active network falls short of communication requirements."}</p>`;
 const candidates=table(["Relay","Range","Slope","Surface LOS","Solar","Power","Path","Recovered"],
 [...r.candidates].sort((a,b)=>candidateSort==="distance"?a.distance-b.distance:b.recovered-a.recovered).map(c=>row([`<button aria-pressed="${c.id===selected}" data-candidate="${c.id}">${c.id}</button>`,n(c.distance,2)+" km",n(c.slope)+"°",c.los===null?"Uncertain":c.los?"Clear":"Blocked",pct(c.solar.availability),pct(c.power.availability),pct(c.coverage.availability),pct(c.recovered)])));
 const choice=r.candidates.find(c=>c.id===selected)??r.candidates[0];
 const switchers=r.recommended.map(id=>`<label><input type="checkbox" data-disable="${id}" ${disabled.has(id)?"checked":""}> Disable ${id}</label>`).join("");
 const nodes=`<div class="network-branches"><div class="network-chart"><span class="node active">Lander</span><span aria-hidden="true">→</span><span class="node">Earth · direct</span></div>${r.recommended.map(id=>`<div class="network-chart"><span class="node">Lander</span><span aria-hidden="true">→</span><span class="node ${disabled.has(id)?"failed":"active"}">${id}${disabled.has(id)?" · disabled":""}</span><span aria-hidden="true">→</span><span class="node">Earth</span></div>`).join("")}</div>`;
 const failures=table(["Disabled relay","Availability","Longest blackout","Coverage lost","Blackout increase","Windows lost","Meets limits"],
 r.failures.map(f=>row([f.id,pct(f.metrics.availability),hours(f.metrics.longest),n(f.availability_lost)+" pp",hours(f.blackout_increase),String(f.windows_lost),f.meets?"Yes":"No"])));
 switch(active){
 case "layout":outlet.innerHTML=layoutView();break;
 case "overview":outlet.innerHTML=`<div class="network-hero"><section class="panel mission-summary"><p class="eyebrow">MISSION / ${escape(r.site.name.toUpperCase())}</p><h2>${net.meets?"A path to connection.":"Every connection counts."}</h2><span class="verdict ${net.meets?"green":"amber"}">${net.meets?"✓ Active network meets communication limits":"△ Active network falls short of communication limits"}</span><div class="hero-value">${pct(net.metrics.availability)} <small>active coverage</small></div><p class="muted">${n(net.metrics.availability-r.direct.availability)} percentage points more of the mission covered than direct contact alone. ${escape(planState())}.</p><div class="hero-stats"><div><span class="muted">Longest active blackout</span><strong>${hours(net.metrics.longest)}</strong></div><div><span class="muted">Screened recommendation</span><strong>${r.recommended.length} relays</strong></div></div><button class="primary" data-go="communication">Next: understand blackouts →</button><button class="quiet" data-go="relays">Inspect individual tower details</button></section><section class="panel"><h2>Towers on the Moon</h2><div id="scene-selection" aria-live="polite"><p class="caption">Select a tower in the Moon view to inspect it. Green = available path, amber = unavailable, red = disabled. Dashed links are unavailable at the selected time.</p></div></section></div>`+kpis+timelinePanel()+`<details id="map-details" class="panel"><summary>See the 2D terrain map</summary>${mapPanel()}</details><div class="split">${panel("From direct to connected",comparison)}${panel("Independent surface paths",nodes+`<p class="caption">Each powered relay supplies a separate two-hop path. No relay-to-relay chain is modeled.</p>`+readout([["Mission length",hours(r.times.length*Number(r.config.minutes)/60)],["Darkness limit",r.darkness_meets?"Met":"Exceeded"]]))}</div>`;break;
 case "sites":outlet.innerHTML=panel("Find the right horizon",`<p class="muted table-note">Approximate research points, evaluated over the applied mission period. Select a region, then Run mission to apply it.</p><button id="compare" class="primary" ${pending==="compare"?"disabled":""}>${pending==="compare"?"Comparing regions…":"Compare all regions"}</button><div id="comparison">${siteRows()}</div>`)+`<div class="split">${mapPanel()}${panel("Applied landing point",context)}</div>`;break;
 case "communication":outlet.innerHTML=timelinePanel()+outageTable()+`<p class="callout">${r.outages.length?"Select a blackout above to see its cause in the charts below.":"No direct blackouts were found in this sampled period."} Continue to Step 05 to test the network.</p>`+charts+panel("Applied landing terrain",context)+panel("Darkness intervals",r.darkness.length?table(["Start","End","Duration"],r.darkness.map(d=>row([utc(d.start),utc(d.end),hours(d.hours)]))):'<p class="muted">No darkness intervals in this sampled period.</p>');break;
 case "relays":outlet.innerHTML=`<div class="split">${mapPanel()}${planPanel()}</div>`+panel("Screened relay candidates",`<div class="controls"><label>Sort by <select id="candidate-sort"><option value="recovered" ${candidateSort==="recovered"?"selected":""}>Recovered coverage ↓</option><option value="distance" ${candidateSort==="distance"?"selected":""}>Deployment range ↑</option></select></label></div>`+(r.candidates.length?candidates:'<p class="muted">No screened candidates for these constraints. Review deployment and terrain settings.</p>'))+(choice?relayDetail(choice):"")+timelinePanel()+`<button class="primary" data-go="resilience">Test network resilience <span aria-hidden="true">↗</span></button>`;break;
 case "resilience":outlet.innerHTML=panel("Test the remaining paths",`<div class="controls">${switchers||'<p class="muted">This scenario uses the direct path only; there are no selected relays to disable.</p>'}</div>`+nodes+`<div class="metrics">${metric("NOMINAL COVERAGE",pct(r.network.availability),"All recommended relays")}${metric("SIMULATED COVERAGE",pct(net.metrics.availability),disabled.size+" disabled relays")}${metric("COVERAGE CHANGE",n(net.metrics.availability-r.network.availability)+" pp","Against nominal")}${metric("NOMINAL BLACKOUT",hours(r.network.longest),"Longest continuous outage")}${metric("BLACKOUT CHANGE",hours(net.metrics.longest-r.network.longest),"Against nominal")}</div>`+comparison)+timelinePanel()+panel("Single-relay criticality",`<p class="muted">Worst individual failure: ${pct(r.worst.availability)} availability · ${hours(r.worst.longest)} longest blackout. Nominal time with at least two independent paths: ${pct(r.redundant)}. The table below tests individual failures; toggles above may simulate multiple failures.</p>`+(r.failures.length?failures:'<p class="muted">No selected relays to evaluate.</p>'));break;
 case "sensitivity":outlet.innerHTML=panel("Assumption sweep",`<p class="muted">Re-run the current mission with one changed assumption at a time.</p><button id="sweep" class="primary" ${pending==="sensitivity"?"disabled":""}>${pending==="sensitivity"?"Computing sweep…":"Run sensitivity analysis"}</button><div id="sensitivity-results">${sensitivity?table(["Variant","Direct","Direct blackout","Direct outages","Network","Network blackout","Eligible relays","Selected relays","Feasible"],sensitivity.map(s=>s.error?`<tr><td>${escape(s.label)}</td><td colspan="8">${escape(s.error)}</td></tr>`:row([escape(s.label),pct(s.direct.availability),hours(s.direct.longest),String(s.direct.intervals),pct(s.network.availability),hours(s.network.longest),String(s.eligible),String(s.count),s.feasible?"Yes":"No"]))):""}</div>`)+(sensitivity?panel("Network availability by assumption",sensitivity.filter(s=>!s.error).map(s=>bars(escape(s.label),s.network.availability,true)).join("")):"");break;
 case "data":outlet.innerHTML=panel("Scenario configuration",configuration())+panel("Planning model, with limits",`<p>MOONMESH estimates communication for approximate lunar research points. It is a planning prototype; terrain sampling, RF assumptions, and a restricted relay catalog limit its results.</p><p class="muted">Flat-horizon visibility excludes local terrain. Terrain visibility adds the sampled horizon. Direct RF also requires adequate link margin. A relay path requires surface line of sight, both radio links, and usable power. Missing terrain is uncertain and excluded from credited availability. N−1 tests individual relay failures while retaining shared Earth receiver assumptions.</p>`)+panel("Data provenance",provenance())+panel("Power assumptions",readout([["Battery endurance",hours(Number(r.config.battery_hours))],["Initial charge",pct(Number(r.config.initial_charge_pct))],["Net charging rate",n(Number(r.config.charge_ratio))+" reserve hours / sunlight hour"]]))+[["earth_radio","Earth radio profile"],["surface_radio","Surface radio profile"]].map(([key,title])=>panel(title,readout(rf.map(([name,label])=>[label,n(Number((r.config[key] as Record<string,number>)[name]),3)])))).join("");break;
 }
 connect();paint();
}

function layoutView(){
 const L=layout;if(!L)return panel("Layout","<p class='muted'>Draw an area and calculate to see its layout.</p>");
 const best=L.layouts[L.recommended], nodes=L.nodes, outside=nodes.filter(v=>v.outside).length, worst=Math.min(best.worst_after_node_loss,best.worst_after_link_loss);
 const kpis=`<div class="metrics">${metric("MINES LINKED",pct(100*L.plan.served),`${L.plan.mines.length} mining sites to ${L.plan.gateways.length} gateways`,L.plan.feasible?"green":"amber")}${metric("WORST SINGLE FAILURE",pct(100*worst),"mines still linked after losing any relay or link in the best layout",worst>=1?"green":"amber")}${metric("RELAYS CHOSEN",String(L.plan.chosen.length),`${L.candidates} candidate sites screened`)}${metric("AREA",n(L.site.area_km2,1)+" km²",escape(L.site.name))}${metric("OUTSIDE YOUR SHAPE",String(outside),outside?"sites just beyond the edge, used because nothing inside links up":"all sites are inside",outside?"amber":"green")}</div>`;
 const rows=Object.entries(L.layouts).map(([key,m])=>row([`<button data-format="${key}" aria-pressed="${key===layoutFormat}">${escape(m.name)}</button>${key===L.recommended?' <span class="pill ok">best</span>':""}<small class="coords">${escape(m.blurb)}</small>`,String(m.links),n(m.link_km,0)+" km",String(m.single_points_of_failure),String(m.single_path_links),pct(100*m.worst_after_node_loss),pct(100*m.worst_after_link_loss),n(m.schneider_r_random,2)]));
 const role:Record<string,string>={gateway:"Gateway",relay:"Relay",mine:"Mining site"};
 const sitesTable=table(["Node","Role","Position","Elevation","Sunlight","Earth view",""],nodes.map(v=>row([`${v.role==="gateway"?"G":v.role==="mine"?"M":"R"}${v.id}`,role[v.role],`${n(v.lat,4)}°, ${n(v.lon,3)}° E`,n(v.elevation,0)+" m",pct(100*v.sun),pct(100*v.earth),v.outside?"outside the area":""])));
 return kpis+panel("Best format for this area",`<p class="callout ${worst>=1?"":"amber"}">${escape(L.reason)}</p><p class="muted">Green links on the Moon show the layout selected below. Violet rings mark sites just outside your shape.</p>`)+
  panel("Compare layouts",table(["Layout","Links","Cable","Single points of failure","Single-path links","Mines linked after worst relay loss","after worst link loss","Robustness to random failure"],rows)+`<p class="caption">Select a layout to draw it on the Moon. Every format uses the same relay sites; they differ in which line-of-sight links are built. Robustness is the Schneider R score (0 to 1, higher is better).</p>`)+
  (L.site.resolution_m>20?`<p class="callout amber">Terrain for this area is ${n(L.site.resolution_m,0)} m per pixel${L.site.projection==="local"?" (global LOLA data; the polar caps have sharper 20 m data)":" (thinned to keep large areas fast)"}. Hills and ridges narrower than that are not seen, so treat the result as a first look.${L.site.projection==="local"?" Relay scores come from a model trained on polar terrain, so they are less reliable here.":""}</p>`:"")+panel("Sites",sitesTable+`<p class="caption">${escape(L.score_method)}. Mining sites sit on the lowest gentle ground inside the shape; gateways are the sites with the longest view of Earth. Sunlight and Earth view are shares of a 30 day period from the scenario start.</p>`);
}

function provenance(){
 return readout(Object.entries(result.provenance).map(([key,value])=>{
  const anchor=(url:string,label:string)=>`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(label)}</a>`;
  return [key.replaceAll("_"," "),typeof value==="string"?escape(value):Array.isArray(value)?value.map(url=>anchor(url,url.split("/").pop()!)).join(" · "):Object.entries(value).map(([name,url])=>anchor(url,name)).join(" · ")];
 }));
}

function move(value:number){cursor=value;paint();}
function paint(){
 if(!result)return;
 moon?.update(cursor,disabled,selected);
 const r=result, s=r.samples, i=cursor;
 const t=document.querySelector<HTMLInputElement>("#cursor");if(t)t.value=String(i);
 const time=document.querySelector("#time");if(time)time.textContent=utc(r.times[i]);
 if(document.querySelector("#timeline")){const data=live();timeline(r,data.combined,data.assisted,i,zoom,move);
 $("#timeline-readout").innerHTML=readout([["Selected sample",utc(r.times[i])],["Direct / active network",`${s.status[i]} / ${data.combined[i]?"available":"unavailable"}`],["Cause",escape(s.reason[i])],["Sunlight",!s.solar_known[i]?"Uncertain":s.solar[i]?"Illuminated":"Dark"]]);}
 if(document.querySelector("#map")?.getBoundingClientRect().width)map(r,layer,disabled,i,c=>{selected=c.id;if(active!=="relays")navigate("relays");else render();},selected,mapZoom);
 if(document.querySelector("#horizon")){
    horizon(r,i);
    $("#point").innerHTML=readout([["Time",utc(r.times[i])],["Earth az / el",n(s.earth_az[i])+"° / "+n(s.earth_el[i])+"°"],
    ["Sun az / el",n(s.sun_az[i])+"° / "+n(s.sun_el[i])+"°"],["Terrain at Earth azimuth",n(s.earth_limit[i])+"°"],
    ["Clearance after allowance",n(s.earth_clearance[i],2)+"°"],["Horizon feature distance",s.obstruction_m[i]===null?"Unknown":n(s.obstruction_m[i]!/1000)+" km"],
    ["RF margin",n(s.rf_margin[i])+" dB"],["Status",escape(s.status[i]+" · "+s.reason[i])]]);
 }
 const candidate=r.candidates.find(c=>c.id===selected);
 const sceneSelection=document.querySelector("#scene-selection");if(sceneSelection&&!candidate&&!r.recommended.length)sceneSelection.innerHTML='<p class="callout">No surface relays were recommended for this scenario. Use the optional screened-candidate controls to inspect alternatives.</p>';if(sceneSelection&&candidate)sceneSelection.innerHTML=readout([["Selected tower",candidate.id+(r.recommended.includes(candidate.id)?" · recommended":" · screened candidate")],["Full path availability",pct(candidate.coverage.availability)],["Deployment distance",n(candidate.distance,2)+" km"],["At this time",disabled.has(candidate.id)?"Disabled":candidate.temporal_coverage[cursor]?"Path available":"Path unavailable"]]);
 if(candidate&&document.querySelector("#power-timeline")){powerTimeline(candidate,Number(r.config.battery_hours),i);$("#power-readout").innerHTML=readout([["At selected time",utc(r.times[i])],["Sunlight / powered",`${candidate.sunlight[i]?"Illuminated":"Dark"} / ${candidate.powered[i]?"Powered":"Unpowered"}`],["Battery reserve",hours(candidate.reserve[i])]]);}
}

function connect(){
 const mapDetails=document.querySelector<HTMLDetailsElement>("#map-details");if(mapDetails)mapDetails.ontoggle=()=>{if(mapDetails.open)paint();};
 const button=(id:string,action:()=>void)=>{const node=document.querySelector<HTMLButtonElement>("#"+id);if(node)node.onclick=action;};
 button("zoom",()=>{zoom=zoom<=Math.max(6,result.times.length/8)?result.times.length:Math.max(6,Math.floor(zoom/2));render();});
 button("map-zoom",()=>{mapZoom=mapZoom===3?1:mapZoom+1;render();});
 button("optimize",()=>{
  const invalid=[...outlet.querySelectorAll<HTMLInputElement>("[data-sync]")].find(node=>!node.checkValidity());
  if(invalid){invalid.reportValidity();return;}
  if(JSON.stringify(values())===lastBody){
   $("#optimize-note").textContent=`Limits unchanged. The selection above is already the ${result.feasible?"minimum among screened candidates":"best evaluated configuration"} for them. Change a limit to re-optimize.`;
   return;
  }
  void run();
 });
 button("improve",()=>navigate("relays"));
 button("compare",()=>void auxiliary("compare"));
 button("sweep",()=>void auxiliary("sensitivity"));
 const range=document.querySelector<HTMLInputElement>("#cursor");if(range)range.oninput=()=>move(Number(range.value));
 const layers=document.querySelector<HTMLSelectElement>("#layer");if(layers){layers.value=layer;layers.onchange=()=>{layer=layers.value;paint();};}
 outlet.querySelectorAll<HTMLButtonElement>("[data-time]").forEach(b=>b.onclick=()=>{
  move(Number(b.dataset.time));
  const chart=document.querySelector("#horizon")??document.querySelector("#timeline");
  chart?.scrollIntoView({behavior:matchMedia("(prefers-reduced-motion: reduce)").matches?"auto":"smooth",block:"center"});
 });
 outlet.querySelectorAll<HTMLInputElement>("[data-sync]").forEach(node=>node.oninput=node.onchange=()=>{
  const target=form.elements.namedItem(node.dataset.sync!) as HTMLInputElement;
  if(node.type==="checkbox")target.checked=node.checked;else target.value=node.value;
  $("#optimize-note").textContent="";dirty();
 });
 outlet.querySelectorAll<HTMLButtonElement>("[data-go]").forEach(b=>b.onclick=()=>navigate(b.dataset.go!));
 outlet.querySelectorAll<HTMLButtonElement>("[data-format]").forEach(b=>b.onclick=()=>{layoutFormat=b.dataset.format!;moon?.setLayout(layout,layoutFormat);render();outlet.querySelector<HTMLButtonElement>(`[data-format="${layoutFormat}"]`)?.focus();});
 outlet.querySelectorAll<HTMLButtonElement>("[data-draw-start]").forEach(b=>b.onclick=()=>startDrawing(b.dataset.drawStart as "rect"|"free"));
 outlet.querySelectorAll<HTMLButtonElement>("[data-pole-go]").forEach(b=>b.onclick=()=>moon?.flyToPlace(-89.6,0,160000));
 outlet.querySelectorAll<HTMLButtonElement>("[data-site]").forEach(b=>b.onclick=()=>{if(!b.dataset.site)return;$<HTMLSelectElement>('[name="site"]').value=b.dataset.site;for(const name of ["latitude","longitude"])$<HTMLInputElement>(`[name="${name}"]`).value="";dirty();navigate("configure");$<HTMLSelectElement>('[name="site"]').focus();});
 const sorter=document.querySelector<HTMLSelectElement>("#candidate-sort");if(sorter)sorter.onchange=()=>{candidateSort=sorter.value;render();$("#candidate-sort").focus();};
 const picker=document.querySelector<HTMLSelectElement>("#map-candidate");if(picker){picker.value=selected;picker.onchange=()=>{selected=picker.value;navigate("relays");};}
 outlet.querySelectorAll<HTMLButtonElement>("[data-candidate]").forEach(b=>b.onclick=()=>{selected=b.dataset.candidate!;render();outlet.querySelector<HTMLButtonElement>(`[data-candidate="${selected}"]`)?.focus();});
 outlet.querySelectorAll<HTMLInputElement>("[data-disable]").forEach(b=>b.onchange=()=>{const id=b.dataset.disable!;if(b.checked)disabled.add(id);else disabled.delete(id);render();outlet.querySelector<HTMLInputElement>(`[data-disable="${id}"]`)?.focus();});
}

let auxiliaryRun=0;
async function auxiliary(key:"compare"|"sensitivity"){
 if(pending)return;
 const previous=version, token=++auxiliaryRun;pending=key;
 const trigger=$<HTMLButtonElement>(key==="compare"?"#compare":"#sweep");trigger.disabled=true;trigger.textContent=key==="compare"?"Comparing regions…":"Computing sweep…";
 status(key==="compare"?"Computing all landing regions…":"Computing the sensitivity sweep…");
 try{
  const response=await api<Comparison[]|Sensitivity[]>(key,result.config);
  if(previous!==version||token!==auxiliaryRun)return;
  if(key==="compare")compared=response as Comparison[];else sensitivity=response as Sensitivity[];
  status(`${response.length} ${key==="compare"?"regions":"scenarios"} evaluated`);render();
 }catch(error){if(previous===version&&token===auxiliaryRun)status((error as Error).message,true);}
 finally{pending="";trigger.disabled=false;if(previous===version&&token===auxiliaryRun)render();}
}

function download(contents:string,extension:string,mime:string){
 const href=URL.createObjectURL(new Blob([contents],{type:mime}));
 const anchor=document.createElement("a");anchor.href=href;anchor.download="moonmesh-"+result.config.site+"."+extension;anchor.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
}
$("#json").onclick=()=>download(JSON.stringify({...result,comparison:compared,sensitivity,disabled:[...disabled],active:live()},null,2),"json","application/json");
$("#csv").onclick=()=>{
 const samples=result.samples, columns=Object.keys(samples).filter(k=>k!=="network") as (keyof typeof samples)[];
 const quote=(s:unknown)=>'"'+String(s??"").replaceAll('"','""')+'"';
 const network=live();
 const rows=result.times.map((timestamp,i)=>[timestamp,...columns.map(c=>samples[c][i]),network.assisted[i],network.combined[i]].map(quote).join(","));
 const section=(head:string, records:unknown[][])=>rows.push("",head,...records.map(record=>record.map(quote).join(",")));
 section("configuration,value",Object.entries(result.config).map(([key,value])=>[key,JSON.stringify(value)]));
 section("interval,start,end,hours,causes",[...result.outages.map(o=>["blackout",o.start,o.end,o.hours,o.causes.join("; ")]),...result.darkness.map(o=>["darkness",o.start,o.end,o.hours,""])]);
 section("relay,latitude,longitude_east,distance_km,slope_deg,surface_los,surface_margin_db,powered_pct,coverage_pct,selected,disabled",result.candidates.map(c=>[c.id,c.lat,c.lon,c.distance,c.slope,c.los,c.surface_margin,c.power.availability,c.coverage.availability,result.recommended.includes(c.id),disabled.has(c.id)]));
 section("failed_relay,availability_pct,longest_blackout_h,meets",result.failures.map(f=>[f.id,f.metrics.availability,f.metrics.longest,f.meets]));
 section("optimization_feasible,eligible_relays,active_coverage_pct,active_longest_blackout_h",[[result.feasible,result.eligible,network.metrics.availability,network.metrics.longest]]);
 section("provenance,description",Object.entries(result.provenance).map(([key,value])=>[key,JSON.stringify(value)]));
 if(compared)rows.push("", "site,latitude,longitude,solar_pct,direct_pct,blackout_h,darkness_h,network_pct",...compared.map(c=>[c.site.name,c.site.lat,c.site.lon,c.solar.availability,c.direct.availability,c.direct.longest,c.solar.longest,c.network.availability].map(quote).join(",")));
 if(sensitivity)rows.push("", "variant,direct_pct,direct_blackout_h,network_pct,relay_count,feasible,error",...sensitivity.map(s=>[s.label,s.direct?.availability,s.direct?.longest,s.network?.availability,s.count,s.feasible,s.error].map(quote).join(",")));
 download(["timestamp_utc,"+columns.join(",")+",relay_path,active_network",...rows].join("\n"),"csv","text/csv");
};
$("#report").onclick=async()=>{
 const view=active;
 navigate("communication",false);
 const chartImages=["map","timeline","horizon"].map(id=>`<img src="${$<HTMLCanvasElement>("#"+id).toDataURL()}" alt="Mission ${id} chart">`).join("");
 const net=live();
 document.querySelector("#printable")?.remove();
 const article=document.createElement("article");article.id="printable";
 article.innerHTML=`<h1>MOONMESH mission report</h1><p>${escape(result.site.name)} · ${result.config.start} to ${result.config.end} UTC</p>`+
 readout([["Site",`${n(result.site.lat,5)}°, ${n(result.site.lon,5)}° E`],["Terrain",`${n(result.site.elevation)} m · ${n(result.site.mean_slope)}° average local slope`],["Sunlight",pct(result.solar.availability)],["Direct communication",pct(result.direct.availability)],["Active network",pct(net.metrics.availability)],["Worst single failure",pct(result.worst.availability)],["Selected relays",result.recommended.join(", ")||"Direct only"],["Selection",result.feasible?"Minimum among screened candidates":"Best evaluated configuration"],["Requirements",net.meets?"Met":"Unmet"],["Disabled",Array.from(disabled).join(", ")||"None"]])+
 chartImages+outageTable()+
 panel("Relay feasibility and single-relay failures",result.candidates.filter(c=>result.recommended.includes(c.id)).map(c=>relayDetail(c,false)).join("")+result.failures.map(f=>`<p>Disable ${f.id} · ${pct(f.metrics.availability)} availability · ${hours(f.metrics.longest)} longest blackout · ${f.meets?"requirements met":"requirements unmet"}</p>`).join(""))+
 panel("Scenario configuration",configuration())+panel("Assumptions and provenance",provenance());
 document.body.append(article);
 await Promise.all(Array.from(article.querySelectorAll("img")).map(img=>img.decode().catch(()=>{})));
 try{window.print();}finally{navigate(view,false);article.remove();}
};

window.addEventListener("resize",paint);
function startDrawing(kind:"rect"|"free"){setPanel(false);moon?.setDrawMode(kind);status("Drawing: drag on the Moon. Near the south pole the terrain is sharpest (use Go to the pole).");}
function chooseRegion(_site:string){startDrawing("rect");}
function onArea(polygon:[number,number][]){
 area=polygon.length>=3?polygon:undefined;areaKey=JSON.stringify(area??[]);areaDirty=true;layout=undefined;layoutKey="";moon?.setLayout(undefined,"");
 if(!area)moon?.showArea(undefined);
 dirty();navigate(area?"configure":"landing",false);
 status(area?"Area drawn. Choose dates and goals, then Calculate network.":"Area cleared.");
}
function selectTower(c:Candidate){
 selected=c.id;moon?.update(cursor,disabled,selected);
 if(active==="overview")paint();else if(active!=="landing"&&active!=="configure")render();
 else{status(`Tower ${c.id} · ${pct(c.coverage.availability)} full-path availability · ${n(c.distance,2)} km from the lander`);}
}
function usePin(lat:number,lon:number,_site:string){
 // A 6 km square built from great-circle offsets, so it stays square at any latitude.
 const dLat=3000/1737400*180/Math.PI, dLon=dLat/Math.max(0.05,Math.cos(lat*Math.PI/180));
 const polygon=[[-1,-1],[-1,1],[1,1],[1,-1]].map(([a,b])=>[Math.max(-90,Math.min(90,lat+a*dLat)),(lon+b*dLon+360)%360] as [number,number]);
 moon?.showArea(polygon);onArea(polygon);
}
moon=new MoonView($("#moon"),{onUsePin:usePin,onChooseRegion:chooseRegion,onSelectCandidate:selectTower,onArea});
(window as unknown as {__moonview?:MoonView}).__moonview=moon;   // diagnostics hook for browser tests
async function initialize(){
 try{
  const conf=await api<{defaults:Record<string,unknown>;sites:Record<string,[string,number,number]>}>("config");
  sites=conf.sites;
  const region=$<HTMLSelectElement>('select[name="site"]');
  region.replaceChildren();
  Object.entries(conf.sites).forEach(([id,[name]])=>region.add(new Option(name,id)));
  for(const [key,value] of Object.entries(conf.defaults)){
   if(value&&typeof value==="object"){for(const [field,v] of Object.entries(value))$<HTMLInputElement>(`[name="${key}.${field}"]`).value=String(v);}
   else{
    const input=document.querySelector<HTMLInputElement>(`[name="${key}"]`);
    if(input){if(input.type==="checkbox")input.checked=Boolean(value);else input.value=value===null?"":String(value);}
   }
  }
  $<HTMLButtonElement>('button[type="submit"]').disabled=false;
  configured=true;dirty();status("Ready. Explore the Moon, then continue to mission setup.");navigate(active,false);
 }catch(error){status((error as Error).message,true);$("#retry").hidden=false;$("#dirty").textContent="Settings unavailable — retry setup";}
}
$("#retry").onclick=()=>{$("#retry").hidden=true;void initialize();};
render();
void initialize();
