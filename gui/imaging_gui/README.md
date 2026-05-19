# Imaging Subsystem GUI

A functional Flask GUI for selecting captured ESP32-CAM images, selecting or uploading background/reference images, running the PC-based particle-processing pipeline, displaying all processing stages and showing metadata/results.

## Folder structure

```text
imaging_gui/
├── app.py
├── captured_images/       # Put ESP32-CAM particle images here
├── background_images/     # Put empty-viewport/background images here
├── outputs/               # Generated processing stages and CSV files
├── requirements.txt
├── templates/
│   └── index.html
└── static/
    ├── css/style.css
    └── js/app.js
```

## Setup

```bash
cd imaging_gui
python -m venv .venv
.venv\Scripts\activate      # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## How to organise images

Put captured particle images here:

```text
captured_images/
```

Put background/reference images here:

```text
background_images/
```

A background image should be an empty-viewport image taken with the same lighting, camera position and viewport setup as the particle image.

Recommended naming options:

```text
background_images/background.png
background_images/reference.png
background_images/viewport_background.png
```

Or use matching names when a background belongs to one image:

```text
captured_images/photo_0013.jpg
background_images/photo_0013.png
```

## Background selection logic

In the GUI, the background dropdown supports Auto mode. Auto mode selects the background in this order:

1. A background with the same filename stem as the captured image.
2. A common background name such as `background.png`, `reference.png` or `viewport_background.png`.
3. The first image found in `background_images/`.
4. If no background image exists, the app falls back to estimating the background from the selected image.

## Metadata format

The GUI looks for metadata files next to the selected image using names such as:

```text
photo_0001.txt
photo_0001.csv
photo_0001.json
metadata.txt
metadata.csv
metadata.json
```

For text/CSV metadata, it searches for a DATA string:

```text
DATA:21,1000,5,1,2026,05,12,13,53,22
```

If metadata is not available, that example string is used as the default.

## Detector version used in this GUI

This updated GUI keeps the same front-end appearance, but the backend processing now follows the proven `particle_stage2_three_shapes.py` detector structure:

- grayscale-only detection
- median background model from `background_images/`
- background difference map
- Otsu thresholding with a minimum threshold
- Canny edge detection and filled edge contours
- top-right LED/reflection artefact exclusion only
- shape classes: `circle`, `rod`, and `irregular`
- CSV exports: `three_shape_measurements.csv` and `three_shape_summary.csv`

## Background image use

Place one or more empty-viewport images in:

```text
background_images/
```

When the GUI is set to **Auto select background image**, it builds a median background model from all images in `background_images/`. This follows the proven command-line script approach where the background folder is used to form the reference background.

If a specific background is selected in the GUI, only that image is used as the background reference.

