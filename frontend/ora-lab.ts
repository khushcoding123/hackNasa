// ORA lab views: relay placement, resilience, digital twin, lunar night, underground, lasers, Earth mines,
// Earth access and model limits. Mountable: the standalone ORA page (ora.ts) and the Moon explorer's side panel
// (app.ts) each give it a container and a scenario. API payloads are documented in ora/api.py; typed loosely here.
type J = any;
import {assessmentBadge, assessmentHtml, modelsHtml} from "./evidence.js";

export type OraTab = "network" | "resilience" | "twin" | "night" | "tunnel" | "laser" | "earth" | "access" | "data";
export const ORA_TABS: Record<OraTab, [string, string, string]> = {
 network:["01 / PLACE RELAYS","Relays for a lunar mine.","The model proposes sites. Line of sight on real terrain decides."],
 resilience:["02 / RESILIENCE LAB","What breaks first?","Fail nodes and links, and compare real terrain with idealized networks."],
 twin:["03 / DIGITAL TWIN","Run the network through time.","Sunlight, battery, temperature and links, hour by hour. Then ask what if."],
 night:["04 / LUNAR NIGHT","354 hours of darkness.","What it costs to keep a relay alive, and how hibernation changes the answer."],
 tunnel:["05 / UNDERGROUND","Follow the dig.","A robot drops repeaters as the mine grows. Three policies, then failures and collapses."],
 laser:["06 / LASERS","Light for data and power.","Optical trunks and laser power from a sunlit rim to a dark crater floor."],
 earth:["07 / EARTH MINES","The same engine on Earth.","Room-and-pillar mines need links that survive a collapse."],
 access:["08 / EARTH ACCESS","Where does Earth shine?","How much of a year Earth is above the horizon near the pole, and which link layouts survive failures."],
 data:["MODELS & LIMITS","What was trained, and how far to trust it.","Measured results, data sources and known limits."],
};
/** Tabs that analyse the current area or scenario; the others are independent of it. */
export const AREA_TABS: OraTab[] = ["network", "resilience", "twin", "night"];

export interface OraEnv {
 view: HTMLElement;                                   // where the active tab renders
 scenarioBody(): Record<string, unknown>;             // the scenario form, or {polygon} for a drawn area
 status(text: string, error?: boolean): void;
 state?(text: string, attention: boolean): void;      // "Working" / "Ready" / "Needs attention"
 header?(chapter: string, title: string, purpose: string): void;
 scenarioFormHidden?(hidden: boolean): void;
 subtitle?(text: string): void;
 /** Embedded use: run each analysis with its defaults instead of waiting for a button. */
 auto?: boolean;
 /** Whether this lab is on screen now; results that arrive later only redraw when it is. */
 visible?(): boolean;
 canBuild?(): boolean;
 building?(on: boolean): void;
}

export function createOraLab(env: OraEnv) {
 const view = env.view;
 const q = <T extends HTMLElement = HTMLElement>(s: string) => view.querySelector<T>(s);
 const C={cyan:"#33adff",green:"#3ddc97",amber:"#ffb84d",red:"#ff6b6b",muted:"#a1a1aa",text:"#ffffff",violet:"#bba9ff",grid:"#2c2c31",bg:"#0a0a0b"};
 let tab: OraTab = "network", net:J, lab:J, sim:J, nightResult:J, mine:J, earthMine:J, beam:J, conf:J, cover:J, dteData:J, topo:J, stress:J, accuracy:J;
 let hour=0, layer="score", mode="betweenness", minePolicy="resilient", kills:{node:number;hour:number}[]=[], busy=0, failed=false, generation=0;
 const running = new Map<string, Promise<unknown>>();

 const n=(x:number|null|undefined,d=1)=>x==null||Number.isNaN(x)?"—":x.toFixed(d);
 const pct=(x:number|null|undefined,d=0)=>x==null?"—":(100*x).toFixed(d)+"%";
 const esc=(s:unknown)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]!));
 const panel=(title:string,html:string)=>`<section class="panel"><h2>${title}</h2>${html}</section>`;
 const metric=(label:string,value:string,caption:string,cls="")=>`<section class="panel metric"><small>${label}</small><strong class="${cls}">${value}</strong><small>${caption}</small></section>`;
 const table=(head:string[],rows:string[][])=>`<div class="table-wrap"><table><thead><tr>${head.map(h=>`<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
 const readout=(rows:[string,string][])=>`<dl>${rows.map(([a,b])=>`<dt>${a}</dt><dd>${b}</dd>`).join("")}</dl>`;
 const key=(items:[string,string,boolean?][])=>`<ul class="key">${items.map(([color,label,line])=>`<li><i class="${line?"line":""}" style="background:${color}"></i>${label}</li>`).join("")}</ul>`;
 const pill=(ok:boolean,yes:string,no:string)=>`<span class="pill ${ok?"ok":"warn"}">${ok?yes:no}</span>`;
 const visible=()=>env.visible?env.visible():true;

 async function api<T=J>(path:string,body?:unknown):Promise<T>{
  let response:Response;
  try{response=await fetch("/api/ora/"+path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});}
  catch{throw new Error("Cannot reach the MOONMESH server. Check that it is running.");}
  const data=await response.json().catch(()=>({detail:`Server error ${response.status}.`}));
  if(!response.ok){
   const detail=data.detail;
   throw new Error(typeof detail==="string"?detail:Array.isArray(detail)?detail.map((d:J)=>`${d.loc.slice(1).join(".")}: ${d.msg}`).join(" "):"Request failed.");
  }
  return data;
 }
 async function task<T>(label:string,work:()=>Promise<T>):Promise<T|undefined>{
  busy++;env.status(label);env.state?.("Working",false);
  try{const out=await work();failed=false;env.status("");return out;}
  catch(error){failed=true;env.status((error as Error).message,true);return undefined;}
  finally{busy--;if(!busy)env.state?.(failed?"Needs attention":"Ready",failed);}
 }
 /** One request per analysis at a time; a second ask while it runs waits on the first. */
 function once<T>(name:string,work:()=>Promise<T>):Promise<T>{
  const current=running.get(name) as Promise<T>|undefined; if(current)return current;
  const job=work().finally(()=>running.delete(name)); running.set(name,job); return job;
 }
 async function ensureConfig(){
  if(conf)return conf;
  const loaded=await once("config",()=>task("Loading the relay lab…",()=>api("config")));
  if(loaded)conf=loaded;
  return conf;
 }

 // ---------- canvas helpers ----------
 function canvas(id:string){
  const element=q<HTMLCanvasElement>("#"+id) as HTMLCanvasElement|null;
  if(!element)return undefined;
  const bounds=element.getBoundingClientRect(), dpi=devicePixelRatio;
  element.width=Math.round(bounds.width*dpi);element.height=Math.round(bounds.height*dpi);
  const ctx=element.getContext("2d")!;ctx.scale(dpi,dpi);ctx.fillStyle=C.bg;ctx.fillRect(0,0,bounds.width,bounds.height);
  return {element,ctx,w:bounds.width,h:bounds.height};
 }
 let shadeCache:{key:string;image:HTMLCanvasElement}|undefined;
 function shadeImage(){
  const rows:number[][]=net.map.image, k=net.site+rows.length;
  if(shadeCache&&shadeCache.key===k)return shadeCache.image;
  const image=document.createElement("canvas");image.width=rows[0].length;image.height=rows.length;
  const ctx=image.getContext("2d")!, data=ctx.createImageData(image.width,image.height);
  rows.forEach((row,y)=>row.forEach((v,x)=>{const i=4*(y*image.width+x);data.data[i]=v*.62;data.data[i+1]=v*.68;data.data[i+2]=v*.8;data.data[i+3]=255;}));
  ctx.putImageData(data,0,0);shadeCache={key:k,image};return image;
 }
 const ROLE:Record<string,string>={gateway:C.cyan,relay:C.green,mine:C.amber,candidate:"#8a8a94"};
 interface MapState{nodes:J[];edges:{a:number;b:number;color:string;dash?:boolean}[];color:(node:J)=>string;label?:(node:J)=>string;pick?:(node:J)=>void}
 function drawMap(id:string,state:MapState,overlay?:string){
  const c=canvas(id);if(!c)return;
  const {ctx,w,h,element}=c, side=Math.min(w,h), x0=(w-side)/2, y0=(h-side)/2, scale=side/net.size[1];
  ctx.imageSmoothingEnabled=true;ctx.drawImage(shadeImage(),x0,y0,side,side);
  if(overlay==="coverage"){
   const grid:number[][]=net.coverage.grid, cell=side/grid.length;
   grid.forEach((row,y)=>row.forEach((v,x)=>{if(v<0)return;ctx.fillStyle=v===0?"rgba(242,132,140,.42)":v===1?"rgba(132,222,255,.20)":"rgba(115,216,174,.30)";ctx.fillRect(x0+x*cell,y0+y*cell,cell+.5,cell+.5);}));
  }
  const at=(v:J)=>[x0+v.x*scale,y0+v.y*scale] as [number,number];
  const byId=new Map<number,J>(state.nodes.map(v=>[v.id,v]));
  for(const e of state.edges){
   const a=byId.get(e.a),b=byId.get(e.b);if(!a||!b)continue;
   ctx.strokeStyle=e.color;ctx.lineWidth=1.6;ctx.setLineDash(e.dash?[5,5]:[]);ctx.beginPath();ctx.moveTo(...at(a));ctx.lineTo(...at(b));ctx.stroke();
  }
  ctx.setLineDash([]);
  for(const v of state.nodes){
   const [x,y]=at(v), big=v.role!=="candidate";
   ctx.fillStyle=state.color(v);ctx.beginPath();
   if(v.role==="mine")ctx.rect(x-5,y-5,10,10);else ctx.arc(x,y,big?5.5:2.4,0,2*Math.PI);
   ctx.fill();
   if(big){ctx.strokeStyle="#050506";ctx.lineWidth=1.2;ctx.stroke();ctx.font="11px monospace";ctx.fillStyle=C.text;ctx.fillText(state.label?state.label(v):String(v.id),x+8,y-6);}
  }
  const km=5, bar=km*1000/net.pixel_m*scale;
  ctx.fillStyle=C.text;ctx.font="11px monospace";ctx.fillRect(x0+10,y0+side-14,bar,2);ctx.fillText(km+" km",x0+10,y0+side-19);ctx.fillText("GRID N ↑",x0+10,y0+16);
  element.onclick=state.pick?event=>{
   const bounds=element.getBoundingClientRect();let best:J,dist=14;
   for(const v of state.nodes){if(v.role==="candidate")continue;const [x,y]=at(v),d=Math.hypot(event.clientX-bounds.left-x,event.clientY-bounds.top-y);if(d<dist){dist=d;best=v;}}
   if(best)state.pick!(best);
  }:null;
 }
 interface Series{label:string;color:string;x:number[];y:number[];band?:number[];dash?:boolean}
 function drawChart(id:string,series:Series[],xLabel:string,yLabel:string,yMax=1,marker?:number){
  const c=canvas(id);if(!c)return;
  const {ctx,w,h}=c, L=46,R=w-12,T=12,B=h-34;
  const xMax=Math.max(...series.flatMap(s=>s.x),1e-9), px=(x:number)=>L+x/xMax*(R-L), py=(y:number)=>B-Math.min(y,yMax)/yMax*(B-T);
  ctx.font="10px monospace";ctx.strokeStyle=C.grid;ctx.lineWidth=1;
  for(let i=0;i<=4;i++){
   const y=T+(B-T)*i/4;ctx.beginPath();ctx.moveTo(L,y);ctx.lineTo(R,y);ctx.stroke();
   ctx.fillStyle=C.muted;ctx.textAlign="right";ctx.fillText((yMax*(1-i/4)).toFixed(yMax<=1?2:0),L-6,y+3);
   ctx.textAlign="center";ctx.fillText((xMax*i/4).toFixed(xMax<=1?2:0),px(xMax*i/4),B+14);
  }
  ctx.fillText(xLabel,(L+R)/2,h-6);ctx.save();ctx.translate(11,(T+B)/2);ctx.rotate(-Math.PI/2);ctx.fillText(yLabel,0,0);ctx.restore();
  for(const s of series){
   if(s.band){
    ctx.fillStyle=s.color+"33";ctx.beginPath();
    s.x.forEach((x,i)=>ctx.lineTo(px(x),py(s.y[i]+s.band![i])));
    for(let i=s.x.length-1;i>=0;i--)ctx.lineTo(px(s.x[i]),py(Math.max(0,s.y[i]-s.band[i])));
    ctx.fill();
   }
   ctx.strokeStyle=s.color;ctx.lineWidth=2;ctx.setLineDash(s.dash?[6,4]:[]);ctx.beginPath();
   s.x.forEach((x,i)=>i?ctx.lineTo(px(x),py(s.y[i])):ctx.moveTo(px(x),py(s.y[i])));ctx.stroke();
  }
  ctx.setLineDash([]);
  if(marker!==undefined){ctx.fillStyle=C.text;ctx.fillRect(px(marker),T,1,B-T);}
  ctx.textAlign="left";
 }
 function drawMine(id:string,data:J,policy:string){
  const c=canvas(id);if(!c)return;
  const {ctx,w,h}=c, pts=Object.values(data.junctions) as number[][];
  const xs=pts.map(p=>p[0]),ys=pts.map(p=>p[1]), minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys);
  const scale=Math.min((w-60)/Math.max(maxX-minX,1),(h-60)/Math.max(maxY-minY,1));
  const at=(p:number[])=>[30+(p[0]-minX)*scale,h/2-((p[1]-(minY+maxY)/2))*scale] as [number,number];
  const result=data.policies[policy], worst=result.worst_collapse as string|null;
  for(const [a,b] of data.segments as string[][]){
   const collapsed=worst===`${a}-${b}`||worst===`${b}-${a}`;
   ctx.strokeStyle=collapsed?C.red:"#3a3a40";ctx.lineWidth=collapsed?5:7;ctx.lineCap="round";ctx.beginPath();ctx.moveTo(...at(data.junctions[a]));ctx.lineTo(...at(data.junctions[b]));ctx.stroke();
  }
  ctx.font="11px monospace";
  for(const [name,radio] of Object.entries(result.radios) as [string,J][]){
   const [x,y]=at(radio.pos), gateway=name==="gateway";
   ctx.fillStyle=gateway?C.cyan:name===result.worst_repeater?C.amber:C.green;ctx.beginPath();ctx.arc(x,y,gateway?7:4.5,0,2*Math.PI);ctx.fill();
   if(gateway){ctx.fillStyle=C.text;ctx.fillText("portal gateway",x+10,y-8);}
  }
  ctx.fillStyle=C.text;ctx.fillRect(30,h-16,100*scale,2);ctx.fillText("100 m",30,h-21);
 }

 // ---------- views ----------
 const waiting=(what:string)=>`<section class="panel initial-state loading-state"><span class="eyebrow">CALCULATING</span><h2>${what}</h2><p class="muted">This runs on its own and fills in here when it finishes. You can keep using the Moon meanwhile.</p></section>`;
 function kpis(){
  const p=net.plan, s=net.structure;
  return `<div class="metrics">${metric("MINES LINKED",pct(p.served),`${p.mines.length} mining sites to ${p.gateways.length} gateways`,p.feasible?"green":"amber")}
  ${metric("RELAYS CHOSEN",String(p.chosen.length),`${p.chain.length} to connect · ${p.added.length} spare`)}
  ${metric("WORST SINGLE FAILURE",pct(p.worst_after_failure),"mines still linked after losing any one node",p.worst_after_failure>=1?"green":"amber")}
  ${metric("GROUND COVERED",n(net.coverage.covered_pct,0)+"%",`${n(net.coverage.redundant_pct,0)}% hears two or more relays`)}
  ${metric("SINGLE POINTS OF FAILURE",String(s.articulation_points),`${s.bridges} single-path links`,s.articulation_points?"amber":"green")}</div>`;
 }
 function networkView(){
  const p=net.plan, byId=new Map<number,J>(net.nodes.map((v:J)=>[v.id,v]));
  const layers=`<div class="controls"><label>Colour candidates by <select id="layer"><option value="score">siting score</option><option value="sun">sunlight</option><option value="earth">Earth visibility</option><option value="coverage">coverage and dead zones</option></select></label></div>`;
  const fragile=table(["Node","Role","Sunlight","Earth view","Mines linked if lost"],net.fragile.map((f:J)=>{const v=byId.get(f.id);return [String(f.id),f.role,pct(v.sun),pct(v.earth),`${pct(f.served_if_lost)} ${f.served_if_lost<1?'<span class="pill warn">critical</span>':""}`];}));
  const paths=Object.entries(net.structure.disjoint_paths as Record<string,number>).map(([id,k])=>`Mine ${id}: ${k} independent path${k===1?"":"s"}`).join(" · ");
  return kpis()+`<div class="split">${panel("Terrain, candidates and the chosen network",layers+`<canvas id="map" class="ora-map" aria-label="Terrain map with candidate sites, chosen relays, gateways and mining sites"></canvas>`+
   key([[C.cyan,"gateway (sees Earth)"],[C.green,"chosen relay"],[C.amber,"mining site"],["#8a8a94","candidate"],["rgba(242,132,140,.8)","dead zone (coverage layer)"]])+
   `<p class="caption">${esc(net.site)} · ${net.nodes.length} sites · ${net.all_edges.toLocaleString()} line-of-sight links within ${n(net.scenario.max_range_km,0)} km.</p>`)}
  ${panel("Placement",`<p>${assessmentBadge(net.assessment)}</p><p class="callout ${p.feasible?"":"amber"}">${p.feasible?"Best evaluated relay set · every mining site linked in static graph":"Best evaluated relay set · some mining sites unreachable"}</p>`+readout([
   ["Siting scores",esc(net.score_method)+(net.score_evidence?.fallback?' <span class="pill warn">fallback</span>':"")],["Chain relays",p.chain.join(", ")||"none needed"],["Spare relays",p.added.join(", ")||"none added"],["Gateways",p.gateways.join(", ")],
   ["After any one failure","mean "+pct(p.mean_after_failure)+" · worst "+pct(p.worst_after_failure)],["Independent paths",paths||"—"]])+
   `<p class="note">Stage 1 connects each mining site to a gateway along the cheapest line-of-sight chain, steering toward sunlit, high-scoring ground. Stage 2 adds spare relays where they most improve what survives a single failure. This is a heuristic search over the screened candidates, not a proven optimum.</p>`)}</div>`+
   panel("What breaks first",fragile+`<p class="note">Each row removes one node from the chosen network and counts the mining sites that can still reach a gateway.</p>`)+
   panel("Requirements status",assessmentHtml(net.assessment))+partsPanel()+coveragePanel();
 }
 function partsPanel(){
  const rows=(net.configs as J[]).map(c=>[String(c.id),esc(c.role),esc((c.parts as J[]).map(p=>p.module).join(", ")),n(c.mass_kg,1)+" kg",n(c.power_w,1)+" W",esc((c.notes as string[]).join(" · ")||"—")]);
  const assumed=(net.configs as J[]).some(c=>(c.assumed_modules||[]).length);
  return panel("Parts per relay",table(["Node","Role","Modules","Mass","Power","Why"],rows)+(assumed?`<p class="note"><span class="pill warn">placeholder</span> Masses and powers come from the module catalogue (<code>ora/modules.yaml</code>) and are not from datasheets yet.</p>`:""));
 }
 function coveragePanel(){
  const body=cover?readout([["Sites chosen",cover.sites.map((x:J)=>x.id).join(", ")],["Ground seen",pct(cover.coverage)+" of sampled points"],["Random sets of the same size",pct(cover.random_coverage)],["Line of sight computed for",`${cover.los_rows} of ${cover.candidates} candidates`]])
   +`<p class="note">${esc(cover.method)}: every candidate's line of sight to ${cover.targets} sampled ground points is computed, and sites are added one at a time by how much new ground each one sees. This is the coverage-first alternative to the mine-linking placement above.</p>`
   :`<p class="note">Choose sites for the most ground seen instead of for linking the mines. Every candidate is checked by exact line of sight.</p>`;
  return panel("Coverage-first sites",`<div class="ora-grid"><label>Relays<input id="cover-budget" type="number" min="1" max="16" value="${cover?cover.sites.length:6}"></label><button id="run-cover" class="primary">Choose for coverage</button></div>`+body);
 }
 function paintNetwork(){
  const chosen=new Set<number>([...net.plan.chosen,...net.plan.gateways,...net.plan.mines]);
  const heat=(t:number)=>`rgb(${Math.round(70+185*t)},${Math.round(110+90*t)},${Math.round(160-90*t)})`;
  const scores=net.nodes.filter((v:J)=>v.score!=null).map((v:J)=>v.score), lo=Math.min(...scores), hi=Math.max(...scores);
  drawMap("map",{nodes:[...net.nodes].sort((a:J,b:J)=>Number(chosen.has(a.id))-Number(chosen.has(b.id))),
   edges:net.edges.map((e:J)=>({a:e.a,b:e.b,color:"rgba(115,216,174,.85)"})),
   color:(v:J)=>v.role!=="candidate"?ROLE[v.role]:layer==="sun"?heat(v.sun):layer==="earth"?heat(v.earth):layer==="coverage"?"rgba(255,255,255,.35)":heat((v.score-lo)/Math.max(hi-lo,1e-9))},layer==="coverage"?"coverage":undefined);
 }
 const GRAPH_COLOR:Record<string,string>={"Real terrain":C.green,"No terrain, same range":C.muted,"No terrain, same mean degree":C.cyan,"Rewired, same degrees":C.violet};
 const MODE_LABEL:Record<string,string>={random:"Random failures",betweenness:"Targeted: busiest relay first",articulation:"Single points of failure first",disk:"Regional failure (landslide, ejecta)",edge:"Link degradation (dust, pointing)"};
 function resilienceView(){
  if(!lab)return env.auto&&running.has("resilience")?waiting("Failing nodes and links on four networks."):panel("Resilience lab",`<p class="note">Fails relays and links in five ways on the full candidate network and compares real terrain with three idealized networks on the same sites. Each random mode is repeated and reported as a mean with a 95% interval.</p><div class="ora-grid"><label>Repeats per mode<input id="seeds" type="number" min="5" max="400" value="100"></label><label>Regional failure radius (m)<input id="disk" type="number" min="100" max="10000" step="100" value="1500"></label><button id="run-lab" class="primary">Run the failure experiments</button></div>`);
  const labels=Object.keys(lab.graphs), modes=Object.keys(lab.graphs[labels[0]].modes);
  const struct=table(["Network","Links","Mean degree","Single points of failure","Single-path links","Largest connected share"],labels.map(l=>{const s=lab.graphs[l].structure;return [esc(l),s.edges.toLocaleString(),n(s.mean_degree),String(s.articulation_points),String(s.bridges),pct(s.giant_fraction)];}));
  const rtable=table(["Failure mode",...labels.map(esc)],modes.map(m=>[MODE_LABEL[m],...labels.map(l=>{const r=lab.graphs[l].modes[m];return `${n(r.schneider_r,3)}${r.runs>1?` ± ${n(r.schneider_r_ci,3)}`:""}`;})]));
  const real=lab.graphs["Real terrain"], matched=lab.graphs["No terrain, same mean degree"];
  const earlier=modes.filter(m=>real.modes[m].schneider_r<matched.modes[m].schneider_r-1e-9).length;
  return panel("Network degradation",`<div class="tabs" role="group" aria-label="Failure mode">${modes.map(m=>`<button data-mode="${m}" aria-pressed="${m===mode}">${MODE_LABEL[m]}</button>`).join("")}</div>
   <canvas id="curve" class="ora-chart" aria-label="Largest connected share of the network as nodes fail"></canvas>${key(labels.map(l=>[GRAPH_COLOR[l],l,true] as [string,string,boolean]))}
   <p class="caption">${lab.nodes} sites · ${n(lab.range_m/1000,0)} km range · ${lab.seeds} repeats for random modes · shaded band is the 95% interval. Mining sites and gateways are removed last.</p>`)+
   panel("Finding",`<p class="callout">Real terrain has ${real.structure.articulation_points} single points of failure; the terrain-free network with the same mean degree has ${matched.structure.articulation_points}. Terrain is less robust than that network in ${earlier} of ${modes.length} failure modes.</p><p class="note">This is the measured result for this site and seed, reported as found. The same-degree comparison isolates the effect of terrain from the effect of simply having fewer links.</p>`)+
   panel("Robustness score (Schneider R, higher is better)",rtable+`<p class="note">R is the mean share of the network still in one piece across the whole removal sequence.</p>`)+panel("Structure",struct)+
   `<p><button id="run-lab" class="primary">Run again</button> <span class="muted">with <input id="seeds" type="number" min="5" max="400" value="${lab.seeds}" style="width:90px"> repeats and a <input id="disk" type="number" min="100" max="10000" step="100" value="${lab.disk_radius_m}" style="width:100px"> m regional radius</span></p>`;
 }
 function paintResilience(){
  if(!lab)return;
  drawChart("curve",Object.entries(lab.graphs as Record<string,J>).map(([label,g])=>{const r=g.modes[mode];return {label,color:GRAPH_COLOR[label],x:r.fraction,y:r.giant,band:r.runs>1?r.giant_ci:undefined,dash:label!=="Real terrain"};}),
   mode==="edge"?"share of links removed":"share of nodes removed","largest connected share");
 }
 const design=()=>conf.design as Record<string,number|boolean>;
 function designFields(){
  const d=design(), num=(k:string,label:string,min:number,max:number,step:number)=>`<label>${label}<input type="number" data-design="${k}" min="${min}" max="${max}" step="${step}" value="${d[k]}"></label>`;
  return `<div class="ora-grid">${num("battery_wh","Battery (Wh)",1,100000,50)}${num("solar_w","Solar panel (W)",0,5000,5)}${num("active_w","Relaying power (W)",0.1,500,0.5)}${num("survival_w","Hibernating power (W)",0,100,0.1)}${num("heater_w","Heater (W)",0,500,0.5)}${num("rhu_w","Radioisotope heat (W)",0,50,0.5)}
  <label class="check"><input type="checkbox" data-design="hibernate" ${d.hibernate?"checked":""}> Hibernate when unlit</label>${num("duty","Wake share while hibernating",0,1,0.05)}</div>`;
 }
 const twinOptions:Record<string,number|boolean>={beam:false,optical:false,dust_db:0,rf_backup:true,night:false,disk_radius_m:0,blast_on:false,blast_kill_m:150,blast_damage_m:1500,blast_hour:0,dark_sink_k:100,lit_sink_k:250,dust_rate_per_day:0,dust_hazard_per_h:0,dust_seed:0,dust_solar_loss:.8};
 function twinBody(){
  return {...env.scenarioBody(),design:design(),kill:kills,beam:twinOptions.beam,optical:twinOptions.optical,dust_db:twinOptions.dust_db,rf_backup:twinOptions.rf_backup,
   night_start_h:twinOptions.night?48:null,...(twinOptions.disk_radius_m&&kills.length?{disk_radius_m:twinOptions.disk_radius_m,disk_node:kills[0].node,disk_hour:kills[0].hour}:{}),
   ...(twinOptions.blast_on&&kills.length?{blast_node:kills[0].node,blast_hour:twinOptions.blast_hour,blast_kill_m:twinOptions.blast_kill_m,blast_damage_m:twinOptions.blast_damage_m}:{}),
   dark_sink_k:twinOptions.dark_sink_k,lit_sink_k:twinOptions.lit_sink_k,dust_rate_per_day:twinOptions.dust_rate_per_day,dust_hazard_per_h:twinOptions.dust_hazard_per_h,dust_seed:twinOptions.dust_seed,dust_solar_loss:twinOptions.dust_solar_loss};
 }
 function twinView(){
  if(!conf)return waiting("Loading the relay lab.");
  const controls=panel("Node design and what-ifs",designFields()+`<div class="ora-grid" style="margin-top:14px">
   <label class="check"><input type="checkbox" data-option="night" ${twinOptions.night?"checked":""}> Force a full lunar night (354 h from hour 48)</label>
   <label class="check"><input type="checkbox" data-option="beam" ${twinOptions.beam?"checked":""}> Laser-power shadowed relays</label>
   <label class="check"><input type="checkbox" data-option="optical" ${twinOptions.optical?"checked":""}> Optical trunks on long hops</label>
   <label>Dust loss on optics (dB)<input type="number" data-option="dust_db" min="0" max="80" step="5" value="${twinOptions.dust_db}"></label>
   <label class="check"><input type="checkbox" data-option="rf_backup" ${twinOptions.rf_backup?"checked":""}> RF backup behind optical trunks</label>
   <label>Regional failure radius (m, around the first killed node)<input type="number" data-option="disk_radius_m" min="0" max="10000" step="100" value="${twinOptions.disk_radius_m}"></label>
   <label class="check"><input type="checkbox" data-option="blast_on" ${twinOptions.blast_on?"checked":""}> Blast at the first killed node</label>
   <label>Blast: certain kill within (m)<input type="number" data-option="blast_kill_m" min="0" max="10000" step="50" value="${twinOptions.blast_kill_m}"></label>
   <label>Blast: damage reaches (m)<input type="number" data-option="blast_damage_m" min="0" max="20000" step="100" value="${twinOptions.blast_damage_m}"></label>
   <label>Blast hour<input type="number" data-option="blast_hour" min="0" step="1" value="${twinOptions.blast_hour}"></label>
   <label>Dark sink (K, assumed)<input type="number" data-option="dark_sink_k" min="20" max="350" value="${twinOptions.dark_sink_k}"></label>
   <label>Sunlit sink (K, assumed)<input type="number" data-option="lit_sink_k" min="20" max="400" value="${twinOptions.lit_sink_k}"></label>
   <label>Dust deposition / day<input type="number" data-option="dust_rate_per_day" min="0" max="1" step=".01" value="${twinOptions.dust_rate_per_day}"></label>
   <label>Dust failure hazard / h (assumed)<input type="number" data-option="dust_hazard_per_h" min="0" max=".1" step=".0001" value="${twinOptions.dust_hazard_per_h}"></label>
   <label>Dust random seed<input type="number" data-option="dust_seed" min="0" max="9999" value="${twinOptions.dust_seed}"></label>
   <label>Solar loss at full dust load<input type="number" data-option="dust_solar_loss" min="0" max="1" step=".05" value="${twinOptions.dust_solar_loss}"></label>
   <button id="run-twin" class="primary">Run the twin</button><button id="clear-kills" ${kills.length?"":"disabled"}>Clear ${kills.length} failure${kills.length===1?"":"s"}</button></div>
   <p class="note">Thermal sinks and dust coefficients are assumed bounds, without Diviner data or hardware calibration. Dust reduces solar output. Click a relay or gateway to schedule a failure.</p>`);
  if(!sim)return env.auto&&running.has("twin")?waiting("Stepping the network through time.")+controls:controls;
  const s=sim.summary;
  return `<div class="metrics">${metric("MINES LINKED TO A GATEWAY",n(s.gateway_availability,0)+"%","of the period",s.always_connected?"green":"amber")}${metric("LINKED TO EARTH",n(s.earth_availability,0)+"%","gateway must also see Earth")}
  ${metric("LONGEST LOCAL OUTAGE",n(s.longest_gateway_outage_h,0)+" h","mines cut off from every gateway",s.longest_gateway_outage_h?"amber":"green")}${metric("RELAYS FROZEN",String(s.frozen.length),s.frozen.length?"nodes "+s.frozen.join(", "):"none below −60 °C",s.frozen.length?"red":"green")}
  ${metric("LASER FEEDS · OPTICAL TRUNKS",`${s.beams} · ${s.optical_trunks}`,"active in this run")}</div>`+
  `<div class="split">${panel("Network state",`<canvas id="twin-map" class="ora-map" aria-label="Network state at the selected hour"></canvas><input id="hour" class="time" type="range" min="0" max="${sim.times.length-1}" value="${hour}" aria-label="Hour of the simulation"><p id="clock" class="caption"></p>`+
   key([[C.green,"relaying"],[C.violet,"hibernating"],["#6b6b75","off or dead"],[C.red,"frozen"],[C.cyan,"gateway seeing Earth"],[C.amber,"laser power feed",true]]))}
  ${panel("Nodes at this hour",`<div id="twin-table"></div>`)}</div>`+
  panel("Scenario status",assessmentHtml(sim.assessment))+hazardPanel()+panel("Service over time",`<canvas id="service" class="ora-chart" aria-label="Share of mining sites linked over time"></canvas>${key([[C.green,"linked to a gateway",true],[C.cyan,"linked to Earth",true]])}`)+controls;
 }
 function hazardPanel(){
  const swaps:J[]=sim.module_swaps||[], blast=sim.blast, dust:J[]=sim.dust_failures||[];
  if(!swaps.length&&!blast&&!dust.length)return "";
  const parts:string[]=[];
  if(blast)parts.push(`<p class="callout amber">Blast at node ${blast.center}: ${blast.destroyed.length} destroyed, ${blast.damaged.length} damaged${blast.destroyed.length?` (destroyed: ${blast.destroyed.join(", ")})`:""}.</p>`);
  if(dust.length)parts.push(`<p class="note">Dust failures: ${dust.map((d:J)=>`node ${d.node} at hour ${n(d.hour,0)}`).join(", ")}.</p>`);
  if(swaps.length)parts.push(table(["Node","Why","Add","Remove","Mass change","Power change"],swaps.map((x:J)=>[String(x.node),esc(x.reason),esc((x.add||[]).join(", ")||"—"),esc((x.remove||[]).join(", ")||"—"),n(x.mass_kg,1)+" kg",n(x.power_w,1)+" W"]))+`<p class="note">Module swaps the configurator proposes for the conditions in this run. Masses and powers are placeholders until sourced.</p>`);
  return panel("Hazards and module swaps",parts.join(""));
 }
 function paintTwin(){
  if(!sim||!q("#twin-map"))return;
  const t=hour, modeColor=(v:J)=>v.frozen_h!=null&&v.frozen_h<=t*sim.step_h?C.red:v.mode[t]===2?(v.role==="gateway"&&v.earth?.[t]?C.cyan:v.role==="mine"?C.amber:C.green):v.mode[t]===1?C.violet:"#6b6b75";
  const base=new Map<number,J>(net.nodes.map((v:J)=>[v.id,v]));
  const nodes=sim.nodes.map((v:J)=>({...v,x:base.get(v.id).x,y:base.get(v.id).y}));
  drawMap("twin-map",{nodes,color:modeColor,label:(v:J)=>`${v.id} ${v.role==="mine"?"":n(v.temp_c[t],0)+"°C"}`,
   edges:[...sim.edges.map((e:J)=>({a:e.a,b:e.b,color:e.up[t]==="1"?(e.kind==="optical"?"rgba(132,222,255,.95)":"rgba(115,216,174,.9)"):"rgba(242,132,140,.55)",dash:false})),
    ...sim.beams.map((b:J)=>({a:b.source,b:b.target,color:C.amber,dash:true}))],
   pick:(v:J)=>{if(v.role==="mine")return;kills=[...kills.filter(k=>k.node!==v.id),{node:v.id,hour:t*sim.step_h}];env.status(`Node ${v.id} will fail at hour ${t*sim.step_h}. Run the twin to apply.`);render();}});
  q("#clock")!.textContent=`${sim.times[t].slice(0,16).replace("T"," ")} UTC · hour ${t*sim.step_h} · ${pct(sim.served_gateway[t])} of mines linked to a gateway, ${pct(sim.served_earth[t])} to Earth`;
  q("#twin-table")!.innerHTML=table(["Node","Role","State","Sun","Battery","Temp"],sim.nodes.map((v:J)=>[String(v.id),v.role,v.killed_h!=null&&v.killed_h<=t*sim.step_h?"killed":v.frozen_h!=null&&v.frozen_h<=t*sim.step_h?"frozen":["off","hibernating","relaying"][v.mode[t]],pct(v.lit[t]),v.role==="mine"?"own power":pct(v.soc[t]),v.role==="mine"?"—":n(v.temp_c[t],0)+" °C"]));
  const x=sim.times.map((_:string,i:number)=>i*sim.step_h);
  drawChart("service",[{label:"gateway",color:C.green,x,y:sim.served_gateway},{label:"earth",color:C.cyan,x,y:sim.served_earth,dash:true}],"hours","share of mines linked",1,t*sim.step_h);
 }
 function nightView(){
  if(!conf)return waiting("Loading the relay lab.");
  const head=panel("Survival energy",`<p class="note">A lunar night is about ${conf.lunar_night_h} hours of darkness. Energy needed is power times hours, so a relay's survival power matters more than its peak performance. Numbers are illustrative: calibrate the thermal constants against a real box before quoting them.</p>`+designFields()+`<p><button id="run-night" class="primary">Size the battery for the night</button></p>`);
  if(!nightResult)return env.auto&&running.has("night")?waiting("Forcing a lunar night and sizing batteries.")+head:head;
  const b=nightResult.budget,hb=nightResult.hibernating;
  const row=(label:string,x:J)=>[label,n(x.electronics_w)+" W",n(x.heater_w)+" W",n(x.draw_w)+" W",n(x.energy_wh,0)+" Wh",n(x.endurance_h,0)+" h",pill(x.survives,"survives","runs out")];
  return `<div class="metrics">${metric("ENERGY FOR ONE NIGHT",n(b.energy_wh,0)+" Wh",`at ${n(b.draw_w)} W for ${n(b.hours,0)} h`,b.survives?"green":"amber")}${metric("BATTERY",n(b.battery_wh,0)+" Wh",b.margin_wh>=0?`${n(b.margin_wh,0)} Wh spare`:`${n(-b.margin_wh,0)} Wh short`,b.survives?"green":"red")}
  ${metric("HEAT TO STAY ABOVE −30 °C",n(b.heat_needed_w,2)+" W","through insulation, to the configured dark sink")}${metric("WITH 10% HIBERNATION",n(hb.energy_wh,0)+" Wh",hb.survives?"fits this battery":"still short",hb.survives?"green":"amber")}</div>`+
  panel("One node through the night",table(["Mode","Electronics","Heater","Total draw","Energy","Battery lasts","Outcome"],[row("Always relaying",b),row("Hibernating, awake 10%",hb)]))+
  panel("Smallest battery that keeps every mine linked all night",table(["Wake share","Smallest battery","Added wait for a sleeping relay","Outcome"],nightResult.design.map((r:J)=>[r.duty>=1?"Always on":pct(r.duty),r.battery_wh?r.battery_wh.toLocaleString()+" Wh":"none up to 5,000 Wh",r.wake_latency_min==null?"never wakes":n(r.wake_latency_min,0)+" min",pill(r.feasible,"mines stay linked","network drops")]))+
   `<p class="note">Each row forces a full night on the chosen network in the twin and scans battery sizes. Hibernation trades battery mass for delay: a sleeping relay only passes traffic in its wake window, so the network becomes time-varying. Relays in permanent shadow never recharge and need laser power or a different site.</p>`)+head;
 }
 function tunnelView(data:J,earth:boolean){
  const controls=earth?`<div class="ora-grid"><label>Entries (rows)<input id="rows" type="number" min="2" max="6" value="4"></label><label>Cross-cuts (columns)<input id="cols" type="number" min="2" max="8" value="6"></label><button id="run-earth" class="primary">Dig the panel</button></div>`
   :`<div class="ora-grid"><label>Layout seed<input id="seed" type="number" min="0" max="9999" value="0"></label><label>Branches<input id="branches" type="number" min="1" max="8" value="4"></label><label class="check"><input id="loops" type="checkbox" checked> Cross-cuts (loops)</label>
    <label>Tunnel loss (dB per 100 m)<input id="alpha" type="number" min="0" max="60" step="0.5" value="4"></label><label>Bend loss (dB per 90°)<input id="bend" type="number" min="0" max="60" step="1" value="14"></label><button id="run-tunnel" class="primary">Dig the mine</button></div>`;
  const intro=earth?`<p class="note">A room-and-pillar panel is the common layout of Earth coal and salt mines. Radio does not pass through rock and GPS does not work underground, so safety depends on links that survive a roof fall. The engine is the same one used for the lunar mine.</p>`
   :`<p class="note">A robot follows the dig front and drops a repeater when its link back to the surface weakens. Radio follows the tunnels: loss grows with distance and with every bend. The loss numbers are placeholders to be fitted from the bench test in <code>hardware/</code>.</p>`;
  if(!data)return env.auto&&running.has(earth?"earth":"tunnel")?waiting(earth?"Digging the room-and-pillar panel.":"Digging the mine under three policies."):panel(earth?"Earth mine case study":"Dig twin",intro+controls);
  const names:Record<string,string>={threshold:"Drop when the link weakens",fixed:"Drop every 70 m",resilient:"Resilience-aware (two links back, every junction)"};
  const rows=Object.entries(data.policies as Record<string,J>).map(([k,r])=>[names[k],String(r.repeaters),pct(r.connected_fraction),pct(r.coverage),pct(r.repeater_failure.mean)+" / "+pct(r.repeater_failure.worst),pct(r.collapse.mean)+" / "+pct(r.collapse.worst)]);
  const r=data.policies[minePolicy];
  return panel(earth?"Room-and-pillar panel":"Mine plan",`<div class="tabs" role="group" aria-label="Repeater policy">${Object.keys(data.policies).map(k=>`<button data-policy="${k}" aria-pressed="${k===minePolicy}">${names[k]}</button>`).join("")}</div>
   <canvas id="${earth?"earth-mine":"mine"}" class="ora-mine" aria-label="Plan view of the mine with repeaters"></canvas>${key([[C.cyan,"portal gateway"],[C.green,"repeater"],[C.amber,"repeater whose loss hurts most"],[C.red,"collapse that hurts most",true]])}
   <p class="caption">${n(data.length_m,0)} m of tunnel · ${data.loops} loop${data.loops===1?"":"s"} · ${r.repeaters} repeaters · robot linked ${pct(r.connected_fraction)} of the dig.</p>`)+
  panel("Policies compared",table(["Policy","Repeaters","Robot linked during dig","Mine covered","After one repeater fails (mean / worst)","After one collapse (mean / worst)"],rows)+
   `<p class="note">After the dig, every repeater is failed in turn and every tunnel segment is collapsed in turn. The share is the part of the mine that can still reach the portal gateway. A collapse on the only way in cuts off everything behind it under any policy; only a second way out (a loop) helps.</p>`)+panel(earth?"Earth mine case study":"Dig twin",intro+controls);
 }
 function laserView(){
  const form2=`<div class="ora-grid"><label>Distance (m)<input id="b-dist" type="number" min="10" max="100000" step="100" value="5000"></label><label>Wavelength (nm)<input id="b-wave" type="number" min="300" max="11000" step="1" value="1064"></label>
   <label>Transmit aperture (m)<input id="b-tx" type="number" min="0.01" max="5" step="0.01" value="0.1"></label><label>Receiver diameter (m)<input id="b-rx" type="number" min="0.01" max="20" step="0.01" value="0.5"></label>
   <label>Electrical power in (W)<input id="b-pow" type="number" min="1" max="1000000" step="50" value="500"></label><label>Dust loss on optics (dB)<input id="b-dust" type="number" min="0" max="80" step="5" value="0"></label><button id="run-beam" class="primary">Work out the budget</button></div>`;
  if(!beam)return env.auto&&running.has("beam")?waiting("Working out the beam budget."):panel("Laser link and power budget",form2);
  const b=beam.budget,l=beam.links;
  return `<div class="metrics">${metric("POWER DELIVERED",n(b.delivered_w)+" W",`${pct(b.end_to_end_efficiency)} end to end`,"green")}${metric("SPOT RADIUS",n(b.spot_radius_m,3)+" m",`divergence ${(b.divergence_rad*1e6).toFixed(1)} µrad`)}
  ${metric("BEAM CAUGHT",pct(b.capture_fraction),"by the receiver",b.capture_fraction>=1?"green":"amber")}${metric("OPTICAL LINK MARGIN",n(l.optical_margin_db)+" dB",`${l.optical_rate_mbps} Mbps · needs ${l.optical_required_db} dB`,l.optical_margin_db>=l.optical_required_db?"green":"red")}${metric("RF LINK MARGIN",n(l.rf_margin_db)+" dB",`${l.rf_rate_kbps} kbps at 2.4 GHz`)}</div>`+
  panel("Power delivered against distance",`<canvas id="beam-chart" class="ora-chart" aria-label="Delivered power against distance"></canvas><p class="note">The spot grows with distance (1.22 λ/D). Once it is wider than the receiver, power falls with the square of distance. Efficiencies assumed: laser 45%, optics 90%, pointing 90%, tuned photovoltaic receiver 45%. These are first-order estimates, not a link design.</p>`)+
  panel("Two kinds of link",table(["Link","Strength","Weakness","Use"],[["RF (UHF or S-band)","Tolerates pointing error, mature","Lower data rate","Default mesh and backup"],["Optical","Very high rate, narrow beam","Needs precise pointing and strict line of sight; dust","Rim to crater-floor trunks"]])+
   `<p class="note">Turn on optical trunks and dust loss in the digital twin to test whether a trunk becomes a single point of failure without an RF backup.</p>`)+panel("Laser link and power budget",form2);
 }
 function accessView(){
  if(!dteData||!topo)return panel("Earth access",`<p class="note">Loading the year-long Earth visibility map and the layout table…</p>`);
  const flat=(a:(number|null)[][])=>a.flat().filter((v):v is number=>v!=null), mean=(v:number[])=>v.reduce((x,y)=>x+y,0)/Math.max(v.length,1);
  const tv=flat(dteData.terrain), never=tv.filter(v=>v===0).length/Math.max(tv.length,1);
  const rows=(topo as J[]).map(t=>[esc(t.topology),String(t.nodes),String(t.links),String(t.articulation_points),String(t.bridges),pct(t.reliability["0.05"]),pct(t.reliability["0.1"]),pct(t.reliability["0.2"])]);
  return `<div class="metrics">${metric("EARTH VISIBLE, WITH TERRAIN",pct(mean(tv)),"mean share of the year, south of 85°S")}${metric("EARTH VISIBLE, SMOOTH MOON",pct(mean(flat(dteData.flat))),"the simple model overstates it","amber")}${metric("NEVER SEE EARTH",pct(never),"of cells all year",never>0.2?"amber":"green")}</div>`+
   panel("Share of the year Earth is above the horizon",`<canvas id="dte-map" class="ora-map" aria-label="Map of the share of the year each cell sees Earth"></canvas><p class="caption">${dteData.cell_km} km cells, ${dteData.days} days, real terrain horizons. Dark is never, bright is always.</p>`)+
   panel("Chain, ring or ladder?",table(["Layout","Nodes","Links","Single points of failure","Single-path links","End to end at 5% link loss","at 10%","at 20%"],rows)+`<p class="note">Share of trials in which the two ends still reach each other when every link fails independently with the stated chance. Extra links cost mass, so this is the price of redundancy.</p>`);
 }
 function paintAccess(){
  if(!dteData)return;
  const c=canvas("dte-map");if(!c)return;
  const {ctx,w,h}=c, g:(number|null)[][]=dteData.terrain, rows=g.length, cols=g[0].length, side=Math.min(w,h), cell=side/Math.max(rows,cols), x0=(w-side)/2, y0=(h-side)/2;
  g.forEach((row,y)=>row.forEach((v,x)=>{if(v==null)return;const t=Math.max(0,Math.min(1,v));ctx.fillStyle=`rgb(${Math.round(20+110*t)},${Math.round(45+170*t)},${Math.round(80+175*t)})`;ctx.fillRect(x0+x*cell,y0+y*cell,cell+.5,cell+.5);}));
  ctx.fillStyle=C.text;ctx.font="11px monospace";ctx.fillText("pole at centre · 85°S at the edge",x0+8,y0+side-8);
 }
 function accuracyPanel(){
  if(!accuracy)return panel("Accuracy against independent truth",`<p class="note">No saved accuracy report. Run <code>python -m ora accuracy</code>.</p>`);
  const h=accuracy.coverage.hold, l=accuracy.coverage.legacy, app="computed sweep used by the app (120 rays, 20 m steps)", fast="fast view estimator (24-ray sweep + learned correction)";
  const old=(o:J)=>Object.entries(o).find(([k])=>k.startsWith("old coverage surrogate"))?.[1] as J;
  const sel=(split:string)=>Object.values(accuracy.selection[split] as Record<string,J>).map((b:J)=>b.new_learned_selector?.mean_pct_of_exact).filter((v:J)=>v!=null);
  const range=(v:number[])=>v.length?`${n(Math.min(...v),1)}–${n(Math.max(...v),1)}%`:"—";
  const rk=(split:string)=>accuracy.ranking[split];
  const rows=[["How much ground a relay sees (app)",`R² ${n(h[app].r2_pct,1)}% · ${n(l[app].r2_pct,1)}%`,`R² ${n(old(h)?.r2_pct,1)}% · ${n(old(l)?.r2_pct,1)}% (old surrogate)`],
   ["Fast learned view estimator",`R² ${n(h[fast].r2_pct,1)}% · ${n(l[fast].r2_pct,1)}%`,"—"],
   ["Ranking relay candidates (app)",`${n(rk("hold")["app now: computed viewshed share"],1)}% · ${n(rk("legacy")["app now: computed viewshed share"],1)}%`,`${n(rk("hold")["app before: surrogate 80% + Earth prior 20%"],1)}% · ${n(rk("legacy")["app before: surrogate 80% + Earth prior 20%"],1)}% (rank correlation)`],
   ["Coverage-first relay selection (app)","exact greedy","about 55% of exact on Shackleton (old shortlist)"],
   ["Learned selector without line of sight",`${range(sel("hold"))} · ${range(sel("legacy"))} of exact greedy`,"74.5–90.3% (old gain model)"],
   ["Earth tower siting model",`AUC ${n(accuracy.earth_siting_prior.auc_pct,1)}%`,"no longer used to rank lunar relays"]];
  return panel("Accuracy against independent truth",table(["What","Now (holdout · legacy test)","Before"],rows)+
   `<p class="note">Truth for coverage is dense line of sight to ${accuracy.protocol.truth_rays.toLocaleString()} random ground points per site. Methods were chosen and trained on ${accuracy.protocol.dev_windows} development windows only; the ${accuracy.protocol.hold_windows} holdout windows (buffered from development) and ${accuracy.protocol.legacy_windows} legacy test windows were scored once. See <code>docs/ACCURACY.md</code>.</p>`);
 }
 function dataView(){
  if(!conf)return waiting("Loading the relay lab.");
  const card=conf.model_card, models:J[]=conf.models||[], missing=models.filter(m=>!m.loaded);
  const acc=accuracyPanel();
  const active=panel("Loaded in this server now",(missing.length?`<p class="callout amber">${missing.length} of ${models.length} trained models are not loaded, so results use the fallback listed below.</p>`:`<p class="callout">All ${models.length} trained models loaded from the files listed below.</p>`)+modelsHtml(conf.models)+
   `<p class="note">This table is read from the running server. The figures further down are the training record (model card${card?.trained?`, trained ${esc(card.trained)}`:""}); they describe how the models were evaluated when trained, not that they are active. Run <code>python -m ora preflight</code> for the full inventory.</p>`);
  if(!card)return acc+active+panel("Training record",`<p class="callout amber">No model card found. Run <code>python -m ora.data --earth</code> then <code>python -m ora.train</code>. Until then siting uses a transparent terrain heuristic.</p>`);
  const p=card.siting_prior,s=card.coverage_surrogate,g=card.marginal_gain;
  const checks=stress?Object.entries(stress.checks as Record<string,J>):[];
  return acc+active+`<h2 class="section-title">Training record (historical, from the model card)</h2><div class="metrics">${metric("SITING PRIOR · AUC",n(p.mean_auc,2),"held-out Earth regions (0.5 is chance)","green")}${metric("TOWERS FOUND IN TOP 10%",pct(p.mean_recall_top10pct),"chance would find 10%")}${metric("COVERAGE SURROGATE · RANK",n(s.mean_spearman,2),"Spearman, held-out lunar blocks")}${metric("COVERAGE SURROGATE · R²",n(s.mean_r2,2),`${s.windows} lunar windows, spatial folds`,s.mean_r2>=0.6?"green":"amber")}</div>`+
  panel("Siting prior: where towers stand on Earth",table(["Held-out region","Towers","AUC","Towers in top 10%","View-only baseline AUC"],p.folds.map((f:J)=>[f.held_out,String(f.towers),n(f.auc,3),pct(f.recall_top10pct),n(f.viewshed_only_auc,3)]))+
   `<p class="note">Labels: ${esc(p.labels)}. Terrain: ${esc(p.terrain)}. Validation: ${esc(p.cross_validation)}, so the score is always tested on a region it never saw.</p><ul class="ora-list">${p.limits.map((x:string)=>`<li>${esc(x)}</li>`).join("")}</ul>`)+
  panel("Coverage surrogate: predicting a site's view on the Moon",table(["Fold","Cells","R²","Rank correlation","Mean error"],s.folds.map((f:J)=>[String(f.fold+1),f.cells.toLocaleString(),n(f.r2,2),n(f.spearman,2),n(f.mean_abs_error,3)]))+
   `<p class="note">${esc(s.task)}. Cross-validation: ${esc(s.cross_validation)}. It screens candidates; the app still computes exact line of sight for the final set.${g?` A second model (${esc(g.task)}, ${g.training_rows.toLocaleString()} examples) picks sites without a line-of-sight matrix.`:""}</p>`)+
  (checks.length?panel(`Stress tests (saved report): ${stress.summary.passed} of ${stress.summary.total} pass`,`<p class="callout ${stress.provenance?"":"amber"}">${stress.provenance?`Generated ${esc(stress.provenance.generated)} at commit ${esc(String(stress.provenance.code?.commit??"?").slice(0,10))}; ${Object.entries(stress.provenance.models||{}).every(([k,v])=>(conf.models||[]).find((m:J)=>m.name===k)?.sha256===v)?"model hashes match the loaded models":"model hashes differ from the loaded models, so these results describe other files"}.`:"Saved before reports recorded a date or model hashes, so it cannot be tied to the loaded models. Rerun python -m ora stress."}</p>`+table(["Check","Value","Criterion","Result"],checks.map(([k,v])=>[esc(k.replaceAll("_"," ")),esc(typeof v.value==="number"?n(v.value,3):v.value),esc(v.criterion),pill(!!v.passed,"pass","fail")]))+`<p class="note">Run on sealed lunar windows and Earth tiles that training never read (<code>python -m ora stress</code>). Criteria were written before the results were seen.</p>`):"")+
  panel("What this tool does not claim",`<ul class="ora-list"><li>No placement is optimal. The search is heuristic over screened candidates.</li><li>The twin runs on simulated telemetry, bounded by 20 m terrain.</li><li>Tunnel radio numbers are placeholders until fitted from a bench test; a bench test validates the method, not lunar values.</li><li>Laser power is evaluated as a technology. It is not flight heritage.</li><li>"Not found in our search" is not "does not exist" for prior work.</li></ul>`);
 }

 function render(){
  const [chapter,title,purpose]=ORA_TABS[tab];
  env.header?.(chapter,title,purpose);
  const areaTab=AREA_TABS.includes(tab);
  env.scenarioFormHidden?.(!areaTab);
  if(!net&&areaTab){
   // Standalone: keep whatever placeholder the page shows. Embedded: say it is on its way.
   if(env.auto)view.innerHTML=running.has("network")?waiting("Placing relays on this area."):panel("Relay lab",`<p class="note">Calculate an area on the Moon to fill in this analysis.</p>`);
   return;
  }
  view.innerHTML=tab==="network"?networkView():tab==="resilience"?resilienceView():tab==="twin"?twinView():tab==="night"?nightView():tab==="tunnel"?tunnelView(mine,false):tab==="earth"?tunnelView(earthMine,true):tab==="laser"?laserView():tab==="access"?accessView():dataView();
  connect();paint();
 }
 function paint(){
  if(tab==="access")paintAccess();
  if(tab==="network"&&net)paintNetwork();
  if(tab==="resilience")paintResilience();
  if(tab==="twin")paintTwin();
  if(tab==="tunnel"&&mine)drawMine("mine",mine,minePolicy);
  if(tab==="earth"&&earthMine)drawMine("earth-mine",earthMine,minePolicy);
  if(tab==="laser"&&beam)drawChart("beam-chart",[{label:"delivered",color:C.amber,x:beam.curve.map((p:J)=>p.distance_m/1000),y:beam.curve.map((p:J)=>p.delivered_w)}],"distance (km)","delivered power (W)",Math.max(...beam.curve.map((p:J)=>p.delivered_w),1));
 }
 /** Redraw only if the given tab is the one on screen. */
 const refresh=(which:OraTab[])=>{if(visible()&&which.includes(tab))render();};
 const value=(id:string)=>Number(q<HTMLInputElement>("#"+id)!.value);

 // ---------- runs (buttons in standalone; automatic with defaults when embedded) ----------
 const runResilience=(seeds=100,disk=1500)=>once("resilience",async()=>{const g=generation;const out=await task("Failing nodes and links on four networks…",()=>api("resilience",{...env.scenarioBody(),seeds,disk_radius_m:disk}));if(out&&g===generation){lab=out;refresh(["resilience"]);}});
 const runTwin=()=>once("twin",async()=>{await ensureConfig();if(!conf)return;const g=generation;const out=await task("Stepping the network through time…",()=>api("twin",twinBody()));if(out&&g===generation){sim=out;hour=Math.min(hour,sim.times.length-1);refresh(["twin"]);}});
 const runNight=()=>once("night",async()=>{await ensureConfig();if(!conf)return;const g=generation;const out=await task("Forcing a lunar night and sizing batteries…",()=>api("night",{...twinBody(),night_start_h:null}));if(out&&g===generation){nightResult=out;refresh(["night"]);}});
 const runTunnel=(body:Record<string,unknown>={layout:"drift",seed:0,branches:4,loops:true,alpha_db_per_100m:4,bend_db_per_90deg:14})=>once("tunnel",async()=>{const out=await task("Digging the mine under three policies…",()=>api("tunnel",body));if(out){mine=out;refresh(["tunnel"]);}});
 const runEarth=(rows=4,cols=6)=>once("earth",async()=>{const out=await task("Digging the room-and-pillar panel…",()=>api("tunnel",{layout:"room",rows,cols}));if(out){earthMine=out;refresh(["earth"]);}});
 const runBeam=(body:Record<string,unknown>={distance_m:5000,wavelength_nm:1064,tx_aperture_m:0.1,rx_diameter_m:0.5,electrical_in_w:500,dust_db:0})=>once("beam",async()=>{const out=await task("Working out the beam budget…",()=>api("beam",body));if(out){beam=out;refresh(["laser"]);}});
 const loadAccess=()=>once("access",async()=>{
  if(!dteData)dteData=await task("Reading the Earth visibility map…",()=>api("dte"));
  if(!topo)topo=await task("Comparing layouts…",()=>api("topologies"));
  refresh(["access"]);
 });
 const loadStress=()=>once("data",async()=>{await ensureConfig();const saved=await task("Loading saved results…",()=>Promise.all([api("stress"),api("accuracy")]));stress=saved?.[0]??null;accuracy=saved?.[1]??null;refresh(["data"]);});

 function connect(){
  const on=(id:string,action:()=>void)=>{const node=q<HTMLButtonElement>("#"+id);if(node)node.onclick=action;};
  const select=q<HTMLSelectElement>("#layer");if(select){select.value=layer;select.onchange=()=>{layer=select.value;paint();};}
  view.querySelectorAll<HTMLButtonElement>("[data-mode]").forEach(b=>b.onclick=()=>{mode=b.dataset.mode!;render();});
  view.querySelectorAll<HTMLButtonElement>("[data-policy]").forEach(b=>b.onclick=()=>{minePolicy=b.dataset.policy!;render();});
  view.querySelectorAll<HTMLInputElement>("[data-design]").forEach(i=>i.onchange=()=>{design()[i.dataset.design!]=i.type==="checkbox"?i.checked:Number(i.value);});
  view.querySelectorAll<HTMLInputElement>("[data-option]").forEach(i=>i.onchange=()=>{twinOptions[i.dataset.option!]=i.type==="checkbox"?i.checked:Number(i.value);});
  on("run-lab",()=>{lab=undefined;void runResilience(value("seeds"),value("disk"));render();});
  on("run-twin",()=>{void runTwin();});
  on("clear-kills",()=>{kills=[];render();});
  on("run-night",()=>{void runNight();});
  on("run-tunnel",()=>{void runTunnel({layout:"drift",seed:value("seed"),branches:value("branches"),loops:q<HTMLInputElement>("#loops")!.checked,alpha_db_per_100m:value("alpha"),bend_db_per_90deg:value("bend")});});
  on("run-earth",()=>{void runEarth(value("rows"),value("cols"));});
  on("run-cover",async()=>{const out=await task("Shortlisting and choosing coverage sites…",()=>api("coverage",{...env.scenarioBody(),budget:value("cover-budget")}));if(out){cover=out;refresh(["network"]);}});
  on("run-beam",()=>{void runBeam({distance_m:value("b-dist"),wavelength_nm:value("b-wave"),tx_aperture_m:value("b-tx"),rx_diameter_m:value("b-rx"),electrical_in_w:value("b-pow"),dust_db:value("b-dust")});});
  const slider=q<HTMLInputElement>("#hour");if(slider)slider.oninput=()=>{hour=Number(slider.value);paintTwin();};
 }

 /** Place relays for the current scenario or area. Clears every analysis that depends on it. */
 async function build(){
  if(busy&&running.has("network"))return running.get("network");
  if(env.canBuild&&!env.canBuild())return;
  const g=++generation;
  net=lab=sim=nightResult=cover=undefined;kills=[];hour=0;
  env.building?.(true);
  if(AREA_TABS.includes(tab)&&visible())render();
  try{
   return await once("network",async()=>{
    const out=await task("Tracing line of sight and choosing relays…",()=>api("network",env.scenarioBody()));
    if(g!==generation)return;
    if(!out){if(visible()&&AREA_TABS.includes(tab)&&env.auto)view.innerHTML=panel("Relay lab unavailable",`<p class="callout amber">The relay network could not be built for this area. The error is in the status line.</p>`);return;}
    net=out;
    env.subtitle?.(`${net.site} · ${net.scenario.start} · ${net.scenario.days} days at ${net.scenario.step_h} h steps`);
    refresh(AREA_TABS);
   });
  }finally{env.building?.(false);}
 }

 return {
  tabs: ORA_TABS,
  get tab(){return tab;},
  get hasNetwork(){return !!net;},
  get busy(){return busy>0;},
  render,
  repaint: paint,
  build,
  /** Show a tab; when embedded, start its analysis with defaults if it has not run yet. */
  open(next: OraTab){
   tab=next;
   if(next==="access"&&(!dteData||!topo))void loadAccess();
   if(next==="data"&&stress===undefined)void loadStress();
   if(env.auto){
    void ensureConfig().then(()=>refresh([next]));
    if(net&&next==="resilience"&&!lab)void runResilience();
    if(net&&next==="twin"&&!sim)void runTwin();
    if(net&&next==="night"&&!nightResult)void runNight();
    if(next==="tunnel"&&!mine)void runTunnel();
    if(next==="earth"&&!earthMine)void runEarth();
    if(next==="laser"&&!beam)void runBeam();
   }
   render();
  },
  /** Embedded: place relays on a new area, then run the area analyses one after another in the background. */
  async analyseArea(){
   await ensureConfig();
   await build();
   if(!net)return;
   await runResilience();
   await runTwin();
   await runNight();
  },
  /** Load the lab's own configuration (scenario list and node design). */
  ensureConfig,
  get config(){return conf;},
 };
}
export type OraLab = ReturnType<typeof createOraLab>;
