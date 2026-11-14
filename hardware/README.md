# ORA bench test

A small physical "mine" to fit and check the tunnel radio model in `ora/tunnel.py`. It validates the method. Indoor 2.4 GHz in plastic pipe is not lunar regolith, so it does not produce lunar numbers.

**Status:** the firmware is written but has not been compiled or flashed. The fitting script is tested (`tests/test_hardware.py`).

## What you need

- 3 or 4 ESP32 development boards (any `esp32dev`-compatible board) and USB cables.
- A model mine: PVC pipe or a cardboard maze with a few straight runs and at least two bends. Larger is better; aim for 5 m or more of total length.
- A tape measure.
- PlatformIO Core. It is already installed on this machine at `~/.platformio/penv/Scripts/pio.exe`. The first build downloads the ESP32 toolchain (several hundred MB).

## Steps

1. Flash one board per environment so each has its own ID:

   ```bash
   pio run -d hardware/esp32_rssi_logger -e node1 -t upload
   ```

   Repeat with `node2`, `node3`, `node4`.
2. Put node 1 at the portal. Move node 2 along the tunnel. At each stop, record the serial output for about 10 seconds:

   ```bash
   pio device monitor -d hardware/esp32_rssi_logger -b 115200
   ```

3. For each stop, write one row in `measurements.csv` with the tunnel distance to node 1, the total bend angle along the way, and the mean RSSI heard from node 1:

   ```csv
   distance_m,bend_deg,rssi_dbm
   1.0,0,-41
   2.0,0,-47
   3.0,90,-63
   ```

   Use several distances on the straight and at least two different bend totals.
4. Fit the model:

   ```bash
   python hardware/fit_tunnel_model.py measurements.csv 2.4
   ```

5. Enter the fitted `alpha_db_per_100m` and `bend_db_per_90deg` in the ORA lab's Underground view (or in `scenarios/mine_tunnel_demo.yaml`) and re-run the dig twin.
6. Breadcrumb run: leave the fixed nodes switched off in a pocket, walk node 2 down the tunnel, and place a fixed node wherever the serial log prints a `# DROP` line. Compare where repeaters ended up, and whether the mover stayed linked, with the dig twin's prediction for the same layout. Report the gap as measured.
