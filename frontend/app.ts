import {map, timeline, horizon, powerTimeline} from "./charts.js";
import type {Result, Comparison, Candidate, Sensitivity} from "./types.js";

const $=<T extends HTMLElement=HTMLElement>(q:string)=>document.querySelector<T>(q)!;
const form=$<HTMLFormElement>("#mission"), outlet=$("#view");
let result:Result, active="overview", cursor=0, zoom=0, mapZoom=1, layer="elevation";
let selected="", compared:Comparison[]|undefined, sensitivity:Sensitivity[]|undefined;
let disabled=new Set<string>(), busy=false, version=0;
let candidateSort="recovered", pending="";
let sites:Record<string,[string,number,number]>={};
const views:Record<string,[string,string]>={overview:["Your mission.<br><span class='muted-heading'>A clearer connection.</span>","Explore the terrain. Understand the blackout. Build a resilient link."],sites:["Choose your horizon.","Compare landing regions over the same mission period and assumptions."],communication:["Understand the silence.","Trace each communication blackout to terrain, geometry, and RF conditions."],relays:["Bridge the distance.","Explore the smallest screened relay configuration and its physical constraints."],resilience:["Plan for the unexpected.","Disable a relay and inspect how the remaining paths perform."],sensitivity:["Question the assumptions.","Change one assumption at a time and compare the modeled outcome."],data:["The science behind the signal.","Inspect sources, model assumptions, and the limits of these planning estimates."]};
function dirty(){
 // Compare only editable fields; API configuration may include additional model defaults.
 const same=(a:unknown,b:unknown):boolean=>a!==null&&typeof a==="object"?Object.entries(a).every(([k,v])=>same(v,(b as Record<string,unknown>)?.[k])):a===b;
 const edited=!!result&&!same(values(),result.config);
 $("#dirty").textContent=edited?"Changes not applied":result?"Scenario applied":"Preparing scenario";
 $("#dirty").classList.toggle("stale",edited);
}
form.addEventListener("input",dirty);
form.addEventListener("change",dirty);
$("#menu-toggle").onclick=()=>{const open=$(".rail").classList.toggle("open");$("#menu-toggle").setAttribute("aria-expanded",String(open));};
function navigate(view:string,focus=true){
 active=view;
 document.querySelectorAll("nav [aria-current]").forEach(b=>b.removeAttribute("aria-current"));
 const nav=$<HTMLButtonElement>(`nav button[data-view="${view}"]`);nav.setAttribute("aria-current","page");
 $("#title").innerHTML=views[view][0];$("#purpose").textContent=views[view][1];
 const index=["overview","sites","communication","relays","resilience"].indexOf(view);
 $("#chapter").textContent=index<0?"ANALYSIS / MISSION WORKSPACE":`0${index+1} / MISSION WORKSPACE`;
 $(".rail").classList.remove("open");$("#menu-toggle").setAttribute("aria-expanded","false");
 render();if(focus)$("#title").focus();
}

const n=(x:number|null|undefined,d=1)=>x==null?"—":x.toFixed(d);
const pct=(x:number)=>n(x)+"%";
const hours=(x:number)=>n(x)+" h";
const utc=(x:string)=>x.slice(0,16).replace("T"," ")+" UTC";
const escape=(s:unknown)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]!));
const status=(s:string,error=false)=>{const node=$("#status");node.textContent=s;node.classList.toggle("error",error);};
const panel=(title:string,html:string)=>`<section class="panel"><h2>${title}</h2>${html}</section>`;
const row=(cells:string[])=>"<tr>"+cells.map(c=>"<td>"+c+"</td>").join("")+"</tr>";
const table=(columns:string[],rows:string[])=>`<div class="table-wrap" role="region" aria-label="Scrollable results table" tabindex="0"><table><thead><tr>${columns.map(c=>'<th scope="col">'+c+"</th>").join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
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
$("#expert").innerHTML=`<fieldset><legend>Mission and deployment constraints</legend><div class="fields">${fields.map(f=>input(f)).join("")}<label title="Require compliance after any selected relay fails"><span><input type="checkbox" name="resilient"> Require N−1</span></label></div></fieldset>`+
 [["earth_radio","Direct Earth and relay-to-Earth RF"],["surface_radio","Lander-to-relay RF"]].map(([key,label])=>`<fieldset><legend>${label}</legend><div class="fields">${rf.map(f=>input(f,key+".")).join("")}</div></fieldset>`).join("");

async function api<T>(url:string,body?:unknown):Promise<T>{
 const response=await fetch("/api/"+url,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
 const data=await response.json();
 if(!response.ok) throw new Error(typeof data.detail==="string"?data.detail:data.detail?.map((d:{loc:string[];msg:string})=>d.loc.slice(1).join(".")+" "+d.msg).join("; ")||"Request failed.");
 return data;
}

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
 if(busy||!form.reportValidity())return;
 busy=true;
 document.body.classList.add("computing");$("#view").setAttribute("aria-busy","true");
 $<HTMLButtonElement>('button[type="submit"]').disabled=true;
 status("Computing terrain, ephemerides and candidate relay paths…");
 try{
   const requested=values();
   const next=await api<Result>("mission",requested);
   result=next; version++;
   disabled.clear(); cursor=0; zoom=result.times.length; mapZoom=1;
   selected=result.recommended[0]??result.candidates[0]?.id??""; compared=undefined; sensitivity=undefined;
   for(const id of ["json","csv","report"]) $<HTMLButtonElement>("#"+id).disabled=false;
   $(".export-options p").textContent="Exports use the applied scenario and current relay failure state.";
   $("#subtitle").textContent=`${result.site.name} · ${result.config.start} to ${result.config.end} UTC`;
   status(`${result.times.length.toLocaleString()} samples · ${result.config.minutes} min intervals · ${result.candidates.length} relay candidates evaluated`);
   dirty();render();
 }catch(error){status((result?"Previous results shown. ":"")+(error as Error).message,true);if(!result)outlet.innerHTML=`<section class="panel initial-state"><h2>Mission could not be computed</h2><p class="muted">Review the settings and use Run mission to retry. The error is shown above.</p></section>`;}
 finally{busy=false;document.body.classList.remove("computing");$("#view").setAttribute("aria-busy","false");$<HTMLButtonElement>('button[type="submit"]').disabled=false;}
}

form.onsubmit=e=>{e.preventDefault();void run();};
$("nav").addEventListener("click",event=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>("button[data-view]");
 if(!button)return;
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
 <div class="legend"><span style="--key:#779ebd">Flat horizon</span><span>Terrain visible</span><span style="--key:var(--green)">Direct RF / combined</span><span style="--key:var(--amber)">Sunlight</span><span style="--key:#bba9ff">Relay path</span><span style="--key:var(--red)">Blackout</span><span class="unknown">Uncertain</span></div><p class="caption">Use arrow keys on the time slider to inspect individual samples. Times are UTC.</p><div id="timeline-readout" class="timeline-readout"></div>`);
}

const horizonPanel=()=>panel("360° terrain horizon",`<canvas id="horizon" class="horizon" aria-label="Horizon elevation versus azimuth"></canvas><p class="caption">Azimuth (°) horizontally · Elevation (°) vertically. Cyan terrain profile · green Earth center · amber Sun center. Gaps indicate unknown terrain.</p><div id="point"></div>`);
const planState=()=>result.feasible?"Minimum among screened candidates · requirements met":"Best evaluated configuration · requirements unmet";
const bars=(label:string,value:number,after=false)=>`<div class="muted">${label} · ${pct(value)}</div><div class="chart-bar ${after?"after":""}"><span style="width:${value}%"></span></div>`;
function outageTable(){
 const rows=result.outages.map(item=>row([`<button data-time="${item.first}" aria-label="Inspect blackout starting ${utc(item.start)}">${utc(item.start)}</button>`,utc(item.end),hours(item.hours),escape(item.causes.join(", "))]));
 return panel("Direct-link blackouts", rows.length?table(["Start","End","Duration","Cause"],rows):"<p class='muted'>No direct blackouts in the sampled period.</p>");
}
function relayDetail(c:Candidate,includeTimeline=true){
 const reason=c.los===null?"Terrain coverage is incomplete.":!c.los?"Terrain obstructs the lander-to-relay path.":c.surface_margin<Number((result.config.surface_radio as Record<string,number>).margin)?"Surface RF margin is below the requirement.":c.recovered>0?`This candidate covers ${pct(c.recovered)} of the mission during direct-link outages.`:"This candidate recovers zero direct-link outage intervals.";
 return panel("Relay "+c.id,`<p class="callout">${reason}</p>`+readout([
 ["Coordinates",`${n(c.lat,5)}°, ${n(c.lon,5)}° E`],["Deployment estimate",n(c.distance,2)+" km"],["Elevation",n(c.elevation)+" m"],
 ["Local slope",n(c.slope)+"°"],["Surface margin",n(c.surface_margin)+" dB"],["Relay-to-Earth feasibility",pct(c.earth.availability)],
 ["Sunlight",pct(c.solar.availability)],["Powered",pct(c.power.availability)],["Full relay path",pct(c.coverage.availability)],["Uncertain Earth samples",String(c.unknown_samples)],
 ["Minimum Earth-link margin",n(c.minimum_earth_margin)+" dB"],["Power outages",String(c.power.intervals)],["Battery capacity",hours(Number(result.config.battery_hours))],["Minimum battery reserve",hours(c.minimum_reserve)],
 ])+(includeTimeline?`<p class="muted">Sunlight and usable power over the mission</p><div id="power-readout"></div><canvas id="power-timeline" class="power-timeline" aria-label="Relay sunlight, power and battery reserve"></canvas>`:""));
}
function planPanel(){
 return panel("Restricted candidate search",`<p class="callout ${result.feasible?"":"amber"}">${planState()}</p>`+readout([
 ["Selected",result.recommended.join(", ")||"Direct path only"],["Candidates with usable windows",String(result.eligible)],
 ["Required coverage",pct(Number(result.config.availability))],["Maximum blackout",hours(Number(result.config.blackout_hours))],
 ["Optimization",result.config.resilient?"Single-relay failure requirement":"Nominal coverage requirement"],
 ])+`<p><button id="optimize" class="primary">Optimize relay network</button></p>`);
}
function siteRows(){
 if(!compared)return '<p class="muted">Compare all three regions over the current period and assumptions.</p>';
 return table(["Region","Coordinates","Solar","Direct RF","Max blackout","Max darkness","Outages","DEM coverage","Relay assisted"],
 compared.map(c=>row([`<button data-site="${Object.entries(sites).find(([,v])=>v[0]===c.site.name)?.[0]??""}">${escape(c.site.name)}${c.site.name===result.site.name?" · current":""}</button>`,`${n(c.site.lat,3)}°, ${n(c.site.lon,3)}° E`,pct(c.solar.availability),pct(c.direct.availability),hours(c.direct.longest),hours(c.solar.longest),String(c.direct.intervals),pct(c.site.terrain_coverage),pct(c.network.availability)])));
}
function render(){
 if(!result)return;
 const net=live(), r=result;
 const charts=`<div class="split">${mapPanel()}${horizonPanel()}</div>`;
 const context=readout([["Position",`${n(r.site.lat,5)}°, ${n(r.site.lon,5)}° E`],["Elevation",n(r.site.elevation)+" m"],
 ["Mean / max slope",n(r.site.mean_slope)+"° / "+n(r.site.max_slope)+"°"],["DEM grid spacing",n(r.site.resolution,0)+" m"],["Local DEM coverage",pct(r.site.terrain_coverage)]]);
 const kpis=`<div class="metrics">${metric("DIRECT TO EARTH",pct(r.direct.availability),`${r.direct.intervals} blackout intervals`)}${metric("SUNLIGHT",pct(r.solar.availability),hours(r.solar.longest)+" longest darkness")}${metric("LONGEST DIRECT BLACKOUT",hours(r.direct.longest),hours(r.direct.total)+" offline in total")}${metric("WITH ACTIVE RELAYS",pct(net.metrics.availability),hours(net.metrics.longest)+" longest blackout","green")}${metric("WORST SINGLE FAILURE",pct(r.worst.availability),hours(r.worst.longest)+" longest blackout")}</div>`;
 const comparison=bars("Direct-to-Earth",r.direct.availability)+bars("Active network",net.metrics.availability,true)+`<p class="callout ${net.meets?"":"amber"}">${net.meets?"Active network meets communication requirements.":"Active network falls short of communication requirements."}</p>`;
 const candidates=table(["Relay","Range","Slope","Surface LOS","Solar","Power","Path","Recovered"],
 [...r.candidates].sort((a,b)=>candidateSort==="distance"?a.distance-b.distance:b.recovered-a.recovered).map(c=>row([`<button aria-pressed="${c.id===selected}" data-candidate="${c.id}">${c.id}</button>`,n(c.distance,2)+" km",n(c.slope)+"°",c.los===null?"Uncertain":c.los?"Clear":"Blocked",pct(c.solar.availability),pct(c.power.availability),pct(c.coverage.availability),pct(c.recovered)])));
 const choice=r.candidates.find(c=>c.id===selected);
 const switchers=r.recommended.map(id=>`<label><input type="checkbox" data-disable="${id}" ${disabled.has(id)?"checked":""}> Disable ${id}</label>`).join("");
 const nodes=`<div class="network-branches"><div class="network-chart"><span class="node active">Lander</span><span aria-hidden="true">→</span><span class="node">Earth · direct</span></div>${r.recommended.map(id=>`<div class="network-chart"><span class="node">Lander</span><span aria-hidden="true">→</span><span class="node ${disabled.has(id)?"failed":"active"}">${id}${disabled.has(id)?" · disabled":""}</span><span aria-hidden="true">→</span><span class="node">Earth</span></div>`).join("")}</div>`;
 const failures=table(["Disabled relay","Availability","Longest blackout","Coverage lost","Blackout increase","Windows lost","Meets limits"],
 r.failures.map(f=>row([f.id,pct(f.metrics.availability),hours(f.metrics.longest),n(f.availability_lost)+" pp",hours(f.blackout_increase),String(f.windows_lost),f.meets?"Yes":"No"])));
 switch(active){
 case "overview":outlet.innerHTML=`<div class="hero">${mapPanel()}<section class="panel mission-summary"><p class="eyebrow">MISSION / ${escape(r.site.name.toUpperCase())}</p><h2>${net.meets?"A path to connection.":"Every connection counts."}</h2><span class="verdict ${net.meets?"green":"amber"}">${net.meets?"✓ Active network meets communication limits":"△ Active network falls short of communication limits"}</span><div class="hero-value">${pct(net.metrics.availability)} <small>active coverage</small></div><p class="muted">${n(net.metrics.availability-r.direct.availability)} percentage points more of the mission covered than direct contact alone. ${escape(planState())}.</p><div class="hero-stats"><div><span class="muted">Longest active blackout</span><strong>${hours(net.metrics.longest)}</strong></div><div><span class="muted">Screened recommendation</span><strong>${r.recommended.length} relays</strong></div></div><button class="primary" data-go="${r.outages.length?"communication":"relays"}">${r.outages.length?"Inspect blackouts":"Explore relays"} <span aria-hidden="true">↗</span></button></section></div>`+kpis+timelinePanel()+`<div class="split">${panel("From direct to connected",comparison)}${panel("Independent surface paths",nodes+`<p class="caption">Each powered relay supplies a separate two-hop path. No relay-to-relay chain is modeled.</p>`+readout([["Mission length",hours(r.times.length*Number(r.config.minutes)/60)],["Darkness limit",r.darkness_meets?"Met":"Exceeded"]]))}</div>`;break;
 case "sites":outlet.innerHTML=panel("Find the right horizon",`<p class="muted table-note">Approximate research points, evaluated over the applied mission period. Select a region, then Run mission to apply it.</p><button id="compare" class="primary" ${pending==="compare"?"disabled":""}>${pending==="compare"?"Comparing regions…":"Compare all regions"}</button><div id="comparison">${siteRows()}</div>`)+`<div class="split">${mapPanel()}${panel("Applied landing point",context)}</div>`;break;
 case "communication":outlet.innerHTML=timelinePanel()+outageTable()+`<button class="primary" data-go="relays">Explore relay paths <span aria-hidden="true">↗</span></button>`+charts+panel("Applied landing terrain",context)+panel("Darkness intervals",r.darkness.length?table(["Start","End","Duration"],r.darkness.map(d=>row([utc(d.start),utc(d.end),hours(d.hours)]))):'<p class="muted">No darkness intervals in this sampled period.</p>');break;
 case "relays":outlet.innerHTML=`<div class="split">${mapPanel()}${planPanel()}</div>`+panel("Screened relay candidates",`<div class="controls"><label>Sort by <select id="candidate-sort"><option value="recovered" ${candidateSort==="recovered"?"selected":""}>Recovered coverage ↓</option><option value="distance" ${candidateSort==="distance"?"selected":""}>Deployment range ↑</option></select></label></div>`+(r.candidates.length?candidates:'<p class="muted">No screened candidates for these constraints. Review deployment and terrain settings.</p>'))+(choice?relayDetail(choice):"")+timelinePanel()+`<button class="primary" data-go="resilience">Test network resilience <span aria-hidden="true">↗</span></button>`;break;
 case "resilience":outlet.innerHTML=panel("Test the remaining paths",`<div class="controls">${switchers||'<p class="muted">This scenario uses the direct path only; there are no selected relays to disable.</p>'}</div>`+nodes+`<div class="metrics">${metric("NOMINAL COVERAGE",pct(r.network.availability),"All recommended relays")}${metric("SIMULATED COVERAGE",pct(net.metrics.availability),disabled.size+" disabled relays")}${metric("COVERAGE CHANGE",n(net.metrics.availability-r.network.availability)+" pp","Against nominal")}${metric("NOMINAL BLACKOUT",hours(r.network.longest),"Longest continuous outage")}${metric("BLACKOUT CHANGE",hours(net.metrics.longest-r.network.longest),"Against nominal")}</div>`+comparison)+timelinePanel()+panel("Single-relay criticality",`<p class="muted">Worst individual failure: ${pct(r.worst.availability)} availability · ${hours(r.worst.longest)} longest blackout. Nominal time with at least two independent paths: ${pct(r.redundant)}. The table below tests individual failures; toggles above may simulate multiple failures.</p>`+(r.failures.length?failures:'<p class="muted">No selected relays to evaluate.</p>'));break;
 case "sensitivity":outlet.innerHTML=panel("Assumption sweep",`<p class="muted">Re-run the current mission with one changed assumption at a time.</p><button id="sweep" class="primary" ${pending==="sensitivity"?"disabled":""}>${pending==="sensitivity"?"Computing sweep…":"Run sensitivity analysis"}</button><div id="sensitivity-results">${sensitivity?table(["Variant","Direct","Direct blackout","Direct outages","Network","Network blackout","Eligible relays","Selected relays","Feasible"],sensitivity.map(s=>s.error?`<tr><td>${escape(s.label)}</td><td colspan="8">${escape(s.error)}</td></tr>`:row([escape(s.label),pct(s.direct.availability),hours(s.direct.longest),String(s.direct.intervals),pct(s.network.availability),hours(s.network.longest),String(s.eligible),String(s.count),s.feasible?"Yes":"No"]))):""}</div>`)+(sensitivity?panel("Network availability by assumption",sensitivity.filter(s=>!s.error).map(s=>bars(escape(s.label),s.network.availability,true)).join("")):"");break;
 case "data":outlet.innerHTML=panel("Planning model, with limits",`<p>MOONMESH estimates communication for approximate lunar research points. It is a planning prototype; terrain sampling, RF assumptions, and a restricted relay catalog limit its results.</p><p class="muted">Flat-horizon visibility excludes local terrain. Terrain visibility adds the sampled horizon. Direct RF also requires adequate link margin. A relay path requires surface line of sight, both radio links, and usable power. Missing terrain is uncertain and excluded from credited availability. N−1 tests individual relay failures while retaining shared Earth receiver assumptions.</p>`)+panel("Data provenance",provenance())+panel("Power assumptions",readout([["Battery endurance",hours(Number(r.config.battery_hours))],["Initial charge",pct(Number(r.config.initial_charge_pct))],["Net charging rate",n(Number(r.config.charge_ratio))+" reserve hours / sunlight hour"]]))+[["earth_radio","Earth radio profile"],["surface_radio","Surface radio profile"]].map(([key,title])=>panel(title,readout(rf.map(([name,label])=>[label,n(Number((r.config[key] as Record<string,number>)[name]),3)])))).join("");break;
 }
 connect();
 paint();
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
 const r=result, s=r.samples, i=cursor;
 const t=document.querySelector<HTMLInputElement>("#cursor");if(t)t.value=String(i);
 const time=document.querySelector("#time");if(time)time.textContent=utc(r.times[i]);
 if(document.querySelector("#timeline")){const data=live();timeline(r,data.combined,data.assisted,i,zoom,move);
 $("#timeline-readout").innerHTML=readout([["Selected sample",utc(r.times[i])],["Direct / active network",`${s.status[i]} / ${data.combined[i]?"available":"unavailable"}`],["Cause",escape(s.reason[i])],["Sunlight",!s.solar_known[i]?"Uncertain":s.solar[i]?"Illuminated":"Dark"]]);}
 if(document.querySelector("#map"))map(r,layer,disabled,i,c=>{selected=c.id;if(active!=="relays")$("nav button[data-view=relays]").click();else render();},selected,mapZoom);
 if(document.querySelector("#horizon")){
    horizon(r,i);
    $("#point").innerHTML=readout([["Time",utc(r.times[i])],["Earth az / el",n(s.earth_az[i])+"° / "+n(s.earth_el[i])+"°"],
    ["Sun az / el",n(s.sun_az[i])+"° / "+n(s.sun_el[i])+"°"],["Terrain at Earth azimuth",n(s.earth_limit[i])+"°"],
    ["Clearance after allowance",n(s.earth_clearance[i],2)+"°"],["Horizon feature distance",s.obstruction_m[i]===null?"Unknown":n(s.obstruction_m[i]!/1000)+" km"],
    ["RF margin",n(s.rf_margin[i])+" dB"],["Status",escape(s.status[i]+" · "+s.reason[i])]]);
 }
 const candidate=r.candidates.find(c=>c.id===selected);
 if(candidate&&document.querySelector("#power-timeline")){powerTimeline(candidate,Number(r.config.battery_hours),i);$("#power-readout").innerHTML=readout([["At selected time",utc(r.times[i])],["Sunlight / powered",`${candidate.sunlight[i]?"Illuminated":"Dark"} / ${candidate.powered[i]?"Powered":"Unpowered"}`],["Battery reserve",hours(candidate.reserve[i])]]);}
}

function connect(){
 const button=(id:string,action:()=>void)=>{const node=document.querySelector<HTMLButtonElement>("#"+id);if(node)node.onclick=action;};
 button("zoom",()=>{zoom=zoom<=Math.max(6,result.times.length/8)?result.times.length:Math.max(6,Math.floor(zoom/2));render();});
 button("map-zoom",()=>{mapZoom=mapZoom===3?1:mapZoom+1;render();});
 button("optimize",()=>void run());
 button("improve",()=>navigate("relays"));
 button("compare",()=>void auxiliary("compare"));
 button("sweep",()=>void auxiliary("sensitivity"));
 const range=document.querySelector<HTMLInputElement>("#cursor");if(range)range.oninput=()=>move(Number(range.value));
 const layers=document.querySelector<HTMLSelectElement>("#layer");if(layers){layers.value=layer;layers.onchange=()=>{layer=layers.value;paint();};}
 outlet.querySelectorAll<HTMLButtonElement>("[data-time]").forEach(b=>b.onclick=()=>{move(Number(b.dataset.time));$("#cursor").focus();});
 outlet.querySelectorAll<HTMLButtonElement>("[data-go]").forEach(b=>b.onclick=()=>navigate(b.dataset.go!));
 outlet.querySelectorAll<HTMLButtonElement>("[data-site]").forEach(b=>b.onclick=()=>{if(!b.dataset.site)return;$<HTMLSelectElement>('[name="site"]').value=b.dataset.site;for(const name of ["latitude","longitude"])$<HTMLInputElement>(`[name="${name}"]`).value="";dirty();$<HTMLSelectElement>('[name="site"]').focus();});
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
 readout([["Site",`${n(result.site.lat,5)}°, ${n(result.site.lon,5)}° E`],["Terrain",`${n(result.site.elevation)} m · ${n(result.site.mean_slope)}° average local slope`],["Sunlight",pct(result.solar.availability)],["Direct communication",pct(result.direct.availability)],["Active network",pct(net.metrics.availability)],["Worst single failure",pct(result.worst.availability)],["Selected relays",result.recommended.join(", ")||"Direct only"],["Disabled",Array.from(disabled).join(", ")||"None"]])+
 chartImages+outageTable()+
 panel("Relay feasibility and single-relay failures",result.candidates.filter(c=>result.recommended.includes(c.id)).map(c=>relayDetail(c,false)).join("")+result.failures.map(f=>`<p>Disable ${f.id} · ${pct(f.metrics.availability)} availability · ${hours(f.metrics.longest)} longest blackout · ${f.meets?"requirements met":"requirements unmet"}</p>`).join(""))+
 panel("Configuration",readout(Object.entries(result.config).map(([k,v])=>[escape(k.replaceAll("_"," ")),escape(typeof v==="object"?JSON.stringify(v):v)])))+panel("Assumptions and provenance",provenance());
 $("main").append(article);
 await Promise.all(Array.from(article.querySelectorAll("img")).map(img=>img.decode().catch(()=>{})));
 try{window.print();}finally{navigate(view,false);article.remove();}
};

window.addEventListener("resize",paint);
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
  await run();
 }catch(error){status((error as Error).message,true);$("#retry").hidden=false;$(".initial-state").innerHTML=`<h2>Mission settings unavailable</h2><p class="muted">Use Retry setup to reconnect.</p>`;}
}
$("#retry").onclick=()=>{$("#retry").hidden=true;void initialize();};
void initialize();
