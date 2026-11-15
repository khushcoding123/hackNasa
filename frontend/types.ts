export interface EnvironmentState {temperature_c:number[]; sink_k:number[]; heater_w:number[]; dust_load:number[]; solar_factor:number[]; soc:number[]; available:boolean[]; reason:string[]; minimum_temperature_c:number; minimum_soc:number; heater_energy_wh:number; frozen_hour:number|null}
export interface EnvironmentReport {version:number; status:string; validated:boolean; primary_case:string; cases:Record<string,{network:Metrics; worst:Metrics; meets:boolean}>; lander:Record<string,EnvironmentState>; geometric_direct:Metrics; assumptions:string}
export interface Metrics { availability:number; longest:number; total:number; intervals:number; average:number }
export interface Site {name:string; lat:number; lon:number; elevation:number; mean_slope:number|null; max_slope:number|null; terrain_coverage:number; resolution:number}
export interface Candidate {environment:Record<string,EnvironmentState>; id:string; x:number; y:number; lat:number; lon:number; distance:number; slope:number; elevation:number; los:boolean|null; surface_margin:number; earth:Metrics; solar:Metrics; power:Metrics; coverage:Metrics; recovered:number; temporal_coverage:boolean[]; unknown_samples:number; minimum_reserve:number; sunlight:boolean[]; powered:boolean[]; reserve:number[]; minimum_earth_margin:number; deployment:Deployment; limiting:Limiting}
export type Factor="terrain"|"rf"|"power"|"temperature"|"data"|"lander";
export interface Limiting {primary:Factor|null; available_pct:number; blocked_pct:Record<Factor,number>; recovered_if_fixed_pct:Record<Factor,number>; power_in_darkness_pct:number; power_in_sunlight_pct:number; surface_link:{terrain:boolean; rf:boolean; unknown:boolean}; summary:string}
export interface Deployment {verdict:"feasible"|"hazard"|"unverified"; reason:string; slope_limit:number; straight_km:number; route_km:number; detour_factor:number; max_slope:number|null; mean_slope:number|null; steep_pct:number; longest_steep_m:number; unknown_pct:number; ascent_m:number; descent_m:number; net_elevation_m:number|null; local_relief_m:number|null; local_steep_pct:number|null; checks:{name:string; value:number|null; limit:number; unit:string; ok:boolean|null}[]; assumption:string}
export interface PowerLossRow {id:string; affects_network:boolean; hours_off:number; note:string; start?:string; end?:string; first?:number; last?:number; connected_during_pct?:number; metrics?:Metrics; meets?:boolean; availability_lost?:number; blackout_increase?:number}
export interface PowerLoss {hours:number; nominal:Metrics; relays:PowerLossRow[]; basis:string}
export interface DeploymentSummary {counts:Record<string,number>; enforced:boolean; slope_limit:number; selected_all_feasible:boolean; if_steep_allowed:{recommended:string[]; feasible:boolean; network:Metrics; excluded:string[]}|null; longest_route_km:number; assumption:string}
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
export interface CostReport {currency:string; budget_usd:number|null; relay_cost_usd:number; total_usd:number; remaining_usd:number|null; within_budget:boolean; assumptions:string; pricing_basis:"illustrative"|"user"; items:{category:string; item:string; quantity:number; unit_cost_usd:number; total_usd:number}[]}
export interface DynamicCostItem {category:string; item:string; quantity:number; unit_cost_usd:number; total_usd:number; driver:string; node:string|null; role:string|null}
export interface DynamicCostNode {id:string; role:string; range_km:number|null; route_km:number|null; slope_deg:number|null; verdict:string|null; total_usd:number; parts:{component:string; category:string; usd:number; driver:string}[]}
export interface SatelliteCost {radius_km:number; speed_km_s:number; period_min:number; gravity:number; inclination_deg:number; access_half_arc_deg:number; duty_pct:number; max_range_km:number; count:number; note:string; delta_v_m_s:number; delta_v_arrival_m_s:number; delta_v_keeping_m_s:number; dry_mass_kg:number; wet_mass_kg:number; propellant_kg:number; coverage_target_pct:number; achieved_pct:number}
export interface DynamicCost {
 model:"dynamic"; total_usd:number; surface_usd:number; satellite_usd:number; flat_reference_usd:number|null; budget_usd:number|null; remaining_usd:number|null; within_budget:boolean;
 assumptions:string; items:DynamicCostItem[]; nodes:DynamicCostNode[]; categories:Record<string,number>; satellite:SatelliteCost|null;
 drivers:{nodes:number; gateways:number; mines:number; longest_link_km:number; total_route_km:number; area_km2:number|null; mission_days:number; contingency_pct:number};
}
export interface Result extends Comparison {
 cost:CostReport;
 power_loss:PowerLoss; deployment:DeploymentSummary;
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
 cost:CostReport;
 site:{key:string; name:string; lat:number; lon:number; area_km2:number; radius_m:number; resolution_m:number; ring_m:number; projection:"polar"|"local"; source:string};
 polygon:[number,number][]; nodes:AreaNode[]; layouts:Record<string,LayoutMetrics>; recommended:string; reason:string; score_method:string; candidates:number;
 score_evidence?:ScoreEvidence; assessment?:Assessment;
 plan:{served:number; worst_after_failure:number; feasible:boolean; chosen:number[]; gateways:number[]; mines:number[]; unreachable:number[]};
}
export interface Sensitivity {label:string; error?:string; direct:Metrics; network:Metrics; feasible:boolean; count:number; eligible:number; status?:Assessment["status"]}
