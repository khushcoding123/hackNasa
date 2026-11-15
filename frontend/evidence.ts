// Shared rendering for result status (met / failed / insufficient evidence) and for which models are loaded now.
import type {Assessment, ModelStatus} from "./types.js";

const esc=(s:unknown)=>String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]!));
const tone:Record<Assessment["status"],string>={requirements_met:"green",requirements_failed:"red",insufficient_evidence:"amber"};
const icon:Record<Assessment["status"],string>={requirements_met:"✓",requirements_failed:"✕",insufficient_evidence:"?"};
const list=(items:string[])=>items.length?`<ul>${items.map(i=>`<li>${esc(i)}</li>`).join("")}</ul>`:"<p class=\"muted\">None.</p>";

function value(v:unknown):string{
 if(v&&typeof v==="object"){
  const o=v as Record<string,unknown>, unit=o.unit?` ${o.unit}`:"";
  if("nominal" in o){
   const f=(x:unknown)=>typeof x==="number"?x.toFixed(1):esc(x);
   return `${f(o.nominal)}${unit} <small class="muted">(${f(o.if_marginal_samples_lost)}${unit} if marginal samples are lost, ${f(o.if_unknown_samples_available)}${unit} if unknown samples are available)</small>`;
  }
  return esc(JSON.stringify(v));
 }
 return typeof v==="number"?String(Math.round(v*10)/10):esc(v);
}

/** One-line badge: status and what it covers. */
export const assessmentBadge=(a:Assessment|undefined)=>a?`<span class="verdict ${tone[a.status]}" title="${esc(a.scope)}">${icon[a.status]} ${esc(a.label)} · ${esc(a.subject)}</span>`:"";

/** Full panel body: checks, evidence gaps, what was and was not evaluated, assumptions. */
export function assessmentHtml(a:Assessment|undefined,open=false){
 if(!a)return `<p class="callout amber">No requirements assessment was returned for this result.</p>`;
 const met=(m:boolean|null)=>m===true?'<span class="green">Met</span>':m===false?'<span class="red">Failed</span>':'<span class="amber">Undecided</span>';
 const checks=`<div class="table-wrap"><table><thead><tr><th scope="col">Requirement</th><th scope="col">Target</th><th scope="col">Value</th><th scope="col">Result</th></tr></thead><tbody>${a.checks.map(c=>`<tr><td>${esc(c.name)}${c.basis?`<br><small class="muted">${esc(c.basis)}</small>`:""}</td><td>${esc(c.target)}</td><td>${value(c.value)}</td><td>${met(c.met)}</td></tr>`).join("")}</tbody></table></div>`;
 const gaps=a.evidence_gaps.length?`<ul>${a.evidence_gaps.map(g=>`<li><strong>${esc(g.name)}</strong>${g.blocking?' <span class="amber">(blocks a "met" status)</span>':""}: ${esc(g.detail)}</li>`).join("")}</ul>`:"<p class=\"muted\">None recorded.</p>";
 return `<p>${assessmentBadge(a)}</p><p class="muted">${esc(a.kind==="scenario outcome"?"Scenario outcome: behaviour under the stated assumptions, not a prediction.":"Calculated window: deterministic geometry and link budgets.")} ${esc(a.scope)}</p>`+
  checks+`<h3>Evidence gaps and caveats</h3>${gaps}`+
  `<details${open?" open":""}><summary>Hazards evaluated (${a.evaluated_hazards.length}) and not evaluated (${a.not_evaluated.length})</summary><h3>Evaluated</h3>${list(a.evaluated_hazards)}<h3>Not evaluated</h3>${list(a.not_evaluated)}</details>`+
  `<details${open?" open":""}><summary>Architecture and assumed parameters</summary><p>${esc(a.architecture)}</p>${list(a.assumptions)}</details>`;
}

/** Models loaded in the running server, as opposed to the training record in the model card. */
export function modelsHtml(models:ModelStatus[]|undefined){
 if(!models)return `<p class="callout amber">The server did not report model status.</p>`;
 const rows=models.map(m=>`<tr><td>${esc(m.name)}</td><td>${m.loaded?'<span class="green">Loaded</span>':'<span class="amber">Not loaded</span>'}</td><td>${esc(m.estimator??"–")}</td><td><code>${esc(m.sha256?m.sha256.slice(0,12):"–")}</code></td><td>${esc(m.saved_with_sklearn??"–")} / ${esc(m.running_sklearn??"–")}</td><td>${m.loaded?(m.model_card.matches_file===true?"matches":m.model_card.matches_file===false?'<span class="red">different file</span>':"no hash recorded"):esc(m.error??"")}${!m.loaded?`<small class="coords">Fallback: ${esc(m.fallback)}</small>`:""}${(m.warnings??[]).map(w=>`<small class="coords amber">${esc(w)}</small>`).join("")}</td></tr>`).join("");
 return `<div class="table-wrap"><table><thead><tr><th scope="col">Artifact</th><th scope="col">State</th><th scope="col">Estimator</th><th scope="col">SHA-256</th><th scope="col">scikit-learn saved / running</th><th scope="col">Model card / error</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
