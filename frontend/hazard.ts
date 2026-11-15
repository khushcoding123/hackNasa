// Hazard response view: a data-driven (Diviner-trained) estimate of how cold the terrain around each relay is,
// shown NEXT TO the physics results. Research / experimental; it never changes the mission calculation.
import type {Result, Candidate} from "./types.js";

interface Interval {value:number; low:number; high:number; percentile:number}
export interface HazardPoint {
 id:string; lat:number; lon:number; in_domain:boolean; reason?:string; inputs?:Record<string,number>;
 temp_avg_k?:Interval; temp_max_k?:Interval; cold_trap_probability?:number;
}
interface Evaluation {split:string; temp_avg_k:Record<string,number>; temp_max_k:Record<string,number>; cold_trap:Record<string,number>;
 beats_baselines:{temp_avg_k:boolean; temp_max_k:boolean; cold_trap:boolean}}
export interface HazardResponse {
 label:string; points:HazardPoint[]; model:{sha256?:string|null; trained?:string|null; evaluation:Partial<Evaluation>; limits?:string[]};
 agreement_with_physics:{spearman_sunlight_vs_predicted_max_temperature:number|null; n:number; note:string};
}

const esc=(s:unknown)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]!));
const f=(x:number|null|undefined,d=1)=>x==null||!Number.isFinite(x)?"—":x.toFixed(d);
const cache=new WeakMap<Result,Promise<HazardResponse>>();

async function request(r:Result):Promise<HazardResponse>{
 const points=r.candidates.map(c=>({id:c.id,lat:c.lat,lon:c.lon,sunlight_pct:c.solar.availability}));
 const response=await fetch("/api/hazard/thermal",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({points})});
 const data=await response.json().catch(()=>({detail:`Server error ${response.status}.`}));
 if(!response.ok)throw new Error(typeof data.detail==="string"?data.detail:"The thermal hazard model could not score these relays.");
 return data as HazardResponse;
}


function relayTable(r:Result,h:HazardResponse){
 const byId=new Map(h.points.map(p=>[p.id,p]));
 const failure=new Map(r.failures.map(x=>[x.id,x]));
 const rows=r.candidates.map((c:Candidate)=>{
  const p=byId.get(c.id), env=c.environment?.cold_dust, selected=r.recommended.includes(c.id), fail=failure.get(c.id);
  const model=p?.in_domain&&p.temp_max_k&&p.temp_avg_k
   ?`<td>${f(p.temp_avg_k.value,0)} K <small class="muted">(${f(p.temp_avg_k.low,0)}–${f(p.temp_avg_k.high,0)})</small></td><td>${f(p.temp_max_k.value,0)} K <small class="muted">(${f(p.temp_max_k.low,0)}–${f(p.temp_max_k.high,0)})</small></td><td>${f(p.temp_max_k.percentile,0)}th percentile</td><td>${f(100*(p.cold_trap_probability??0),2)}%</td>`
   :`<td colspan="4" class="muted">${esc(p?.reason??"not scored")}</td>`;
  return `<tr><th scope="row">${esc(c.id)}${selected?' <span class="green" title="Selected in the recommended plan">●</span>':""}</th>`+
   `<td>${f(c.solar.availability,0)}%</td><td>${env?f(env.minimum_temperature_c,0)+" °C":"—"}</td><td>${fail?f(fail.availability_lost,1)+" pp":"—"}</td>`+model+`</tr>`;
 });
 return `<div class="table-wrap" role="region" aria-label="Scrollable hazard table" tabindex="0"><table><thead>
  <tr><th rowspan="2" scope="col">Relay</th><th colspan="3" scope="colgroup">Physics (computed for this mission)</th><th colspan="4" scope="colgroup">Diviner-trained model (experimental)</th></tr>
  <tr><th scope="col">Sunlight</th><th scope="col">Min node temp (cold + dust case)</th><th scope="col">Coverage lost if it fails</th><th scope="col">Annual mean (80% range)</th><th scope="col">Annual maximum (80% range)</th><th scope="col">Maximum-temperature rank among 80–90° S facets (0 = coldest)</th><th scope="col">Cold-trap probability</th></tr>
  </thead><tbody>${rows.join("")}</tbody></table></div>`;
}

function evaluationHtml(h:HazardResponse){
 const e=h.model.evaluation;
 if(!e.temp_max_k||!e.temp_avg_k||!e.cold_trap)return `<p class="callout amber">The model card has no held-out evaluation for this artifact, so its accuracy is unknown.</p>`;
 const t=(k:Record<string,number>,key:string,d=2)=>f(k[key],d);
 const beats=(b?:boolean)=>b===true?'<span class="green">beats</span>':b===false?'<span class="red">does not beat</span>':'<span class="amber">unknown</span>';
 const b=e.beats_baselines;
 return `<p class="muted">Scored once on spatial blocks never used for fitting or tuning (${esc(e.split??"")}).</p><div class="table-wrap"><table><thead><tr><th scope="col">Output</th><th scope="col">Held-out error</th><th scope="col">R²</th><th scope="col">80% range contains truth</th><th scope="col">Versus simple baselines</th></tr></thead><tbody>
 <tr><th scope="row">Annual mean temperature</th><td>${t(e.temp_avg_k,"mae_k")} K MAE</td><td>${t(e.temp_avg_k,"r2",3)}</td><td>${f(100*(e.temp_avg_k.interval_coverage??NaN),0)}%</td><td>${beats(b?.temp_avg_k)} every baseline</td></tr>
 <tr><th scope="row">Annual maximum temperature</th><td>${t(e.temp_max_k,"mae_k")} K MAE</td><td>${t(e.temp_max_k,"r2",3)}</td><td>${f(100*(e.temp_max_k.interval_coverage??NaN),0)}%</td><td>${beats(b?.temp_max_k)} every baseline</td></tr>
 <tr><th scope="row">Cold-trap probability</th><td>ROC-AUC ${t(e.cold_trap,"roc_auc",3)}</td><td>—</td><td>—</td><td>${beats(b?.cold_trap)} the baselines (average precision ${t(e.cold_trap,"average_precision",3)} at ${f(100*(e.cold_trap.prevalence??NaN),2)}% prevalence)</td></tr>
 </tbody></table></div>`;
}

/** Render the view into ``outlet`` and fill it in when the model answers. */
export function hazardView(outlet:HTMLElement,r:Result,after:()=>void=()=>{}){
 outlet.innerHTML=`<section class="panel"><h2>Hazard response <span class="verdict amber">Research · experimental</span></h2>
  <p>How cold is the terrain around each candidate relay, according to a model trained on NASA LRO Diviner polar temperatures? This is shown <strong>next to</strong> the physics results and does not change them, the relay choice, or the requirements status.</p>
  <div id="hazard-body"><p class="muted">Scoring ${r.candidates.length} candidate relays…</p></div></section>`;
 if(!cache.has(r))cache.set(r,request(r));
 const promise=cache.get(r)!;
 promise.then(h=>{
  if(!document.body.contains(outlet.querySelector("#hazard-body")))return;
  const scored=h.points.filter(p=>p.in_domain).length, rho=h.agreement_with_physics.spearman_sunlight_vs_predicted_max_temperature;
  const selected=r.recommended.map(id=>h.points.find(p=>p.id===id)).filter((p):p is HazardPoint=>!!p&&p.in_domain&&!!p.temp_max_k);
  const coldest=selected.length?selected.reduce((a,b)=>a.temp_max_k!.value<b.temp_max_k!.value?a:b):undefined;
  outlet.querySelector("#hazard-body")!.innerHTML=
   `<div class="metrics"><section class="panel metric"><small>RELAYS SCORED</small><strong>${scored} / ${h.points.length}</strong><small>inside the 80–90° S training cap</small></section>`+
   `<section class="panel metric"><small>COLDEST SELECTED RELAY</small><strong>${coldest?esc(coldest.id):"—"}</strong><small>${coldest?`annual maximum ≈ ${f(coldest.temp_max_k!.value,0)} K`:"no selected relay scored"}</small></section>`+
   `<section class="panel metric"><small>AGREES WITH PHYSICS SUNLIGHT</small><strong>${rho==null?"—":"ρ = "+f(rho,2)}</strong><small>rank correlation, ${h.agreement_with_physics.n} relays</small></section></div>`+
   `<h3>Per relay</h3>${relayTable(r,h)}`+
   `<p class="caption">● selected in the recommended plan. Temperatures are annual statistics of a ~500 m terrain facet around the relay, not the relay's electronics temperature and not a forecast for the mission dates. The rank is relative to the whole 80–90° S training cap (so sites near the pole rank low), not a survival limit. ${esc(h.agreement_with_physics.note)}</p>`+
   `<h3>How good is it?</h3>${evaluationHtml(h)}`+
   `<details><summary>What this model is, and is not</summary><ul>${(h.model.limits??[]).map(l=>`<li>${esc(l)}</li>`).join("")}<li>It is not used to choose or rank relays, and it does not feed the requirements status.</li></ul><p class="caption">Model file SHA-256 <code>${esc(h.model.sha256??"unknown")}</code>${h.model.trained?`, trained at commit <code>${esc(String(h.model.trained).slice(0,10))}</code>`:""}.</p></details>`;
  after();
 }).catch((error:Error)=>{
  cache.delete(r);
  const target=outlet.querySelector("#hazard-body");
  if(target)target.innerHTML=`<p class="callout amber">${esc(error.message)} The physics results are unaffected.</p>`;
 });
}
