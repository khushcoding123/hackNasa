# Step 13: U-Net (only if recommended)

Read `reports/eval/unet_decision.json` (from step 11).

- If "recommended" is false, do not build anything. Write the status file with status "skipped" and the reasons from the decision file. Skipping is allowed for this step.
- If it is true, continue below.

Add torch to `requirements-training.txt` and install it into the training venv with uv. Use the CUDA build only if an NVIDIA GPU is present; otherwise use the CPU build. Implement a small transmitter-conditioned U-Net in `training/src/moonmesh_train/unet.py`, with training in `training/scripts/12_train_unet.py`:

- Input: tx-centered 832 x 832 patches at 20 m (about +/-8.3 km), so the labels need no resampling.
- Channels: DEM minus the tx ground elevation (scaled), slope, valid mask, distance, sin/cos bearing, and constant planes for h_tx, h_rx and log10(f).
- Three heads: min_clearance (Huber), link_margin (Huber) and an LOS logit (BCE), all masked by the valid mask.
- Size: about 5-8M parameters.
- Training:
  - AdamW, learning rate 1e-3 with a cosine schedule, batch 4, AMP when on GPU
  - early stopping on val false-covered area + boundary F1
  - augmentation: 90-degree rotations and flips, recomputing the bearing channels after each transform
  - use the same `training/splits.json`
  - checkpoint every epoch so training can resume; cap total training at about 3 hours of wall time
- Log the parameter count and training time.
- Register it as a predictor in `10_evaluate.py`, run the val evaluation, and compare it with LightGBM and the deterministic labeler.
- Add a pytest marked slow that overfits 4 patches on CPU.

## Gates (when not skipped)

1. The model trained and saved its checkpoints.
2. The val evaluation ran and is compared in a report.

Status file: `reports/pipeline/step_13.json`.
