import type {Result, DiagnosticOutage, DiagnosticGate} from './types.js';

const esc=(v:unknown)=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const number=(v:number|null|undefined)=>v==null?'Unknown':v.toFixed(2);
const stamp=(v:string)=>esc(v.replace('T',' ').replace('+00:00',' UTC'));
const section=(title:string,body:string)=>`<section class="panel"><h2>${title}</h2>${body}</section>`;
const table=(headers:string[],rows:string[][])=>`<div class="table-wrap" role="region" aria-label="Scrollable evidence table" tabindex="0"><table><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(cell=>`<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
export const failureKey=(disabled:Set<string>)=>[...disabled].sort().join(',')||'nominal';
export function participatingPaths(r:Result){
 return Object.values(r.diagnostics?.paths??{}).filter(p=>p.relay===null||r.recommended.includes(p.relay));
}
export function candidateAvailable(r:Result,id:string,caseName:string,disabled:Set<string>,index:number){
 if(!r.diagnostics)return !disabled.has(id)&&!!r.candidates.find(c=>c.id===id)?.temporal_coverage[index];
 return Object.values(r.diagnostics.paths).some(p=>p.relay===id&&!p.nodes.some(n=>disabled.has(n))&&p.available[caseName][index]);
}
export function earthHorizonNote(r:Result,index:number){
 const s=r.samples,limit=s.earth_limit[index],clearance=Number(r.config.clearance)||0;
 if(limit===null)return 'terrain data incomplete';
 if(s.terrain[index]){
  const radio=r.config.earth_radio as {margin:number};
  const low=s.rf_margin[index]<radio.margin;
  return `clear by ${(s.earth_clearance[index]??0).toFixed(1)}°${low?' · RF margin too low':''}`;
 }
 if(!s.flat[index])return 'below the horizon';
 if(s.earth_el[index]>limit)return `inside the ${clearance}° clearance allowance`;
 const distance=s.obstruction_m[index];
 return `blocked by terrain${distance===null?'':` ${(distance/1000).toFixed(1)} km away (sampled ray)`}`;
}
export function scopedOutages(r:Result,disabled:Set<string>,scope:string,caseName:string):DiagnosticOutage[]{
 const state=r.failure_states[failureKey(disabled)];
 const c=state.cases?.[caseName];
 return scope==='network'?(c?.outages??state.outages??[]):c?.path_outages[scope]??[];
}
export function outageEvidence(r:Result,disabled:Set<string>,scope:string,caseName:string){
 if(!r.diagnostics)return section('Outage evidence unavailable','<p>Recalculate this older snapshot to obtain network diagnostics.</p>');
 const options=[['network','Active network'],...participatingPaths(r).map(p=>[p.id,p.id.replaceAll('->',' → ')])];
 const outages=scopedOutages(r,disabled,scope,caseName);
 return section(scope==='network'?'Active network outages':'Individual-path outages',
  `<div class="fields"><label>Outage scope<select id="outage-scope">${options.map(([id,label])=>`<option value="${esc(id)}" ${id===scope?'selected':''}>${esc(label)}</option>`).join('')}</select></label><label>Environmental case<select id="evidence-case">${r.diagnostics.cases.map(c=>`<option ${c===caseName?'selected':''}>${esc(c)}</option>`).join('')}</select></label></div><p class="caption">Simulated · ${r.diagnostics.sample_minutes} minute samples · ${esc(r.diagnostics.interval_convention)}. ${esc(r.diagnostics.timing)}</p>`+
  (outages.length?table(['Start UTC','End UTC (exclusive)','Duration','Blocking conditions / unknown evidence'],outages.map(o=>[
   `<button data-evidence-time="${o.first}">${stamp(o.start)}</button>`,`${stamp(o.end)}${o.end_boundary==='mission_end'?' · mission ends; recovery unobserved':' · sampled recovery boundary'}`,`${number(o.hours)} h`,
   `<details><summary>${o.causes.length} component conditions; ${o.segments.length} cause segments</summary>${o.segments.map(s=>`<p><button data-evidence-time="${s.first}">${stamp(s.start)}</button> to ${stamp(s.end)}</p><ul>${s.paths.map(p=>`<li>${esc(p.id.replaceAll('->',' → '))}<ul>${p.issues.map(i=>`<li>${esc(i.component)} · ${esc(i.code.replaceAll('_',' '))} · ${esc(i.status)}</li>`).join('')||'<li>Cause unknown</li>'}</ul></li>`).join('')}</ul>`).join('')}</details>`
  ])):'<p class="callout">No outages for this scope and failure state at the configured sampling resolution.</p>'));
}

/** Geographic evidence uses only returned coordinates; missing locations stay absent. */
export function evidenceFocus(r:Result,disabled:Set<string>,scope:string,caseName:string,index:number){
 const d=r.diagnostics;
 const nodes=new Map<string,'failed'|'unknown'>(),links=new Map<string,'failed'|'unknown'>();
 const markers:{id:string;lat:number;lon:number;map_x_m?:number|null;map_y_m?:number|null;status:'failed'|'unknown';approximate:boolean}[]=[];
 if(!d)return {nodes,links,markers};
 const status=(gates:DiagnosticGate[])=>gates.some(g=>g.ok[index]===false)?'failed':gates.some(g=>g.ok[index]==null)?'unknown':undefined;
 const paths=participatingPaths(r).filter(p=>scope==='network'||p.id===scope);
 for(const p of paths){
  for(const id of p.nodes){const state=disabled.has(id)?'failed':status(d.nodes[id]?.cases?.[caseName]?.gates??[]);if(state)nodes.set(id,state);}
  for(const id of p.links){
   const link=d.links[id],state=status(link.gates);if(!state)continue;links.set(id,state);
   const terrainIssue=link.gates.some(g=>['terrain_clearance','terrain_coverage','surface_los'].includes(g.code)&&g.ok[index]!==true);
   if(!terrainIssue)continue;
   const h=link.horizon;
   const rays=h?[h.rays[h.left[index]],...(h.weight[index]===0?[]:[h.rays[h.right[index]]])]:link.location?[link.location]:[];
   rays.forEach((ray,j)=>{if(ray.lat!=null&&ray.lon!=null)markers.push({id:`${id} terrain sample ${j+1}`,lat:ray.lat,lon:ray.lon,map_x_m:ray.map_x_m,map_y_m:ray.map_y_m,status:state,approximate:true});});
  }
 }
 return {nodes,links,markers};
}
export function marginalMetrics(m:NonNullable<Result['optimization_evidence']>['marginal'][number],caseName:string){
 return m.cases?.[caseName]??(caseName==='cold_dust'?m:undefined);
}

function gateRow(component:string,g:DiagnosticGate,index:number){
 const ok=g.ok[index];
 return [esc(component),esc(g.code.replaceAll('_',' ')),ok===null?'Unknown':ok?'Pass':'Failed',
  g.values?`${number(g.values[index])} ${esc(g.unit)}`:'—',g.threshold===null?'—':`${esc(g.comparison)} ${number(g.threshold)} ${esc(g.unit)}`];
}
export function sampleEvidence(r:Result,disabled:Set<string>,scope:string,caseName:string,index:number){
 const d=r.diagnostics;if(!d)return '';
 const paths=participatingPaths(r).filter(p=>scope==='network'||p.id===scope);
 const rows:string[][]=[],locations:string[]=[];
 const nodes=new Set(paths.flatMap(p=>p.nodes)),links=new Set(paths.flatMap(p=>p.links));
 for(const id of nodes){
  const node=d.nodes[id];
  if(disabled.has(id))rows.push([esc(id),'Injected failure','Failed','Disabled','—']);
  for(const g of node?.cases?.[caseName].gates??[])rows.push(gateRow(id,g,index));
  if(node?.lat!==undefined&&node.lon!==undefined)locations.push(`${esc(id)}: ${number(node.lat)}°, ${number(node.lon)}° E`);
  else if(id==='S1'&&r.satellite)locations.push(`S1: ${number(r.satellite.track.lat[index])}°, ${number(r.satellite.track.lon[index])}° E (sub-satellite point)`);
  else if(node?.todo)locations.push(`${esc(id)}: ${esc(node.todo)}`);
 }
 for(const id of links){
  const link=d.links[id];
  for(const g of link.gates)rows.push(gateRow(id,g,index));
  if(link.horizon){
   const h=link.horizon;
   const indices=[h.left[index],...(h.weight[index]===0?[]:[h.right[index]])];
   locations.push(`${esc(id)} sampled horizon rays (approximate): ${indices.map(k=>{const ray=h.rays[k];return ray.lat===null?esc(ray.todo):`${number(ray.azimuth_deg)}° azimuth, ${number(ray.distance_m)} m, ${number(ray.lat)}°, ${number(ray.lon)}° E`;}).join('; ')}. ${esc(h.basis)}`);
  }else if(link.location)locations.push(`${esc(id)}: ${number(link.location.lat)}°, ${number(link.location.lon)}° E. ${esc(link.location.basis)}`);
  else if(link.todo)locations.push(`${esc(id)}: ${esc(link.todo)}`);
 }
 const current=r.failure_states[failureKey(disabled)].cases?.[caseName];
 return `<p class="callout" role="status" aria-live="polite">${stamp(r.times[index])} · ${esc(caseName)} · active network ${current?.combined[index]?'available under assumptions':'unavailable'}</p>`+
  `<p>${paths.map(p=>{const up=!p.nodes.some(n=>disabled.has(n))&&p.available[caseName][index];return `${esc(p.id.replaceAll('->',' → '))}: ${up?'available under assumptions':'unavailable'}`;}).join('<br>')}</p>`+
  table(['Component / endpoints','Condition','State','Modeled value','Required threshold'],rows)+
  `<details><summary>Node and terrain-sample locations</summary><ul>${locations.map(s=>`<li>${s}</li>`).join('')}</ul></details><p class="caption">Missing RF remains unknown; geometry-only paths are upper bounds. Conditions are component evidence, not calibrated root-cause probabilities. Thermal freeze is irreversible; independent power state after freeze is unknown.</p>`;
}

export function relayEvidence(r:Result,preview:string,caseName="cold_dust"){
 const e=r.optimization_evidence;if(!e)return '';
 const ids=[e.selected,e.baseline,...e.counts.map(c=>c.best),...e.alternatives];
 const unique=[...new Set(ids)];
 return section('Why these relays?',`<p class="caption">Simulated results. Unknown satellite RF means geometry-only upper bounds. Costs cover surface relays; satellite service cost remains unknown.</p><p>${esc(e.scope)}</p><p>${esc(e.objective)}</p><p>${e.evaluated} configurations evaluated; ${e.feasible_count} feasible. ${e.tie_count} equal-score alternatives to the recommendation.</p><details><summary>Deterministic tie policy</summary><p>${esc(e.tie_policy)}</p></details><label>Environmental case<select id="evidence-case">${(r.diagnostics?.cases??['cold_dust']).map(c=>`<option ${c===caseName?'selected':''}>${esc(c)}</option>`).join('')}</select></label>`+
  `<label>Compare a configuration<select id="alternative-preview"><option value="">No alternative overlay</option>${Object.values(e.configurations).map(c=>`<option value="${esc(c.id)}" ${preview===c.id?'selected':''}>${esc(c.id)}${c.id===e.selected?' · recommendation':''}</option>`).join('')}</select></label><p class="caption">Violet preview is nominal for the displayed environmental case. Failure toggles apply to the recommendation. Preview does not replace the applied plan.</p>`+
  table(['Configuration','Relays','Coverage / total / longest blackout','Worst loss coverage / longest blackout','Installed cost USD','All-case limits / explanation'],unique.map(id=>{
   const c=e.configurations[id],current=c.cases[caseName];return [esc(id)+(id===e.selected?' · recommended':''),String(c.relay_ids.length),`${number(current.network.availability)}% / ${number(current.network.total)} h / ${number(current.network.longest)} h`,`${number(current.worst.availability)}% / ${number(current.worst.longest)} h`,number(c.total_usd),esc(c.comparison_reason??'')+' '+(c.feasible?(c.tied_with_selected&&id!==e.selected?'Feasible · equal objective score':'Feasible under assumptions'):esc(c.violations.map(v=>`${v.case}: ${v.requirement} ${number(v.value)} vs ${number(v.target)}`).join('; ')))];
  }))+
  `<p class="caption">Conditional contributions use ${esc(caseName)}, with each relay removed from the applied set. Overlapping coverage is counted once. All comparisons use the same candidate catalog and mission; feasibility checks all four cases.</p>`+table(['Relay removed','Conditional coverage gain','Longest blackout reduction','Recovered time','Best single replacement configuration'],e.marginal.map(m=>{const v=marginalMetrics(m,caseName);return [esc(m.relay_id),v?`${number(v.availability_gain_pp)} pp`:'Recalculate for this case',v?`${number(v.blackout_reduction_hours)} h`:'Unknown',v?`${number(v.recovered_hours)} h`:'Unknown',esc(m.replacement??'No eligible replacement')];}))+
  `<details><summary>Candidate screening and eligibility</summary><pre>${esc(JSON.stringify(e.screening,null,2))}</pre>${table(['Candidate','Eligible','Reason'],r.candidates.map(c=>[esc(c.id),String(c.eligibility?.eligible??false),esc(c.eligibility?.reasons.join('; '))]))}</details>`);
}
