// Standalone ORA lab page (web/ora.html). The views themselves live in ora-lab.ts, which the Moon explorer also mounts.
import {createOraLab, type OraTab} from "./ora-lab.js";

const $=<T extends HTMLElement=HTMLElement>(q:string)=>document.querySelector<T>(q)!;
const form=$<HTMLFormElement>("#scenario"), view=$("#view");
const submit=()=>$<HTMLButtonElement>('#scenario button[type="submit"]');

const oraLab=createOraLab({
 view,
 scenarioBody(){
  const out:Record<string,unknown>={};
  for(const [k,v] of new FormData(form))out[k]=k==="scenario"?v:Number(v);
  return out;
 },
 status(text,error=false){const node=$("#status");node.textContent=text;node.classList.toggle("error",error);},
 state(text,attention){const node=$("#state");node.textContent=text;node.classList.toggle("stale",attention);},
 header(chapter,title,purpose){$("#chapter").textContent=chapter;$("#title").textContent=title;$("#purpose").textContent=purpose;},
 scenarioFormHidden(hidden){form.hidden=hidden;},
 subtitle(text){$("#subtitle").textContent=text;},
 canBuild:()=>form.reportValidity(),
 building(on){
  submit().disabled=on;view.setAttribute("aria-busy",String(on));
  if(on&&!oraLab.hasNetwork)view.innerHTML='<section class="panel initial-state loading-state"><span class="eyebrow">CALCULATING NETWORK</span><h2>Reading the terrain.</h2><p class="muted">Tracing line of sight and finding relay sites. This may take a few moments.</p></section>';
 },
});

async function buildNetwork(){
 await oraLab.build();
 if(!oraLab.hasNetwork){
  view.innerHTML=`<section class="panel"><h2>Network unavailable</h2><p class="callout amber">${$("#status").textContent?.replace(/[&<>]/g,"")}</p><p class="muted">Choose another scenario above or rebuild when its terrain is available.</p></section>`;
  $("#subtitle").textContent="No network calculated.";
 }
}
form.onsubmit=event=>{event.preventDefault();void buildNetwork();};
$("#ora-nav").addEventListener("click",event=>{
 const button=(event.target as HTMLElement).closest<HTMLButtonElement>("button[data-tab]");if(!button)return;
 $("#ora-nav [aria-current]")?.removeAttribute("aria-current");button.setAttribute("aria-current","page");
 oraLab.open(button.dataset.tab as OraTab);
 $("#navigation").classList.remove("open");$("#menu-toggle").setAttribute("aria-expanded","false");$("#title").focus({preventScroll:true});
});
$("#menu-toggle").onclick=()=>{const open=$("#navigation").classList.toggle("open");$("#menu-toggle").setAttribute("aria-expanded",String(open));};
window.addEventListener("resize",()=>oraLab.repaint());
(async()=>{
 const conf=await oraLab.ensureConfig();
 if(!conf)return;
 const select=$<HTMLSelectElement>('select[name="scenario"]');
 for(const name of Object.keys(conf.scenarios))select.add(new Option(name.replaceAll("_"," "),name));
 const fill=()=>{const s=conf.scenarios[select.value];for(const k of ["candidates","max_range_km","relay_mast_m","mining_sites","extra_relays"])$<HTMLInputElement>(`[name="${k}"]`).value=String(s[k]);};
 if(conf.scenarios.shackleton_rim_to_floor)select.value="shackleton_rim_to_floor";
 select.onchange=fill;fill();
 submit().disabled=false;
 await buildNetwork();
})();
