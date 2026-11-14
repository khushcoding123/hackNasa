import numpy as np

SUN_RADIUS_KM = 695_700


def apparent_semidiameter_deg(radius_km, distance_km):
    return np.degrees(np.arcsin(np.clip(radius_km / np.asarray(distance_km), 0, 1)))
