"""Convert NASA SVS ldem_16.tif (float32 kilometres) to a browser elevation grid.
Tooling dependencies: numpy, tifffile. Usage: python scripts/prepare_moon_topography.py SOURCE.tif
Source: https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_16.tif
"""
import json
from pathlib import Path
import sys
import numpy as np
import tifffile

source = tifffile.imread(sys.argv[1])
assert source.shape == (2880, 5760) and source.dtype.kind == 'f', (source.shape, source.dtype)
assert np.isfinite(source).all() and -20 < float(source.min()) < -5 and 5 < float(source.max()) < 20
# Keep the native 16 pixel/degree grid (about 1.9 km/pixel at the equator): no resampling, only metre rounding.
height, width = source.shape
elevations = np.rint(source*1000).astype('<i2')
out = Path(__file__).resolve().parents[1] / 'web' / 'assets'
elevations.tofile(out / 'moon-elevation.i16')
metadata = dict(width=width, height=height, encoding='int16-le', unit='m', referenceRadiusM=1737400,
                minimumM=int(elevations.min()), maximumM=int(elevations.max()),
                registration='pixel-center; north to south; longitude -180 to 180 eastward',
                source='https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_16.tif')
(out / 'moon-elevation.json').write_text(json.dumps(metadata, indent=2)+'\n')
print(metadata)
