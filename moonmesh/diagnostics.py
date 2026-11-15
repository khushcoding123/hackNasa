"""Traceable, sampled communication evidence. These functions do not change physics."""
import numpy as np

from lunar_core.terrain import radial_points
from .costs import cost_report
from .network import evaluate, metrics, spans


def nullable(values):
    return np.where(np.isfinite(values), values, None).tolist()


def gate(code, ok, values=None, threshold=None, unit=None, unknown_blocks=True):
    """Unknown RF may support a geometry-only proxy; unknown terrain never does."""
    return dict(code=code, ok=[None if value is None else bool(value) for value in ok], values=values, threshold=threshold, unit=unit,
                comparison=('>' if code == 'terrain_clearance' else '>=') if threshold is not None else None,
                unknown_blocks=unknown_blocks)


def horizon_evidence(point, horizon, azimuth):
    step = 360 / len(horizon.azimuth_deg)
    az = np.asarray(azimuth) % 360
    left = np.floor(az / step).astype(int)
    rays = []
    for bearing, height, distance in zip(horizon.azimuth_deg, horizon.elevation_deg, horizon.distance_m):
        valid = bool(np.isfinite(height) and np.isfinite(distance))
        lat, lon = radial_points(point.lat, point.lon, bearing, distance) if valid else (None, None)
        rays.append(dict(azimuth_deg=float(bearing), elevation_deg=float(height) if valid else None,
            distance_m=float(distance) if valid else None, lat=float(lat) if valid else None,
            lon=float(lon) if valid else None, approximate=True,
            todo=None if valid else "TODO: terrain coverage is incomplete on this sampled ray."))
    return dict(rays=rays, left=left.tolist(), right=((left + 1) % len(rays)).tolist(),
                weight=(az / step - left).tolist(),
                basis="Controlling terrain samples on adjacent horizon rays; not an exact obstruction intersection.")


def sky_link(node_id, target, point, horizon, data, mission):
    satellite = target == 'S1'
    az, el = np.array(data['az'] if satellite else data['earth_az']), np.array(data['el'] if satellite else data['earth_el'])
    limit = horizon.at(az)
    clearance = el - limit - mission.clearance
    radio = mission.satellite.surface_to_sat_radio if satellite else mission.earth_radio
    margin = data['margin'] if satellite else data['rf_margin']
    known = np.isfinite(limit)
    gates = [gate('terrain_coverage', [True if value else None for value in known]),
        gate('terrain_clearance', [bool(v > 0) if k else None for v, k in zip(clearance, known)],
             nullable(clearance), 0, 'deg')]
    if satellite:
        gates.append(gate('elevation_mask', (el >= mission.satellite.min_elevation_deg).tolist(),
                          el.tolist(), mission.satellite.min_elevation_deg, 'deg'))
    gates.append(gate('rf_margin', [None] * len(el) if radio is None else (np.array(margin) >= radio.margin).tolist(),
                      margin, None if radio is None else radio.margin, 'dB', unknown_blocks=False))
    available = known & (clearance > 0)
    if satellite:
        available &= el >= mission.satellite.min_elevation_deg
    if radio is not None:
        available &= np.array(margin) >= radio.margin
    return dict(id=f'{node_id}->{target}', nodes=[node_id, target], available=available.tolist(), gates=gates,
        azimuth_deg=az.tolist(), elevation_deg=el.tolist(),
        range_km=data['range_km'] if satellite else data['earth_range_km'],
        horizon=horizon_evidence(point, horizon, az), geometry_only=radio is None)


def earth_backhaul(data, mission):
    radio = mission.satellite.sat_to_earth_radio
    margin = data['margin']
    return dict(id='S1->Earth', nodes=['S1', 'Earth'], available=data['available'], geometry_only=radio is None,
        range_km=data['range_km'], location=None, todo="Earth receiver station is not specified.",
        gates=[gate('moon_occultation', [not v for v in data['occulted']]),
               gate('rf_margin', [None] * len(data['available']) if radio is None else (np.array(margin) >= radio.margin).tolist(),
                    margin, None if radio is None else radio.margin, 'dB', unknown_blocks=False)])


def node_evidence(point, states, known, mission):
    cases = {}
    for case, state in states.items():
        frozen = state['frozen_step']
        alive = [frozen < 0 or i < frozen for i in range(len(known))]
        # The thermal solver does not expose independent power shortfalls once frozen.
        # Preserve this as unknown rather than inferring a power failure from low SOC.
        power = [True if reason == 'Available' else False if reason == 'Insufficient power' else None
                 for reason in state['reason']]
        cases[case] = dict(available=state['available'].tolist(), gates=[
            gate('environment_known', [True if value else None for value in known]), gate('thermal_survival', alive,
                state['temp_k'].tolist(), mission.freeze_k, 'K'),
            gate('node_power', power, state['soc'].tolist(), None, 'charge fraction', unknown_blocks=False)])
    return dict(lat=point.lat, lon=point.lon, elevation_m=point.ground, mast_m=point.mast, cases=cases,
        power_todo="Independent power state after freeze or missing environment is not exposed by the solver.")


def component_issues(component_id, gates, index):
    return [(component_id, g['code'], 'unknown' if g['ok'][index] is None else 'failed')
            for g in gates if g['ok'][index] is not True]


def add_path(diagnostics, identity, nodes, links, relay=None):
    available = {}
    for case in diagnostics['cases']:
        flags = np.logical_and.reduce([diagnostics['links'][link]['available'] for link in links])
        for node_id in nodes:
            node = diagnostics['nodes'].get(node_id, {})
            if 'cases' in node:
                flags &= node['cases'][case]['available']
        available[case] = flags.tolist()
    diagnostics['paths'][identity] = dict(id=identity, nodes=nodes, links=links, relay=relay, available=available)


def path_issues(diagnostics, path, index, case, disabled=()):
    issues = []
    for node_id in path['nodes']:
        if node_id in disabled:
            issues.append((node_id, 'injected_failure', 'failed'))
        node = diagnostics['nodes'].get(node_id)
        if node and 'cases' in node:
            issues.extend(component_issues(node_id, node['cases'][case]['gates'], index))
    for link_id in path['links']:
        issues.extend(component_issues(link_id, diagnostics['links'][link_id]['gates'], index))
    return sorted(set(issues))


def outage_report(flags, paths, diagnostics, times, end, hours, case, disabled=()):
    """Intervals retain cause changes; causes identify components rather than asserted root causes."""
    stamps = (*times, end)
    output = []
    for a, b in spans(flags):
        a, b = int(a), int(b)
        segments = []
        last_signature = None
        for i in range(a, b):
            signature = tuple((p['id'], tuple(path_issues(diagnostics, p, i, case, disabled))) for p in paths)
            if signature == last_signature:
                segments[-1]['last'] = i + 1
                segments[-1]['end'] = stamps[i + 1].isoformat()
                segments[-1]['hours'] += hours
            else:
                segments.append(dict(first=i, last=i+1, start=stamps[i].isoformat(), end=stamps[i+1].isoformat(),
                    hours=hours, paths=[dict(id=path_id, issues=[dict(component=c, code=code, status=status)
                        for c, code, status in issues]) for path_id, issues in signature]))
            last_signature = signature
        causes = sorted({f"{issue['component']}: {issue['code']} ({issue['status']})"
            for segment in segments for path in segment['paths'] for issue in path['issues']})
        output.append(dict(first=a, last=b, start=stamps[a].isoformat(), end=stamps[b].isoformat(),
            hours=(b-a)*hours, causes=causes or ['No usable selected path; cause unknown'], segments=segments,
            start_boundary='mission_start' if a == 0 else 'sampled',
            end_boundary='mission_end' if b == len(times) else 'sampled'))
    return output


def attach_outages(result, diagnostics, times, end, mission):
    hours = mission.minutes / 60
    selected = set(result['recommended'])
    paths = list(diagnostics['paths'].values())
    active_paths = [p for p in paths if p['relay'] is None or p['relay'] in selected]
    for key, state in result['failure_states'].items():
        disabled = [] if key == 'nominal' else key.split(',')
        state['outages'] = outage_report(state['combined'], active_paths, diagnostics, times, end,
                                         hours, 'cold_dust', disabled)
        # Other cases use the same selected architecture, not a re-optimized plan.
        state['cases'] = {}
        for case in diagnostics['cases']:
            flags = np.zeros(len(times), dtype=bool)
            assisted = np.zeros(len(times), dtype=bool)
            direct = np.zeros(len(times), dtype=bool)
            for path in active_paths:
                if not set(path['nodes']).intersection(disabled):
                    flags |= np.asarray(path['available'][case])
                    if path['relay'] is None:
                        direct |= np.asarray(path['available'][case])
                    else:
                        assisted |= np.asarray(path['available'][case])
            case_outages = state['outages'] if case == 'cold_dust' else outage_report(
                flags, active_paths, diagnostics, times, end, hours, case, disabled)
            state['cases'][case] = dict(combined=flags.tolist(), assisted=assisted.tolist(), direct=direct.tolist(),
                metrics=metrics(flags, hours), outages=case_outages,
                path_outages={path['id']: outage_report(
                    np.zeros(len(times), dtype=bool) if set(path['nodes']).intersection(disabled) else path['available'][case],
                    [path], diagnostics, times, end, hours, case, disabled) for path in active_paths})
    for path in paths:
        path['metrics'] = {case: metrics(flags, hours) for case, flags in path['available'].items()}
        path['outages'] = outage_report(path['available']['cold_dust'], [path], diagnostics,
                                        times, end, hours, 'cold_dust')
    result['diagnostics'] = diagnostics


def optimization_evidence(records, selected, options, direct, relays, mission, without_satellite):
    """Summarize records from the production search; only displayed rows receive timelines."""
    by_selection = {r['selection']: r for r in records}
    chosen = by_selection[selected]
    # Feasible ties use max(selection), as the production optimizer does. Fallback
    # ties keep the first enumerated tuple because its comparison is strictly >.
    def ranked(rows):
        return sorted(rows, key=lambda r: (r['feasible'], r['key'],
            r['selection'] if r['feasible'] else tuple(-i for i in r['selection'])), reverse=True)

    best_by_count = [ranked([r for r in records if len(r['selection']) == count])[0]
                     for count in sorted({len(r['selection']) for r in records})]
    alternatives = ranked([r for r in records if len(r['selection']) == len(selected) and r['selection'] != selected])[:5]
    removals, replacements = [], []
    for index in selected:
        retained = tuple(i for i in selected if i != index)
        removal = by_selection[retained]
        swaps = [r for r in records if len(r['selection']) == len(selected) and index not in r['selection']
                 and set(retained).issubset(r['selection'])]
        replacement = ranked(swaps)[0] if swaps else None
        removals.append((index, removal))
        replacements.append((index, replacement))
    displayed = [chosen, by_selection[()], *best_by_count, *alternatives,
                 *[r for _, r in removals], *[r for _, r in replacements if r]]
    def config_id(row):
        return ','.join(options[i]['id'] for i in row['selection']) or 'none'

    configs = {}
    for row in displayed:
        identity = config_id(row)
        if identity in configs:
            continue
        selection = row['selection']
        online, nominal, worst, _ = evaluate(direct['cold_dust'], relays['cold_dust'], selection, mission,
            None if without_satellite is None else (without_satellite[0]['cold_dust'], without_satellite[1]['cold_dust']))
        violations = []
        for case, report in row['cases'].items():
            target = report['worst'] if mission.resilient else report['network']
            if target['availability'] < mission.availability:
                violations.append(dict(case=case, requirement='availability', value=target['availability'], target=mission.availability))
            if target['longest'] > mission.blackout_hours:
                violations.append(dict(case=case, requirement='blackout_hours', value=target['longest'], target=mission.blackout_hours))
        configs[identity] = dict(id=identity, relay_ids=[options[i]['id'] for i in selection],
            feasible=row['feasible'], score=list(row['key']), cases=row['cases'], network=nominal, worst=worst,
            violations=violations, total_usd=cost_report(len(selection), mission.budget_usd, mission.relay_cost_usd)['total_usd'],
            timeline=online.tolist(), timelines={case: evaluate(direct[case], relays[case], selection, mission)[0].tolist()
                for case in direct}, tied_with_selected=row['key'] == chosen['key'] and row['feasible'] == chosen['feasible'])
        if selection == selected:
            reason = 'Applied recommendation.'
        elif chosen['feasible'] and not row['feasible']:
            reason = 'Does not meet all configured requirements across the environmental cases.'
        elif chosen['feasible'] and len(selection) > len(selected):
            reason = 'Uses more relays than the first affordable count meeting all requirements.'
        elif row['key'] == chosen['key']:
            reason = 'Equal objective score; the deterministic tie policy selects the recommendation.'
        else:
            reason = 'Lower objective score: availability, then longest blackout, then relay count.'
        configs[identity]['comparison_reason'] = reason
    marginal = []
    for (index, removal), (_, replacement) in zip(removals, replacements):
        before = configs[config_id(removal)]['network']
        after = configs[config_id(chosen)]['network']
        per_case = {}
        for case in direct:
            before_case = removal['cases'][case]['network']
            after_case = chosen['cases'][case]['network']
            per_case[case] = dict(availability_gain_pp=after_case['availability']-before_case['availability'],
                blackout_reduction_hours=before_case['longest']-after_case['longest'],
                recovered_hours=before_case['total']-after_case['total'])
        marginal.append(dict(relay_id=options[index]['id'], without=config_id(removal), cases=per_case,
            replacement=None if replacement is None else config_id(replacement),
            availability_gain_pp=after['availability']-before['availability'],
            blackout_reduction_hours=before['longest']-after['longest'],
            recovered_hours=before['total']-after['total']))
    return dict(version=1, status='simulated', selected=config_id(chosen), baseline='none',
        objective='First affordable relay count passing all four environment cases; maximize worst-case availability, then minimize worst-case longest blackout. N-1 includes the shared satellite when enabled.',
        scope='Minimum within the screened catalog; no global terrain optimum is claimed.',
        tie_policy='Feasible score ties select the lexicographically greatest candidate-index tuple; fallback score ties retain the first enumerated tuple.',
        tie_count=sum(r['key'] == chosen['key'] and r['feasible'] == chosen['feasible'] for r in records)-1,
        evaluated=len(records), feasible_count=sum(r['feasible'] for r in records),
        counts=[dict(count=len(r['selection']), evaluated=sum(len(v['selection']) == len(r['selection']) for v in records), best=config_id(r)) for r in best_by_count],
        alternatives=[config_id(r) for r in alternatives], marginal=marginal, configurations=configs)
