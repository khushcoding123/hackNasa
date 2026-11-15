import assert from 'node:assert/strict';
import {pathToFileURL} from 'node:url';
const {candidateAvailable,outageEvidence,sampleEvidence,relayEvidence,earthHorizonNote,evidenceFocus}=await import(pathToFileURL(process.argv[2]).href);
const metric={availability:50,longest:1,total:1,intervals:1,average:1};
const path={id:'Lander->R1->S1->Earth',nodes:['Lander','R1','S1','Earth'],links:['R1->S1'],relay:'R1',available:{cold_dust:[true,false],nominal:[true,true]},outages:[]};
const issue={component:'R1->S1',code:'rf_margin',status:'unknown'};
const outage={start:'2026-11-01T00:30:00+00:00',end:'2026-11-01T01:00:00+00:00',first:1,last:2,hours:.5,causes:['Unknown RF'],segments:[{first:1,last:2,start:'2026-11-01T00:30:00+00:00',end:'2026-11-01T01:00:00+00:00',hours:.5,paths:[{id:path.id,issues:[issue]}]}]};
const caseState={combined:[true,false],metrics:metric,outages:[outage],path_outages:{[path.id]:[outage]}};
const result={times:['2026-11-01T00:00:00+00:00','2026-11-01T00:30:00+00:00'],recommended:['R1'],candidates:[{id:'R1',temporal_coverage:[true,true],eligibility:{eligible:true,reasons:['Usable path']}}],
 diagnostics:{sample_minutes:30,timing:'Transitions unresolved',interval_convention:'[start,end)',cases:['cold_dust','nominal'],nodes:{Lander:{lat:-90,lon:0},R1:{lat:-89,lon:0},S1:{},Earth:{todo:'TODO: station unknown'}},paths:{[path.id]:path},links:{'R1->S1':{nodes:['R1','S1'],gates:[{code:'rf_margin',ok:[null,null],values:null,threshold:null,unit:'dB',unknown_blocks:false}],todo:'TODO: radio unknown'}}},
 failure_states:{nominal:{combined:[true,false],metrics:metric,cases:{cold_dust:caseState,nominal:{...caseState,outages:[]}}},S1:{combined:[false,false],metrics:metric,cases:{cold_dust:{...caseState,outages:[outage]}}}}};
assert.equal(candidateAvailable(result,'R1','cold_dust',new Set(),0),true);
assert.equal(candidateAvailable(result,'R1','cold_dust',new Set(['S1']),0),false);
assert.equal(candidateAvailable(result,'R1','cold_dust',new Set(),1),false);
assert.equal(candidateAvailable(result,'R1','nominal',new Set(),1),true);
const focus=evidenceFocus(result,new Set(['S1']),'network','cold_dust',0);
assert.equal(focus.nodes.get('S1'),'failed');
assert.equal(focus.links.get('R1->S1'),'unknown');
assert.match(outageEvidence(result,new Set(),'network','cold_dust'),/Active network outages/);
assert.match(outageEvidence(result,new Set(),'network','nominal'),/No outages/);
assert.match(outageEvidence(result,new Set(),path.id,'cold_dust'),/Individual-path outages/);
assert.match(sampleEvidence(result,new Set(['S1']),'network','cold_dust',0),/Injected failure/);
assert.match(sampleEvidence(result,new Set(),'network','cold_dust',0),/Unknown/);
result.diagnostics.nodes.Earth.todo='<img src=x onerror=alert(1)>';
assert.ok(!sampleEvidence(result,new Set(),'network','cold_dust',0).includes('<img'));
const config={id:'R1',relay_ids:['R1'],network:metric,worst:metric,cases:{cold_dust:{network:metric,worst:metric}},feasible:true,total_usd:100,tied_with_selected:true,violations:[],timelines:{cold_dust:[true,false]}};
result.optimization_evidence={selected:'R1',baseline:'R1',counts:[{count:1,best:'R1'}],alternatives:[],configurations:{R1:config},marginal:[],tie_count:0,evaluated:1,feasible_count:1,scope:'Screened catalog only',objective:'Exact objective',tie_policy:'Deterministic ties',screening:{catalog_size:1}};
assert.match(relayEvidence(result,'R1'),/Why these relays\?/);
assert.match(relayEvidence(result,'R1'),/Preview does not replace/);
assert.deepEqual(result.recommended,['R1']);
result.samples={earth_limit:[0,0],terrain:[true,false],earth_clearance:[1,-1],earth_el:[1,-1],
 flat:[true,false],rf_margin:[10,10],direct:[false,true],obstruction_m:[100,100]};
result.config={earth_radio:{margin:3},clearance:.1};
assert.equal(earthHorizonNote(result,0),'clear by 1.0°'); // Loss of power must not be labeled RF loss.
assert.equal(earthHorizonNote(result,1),'below the horizon'); // Satellite rescue does not change Earth geometry.
result.samples.rf_margin[0]=1;
assert.match(earthHorizonNote(result,0),/RF margin too low/);
console.log('Mission evidence UI: failure gating, environmental cases, outage scope, unknown evidence, escaping, and preview isolation passed');
