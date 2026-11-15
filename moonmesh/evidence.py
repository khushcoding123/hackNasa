"""Result status: requirements met, requirements failed, or insufficient evidence.

A result is only "met" when every requirement check passes under the evidence the app actually has. A check
whose answer depends on missing or marginal data is "insufficient evidence", never a quiet pass or fail.
Every assessment also lists the hazards that were evaluated, the ones that were not, and the assumed
parameters, so a reader can see what the status does not cover.

Kinds of result (kept distinct on purpose):
- "calculated window": deterministic geometry (terrain obstruction, Sun and Earth position, link margin).
- "scenario outcome": what happens under stated assumptions (cold, dust, equipment loss). Not a prediction.
No result here is a forecast or a probability of real-world failure.
"""
MET, FAILED, INSUFFICIENT = "requirements_met", "requirements_failed", "insufficient_evidence"
LABELS = {MET: "Requirements met", FAILED: "Requirements failed", INSUFFICIENT: "Insufficient evidence"}


def check(name, target, value, met, basis=""):
    """One requirement. ``met`` is True, False, or None when the evidence cannot decide it."""
    return dict(name=name, target=target, value=value, met=met, basis=basis)


def gap(name, detail, blocking=True):
    """Missing or weak evidence. A blocking gap stops a "met" status; a non-blocking one is a stated caveat."""
    return dict(name=name, detail=detail, blocking=blocking)


def assess(checks, gaps=(), evaluated=(), not_evaluated=(), assumptions=(), architecture="", kind="calculated window", metrics=None,
           subject="mission communication"):
    """``subject`` names the requirement set, so a status is never read as covering more than it checked."""
    checks, gaps = list(checks), list(gaps)
    if any(c["met"] is False for c in checks):
        status = FAILED
    elif any(c["met"] is None for c in checks) or any(g["blocking"] for g in gaps) or not checks:
        status = INSUFFICIENT
    else:
        status = MET
    return dict(status=status, label=LABELS[status], subject=subject, kind=kind, architecture=architecture, checks=checks,
                evidence_gaps=gaps, evaluated_hazards=list(evaluated), not_evaluated=list(not_evaluated),
                assumptions=list(assumptions), metrics=metrics or {},
                scope="Status covers only the evaluated hazards listed. It is not a forecast, a failure probability or flight qualification.")


# Hazards the planners do not model yet. Listed with every result so a "met" is never read as covering them.
NOT_MODELLED = (
    "Equipment temperature limits, heater power and battery capacity loss in the cold (no calibrated thermal model)",
    "Dust accumulation on solar panels, radiators and optics",
    "Space weather: solar particle events, surface charging and radiation upsets",
    "Earth ground-station scheduling, atmospheric attenuation and receiver outages",
    "Hardware failure rates (relay loss is tested as a what-if, not as a probability)",
    "Lunar orbiting relay satellites",
)
