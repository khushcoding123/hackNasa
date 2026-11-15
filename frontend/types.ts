export interface EnvironmentState {temperature_c:number[]; sink_k:number[]; heater_w:number[]; dust_load:number[]; solar_factor:number[]; soc:number[]; available:boolean[]; reason:string[]; minimum_temperature_c:number; minimum_soc:number; heater_energy_wh:number; frozen_hour:number|null}
export interface EnvironmentReport {version:number; status:string; validated:boolean; primary_case:string; cases:Record<string,{network:Metrics; worst:Metrics; meets:boolean}>; lander:Record<string,EnvironmentState>; geometric_direct:Metrics; assumptions:string}
export interface Metrics { availability:number; longest:number; total:number; intervals:number; average:number }
export interface Site {name:string; lat:number; lon:number; elevation:number; mean_slope:number|null; max_slope:number|null; terrain_coverage:number; resolution:number}
export interface Candidate {environment:Record<string,EnvironmentState>; id:string; x:number; y:number; lat:number; lon:number; distance:number; slope:number; elevation:number; los:boolean|null; surface_margin:number; earth:Metrics; solar:Metrics; power:Metrics; coverage:Metrics; recovered:number; temporal_coverage:boolean[]; unknown_samples:number; minimum_reserve:number; sunlight:boolean[]; powered:boolean[]; reserve:number[]; minimum_earth_margin:number}
export interface Outage {start:string; end:string; first:number; last:number; hours:number; causes:string[]}
export interface Assessment {
 status:"requirements_met"|"requirements_failed"|"insufficient_evidence"; label:string; subject:string; kind:"calculated window"|"scenario outcome";
 architecture:string; scope:string; checks:{name:string; target:string; value:unknown; met:boolean|null; basis:string}[];
 evidence_gaps:{name:string; detail:string; blocking:boolean}[]; evaluated_hazards:string[]; not_evaluated:string[]; assumptions:string[]; metrics:Record<string,number>;
}
export interface ModelStatus {
 name:string; present:boolean; loaded:boolean; error?:string|null; warnings?:string[]; fallback:string; estimator?:string; sha256?:string;
 saved_with_sklearn?:string|null; running_sklearn?:string|null; model_card:{section_present:boolean; trained?:string; matches_file:boolean|null};
}
export interface ScoreEvidence {method:string; fallback:boolean; note:string; models:{name:string; loaded:boolean; used:boolean; sha256?:string|null; error?:string|null; warnings:string[]}[]}
export interface Comparison {site:Site; direct:Metrics; solar:Metrics; network:Metrics; worst:Metrics; recommended:string[]; feasible:boolean; assessment?:Assessment}
export interface Result extends Comparison {
 environment:EnvironmentReport; config:Record<string, unknown>; times:string[]; redundant:number; darkness_meets:boolean; eligible:number;
 outages:Outage[]; darkness:Outage[]; candidates:Candidate[];
 failures:{id:string; metrics:Metrics; meets:boolean; windows_lost:number; availability_lost:number; blackout_increase:number}[];
 failure_states:Record<string,{combined:boolean[]; assisted:boolean[]; metrics:Metrics; meets:boolean}>;
 horizon:{azimuth:number[]; elevation:(number|null)[]; distance:(number|null)[]};
 samples:{earth_az:number[]; earth_el:number[]; earth_limit:(number|null)[]; earth_clearance:(number|null)[]; obstruction_m:(number|null)[]; earth_range_km:number[]; sun_az:number[]; sun_el:number[]; sun_limit:(number|null)[]; solar_clearance:(number|null)[]; rf_margin:number[]; flat:boolean[]; terrain:boolean[]; direct:boolean[]; solar:boolean[]; solar_known:boolean[]; network:boolean[]; reason:string[]; status:string[]};
 map:{extent:number; x:number; y:number; elevation:(number|null)[][]; slope:(number|null)[][]};
 provenance:Record<string,string|string[]|Record<string,string>>;
}
export interface LayoutMetrics {name:string; blurb:string; edges:[number,number][]; links:number; link_km:number; nominal_served:number; single_points_of_failure:number; single_path_links:number; worst_after_node_loss:number; mean_after_node_loss:number; worst_after_link_loss:number; schneider_r_random:number}
export interface AreaNode {id:number; role:"relay"|"gateway"|"mine"; lat:number; lon:number; elevation:number; sun:number; earth:number; score:number|null; outside:boolean}
export interface AreaResult {
 site:{key:string; name:string; lat:number; lon:number; area_km2:number; radius_m:number; resolution_m:number; ring_m:number; projection:"polar"|"local"; source:string};
 polygon:[number,number][]; nodes:AreaNode[]; layouts:Record<string,LayoutMetrics>; recommended:string; reason:string; score_method:string; candidates:number;
 score_evidence?:ScoreEvidence; assessment?:Assessment;
 plan:{served:number; worst_after_failure:number; feasible:boolean; chosen:number[]; gateways:number[]; mines:number[]; unreachable:number[]};
}
export interface Sensitivity {label:string; error?:string; direct:Metrics; network:Metrics; feasible:boolean; count:number; eligible:number; status?:Assessment["status"]}
