# Lunar imagery

The bundled 4k map is the lower-detail fallback used when the live LROC WAC service is unavailable; the primary imagery streams from the public WMS (`luna_wac_global`, 100 m/pixel). The fallback uses NASA's Scientific Visualization Studio **CGI Moon Kit**, derived from LROC imagery and polar albedo data.

- Source and credits: https://svs.gsfc.nasa.gov/4720/
- Source image: https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/lroc_color_poles_8k.tif (2019 color map, 8192 × 4096).
- Credit: NASA's Scientific Visualization Studio; Ernie Wright. Instrument/source teams are described on the source page.

The source TIFF is converted to JPEG without changing its geographic registration:

| Asset | Dimensions | Loading |
| --- | --- | --- |
| `moon-color.jpg` | 2048 × 1024 | Initial globe |
| `moon-color-4k.jpg` | 4096 × 2048 | Zoomed phone/smaller viewer, or limited GPU texture size |
| `moon-color-8k.jpg` | 8192 × 4096 | Zoomed desktop viewer with sufficient GPU texture capacity |

Detailed imagery loads on demand while the existing texture stays visible. Labels use IAU/USGS feature centers, geographic pole/hemisphere references, and the project's approximate research points; per-feature source links are stored in `frontend/landmarks.ts` and displayed in the inspector.

These color maps are optimized for visualization, and polar imagery has lower underlying resolution than some equatorial areas. Scientific results continue to use the separate LOLA terrain and geometry pipeline. Zoom does not create new observational detail beyond the source imagery.

## Global elevation geometry

`moon-elevation.i16` is a 5760 × 2880 signed little-endian Int16 grid of elevations **in metres**, relative to the **1,737,400 m** lunar reference radius. `moon-elevation.json` describes its dimensions, units, range, and source. Pixel centers run north to south and from −180° to +180° east longitude.

Source: NASA SVS / LOLA `ldem_16.tif`, a 5760 × 2880 floating-point map in kilometres: https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/ldem_16.tif . The grid keeps the source's native 16 pixel/degree sampling (about 1.9 km/pixel at the equator) and is only rounded to the nearest metre. This encoding precision is not a claim of one-metre terrain resolution.

Regenerate with `python scripts/prepare_moon_topography.py /path/to/ldem_16.tif` using a tooling environment with numpy, scipy, and tifffile. These conversion dependencies are not required by the browser or production application.

The global grid is served to the Cesium viewer through `GET /api/terrain/tile` (see `moonmesh/tiles.py`), rendered at 1× scale. The server blends it with the regional LOLA DEMs per sample, so adjacent tiles agree exactly on shared edges. Local mission terrain and mission calculations retain their independent LOLA pipeline.
