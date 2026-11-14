from functools import lru_cache
from pathlib import Path
from threading import RLock

import spiceypy as spice

BASE = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/"
SPICE_LOCK = RLock()
KERNELS = {
    "naif0012.tls": BASE + "lsk/naif0012.tls",
    "moon_de440_250416.tf": BASE + "fk/satellites/moon_de440_250416.tf",
    "moon_pa_de440_200625.bpc": BASE + "pck/moon_pa_de440_200625.bpc",
    "de440s.bsp": BASE + "spk/planets/de440s.bsp",
}


@lru_cache(maxsize=1)
def load_kernels(directory: Path) -> None:
    for name in KERNELS:
        if not (directory / name).is_file():
            raise FileNotFoundError(f"Missing SPICE kernel {name}. Run python -m moonmesh.data.")
    with SPICE_LOCK:
        for name in KERNELS:
            spice.furnsh(str(directory / name))
