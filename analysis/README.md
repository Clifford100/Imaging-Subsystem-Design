# Particle Analysis

This folder contains the OpenCV particle-analysis pipeline.

## Input Folders

```text
input/background_images/  background/no-particle images
input/sample_images/      captured particle images
```

## Run

```powershell
python scripts\particle_analysis.py `
  --input-dir input\sample_images `
  --background-dir inputackground_images `
  --output-dir outputsun_001 `
  --fov-width-mm 85 `
  --fov-height-mm 85 `
  --min-area-px 45 `
  --ignore-top-right-corner-px 34
```

## Output Files

- `particle_measurements.csv` — per-particle measurements
- `image_summary.csv` — per-image shape counts and total particle areas
- `run_config.json` — reproducibility record for the run
- `annotated_overlay/` — labelled particle overlays
- `preview_grid/` — combined visual debugging summaries
