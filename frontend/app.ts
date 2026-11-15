import {outageEvidence, sampleEvidence, relayEvidence, scopedOutages, participatingPaths, candidateAvailable, marginalMetrics} from "./mission-evidence.js";
import {MoonView} from "./moon.js";
import {surfaceCoverage} from "./surface.js";
import {icon} from "./icons.js";
import {map, timeline, horizon, powerTimeline} from "./charts.js";
import type {Result, Comparison, Candidate, Sensitivity, Metrics, AreaResult} from "./types.js";
import {assessmentBadge, assessmentHtml} from "./evidence.js";
import {ORBIT_PERIOD, satelliteAccess, satellitePosition} from "./orbital-preview.js";

const $=<T extends HTMLElement=HTMLElement>(q:string)=>document.querySelector<T>(q)!;
const form=$<HTMLFormElement>("#mission"), outlet=$("#view");
let result:Result, active="landing", cursor=0, zoom=0, mapZoom=1, layer="elevation";
let selected="", compared:Comparison[]|undefined, sensitivity:Sensitivity[]|undefined;
let disabled=new Set<string>(), busy=false, version=0, lastBody="";
let candidateSort="recovered", pending="", exporting=false;
let moon:MoonView|undefined;
let stale=false, configured=false, shownStale=false;
let area:[number,number][]|undefined, areaKey="", areaDirty=false, layout:AreaResult|undefined, layoutKey="", layoutFormat="";
let satelliteSeconds=0, researchMission=false;
let evidenceScope="network", evidenceCase="cold_dust", alternativePreview="";
const steps=["landing","configure","layout","overview","communication","resilience","cost"];
const MOON_R=1737400, DEG=Math.PI/180;
const toXY=(lat:number,lon:number)=>{const rho=2*MOON_R*Math.tan((90+lat)*DEG/2);return [rho*Math.sin(lon*DEG),rho*Math.cos(lon*DEG)];};
const toLatLon=(x:number,y:number):[number,number]=>[2*Math.atan(Math.hypot(x,y)/(2*MOON_R))/DEG-90,((Math.atan2(x,y)/DEG)%360+360)%360];
let sites:Record<string,[string,number,number]>={};
const views:Record<string,[string,string]>={landing:["Start with the Moon.","01 · Explore the surface. Then draw the area you want to analyse: a square or any shape."],configure:["Define your mission.","Select an area, choose your dates, and set your communication goals."],layout:["Plan the network.","03 · The best layout of towers and links inside the area you drew, compared with simpler formats."],overview:["Your mission, in three dimensions.","04 · Inspect the lander and calculated relay network. Explore the local terrain, or switch to Whole Moon for context."],sites:["Choose your horizon.","Compare landing regions over the same mission period and assumptions."],communication:["Understand the silence.","Trace each communication blackout to terrain, geometry, and RF conditions."],relays:["Bridge the distance.","Explore the smallest screened relay configuration and its physical constraints."],resilience:["Plan for the unexpected.","Disable a relay and inspect how the remaining paths perform."],cost:["Review the network cost.","See the total installed network estimate and download the itemized Excel breakdown."],sensitivity:["Question the assumptions.","Change one assumption at a time and compare the modeled outcome."],data:["The science behind the signal.","Inspect sources, model assumptions, and the limits of these planning estimates."]};
views.surface=["Surface coverage","Explore simulated surface links independently of mission dates."];
document.querySelector('.menu-pop')?.insertAdjacentHTML('afterbegin','<button data-view="surface">Surface coverage</button>');
function dirty(){
 // Compare only editable fields; API configuration may include additional model defaults.
 const same=(a:unknown,b:unknown,key=""):boolean=>key==="epoch"&&typeof a==="string"&&typeof b==="string"&&Number.isFinite(Date.parse(a))?Date.parse(a)===Date.parse(b):a!==null&&typeof a==="object"?Object.entries(a).every(([k,v])=>same(v,(b as Record<string,unknown>)?.[k],k)):a===b;
 const edited=(!!result&&!same(values(),result.config))||(!!result&&areaDirty);stale=edited;
 $("#dirty").textContent=edited?"Changes not applied":result?"Scenario applied":!area&&!researchMission?"Draw an area first":configured?"Ready to calculate":"Preparing scenario";
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
$(".skip-link").onclick=e=>{e.preventDefault();setPanel(true);$("#title").focus();};
function setNavigation(open:boolean){
 document.body.classList.toggle("nav-open",open);
 $("#workspace-menu").setAttribute("aria-expanded",String(open));
}
$("#workspace-menu").onclick=()=>setNavigation(!document.body.classList.contains("nav-open"));
$("#nav-scrim").onclick=()=>setNavigation(false);
$("#journey").addEventListener("click",e=>{if((e.target as HTMLElement).closest("button"))setNavigation(false);});
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!document.body.classList.contains("panel-collapsed")&&matchMedia("(max-width:767px)").matches&&(e.target as HTMLElement).closest?.("#panel"))setPanel(false);});
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&document.body.classList.contains("nav-open"))setNavigation(false);});
function updateNavigation(){
 document.querySelectorAll<HTMLButtonElement>('[data-view]').forEach(b=>{
  const available=["landing","configure","surface"].includes(b.dataset.view!)||(!!result&&!stale);
  b.disabled=!available;b.title=available?"":"Complete mission setup and calculate a network first.";
  b.classList.toggle("completed",!!result&&!stale&&steps.indexOf(b.dataset.view!)<steps.indexOf(active));
 });
 for(const id of ["json","csv","report","xlsx"])$<HTMLButtonElement>("#"+id).disabled=!result||stale||(id==="xlsx"&&exporting);
 document.querySelectorAll<HTMLButtonElement>("[data-excel]").forEach(b=>b.disabled=!result||stale||exporting);
 $(".export-menu").hidden=!result||stale||active==="landing";
 if(moon&&shownStale!==stale){shownStale=stale;moon.setMission(stale?undefined:result);}
 $("#start-mission").hidden=active!=="landing";
 $<HTMLButtonElement>('button[type="submit"]').disabled=busy||!configured||(!area&&!researchMission);
}
$("#start-mission").onclick=()=>navigate("configure");
$("#explore-surface").onclick=()=>{
 document.body.classList.add("exploring");
 moon?.flyToSite(-89.6,0,160_000);
 status("Explore the south pole. Draw an area to start planning your network.");
};
// Landing hero: touching the Moon leaves the hero and opens the full-screen explorer. Presentation only.
$("#moon").addEventListener("pointerdown",()=>{
 if(active!=="landing"||!moon||!document.body.classList.contains("panel-collapsed")||document.body.classList.contains("exploring"))return;
 document.body.classList.add("exploring");status("Exploring the Moon. Draw an area to start planning your network.");
},true);
// Cards follow the pointer with a soft highlight.
document.querySelectorAll<HTMLElement>(".explore-card").forEach(card=>card.addEventListener("pointermove",e=>{
 const box=card.getBoundingClientRect();card.style.setProperty("--mx",`${e.clientX-box.left}px`);card.style.setProperty("--my",`${e.clientY-box.top}px`);
}));
const regionViews:Record<string,[number,number]>={haworth:[-86.8,337.2],nobile:[-85.4,37.3],malapert:[-85.964,357.681]};
document.querySelectorAll<HTMLButtonElement>("[data-region]").forEach(button=>button.onclick=()=>{
 const region=button.dataset.region!, position=regionViews[region]; if(!position)return;
 document.body.classList.add("exploring");
 moon?.flyToSite(position[0],position[1],40_000);
 status(`Exploring ${button.textContent?.replace("↗", "").trim()}. Draw an area to plan a network.`);
});
function navigate(view:string,focus=true){
 if(!["landing","configure","surface"].includes(view)&&(!result||stale))view="configure";
 active=view;
 moon?.setSatellitePreview(view==="layout"&&!result?.satellite?layout:undefined,satelliteSeconds);
 if(view==="landing")document.body.classList.remove("exploring");
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
 const next=({landing:"configure",layout:"overview",overview:"communication",communication:"resilience",resilience:"cost"} as Record<string,string>)[active]??"";
 const nextLabel=next==="configure"?"Set up my mission":next==="overview"?"Next: the Earth link":next==="communication"?"Next: understand blackouts":next==="resilience"?"Next: test resilience":"Next: review cost";
 $("#flow-actions").innerHTML=`<div><span class="eyebrow">${index<0?"OPTIONAL ANALYSIS":`STEP ${index+1} / ${steps.length}`}</span><p>${active==="landing"?"First, explore. Then set the mission parameters.":active==="configure"?"Your network is calculated only when you submit this form.":active==="cost"?"Download the itemized workbook or revise the mission assumptions.":"Your applied mission stays with you at every step."}</p></div><div class="flow-buttons">${index!==0?`<button data-go="${previous}">← ${index<0?"Back to the Earth link":"Back"}</button>`:""}${next?`<button class="primary" data-go="${next}">${nextLabel} →</button>`:active==="cost"?'<button data-go="configure">Revise mission parameters</button>':""}</div>`;
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
 ["budget_usd","Relay budget (USD, optional)",0,1e12,.01,"Hard limit on total relay deployment cost. Leave blank for no cost limit."],
 ["relay_cost_usd","All-in installed network node cost (USD, optional)",0,1e12,.01,"Leave blank for the illustrative $100,000 equipment and installation estimate per installed node. Enter your own all-in estimate to override it. Launch and operations are excluded."],
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
const environmentalFields:typeof fields=[
 ["dark_sink_k","Nominal dark sink (K)",20,350,1,"Assumed effective thermal sink, not air temperature or Diviner measurements."],
 ["cold_sink_k","Cold case sink (K)",20,350,1,"Colder bounding case; must not exceed nominal dark sink."],
 ["lit_sink_k","Sunlit sink (K)",20,400,1,"Assumed effective thermal sink in full sunlight."],
 ["node_load_w","Electronics load (W)",.1,500,.1,"Applied to each relay and the lander; battery Wh = endurance hours × load."],
 ["heater_w","Heater capacity (W)",0,500,.1,"Thermostat controlled heater; its energy comes from the node battery."],
 ["heat_capacity","Box heat capacity (J/K)",100,1e7,100,"Illustrative hardware parameter; calibrate against a real design."],
 ["emissivity","Insulated box emissivity",.001,1,.001,"Effective emissivity below radiator opening temperature."],
 ["radiating_area_m2","Radiating area (m²)",.001,100,.001,"Hardware heat loss area."],
 ["conductance_w_k","Ground conductance (W/K)",0,10,.001,"Assumed conductive heat path to the effective sink."],
 ["heater_on_k","Heater set point (K)",100,350,.01,"Heater starts below this internal temperature."],
 ["freeze_k","Irreversible freeze limit (K)",50,300,.01,"Assumed hardware survival limit; must be below the heater set point."],
 ["lander_battery_hours","Lander endurance (h)",0,1440,1,"Lander battery capacity at configured electronics load."],
 ["lander_initial_charge_pct","Lander initial charge (%)",0,100,1,"Relay initial charge is set separately above."],
 ["dust_start_h","Dust event start (mission h)",0,1440,.5,"Local deposition event, not a lunar weather forecast."],
 ["dust_duration_h","Dust event duration (h)",.1,1440,.1,"Duration of deposition; deposited dust remains until cleaned."],
 ["dust_radius_km","Dust event radius (km)",0,20,.1,"Assumed affected radius centered on the lander."],
 ["dust_deposition_per_day","Dust deposition / day",0,1,.05,"Normalized fouling added per day during exposure."],
 ["dust_cleaning_per_day","Dust removal / day",0,1,.05,"Assumed cleaning effectiveness; zero means no cleaning."],
 ["dust_solar_loss","Solar loss at full fouling",0,1,.05,"Fraction of solar output lost at dust load 1. RF propagation is unchanged."],
];
const rf:[string,string,number,number,number,string][]=[
 ["ghz","Frequency (GHz)",.001,100,.001,"Carrier frequency."],["watts","Transmitter (W)",.01,1e5,.01,"Radiated profile uses transmitter power and antenna gain."],
 ["tx_gain","TX gain (dBi)",-30,120,.1,"Assumed transmitting antenna gain."],["rx_gain","RX gain (dBi)",-30,120,.1,"Assumed receiving antenna gain."],
 ["sensitivity","Sensitivity (dBm)",-200,0,.1,"Receiver threshold at reference data rate of 128 kbps."],
 ["loss","Losses (dB)",0,100,.1,"Additional modeled losses."],["margin","Required margin (dB)",0,60,.1,"Minimum margin over receiver threshold."],
 ["rate_kbps","Data rate (kbps)",.001,1e7,.001,"Rate scales the reference receiver threshold."],
];
const input=([key,label,min,max,step,help]:typeof fields[number],prefix="")=>`<label title="${help}">${label}<input type="number" min="${min}" max="${max}" step="${step}" name="${prefix+key}" ${["latitude","longitude","budget_usd","relay_cost_usd"].includes(key)?"":"required"}></label>`;
$("#expert").innerHTML=`<fieldset><legend>Mission and deployment constraints</legend><div class="fields">${fields.filter(f=>!["availability","blackout_hours","max_relays","deployment_km","budget_usd","relay_cost_usd"].includes(f[0])).map(f=>input(f)).join("")}<label title="Require compliance after any selected relay or the configured satellite fails"><span><input type="checkbox" name="resilient"> Require N−1</span></label></div></fieldset>`+
 [["earth_radio","Direct Earth and relay-to-Earth RF"],["surface_radio","Lander-to-relay RF"]].map(([key,label])=>`<fieldset><legend>${label}</legend><div class="fields">${rf.map(f=>input(f,key+".")).join("")}</div></fieldset>`).join("");

$("#expert").insertAdjacentHTML("beforeend",`<fieldset><legend>Night temperatures and dust exposure</legend><p>Four cases are checked: nominal, cold, dust, and cold with dust. Temperatures and deposition are assumed bounds; hardware constants are illustrative. The Moon has no wind driven dust storms.</p><div class="fields">${environmentalFields.map(f=>input(f)).join("")}</div></fieldset>`);

// Satellite settings are explicit scenario inputs. Empty RF profiles remain unknown.
$("#expert").insertAdjacentHTML("beforeend",`<fieldset><legend>Simulated satellite backhaul</legend><label>Orbit <select name="satellite_preset"><option value="">Disabled · Earth baseline</option><option value="polar_500km">500 km polar · simulated</option><option value="frozen_elliptical_south">South elliptical · simulated</option><option value="custom">Custom two-body elements</option></select></label><label>Epoch (ISO UTC, optional)<input name="satellite_epoch" placeholder="Mission start if blank"></label><label>Minimum elevation (°)<input name="satellite_mask" type="number" min="0" max="60" value="5"></label><p>Elements use Moon-centred J2000 axes, km and degrees. Preset overrides are optional; custom requires all six.</p><div class="fields">${["semi_major_axis_km","eccentricity","inclination_deg","raan_deg","arg_periapsis_deg","mean_anomaly_deg"].map(k=>`<label>${k.replaceAll("_"," ")}<input type="number" step="any" name="satellite_${k}"></label>`).join("")}</div><p>TODO: supply verified radios. Blank profiles give geometry-only upper bounds. Enter a complete Radio JSON object to evaluate each RF hop.</p><label>Surface → satellite radio JSON<textarea name="satellite_surface_rf" rows="3"></textarea></label><label>Satellite → Earth radio JSON<textarea name="satellite_earth_rf" rows="3"></textarea></label><p class="caption">Satellite power, pointing and capacity are assumed; service cost is unknown.</p></fieldset>`);

$("#basic-constraints").innerHTML=fields.filter(f=>["availability","blackout_hours","max_relays","deployment_km","budget_usd","relay_cost_usd"].includes(f[0])).map(f=>input(f)).join("");

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
   if(key.startsWith("satellite_"))continue;
   const [parent,child]=key.split(".");
   if(child)(next[parent] as Record<string,number>)[child]=Number(value);
   else next[key]=["site","start","end"].includes(key)?value:value===""?null:Number(value);
 }
 const field=(key:string)=>(form.elements.namedItem("satellite_"+key) as HTMLInputElement).value.trim();
 const preset=field("preset");
 const satellite:Record<string,unknown>={preset};
 if(field("mask"))satellite.min_elevation_deg=Number(field("mask"));
 for(const key of ["semi_major_axis_km","eccentricity","inclination_deg","raan_deg","arg_periapsis_deg","mean_anomaly_deg"])if(field(key))satellite[key]=Number(field(key));
 if(field("epoch"))satellite.epoch=field("epoch");
 for(const [name,key] of [["surface_rf","surface_to_sat_radio"],["earth_rf","sat_to_earth_radio"]]){
  const node=form.elements.namedItem("satellite_"+name) as HTMLTextAreaElement;
  try{satellite[key]=node.value.trim()?JSON.parse(node.value):null;node.setCustomValidity("");}
  catch{node.setCustomValidity(preset?"Enter a valid Radio JSON object.":"");satellite[key]=null;}
 }
 next.satellite=preset?satellite:null;
 next.resilient=$<HTMLInputElement>('input[name="resilient"]').checked;
 return next;
}

async function run(){
 if(!configured||busy||(!area&&!researchMission)||!form.reportValidity())return;
 busy=true;
 document.body.classList.add("computing");$("#view").setAttribute("aria-busy","true");
 $<HTMLButtonElement>('button[type="submit"]').disabled=true;
 status("Computing terrain, ephemerides and candidate relay paths…");
 try{
   const limits=values();
   const layoutRequest={polygon:area,max_relays:limits.max_relays,budget_usd:limits.budget_usd,relay_cost_usd:limits.relay_cost_usd};
   const fresh=!!area&&layoutKey!==JSON.stringify(layoutRequest);
   if(fresh){
    status("Choosing relay sites and comparing layouts inside your area… about 20 seconds, up to a minute or two for very large areas.");
    layout=await api<AreaResult>("ora/area",layoutRequest);layoutKey=JSON.stringify(layoutRequest);layoutFormat=layout.recommended;
    const region=$<HTMLSelectElement>('[name="site"]');if(!region.querySelector(`option[value="${layout.site.key}"]`))region.add(new Option(layout.site.name,layout.site.key));
    region.value=layout.site.key;for(const name of ["latitude","longitude"])$<HTMLInputElement>(`[name="${name}"]`).value="";
    sites[layout.site.key]=[layout.site.name,layout.site.lat,layout.site.lon];
    status("Computing terrain, ephemerides and candidate relay paths…");
   }
   const requested=values(), key=JSON.stringify(requested);
   const next=await api<Result>("mission",requested);
   result=next; version++;
   disabled.clear(); evidenceScope="network";evidenceCase="cold_dust";alternativePreview="";cursor=0; zoom=result.times.length; mapZoom=1;
   selected=result.recommended[0]??""; if(key!==lastBody){compared=undefined;sensitivity=undefined;}lastBody=key;
   for(const id of ["json","csv","report"]) $<HTMLButtonElement>("#"+id).disabled=false;
   $(".export-options p").textContent="Exports use the applied scenario and current relay failure state.";
   $("#subtitle").textContent=`${result.site.name} · ${result.config.start} to ${result.config.end} UTC`;
   status(`${result.times.length.toLocaleString()} samples · ${result.config.minutes} min intervals · ${result.candidates.length} relay candidates evaluated`);
   areaDirty=false;shownStale=false;dirty();moon?.setMission(result);
   if(fresh&&layout){satelliteSeconds=0;moon?.setLayout(layout,layoutFormat);if(!result.satellite){moon?.setSatellitePreview(layout,0);moon?.showSatelliteOverview(layout,0);}else moon?.showMissionSatellite();}
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
 const state=result.failure_states[[...disabled].sort().join(",")||"nominal"];
 const current=state.cases?.[evidenceCase];
 if(!current)return state;
 const assisted=result.times.map((_,i)=>participatingPaths(result).some(p=>p.relay!==null&&!p.nodes.some(n=>disabled.has(n))&&p.available[evidenceCase][i]));
 return {...state,combined:current.combined,metrics:current.metrics,assisted};
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
 <div class="legend"><span style="--key:#779ebd">Flat horizon</span><span>Terrain visible</span><span style="--key:var(--green)">Lander paths / combined</span><span style="--key:var(--amber)">Sunlight</span><span style="--key:#bba9ff">Relay path</span><span style="--key:var(--red)">Blackout</span><span class="unknown">Uncertain</span></div><p class="caption">Use arrow keys on the time slider to inspect individual samples. Times are UTC.</p><div id="timeline-readout" class="timeline-readout"></div><details class="track-help"><summary>What the tracks mean</summary><dl>
 <dt>Flat horizon</dt><dd>Earth is above the horizon of a smooth Moon with no terrain.</dd>
 <dt>Terrain visible</dt><dd>Earth clears the real terrain horizon plus the clearance allowance. A site on high ground can see past the flat horizon, so this can be on while Flat horizon is off.</dd>
 <dt>Lander paths</dt><dd>The powered lander reaches Earth directly or through configured S1; missing satellite radios mean geometry-only upper bounds.</dd>
 <dt>Sunlight</dt><dd>The Sun's center clears the terrain horizon plus the clearance allowance.</dd>
 <dt>Relay path</dt><dd>At least one active relay links the lander to Earth.</dd>
 <dt>Combined</dt><dd>Direct or relay communication is available.</dd>
 <dt>Network outage</dt><dd>Every selected complete path is unavailable in the displayed environmental case and failure state.</dd>
 <dt><span class="swatch uncertain"></span>Brown</dt><dd>Uncertain: terrain data is incomplete in that direction.</dd>
 </dl></details>`);
}

const horizonPanel=()=>panel("360° terrain horizon",`<canvas id="horizon" class="horizon" aria-label="Horizon elevation versus azimuth"></canvas><p class="caption">Azimuth (°) horizontally · Elevation (°) vertically. Solid cyan: terrain; dashed: clearance allowance. Green Earth and amber Sun markers. The line beneath Earth shows its clearance (green clear, red blocked); gaps indicate unknown terrain.</p><div id="point"></div>`);
const usd=(value:number)=>new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",maximumFractionDigits:2}).format(value);
function costPanel(cost:Result["cost"],scope:"mission"|"area"="mission",downloadable=true){
 return panel("Total estimated network installation cost",`<div class="hero-value">${usd(cost.total_usd)}</div><p class="caption">${escape(cost.assumptions)}</p>${downloadable?`<button data-excel="${scope}" ${exporting||stale?"disabled":""}>${exporting?"Preparing Excel…":"Download Excel breakdown"}</button>`:""}`);
}
const planState=()=>!result.feasible?"Best evaluated configuration · requirements unmet":result.recommended.length?"Minimum among screened candidates · modeled limits met under assumptions":"Direct path meets modeled limits under assumptions · no relays needed";
const bars=(label:string,value:number,after=false)=>`<div class="muted">${label} · ${pct(value)}</div><div class="chart-bar ${after?"after":""}"><span style="width:${value}%"></span></div>`;
function outageTable(){
 return outageEvidence(result,disabled,evidenceScope,evidenceCase)+'<p class="caption">Geographic evidence: red outlines mark failed components, amber marks unknown evidence, squares mark approximate controlling terrain samples. Locations outside the view or without coordinates remain in the inspector.</p>'+panel("Selected sample · path and node evidence",'<div id="outage-inspector"></div>');
}

function powerImage(c:Candidate){
 const element=document.createElement("canvas");
 powerTimeline(c,Number(result.config.battery_hours),-1,element);
 return element.toDataURL();
}
function relayDetail(c:Candidate,includeTimeline=true){
 const reason=c.los===null?"Terrain coverage is incomplete.":!c.los?"Terrain obstructs the lander-to-relay path.":c.surface_margin<Number((result.config.surface_radio as Record<string,number>).margin)?"Surface RF margin is below the requirement.":c.recovered>0?`This candidate covers ${pct(c.recovered)} of the mission during lander-path outages.`:"This candidate recovers zero lander-path outage intervals.";
 return panel("Relay "+c.id,`<p class="callout">${reason}</p>`+readout([
 ["Coordinates",`${n(c.lat,5)}°, ${n(c.lon,5)}° E`],["Deployment estimate",n(c.distance,2)+" km"],["Elevation",n(c.elevation)+" m"],
 ["Local slope",n(c.slope)+"°"],["Surface margin",n(c.surface_margin)+" dB"],[result.satellite?"Relay backhaul (Earth or S1)":"Relay-to-Earth feasibility",pct(c.earth.availability)],
 ["Sunlight",pct(c.solar.availability)],["Powered",pct(c.power.availability)],["Full relay path",pct(c.coverage.availability)],["Uncertain Earth samples",String(c.unknown_samples)],
 ["Minimum temperature",n(c.environment.cold_dust.minimum_temperature_c)+" °C"],["Heater energy",n(c.environment.cold_dust.heater_energy_wh)+" Wh"],["Final dust load",n(c.environment.cold_dust.dust_load.at(-1),2)],
 ["Minimum Earth-link margin",n(c.minimum_earth_margin)+" dB"],["Power outages",String(c.power.intervals)],["Battery capacity",hours(Number(result.config.battery_hours))],["Minimum battery reserve",hours(c.minimum_reserve)],
 ])+(includeTimeline?`<p class="muted">Sunlight and usable power over the mission</p><div id="power-readout"></div><canvas id="power-timeline" class="power-timeline" aria-label="Relay sunlight, power and battery reserve"></canvas>`:`<img src="${powerImage(c)}" alt="Relay ${c.id} sunlight, power and battery reserve">`));
}
function planPanel(){
 const field=(key:string)=>form.elements.namedItem(key) as HTMLInputElement;
 const limit=(key:string,label:string,min:number,max:number,step:number)=>`<label>${label}<input type="number" data-sync="${key}" min="${min}" max="${max}" step="${step}" value="${escape(field(key).value)}" ${["budget_usd","relay_cost_usd"].includes(key)?"":"required"}></label>`;
 return panel("Restricted candidate search",`<p class="callout ${result.feasible?"":"amber"}">${planState()}</p>`+readout([
 ["Selected",result.recommended.join(", ")||"Direct path only"],["Usable candidates",`${result.eligible} of ${result.candidates.length} screened`],
 ["Required coverage",pct(Number(result.config.availability))],["Maximum blackout",hours(Number(result.config.blackout_hours))],
 ["Requirement",result.config.resilient?"Survive any single relay failure (N−1)":"Nominal coverage"],
 ])+`<h3 class="limits-title">Optimization limits</h3><div class="limits">${limit("max_relays","Maximum relays",0,4,1)}${limit("budget_usd","Relay budget (USD, optional)",0,1e12,.01)}${limit("availability","Minimum coverage (%)",0,100,.1)}${limit("blackout_hours","Max blackout (h)",0,1440,.5)}<label class="check"><input type="checkbox" data-sync="resilient" ${field("resilient").checked?"checked":""}> Require N−1</label></div>
 <p><button id="optimize" class="primary">Optimize relay network</button></p><p id="optimize-note" class="muted" role="status"></p>`);
}
function siteRows(){
 if(!compared)return '<p class="muted">Compare all three regions over the current period and assumptions.</p>';
 return table(["Region","Sunlight","Longest darkness","Direct to Earth","Longest direct blackout","With relays","Relays","Limits met","DEM coverage"],
 compared.map(c=>row([`<button data-site="${Object.entries(sites).find(([,v])=>v[0]===c.site.name)?.[0]??""}">${escape(c.site.name)}${c.site.name===result.site.name?" · current":""}</button><small class="coords">${n(c.site.lat,3)}°, ${n(c.site.lon,3)}° E</small>`,pct(c.solar.availability),hours(c.solar.longest),pct(c.direct.availability),hours(c.direct.longest),
 `<strong>${pct(c.network.availability)}</strong>`,escape(c.recommended.join(", ")||"None"),c.assessment?assessmentBadge(c.assessment):c.feasible?'<span class="amber">Met under assumptions</span>':'<span class="amber">Unmet</span>',pct(c.site.terrain_coverage)])),"compare");
}
function configuration(){
 const names:Record<string,string>={site:"Landing region",start:"Start (UTC)",end:"End (UTC)",minutes:"Sampling (min)",resilient:"Require N−1",dem_bias_m:"Terrain bias (m)",
  earth_radio:"Direct Earth and relay-to-Earth RF",surface_radio:"Lander-to-relay RF",...Object.fromEntries([...fields,...environmentalFields].map(([key,label])=>[key,label]))};
 const radio=(r:Record<string,number>)=>`${r.ghz} GHz · ${r.watts} W · TX ${r.tx_gain} dBi · RX ${r.rx_gain} dBi · ${r.sensitivity} dBm sensitivity · ${r.loss} dB losses · ${r.margin} dB required margin · ${r.rate_kbps} kbps`;
 return readout(Object.entries(result.config).filter(([,v])=>v!==null).map(([key,v])=>[names[key]??key.replaceAll("_"," "),
  escape(key==="site"?result.site.name:typeof v==="boolean"?(v?"Yes":"No"):v&&typeof v==="object"?radio(v as Record<string,number>):String(v))]));
}
function environmentPanel(){
 const e=result.environment, state=e.lander[evidenceCase];
 return panel("Night temperature and dust checks",`<p class="callout amber">Assumed environmental cases · hardware survival is unvalidated.</p><p>${escape(e.assumptions)}</p>`+
 table(["Case","Network coverage","Longest blackout","Worst single failure","Modeled limits"],Object.entries(e.cases).map(([name,c])=>row([escape(name.replaceAll("_"," + ")),pct(c.network.availability),hours(c.network.longest),pct(c.worst.availability),c.meets?"Pass under assumptions":"Unmet"])))+
 readout([["Displayed timeline",evidenceCase.replaceAll("_"," + ")],["Lander minimum temperature",n(state.minimum_temperature_c)+" °C"],["Lander minimum charge",pct(state.minimum_soc*100)],["Lander heater energy",n(state.heater_energy_wh)+" Wh"],["Lander geometry/RF paths (before power)",pct(e.geometric_direct.availability)]]));
}
function render(){
 document.body.dataset.step=active;flow();form.hidden=active!=="configure";$("#area-setup").hidden=active!=="configure";
 if(active==="surface"){$("#flow-actions").innerHTML="";surfaceCoverage(outlet);return;}
 if(active==="landing"){
  outlet.innerHTML=`<section class="panel introduction"><p class="eyebrow">A CONNECTION STARTS HERE</p><h2>From the surface to a working plan.</h2><p>Search, zoom and click the Moon to read coordinates, elevation and nearby features. Then <strong>draw the area you care about</strong>, a square or any shape of any size anywhere on the Moon, and MOONMESH plans the towers and links inside it.</p><div class="draw-actions"><button class="primary" data-draw-start="rect">${icon("rectangle")} Draw a square</button><button data-draw-start="free">${icon("pen")} Draw freeform</button><button data-pole-go>${icon("target")} Go to the pole</button></div><p>Terrain can hide Earth from a lunar lander. Relay towers may help restore contact. MOONMESH shows when, where, and under which assumptions.</p><ol class="journey-list"><li><strong>Draw an area</strong><span>Any size, anywhere on the Moon.</span></li><li><strong>Plan the layout</strong><span>Towers, gateways and links, with tree, loop and mesh compared.</span></li><li><strong>Understand and test it</strong><span>The Earth link, blackouts and relay failures.</span></li></ol><button class="primary" data-go="configure">Set up my mission →</button><p class="caption">${result&&!stale?"Your applied mission is ready in Step 3.":"No towers are placed before you calculate a mission."}</p></section>`;
  connect();return;
 }
 if(active==="configure"){
  $("#area-setup").innerHTML=panel("01 / Mission area",area?`<p class="callout">${area.length} corner${area.length===1?"":"s"} drawn. Calculate to plan the network inside it.</p><div class="draw-actions"><button data-draw-start="rect">Redraw as a square</button><button data-draw-start="free">Redraw freeform</button></div>`:`<p class="callout amber">Draw the area to analyse first. Click a button below, then drag on the Moon.</p><div class="draw-actions"><button class="primary" data-draw-start="rect">${icon("rectangle")} Draw a square</button><button data-draw-start="free">${icon("pen")} Draw freeform</button><button data-pole-go>${icon("target")} Go to the pole</button></div>`).replace("</section>",`<label>Research point<select id="research-site">${Object.entries(sites).filter(([k])=>!k.startsWith("area-")).map(([k,v])=>`<option value="${k}" ${String(form.elements.namedItem("site")&&(form.elements.namedItem("site") as HTMLSelectElement).value)===k?"selected":""}>${escape(v[0])}</option>`).join("")}</select></label><button type="button" id="use-research">Use research point · skip area layout</button>${researchMission?'<p class="callout">Research-point mission selected. Existing terrain windows are reused.</p>':""}</section>`);outlet.innerHTML=panel("What happens next?",`<p>Choose <strong>Calculate network</strong> above. We plan towers and links inside your area, then evaluate terrain, sunlight and direct communication for your mission period. Step 03 opens with the layout.</p><p class="muted">${stale?"You changed the mission parameters. Calculate again to update the network; analysis steps are locked until then.":"Start with the provided research scenario, or change the parameters. No mission runs automatically."}</p>`);connect();return;
 }
 if(!result)return;
 const net=live(), r=result, nominal=r.failure_states.nominal.cases?.[evidenceCase]?.metrics??r.network;
 const charts=`<div class="split">${mapPanel()}${horizonPanel()}</div>`;
 const context=readout([["Position",`${n(r.site.lat,5)}°, ${n(r.site.lon,5)}° E`],["Elevation",n(r.site.elevation)+" m"],
 ["Mean / max slope",n(r.site.mean_slope)+"° / "+n(r.site.max_slope)+"°"],["DEM grid spacing",n(r.site.resolution,0)+" m"],["Local DEM coverage",pct(r.site.terrain_coverage)]]);
 const dte=r.diagnostics?.paths["Lander->Earth"].metrics?.[evidenceCase]??r.satellite?.direct_earth??r.direct;
 const kpis=`<div class="metrics">${metric("DIRECT TO EARTH",pct(dte.availability),`${dte.intervals} blackout intervals`)}${metric("SUNLIGHT",pct(r.solar.availability),hours(r.solar.longest)+" longest darkness")}${metric("LONGEST DIRECT BLACKOUT",hours(dte.longest),hours(dte.total)+" offline in total")}${metric("WITH ACTIVE RELAYS",pct(net.metrics.availability),hours(net.metrics.longest)+" longest blackout","amber")}${metric("WORST SINGLE FAILURE",pct(r.worst.availability),hours(r.worst.longest)+" longest blackout","amber")}</div>`;
 const comparison=bars("Direct-to-Earth",dte.availability)+bars("Active network",net.metrics.availability,true)+`<p class="callout ${net.meets?"":"amber"}">${net.meets?"Active network meets modeled limits under assumptions.":"Active network falls short of communication requirements."}</p>`;
 const candidates=table(["Relay","Range","Slope","Surface LOS","Solar","Power","Path","Recovered"],
 [...r.candidates].sort((a,b)=>candidateSort==="distance"?a.distance-b.distance:b.recovered-a.recovered).map(c=>row([`<button aria-pressed="${c.id===selected}" data-candidate="${c.id}">${c.id}</button>`,n(c.distance,2)+" km",n(c.slope)+"°",c.los===null?"Uncertain":c.los?"Clear":"Blocked",pct(c.solar.availability),pct(c.power.availability),pct(c.coverage.availability),pct(c.recovered)])));
 const choice=r.candidates.find(c=>c.id===selected)??r.candidates[0];
 const switchers=r.recommended.map(id=>`<label><input type="checkbox" data-disable="${id}" ${disabled.has(id)?"checked":""}> Disable ${id}</label>`).join("");
 const nodes=`<div class="network-branches"><div class="network-chart"><span class="node active">Lander</span><span aria-hidden="true">→</span><span class="node">Earth · direct</span></div>${r.recommended.map(id=>`<div class="network-chart"><span class="node">Lander</span><span aria-hidden="true">→</span><span class="node ${disabled.has(id)?"failed":"active"}">${id}${disabled.has(id)?" · disabled":""}</span><span aria-hidden="true">→</span><span class="node">${r.satellite?"Earth or S1 → Earth":"Earth"}</span></div>`).join("")}</div>`;
 const failures=table(["Disabled node","Availability","Longest blackout","Coverage lost","Blackout increase","Windows lost","Meets limits"],
 r.failures.map(f=>row([f.id,pct(f.metrics.availability),hours(f.metrics.longest),n(f.availability_lost)+" pp",hours(f.blackout_increase),String(f.windows_lost),f.meets?"Yes":"No"])));
 switch(active){
 case "layout":outlet.innerHTML=layoutView();break;
 case "overview":outlet.innerHTML=relayEvidence(r,alternativePreview,evidenceCase)+missionSatellitePanel()+`<div class="network-hero"><section class="panel mission-summary"><p class="eyebrow">MISSION / ${escape(r.site.name.toUpperCase())}</p><h2>${net.meets?"A path to connection.":"Every connection counts."}</h2><span class="verdict amber">${net.meets?"✓ Active network meets modeled limits under assumptions":"△ Active network falls short of communication limits"}</span><div class="hero-value">${pct(net.metrics.availability)} <small>active coverage</small></div><p class="muted">${n(net.metrics.availability-dte.availability)} percentage points more of the mission covered than direct contact alone. ${escape(planState())}.</p><div class="hero-stats"><div><span class="muted">Longest active blackout</span><strong>${hours(net.metrics.longest)}</strong></div><div><span class="muted">Screened recommendation</span><strong>${r.recommended.length} relays</strong></div></div><button class="primary" data-go="communication">Next: understand blackouts →</button><button class="quiet" data-go="relays">Inspect individual tower details</button></section><section class="panel"><h2>Towers on the Moon</h2><div id="scene-selection" aria-live="polite"><p class="caption">Select a tower in the Moon view to inspect it. Green = available path, amber = unavailable, red = disabled. Dashed links are unavailable at the selected time.</p></div></section></div>`+kpis+panel("Requirements status",assessmentHtml(r.assessment)+'<p class="caption">For the applied plan with every recommended relay active. Relay toggles in Resilience do not change it.</p>')+timelinePanel()+`<details id="map-details" class="panel"><summary>See the 2D terrain map</summary>${mapPanel()}</details><div class="split">${panel("From direct to connected",comparison)}${panel("Surface paths and shared backhaul",nodes+`<p class="caption">Surface paths may share S1. N−1 includes loss of S1 when configured. No relay-to-relay chain is modeled.</p>`+readout([["Mission length",hours(r.times.length*Number(r.config.minutes)/60)],["Darkness limit",r.darkness_meets?"Met":"Exceeded"]]))}</div>`;break;
 case "sites":outlet.innerHTML=panel("Find the right horizon",`<p class="muted table-note">Approximate research points, evaluated over the applied mission period. Select a region, then Run mission to apply it.</p><button id="compare" class="primary" ${pending==="compare"?"disabled":""}>${pending==="compare"?"Comparing regions…":"Compare all regions"}</button><div id="comparison">${siteRows()}</div>`)+`<div class="split">${mapPanel()}${panel("Applied landing point",context)}</div>`;break;
 case "communication":outlet.innerHTML=missionSatellitePanel()+timelinePanel()+outageTable()+`<p class="caption">Select an outage or a cause segment to inspect its UTC sample, component conditions, and locations. A failed path is rescued whenever another selected path remains available.</p>`+charts+panel("Applied landing terrain",context)+panel("Darkness intervals",r.darkness.length?table(["Start","End","Duration"],r.darkness.map(d=>row([utc(d.start),utc(d.end),hours(d.hours)]))):'<p class="muted">No darkness intervals in this sampled period.</p>');break;
 case "relays":outlet.innerHTML=relayEvidence(r,alternativePreview,evidenceCase)+`<div class="split">${mapPanel()}${planPanel()}</div>`+panel("Screened relay candidates",`<div class="controls"><label>Sort by <select id="candidate-sort"><option value="recovered" ${candidateSort==="recovered"?"selected":""}>Recovered coverage ↓</option><option value="distance" ${candidateSort==="distance"?"selected":""}>Deployment range ↑</option></select></label></div>`+(r.candidates.length?candidates:'<p class="muted">No screened candidates for these constraints. Review deployment and terrain settings.</p>'))+(choice?relayDetail(choice):"")+timelinePanel()+`<button class="primary" data-go="resilience">Test network resilience <span aria-hidden="true">↗</span></button>`;break;
 case "resilience":outlet.innerHTML=outageTable()+missionSatellitePanel()+panel("Test the remaining paths",`<div class="controls">${r.satellite?`<label><input type="checkbox" data-disable="S1" ${disabled.has("S1")?"checked":""}> Disable S1 · shared satellite</label>`:""}${switchers||'<p class="muted">This scenario uses the direct path only; there are no selected relays to disable.</p>'}</div>`+nodes+`<div class="metrics">${metric("NOMINAL COVERAGE",pct(nominal.availability),"All recommended relays · same environmental case")}${metric("SIMULATED COVERAGE",pct(net.metrics.availability),disabled.size+" disabled nodes")}${metric("COVERAGE CHANGE",n(net.metrics.availability-nominal.availability)+" pp","Against nominal")}${metric("NOMINAL BLACKOUT",hours(nominal.longest),"Longest continuous outage")}${metric("BLACKOUT CHANGE",hours(net.metrics.longest-nominal.longest),"Against nominal")}</div>`+comparison)+timelinePanel()+panel("Single-node criticality",`<p class="muted">Worst individual failure: ${pct(r.worst.availability)} availability · ${hours(r.worst.longest)} longest blackout. Time with multiple paths and a surviving path after satellite loss: ${pct(r.redundant)}. The table below tests individual failures; toggles above may simulate multiple failures.</p>`+(r.failures.length?failures:'<p class="muted">No selected relays to evaluate.</p>'));break;
 case "cost":{const networkCost=layout?.cost??r.cost;outlet.innerHTML=costPanel(networkCost,layout?"area":"mission")+panel("What this estimate covers",`<p>Every installed node shown in the network, including gateways, relay nodes and communications endpoints, plus equipment, integration, deployment and installation.</p><p class="caption">Launch, lander, satellite services, ongoing operations and broader mission costs are excluded. The itemized prices are available only in the Excel download.</p>`);break;}
 case "sensitivity":outlet.innerHTML=panel("Assumption sweep",`<p class="muted">Re-run the current mission with one changed assumption at a time.</p><button id="sweep" class="primary" ${pending==="sensitivity"?"disabled":""}>${pending==="sensitivity"?"Computing sweep…":"Run sensitivity analysis"}</button><div id="sensitivity-results">${sensitivity?table(["Variant","Direct","Direct blackout","Direct outages","Network","Network blackout","Eligible relays","Selected relays","Requirements"],sensitivity.map(s=>s.error?`<tr><td>${escape(s.label)}</td><td colspan="8">${escape(s.error)}</td></tr>`:row([escape(s.label),pct(s.direct.availability),hours(s.direct.longest),String(s.direct.intervals),pct(s.network.availability),hours(s.network.longest),String(s.eligible),String(s.count),s.status?{requirements_met:"Met",requirements_failed:"Failed",insufficient_evidence:"Insufficient evidence"}[s.status]:s.feasible?"Yes":"No"]))):""}</div>`)+(sensitivity?panel("Network availability by assumption",sensitivity.filter(s=>!s.error).map(s=>bars(escape(s.label),s.network.availability,true)).join("")):"");break;
 case "data":outlet.innerHTML=panel("Requirements status",assessmentHtml(r.assessment)+'<p class="caption">For the applied plan with every recommended relay active. Relay toggles in Resilience do not change it.</p>')+panel("Scenario configuration",configuration())+panel("Planning model, with limits",`<p>MOONMESH estimates communication for approximate lunar research points. It is a planning prototype; terrain sampling, RF assumptions, and a restricted relay catalog limit its results.</p><p class="muted">Flat-horizon visibility excludes local terrain. Terrain visibility adds the sampled horizon. Direct RF also requires adequate link margin. A relay path requires surface line of sight, both radio links, and usable power. Missing terrain is uncertain and excluded from credited availability. N−1 tests individual relay failures while retaining shared Earth receiver assumptions.</p>`)+panel("Data provenance",provenance())+panel("Power assumptions",readout([["Battery endurance",hours(Number(r.config.battery_hours))],["Initial charge",pct(Number(r.config.initial_charge_pct))],["Nominal net charging rate",n(Number(r.config.charge_ratio))+" reserve hours / sunlight hour before heater and dust losses"]]))+[["earth_radio","Earth radio profile"],["surface_radio","Surface radio profile"]].map(([key,title])=>panel(title,readout(rf.map(([name,label])=>[label,n(Number((r.config[key] as Record<string,number>)[name]),3)])))).join("");break;
 }
 if(!["layout","cost"].includes(active))outlet.insertAdjacentHTML("beforeend",environmentPanel());
 connect();paint();
}

function missionSatelliteReadout(){
 const s=result.satellite;if(!s)return "";
 const i=cursor, state=result.environment.lander[evidenceCase];
 const hops=[{id:"Lander",hop:s.lander,operational:state.available[i],surface:"Origin"},...result.candidates.filter(c=>result.recommended.includes(c.id)&&c.satellite).map(c=>({id:c.id,hop:c.satellite!,operational:c.environment[evidenceCase].available[i],surface:c.los&&c.surface_margin>=Number((result.config.surface_radio as Record<string,number>).margin)?`${n(c.surface_margin)} dB`:"Surface hop blocked"}))];
 return `<p class="callout">${utc(result.times[i])} · S1 ${disabled.has("S1")?"disabled":"active"} · ${s.earth.occulted?.[i]?"Earth occulted by Moon":"Earth not occulted"}</p>`+table(["Path","Surface margin","Sat elevation / range","Surface → S1 margin","S1 → Earth margin","Failed hop / state"],hops.map(({id,hop,operational,surface})=>row([`${id==="Lander"?"Lander":`Lander → ${id}`} → S1 → Earth`,surface,`${n(hop.el?.[i])}° / ${n(hop.range_km[i])} km`,hop.margin?`${n(hop.margin[i])} dB`:"Unknown · geometry only",s.earth.margin?`${n(s.earth.margin[i])} dB`:"Unknown · geometry only",disabled.has("S1")?"Satellite lost":disabled.has(id)?"Relay disabled":!operational?"Surface node power / thermal":surface==="Surface hop blocked"?surface:!hop.available[i]?escape(hop.reason[i]):!s.earth.available[i]?escape(s.earth.reason[i]):"Available under assumptions"])));
}
function missionSatellitePanel(){
 const s=result.satellite;if(!s)return "";
 return panel("Simulated satellite · mission samples",`<p>${escape(s.preset)} · ${n(s.period_min)} min period · lander satellite path ${pct(s.lander_path.availability)} · network if S1 lost ${pct(s.network_without_satellite.availability)}</p><button data-mission-satellite-view>Show computed satellite and surface</button><label>Mission time<input id="satellite-sample" type="range" min="0" max="${result.times.length-1}" value="${cursor}"></label><div id="mission-satellite-readout">${missionSatelliteReadout()}</div><p class="caption">${escape(s.validation)} ${escape(s.assumptions)} ${escape(s.cost_todo)}</p>`);
}

function satelliteReadout(L:AreaResult){
 const satellite=satellitePosition(L.site.lat,L.site.lon,satelliteSeconds);
 return `<p class="callout">Pass time: +${n(satelliteSeconds/60,1)} min · ${L.nodes.filter(v=>satelliteAccess(v,satellite).visible).length} of ${L.nodes.length} nodes above the 10° mask</p>`+table(["Node → S1","Elevation","Range","Geometric access"],L.nodes.map(v=>{const a=satelliteAccess(v,satellite);return row([`${v.role==="gateway"?"G":v.role==="mine"?"M":"R"}${v.id} → S1`,`${n(a.elevationDeg,1)}°`,`${n(a.rangeM/1000,0)} km`,a.visible?'<span class="green">Above mask</span>':'<span class="amber">Below mask · no link drawn</span>']);}));
}
function satellitePanel(L:AreaResult){
 return panel("Surface nodes → relay satellite",`<p>Blue beams show each node’s geometric access to S1; green lines show the surface network. Move through the pass to see access open and close.</p><div class="controls"><button data-satellite-view>Show satellite and nodes</button><button data-surface-view>Zoom to surface network</button></div><label for="satellite-time">Satellite pass · minutes after overhead</label><input id="satellite-time" type="range" min="0" max="${Math.round(ORBIT_PERIOD)}" step="10" value="${satelliteSeconds}" style="width:100%"><div id="satellite-readout" aria-live="polite">${satelliteReadout(L)}</div><p class="caption">Assumed single satellite: circular polar orbit, 500 km altitude, ${n(ORBIT_PERIOD/60,1)} minute period; overhead at preview time zero. Approximate uniform lunar rotation, 10 m antennas and 10° elevation mask. Marker enlarged for visibility. <a href="https://ssd.jpl.nasa.gov/astro_par.html" target="_blank" rel="noopener noreferrer">Lunar gravity: JPL</a>.</p><p class="caption">Illustrative ORA surface-layout preview only; mission satellite results are in The Earth link and Blackouts views. Local terrain blockage, radio budgets, pointing, capacity, power and satellite-to-Earth access are not evaluated. A node below the mask needs a surface route to another node with satellite access. Existing coverage and failure results still use the direct-to-Earth baseline.</p>`);
}
function layoutView(){
 const L=layout;if(!L)return panel("Layout","<p class='muted'>Draw an area and calculate to see its layout.</p>");
 const best=L.layouts[L.recommended], nodes=L.nodes, outside=nodes.filter(v=>v.outside).length, worst=Math.min(best.worst_after_node_loss,best.worst_after_link_loss);
 const kpis=`<div class="metrics">${metric("MINES LINKED",pct(100*L.plan.served),`${L.plan.mines.length} mining sites to ${L.plan.gateways.length} gateways`,L.plan.feasible?"green":"amber")}${metric("WORST SINGLE FAILURE",pct(100*worst),"mines still linked after losing any relay or link in the best layout",worst>=1?"green":"amber")}${metric("RELAYS CHOSEN",String(L.plan.chosen.length),`${L.candidates} candidate sites screened`)}${metric("AREA",n(L.site.area_km2,1)+" km²",escape(L.site.name))}${metric("OUTSIDE YOUR SHAPE",String(outside),outside?"sites just beyond the edge, used because nothing inside links up":"all sites are inside",outside?"amber":"green")}</div>`;
 const rows=Object.entries(L.layouts).map(([key,m])=>row([`<button data-format="${key}" aria-pressed="${key===layoutFormat}">${escape(m.name)}</button>${key===L.recommended?' <span class="pill ok">best</span>':""}<small class="coords">${escape(m.blurb)}</small>`,String(m.links),n(m.link_km,0)+" km",String(m.single_points_of_failure),String(m.single_path_links),pct(100*m.worst_after_node_loss),pct(100*m.worst_after_link_loss),n(m.schneider_r_random,2)]));
 const role:Record<string,string>={gateway:"Gateway",relay:"Relay",mine:"Mining site"};
 const sitesTable=table(["Node","Role","Position","Elevation","Sunlight","Earth view",""],nodes.map(v=>row([`${v.role==="gateway"?"G":v.role==="mine"?"M":"R"}${v.id}`,role[v.role],`${n(v.lat,4)}°, ${n(v.lon,3)}° E`,n(v.elevation,0)+" m",pct(100*v.sun),pct(100*v.earth),v.outside?"outside the area":""])));
 return kpis+(result?.satellite?missionSatellitePanel():satellitePanel(L))+panel("Best format for this area",`<p>${assessmentBadge(L.assessment)}</p><p class="callout ${worst>=1?"":"amber"}">${escape(L.reason)}</p><p class="muted">Green links on the Moon show the layout selected below. Violet rings mark sites just outside your shape.</p>`)+
  panel("Compare layouts",table(["Layout","Links","Cable","Single points of failure","Single-path links","Mines linked after worst relay loss","after worst link loss","Robustness to random failure"],rows)+`<p class="caption">Select a layout to draw it on the Moon. Every format uses the same relay sites; they differ in which line-of-sight links are built. Robustness is the Schneider R score (0 to 1, higher is better).</p>`)+
  (L.site.resolution_m>20?`<p class="callout amber">Terrain for this area is ${n(L.site.resolution_m,0)} m per pixel${L.site.projection==="local"?" (global LOLA data; the polar caps have sharper 20 m data)":" (thinned to keep large areas fast)"}. Hills and ridges narrower than that are not seen, so treat the result as a first look.${L.site.projection==="local"?" Relay scores come from a model trained on polar terrain, so they are less reliable here.":""}</p>`:"")+panel("Sites",sitesTable+`<p class="caption">${escape(L.score_method)}. Mining sites sit on the lowest gentle ground inside the shape; gateways are the sites with the longest view of Earth. Sunlight and Earth view are shares of a 30 day period from the scenario start.</p>`)+
  panel("Requirements status",assessmentHtml(L.assessment)+(L.score_evidence?`<p class="caption">Siting: ${escape(L.score_evidence.method)}${L.score_evidence.fallback?" (fallback: trained models not loaded)":""}. ${escape(L.score_evidence.note)}</p>`:""));
}

function provenance(){
 const valueHtml=(value:unknown):string=>{
  if(typeof value==="string")return /^https?:\/\//.test(value)?`<a href="${escape(value)}" target="_blank" rel="noopener noreferrer">${escape(value)}</a>`:escape(value);
  if(Array.isArray(value))return value.map(valueHtml).join(" · ");
  if(value&&typeof value==="object")return Object.entries(value).map(([name,v])=>`${escape(name)}: ${valueHtml(v)}`).join(" · ");
  return escape(value===null?"Unknown":String(value));
 };
 return readout(Object.entries(result.provenance).map(([key,value])=>[key.replaceAll("_"," "),valueHtml(value)]));
}

function move(value:number){cursor=value;paint();}
function paint(){
 if(!result)return;
 moon?.update(cursor,disabled,selected,evidenceCase,["communication","resilience"].includes(active)?evidenceScope:undefined);
 if(document.querySelector("#outage-inspector"))$("#outage-inspector").innerHTML=sampleEvidence(result,disabled,evidenceScope,evidenceCase,cursor);
 if(document.querySelector("#mission-satellite-readout"))$("#mission-satellite-readout").innerHTML=missionSatelliteReadout();
 const satSample=document.querySelector<HTMLInputElement>("#satellite-sample");if(satSample)satSample.value=String(cursor);
 const r=result, s=r.samples, i=cursor;
 const t=document.querySelector<HTMLInputElement>("#cursor");if(t)t.value=String(i);
 const time=document.querySelector("#time");if(time)time.textContent=utc(r.times[i]);
 if(document.querySelector("#timeline")){const data=live();timeline(r,data.combined,data.assisted,i,zoom,move,alternativePreview?r.optimization_evidence?.configurations[alternativePreview]?.timelines[evidenceCase]:undefined,evidenceCase);
 $("#timeline-readout").innerHTML=readout([["Selected sample",utc(r.times[i])],["Environmental case",escape(evidenceCase)],["Active network",data.combined[i]?"Available under assumptions":"Unavailable"],["Lander paths",participatingPaths(r).some(p=>p.relay===null&&!p.nodes.some(n=>disabled.has(n))&&p.available[evidenceCase][i])?"Available under assumptions":"Unavailable"],["Sunlight",!s.solar_known[i]?"Uncertain":s.solar[i]?"Illuminated":"Dark"]]);}
 if(document.querySelector("#map")?.getBoundingClientRect().width)map(r,layer,disabled,i,c=>{selected=c.id;if(active!=="relays")navigate("relays");else render();},selected,mapZoom,new Set(result.optimization_evidence?.configurations[alternativePreview]?.relay_ids??[]),evidenceCase,["communication","resilience"].includes(active)?evidenceScope:undefined);
 if(document.querySelector("#horizon")){
    horizon(r,i);
    $("#point").innerHTML=readout([["Time",utc(r.times[i])],["Earth az / el",n(s.earth_az[i])+"° / "+n(s.earth_el[i])+"°"],
    ["Sun az / el",n(s.sun_az[i])+"° / "+n(s.sun_el[i])+"°"],["Terrain at Earth azimuth",n(s.earth_limit[i])+"°"],
    ["Clearance after allowance",n(s.earth_clearance[i],2)+"°"],["Horizon feature distance",s.obstruction_m[i]===null?"Unknown":n(s.obstruction_m[i]!/1000)+" km"],
    ["RF margin",n(s.rf_margin[i])+" dB"],["Status",escape(s.status[i]+" · "+s.reason[i])]]);
 }
 const candidate=r.candidates.find(c=>c.id===selected);
 const sceneSelection=document.querySelector("#scene-selection");if(sceneSelection&&!candidate&&!r.recommended.length)sceneSelection.innerHTML='<p class="callout">No surface relays were recommended for this scenario. Use the optional screened-candidate controls to inspect alternatives.</p>';if(sceneSelection&&candidate)sceneSelection.innerHTML=readout([["Selected tower",candidate.id+(r.recommended.includes(candidate.id)?" · recommended":" · screened candidate")],["Full path availability",pct(candidate.coverage.availability)],["Deployment distance",n(candidate.distance,2)+" km"],["At this time",disabled.has(candidate.id)?"Disabled":candidateAvailable(r,candidate.id,evidenceCase,disabled,cursor)?"Path available":"Path unavailable"]]);
 if(candidate&&document.querySelector("#power-timeline")){const state=candidate.environment[evidenceCase], display={...candidate,powered:state.available,reserve:state.soc.map(v=>v*Number(r.config.battery_hours))};powerTimeline(display,Number(r.config.battery_hours),i);$("#power-readout").innerHTML=readout([["At selected time",utc(r.times[i])],["Sunlight / powered",`${candidate.sunlight[i]?"Illuminated":"Dark"} / ${state.available[i]?"Powered":"Unpowered"}`],["Battery reserve",hours(display.reserve[i])]]);}
}

function connect(){
 const scope=outlet.querySelector<HTMLSelectElement>("#outage-scope");if(scope)scope.onchange=()=>{evidenceScope=scope.value;render();};
 const env=outlet.querySelector<HTMLSelectElement>("#evidence-case");if(env)env.onchange=()=>{evidenceCase=env.value;render();};
 const alternative=outlet.querySelector<HTMLSelectElement>("#alternative-preview");if(alternative)alternative.onchange=()=>{alternativePreview=alternative.value;moon?.setAlternativePreview(result.optimization_evidence?.configurations[alternativePreview]?.relay_ids??[]);render();};
 outlet.querySelectorAll<HTMLButtonElement>("[data-evidence-time]").forEach(button=>button.onclick=()=>{
  const i=Number(button.dataset.evidenceTime);const paths=participatingPaths(result).filter(p=>evidenceScope==="network"||p.id===evidenceScope);
  const blocked=paths.find(p=>p.relay&&(!p.available[evidenceCase][i]||p.nodes.some(n=>disabled.has(n))));
  if(blocked?.relay)selected=blocked.relay;
  move(i);$("#outage-inspector")?.scrollIntoView({block:"nearest",behavior:"smooth"});
 });
 const research=outlet.ownerDocument.querySelector<HTMLButtonElement>("#use-research");
 if(research)research.onclick=()=>{researchMission=true;area=undefined;layout=undefined;layoutKey="";areaDirty=true;const chosen=$<HTMLSelectElement>("#research-site").value;$<HTMLSelectElement>('[name="site"]').value=chosen;for(const name of ["latitude","longitude"])$<HTMLInputElement>(`[name="${name}"]`).value="";moon?.setLayout(undefined,"");moon?.showArea(undefined);dirty();render();};
 document.querySelectorAll<HTMLButtonElement>("[data-excel]").forEach(b=>b.onclick=()=>void downloadExcel(b.dataset.excel as "mission"|"area"));
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
 outlet.querySelector<HTMLButtonElement>("[data-mission-satellite-view]")?.addEventListener("click",()=>moon?.showMissionSatellite());
 const sample=outlet.querySelector<HTMLInputElement>("#satellite-sample");if(sample)sample.oninput=()=>move(Number(sample.value));
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
 outlet.querySelectorAll<HTMLButtonElement>("[data-format]").forEach(b=>b.onclick=()=>{layoutFormat=b.dataset.format!;moon?.setLayout(layout,layoutFormat,satelliteSeconds);render();outlet.querySelector<HTMLButtonElement>(`[data-format="${layoutFormat}"]`)?.focus();});
 const satelliteSlider=outlet.querySelector<HTMLInputElement>("#satellite-time");
 if(satelliteSlider)satelliteSlider.oninput=()=>{satelliteSeconds=Number(satelliteSlider.value);moon?.setSatellitePreview(layout,satelliteSeconds);if(layout)$("#satellite-readout").innerHTML=satelliteReadout(layout);};
 outlet.querySelector<HTMLButtonElement>("[data-satellite-view]")?.addEventListener("click",()=>{if(layout)moon?.showSatelliteOverview(layout,satelliteSeconds);});
 outlet.querySelector<HTMLButtonElement>("[data-surface-view]")?.addEventListener("click",()=>{if(layout)moon?.flyToSite(layout.site.lat,layout.site.lon,Math.max(30000,layout.site.radius_m*5+16000));});
 $("#panel").querySelectorAll<HTMLButtonElement>("[data-draw-start]").forEach(b=>b.onclick=()=>startDrawing(b.dataset.drawStart as "rect"|"free"));
 $("#panel").querySelectorAll<HTMLButtonElement>("[data-pole-go]").forEach(b=>b.onclick=()=>moon?.flyToPlace(-89.6,0,160000));
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

function download(contents:string|Blob,extension:string,mime:string,filename?:string){
 const href=URL.createObjectURL(new Blob([contents],{type:mime}));
 const anchor=document.createElement("a");anchor.href=href;anchor.download=filename??"moonmesh-"+result.config.site+"."+extension;anchor.click();setTimeout(()=>URL.revokeObjectURL(href),1000);
}
async function downloadExcel(scope:"mission"|"area"){
 if(!result||stale||exporting)return;
 exporting=true;updateNavigation();
 document.querySelectorAll<HTMLButtonElement>("[data-excel]").forEach(b=>b.textContent="Preparing Excel…");
 status("Preparing your Excel breakdown…");
 try{
  const snapshot=result;
  const response=await fetch("/api/export/xlsx",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({result:snapshot,area:layout,scope,disabled:[...disabled],comparison:compared,sensitivity,outage_scope:evidenceScope,environment_case:evidenceCase,preview_config_id:alternativePreview||null})});
  if(!response.ok)throw new Error("Excel download failed. Try again, or recalculate the network.");
  const blob=await response.blob();
  if(result!==snapshot||stale)throw new Error("The scenario changed. Recalculate before downloading.");
  const site=String(snapshot.config.site).replace(/[^a-zA-Z0-9_-]/g,"-");
  download(blob,"xlsx",blob.type,`moonmesh-${site}-${snapshot.config.start}-${snapshot.config.end}-${scope}.xlsx`);
  status("Excel breakdown downloaded.");
 }catch(error){status((error as Error).message,true);}
 finally{
  exporting=false;updateNavigation();
  document.querySelectorAll<HTMLButtonElement>("[data-excel]").forEach(b=>b.textContent="Download Excel breakdown");
 }
}
$("#xlsx").onclick=()=>void downloadExcel(active==="layout"?"area":"mission");
$("#json").onclick=()=>download(JSON.stringify({...result,comparison:compared,sensitivity,disabled:[...disabled],outage_scope:evidenceScope,environment_case:evidenceCase,preview_config_id:alternativePreview||null,active:live()},null,2),"json","application/json");
$("#csv").onclick=()=>{
 const samples=result.samples, columns=Object.keys(samples).filter(k=>k!=="network") as (keyof typeof samples)[];
 const quote=(s:unknown)=>'"'+String(s??"").replaceAll('"','""')+'"';
 const network=live();
 const rows=result.times.map((timestamp,i)=>[timestamp,...columns.map(c=>samples[c][i]),network.assisted[i],network.combined[i]].map(quote).join(","));
 const section=(head:string, records:unknown[][])=>rows.push("",head,...records.map(record=>record.map(quote).join(",")));
 section("relay_cost_usd,budget_usd,estimated_spend_usd,remaining_usd",[[result.cost.relay_cost_usd,result.cost.budget_usd,result.cost.total_usd,result.cost.remaining_usd]]);
 section("cost_category,item,quantity,unit_cost_usd,total_usd",result.cost.items.map(i=>[i.category,i.item,i.quantity,i.unit_cost_usd,i.total_usd]));
 section("configuration,value",Object.entries(result.config).map(([key,value])=>[key,JSON.stringify(value)]));
 section("interval,start,end,hours,causes",[...scopedOutages(result,disabled,"network",evidenceCase).map(o=>["active network blackout",o.start,o.end,o.hours,o.causes.join("; ")]),...result.darkness.map(o=>["darkness",o.start,o.end,o.hours,""])]);
 section("relay,latitude,longitude_east,distance_km,slope_deg,surface_los,surface_margin_db,powered_pct,coverage_pct,selected,disabled",result.candidates.map(c=>[c.id,c.lat,c.lon,c.distance,c.slope,c.los,c.surface_margin,c.power.availability,c.coverage.availability,result.recommended.includes(c.id),disabled.has(c.id)]));
 section("failed_relay,availability_pct,longest_blackout_h,meets",result.failures.map(f=>[f.id,f.metrics.availability,f.metrics.longest,f.meets]));
 section("optimization_feasible,eligible_relays,active_coverage_pct,active_longest_blackout_h",[[result.feasible,result.eligible,network.metrics.availability,network.metrics.longest]]);
 section("environment_case,network_pct,longest_blackout_h,worst_single_failure_pct,meets_assumed_limits",Object.entries(result.environment.cases).map(([name,c])=>[name,c.network.availability,c.network.longest,c.worst.availability,c.meets]));
 section("node,case,timestamp_utc,temperature_c,soc,heater_w,dust_load,solar_factor,available,reason",[{id:"lander",environment:result.environment.lander},...result.candidates].flatMap(c=>Object.entries(c.environment).flatMap(([name,state])=>result.times.map((t,i)=>[c.id,name,t,state.temperature_c[i],state.soc[i],state.heater_w[i],state.dust_load[i],state.solar_factor[i],state.available[i],state.reason[i]]))));
 section("evidence_status,environment_case,disabled_nodes,preview_configuration",[["simulated",evidenceCase,[...disabled].join(";"),alternativePreview||null]]);
 section("segment_start_utc,segment_end_utc,path,component,condition,state",scopedOutages(result,disabled,evidenceScope,evidenceCase).flatMap(o=>o.segments.flatMap(s=>s.paths.flatMap(p=>p.issues.map(issue=>[s.start,s.end,p.id,issue.component,issue.code,issue.status])))));
 const optimization=result.optimization_evidence;
 if(optimization){
  section("configuration,case,relay_count,availability_pct,total_blackout_h,longest_blackout_h,worst_loss_pct,worst_loss_blackout_h,installed_usd,explanation",Object.values(optimization.configurations).map(c=>{const v=c.cases[evidenceCase];return [c.id,evidenceCase,c.relay_ids.length,v.network.availability,v.network.total,v.network.longest,v.worst.availability,v.worst.longest,c.total_usd,c.comparison_reason];}));
  section("relay,case,coverage_gain_pp,longest_blackout_reduction_h,recovered_h,replacement",optimization.marginal.map(m=>{const v=marginalMetrics(m,evidenceCase);return [m.relay_id,evidenceCase,v?.availability_gain_pp,v?.blackout_reduction_hours,v?.recovered_hours,m.replacement];}));
 }
 section("provenance,description",Object.entries(result.provenance).map(([key,value])=>[key,JSON.stringify(value)]));
 if(result.assessment){
  const a=result.assessment;
  section("requirements_status,subject,kind,scope",[[a.status,a.subject,a.kind,a.scope]]);
  section("requirement,target,value,met",a.checks.map(c=>[c.name,c.target,JSON.stringify(c.value),c.met===null?"undecided":c.met]));
  section("evidence_gap,blocking,detail",a.evidence_gaps.map(g=>[g.name,g.blocking,g.detail]));
  section("hazard,evaluated",[...a.evaluated_hazards.map(h=>[h,true]),...a.not_evaluated.map(h=>[h,false])]);
 }
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
 environmentPanel()+chartImages+outageTable()+sampleEvidence(result,disabled,evidenceScope,evidenceCase,cursor)+relayEvidence(result,alternativePreview,evidenceCase)+
 panel("Relay feasibility and single-relay failures",result.candidates.filter(c=>result.recommended.includes(c.id)).map(c=>relayDetail(c,false)).join("")+result.failures.map(f=>`<p>Disable ${f.id} · ${pct(f.metrics.availability)} availability · ${hours(f.metrics.longest)} longest blackout · ${f.meets?"modeled limits met under assumptions":"requirements unmet"}</p>`).join(""))+
 costPanel(result.cost,"mission",false)+panel("Requirements status",assessmentHtml(result.assessment,true))+panel("Scenario configuration",configuration())+panel("Assumptions and provenance",provenance());
 document.body.append(article);
 await Promise.all(Array.from(article.querySelectorAll("img")).map(img=>img.decode().catch(()=>{})));
 try{window.print();}finally{navigate(view,false);article.remove();}
};

window.addEventListener("resize",paint);
function startDrawing(kind:"rect"|"free"){setPanel(false);moon?.setDrawMode(kind);status("Drawing: drag on the Moon. Near the south pole the terrain is sharpest (use Go to the pole).");}
function chooseRegion(_site:string){startDrawing("rect");}
function onArea(polygon:[number,number][]){
 researchMission=false;
 area=polygon.length>=3?polygon:undefined;areaKey=JSON.stringify(area??[]);areaDirty=true;layout=undefined;layoutKey="";moon?.setLayout(undefined,"");
 if(!area)moon?.showArea(undefined);
 dirty();navigate(area?"configure":"landing",false);
 status(area?"Area drawn. Choose dates and goals, then Calculate network.":"Area cleared.");
}
function selectTower(c:Candidate){
 selected=c.id;moon?.update(cursor,disabled,selected,evidenceCase,["communication","resilience"].includes(active)?evidenceScope:undefined);
 if(active==="overview")paint();else if(active!=="landing"&&active!=="configure")render();
 else{status(`Tower ${c.id} · ${pct(c.coverage.availability)} full-path availability · ${n(c.distance,2)} km from the lander`);}
}
function usePin(lat:number,lon:number,_site:string){
 // A 6 km square built from great-circle offsets, so it stays square at any latitude.
 const dLat=3000/1737400*180/Math.PI, dLon=dLat/Math.max(0.05,Math.cos(lat*Math.PI/180));
 const polygon=[[-1,-1],[-1,1],[1,1],[1,-1]].map(([a,b])=>[Math.max(-90,Math.min(90,lat+a*dLat)),(lon+b*dLon+360)%360] as [number,number]);
 moon?.showArea(polygon);onArea(polygon);
}
// The viewer can fail if it starts before the page has a real size (a hidden or just-resized window).
// Wait for a size, and retry a failed start a few times before leaving the static fallback in place.
function startMoon(attempt=0){
 const host=$("#moon"), box=host.getBoundingClientRect();
 if((box.width<2||box.height<2||document.hidden)&&attempt<50){setTimeout(()=>startMoon(attempt+1),200);return;}
 moon=new MoonView(host,{onUsePin:usePin,onChooseRegion:chooseRegion,onSelectCandidate:selectTower,onArea});
 (window as unknown as {__moonview?:MoonView}).__moonview=moon;   // diagnostics hook for browser tests
 if(host.querySelector(".vw-fallback")&&attempt<53){
  const failed=moon;setTimeout(()=>{if(moon!==failed)return;failed.dispose();host.replaceChildren();startMoon(Math.max(attempt,50)+1);},800);
 }
}
startMoon();
async function initialize(){
 try{
  const conf=await api<{defaults:Record<string,unknown>;sites:Record<string,[string,number,number]>}>("config");
  sites=conf.sites;
  const region=$<HTMLSelectElement>('select[name="site"]');
  region.replaceChildren();
  Object.entries(conf.sites).forEach(([id,[name]])=>region.add(new Option(name,id)));
  for(const [key,value] of Object.entries(conf.defaults)){
   if(key==="satellite")continue;
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
