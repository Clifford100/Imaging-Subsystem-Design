# Imaging Subsystem Design

This repository contains the final organised software package for the **Imaging Subsystem** of the sediment-trap based particle monitoring project.

The project is organised into three main parts:

1. **Firmware** for the ESP32-CAM image capture system.
2. **GUI** for viewing captured images and image-processing results.
3. **Analysis** scripts for particle detection, classification and measurement.

The subsystem captures viewport images using an ESP32-CAM, saves the image and metadata to a microSD card, and supports PC-based post-processing using Python and OpenCV.

---

## Project Overview

The Imaging Subsystem was developed to support controlled testing of visible particles inside a sediment-trap imaging setup.

The system performs the following functions:

- Captures images using an ESP32-CAM.
- Receives CTD/event data over serial communication.
- Uses GPIO13 as the LED-control signal.
- Saves image files and metadata to a microSD card.
- Organises captured data into timestamped folders.
- Provides a GUI for selecting and processing captured images.
- Runs image analysis to detect and classify particles as:
  - `circle`
  - `rod`
  - `irregular`

The final analysis outputs include annotated overlays, particle masks, preview grids, measurement tables and summary CSV files.

---

## Repository Structure

The final repository structure is:

```text
Imaging-Subsystem-Design/
├── .github/
├── .pio/
├── .vscode/
├── analysis/
├── firmware/
├── gui/
├── .gitattributes
├── .gitignore
└── README.md
```

### Folder Descriptions

| Folder/File | Description |
|---|---|
| `.github/` | GitHub configuration files, if used. |
| `.pio/` | PlatformIO build folder generated locally. This should usually not be committed. |
| `.vscode/` | VS Code workspace settings. |
| `analysis/` | Python/OpenCV particle-analysis scripts, input folders and output folders. |
| `firmware/` | ESP32-CAM firmware, PlatformIO project files, serial test tools and firmware-related extras. |
| `gui/` | Web-based graphical interface for selecting images and viewing processing results. |
| `.gitattributes` | Git line-ending and repository handling rules. |
| `.gitignore` | Files and folders excluded from Git tracking. |
| `README.md` | Main project documentation. |

---

## Main Project Components

## 1. Firmware

The firmware folder contains the ESP32-CAM code used to capture images and save metadata.

Typical firmware responsibilities:

1. Initialise the ESP32-CAM.
2. Mount the SD card in 1-bit mode.
3. Initialise the camera.
4. Configure GPIO13 as the LED-control signal.
5. Send `ON` over serial when ready.
6. Wait for a serial `DATA:` line.
7. Turn the LED signal ON.
8. Capture an image.
9. Save the image, raw serial data and metadata to the SD card.
10. Turn the LED signal OFF.
11. Send `COMPLETE` after a successful capture.

### Upload Wiring

For uploading only:

```text
USB-to-Serial  ->  ESP32-CAM
5V             ->  5V
GND            ->  GND
TX             ->  U0R / RX
RX             ->  U0T / TX
GND            ->  GPIO0
```

GPIO0 must be connected to GND only while uploading.

After uploading, remove GPIO0 from GND before running the firmware.

### Upload Firmware

Open PowerShell in the firmware folder that contains `platformio.ini`.

Example:

```powershell
cd firmware
```

If `platformio.ini` is inside a nested folder, enter that folder instead.

Then upload using:

```powershell
pio run -t clean
pio run -t upload
```

After upload:

1. Disconnect power.
2. Remove GPIO0 from GND.
3. Reconnect power.
4. Press `RST`.

If GPIO0 remains connected to GND, the firmware will not run.

---

## Serial Monitor Test

After uploading the firmware and removing GPIO0 from GND, open the serial monitor:

```powershell
pio device monitor -p COM8 -b 115200
```

Then press `RST`.

Expected output:

```text
BOOT:ESP32CAM_IMAGING_FINAL
STATUS:INIT_START
STATUS:SD_CARD_SIZE_MB:xxxx
STATUS:SD_OK
STATUS:CAMERA_OK
STATUS:READY
ON
```

The exact SD-card size depends on the card used.

---

## Serial DATA Format

The ESP32-CAM expects the following serial data format:

```text
DATA:T,P,C,Activation_Flag,YYYY,MM,DD,hh,mm,ss
```

Example:

```text
DATA:21,1000,5,1,2026,05,12,13,53,22
```

| Field | Meaning |
|---|---|
| `T` | Temperature in degrees Celsius |
| `P` | Pressure in hPa |
| `C` | Conductivity in S/cm |
| `Activation_Flag` | `1` for post-activation, `0` for baseline |
| `YYYY` | Year |
| `MM` | Month |
| `DD` | Day |
| `hh` | Hour |
| `mm` | Minute |
| `ss` | Second |

---

## SD Card Output Structure

The ESP32-CAM saves captured data to the SD card using this structure:

```text
/IMAGING/
├── all_metadata.csv
└── session_YYYY-MM-DD_hh-mm-ss/
    └── post_activation/
        ├── photo_YYYY-MM-DD_hh-mm-ss.jpg
        ├── photo_YYYY-MM-DD_hh-mm-ss_raw_data.txt
        └── photo_YYYY-MM-DD_hh-mm-ss_metadata.csv
```

For baseline data, the folder is:

```text
/IMAGING/
└── session_YYYY-MM-DD_hh-mm-ss/
    └── baseline/
        ├── photo_YYYY-MM-DD_hh-mm-ss.jpg
        ├── photo_YYYY-MM-DD_hh-mm-ss_raw_data.txt
        └── photo_YYYY-MM-DD_hh-mm-ss_metadata.csv
```

The `all_metadata.csv` file stores a combined log of all captured records.

---

## GPIO13 and SD Card Note

GPIO13 is used as the LED-control signal.

On the ESP32-CAM, GPIO13 is also associated with the SD-card interface. To avoid conflict, the firmware mounts the SD card in 1-bit mode:

```cpp
SD_MMC.begin("/sdcard", true)
```

This avoids the normal 4-bit SD mode conflict with GPIO13.

GPIO13 should only be used as a logic-level control signal. The LED strip should be powered through a suitable external switching circuit and not directly from the GPIO pin.

---

## 2. GUI

The `gui/` folder contains the web-based image-processing interface.

The GUI is used to:

- Select captured images.
- Display processing stages.
- Show intermediate outputs.
- Display metadata where available.
- Use default/example metadata if metadata is unavailable.
- Present particle-analysis results in a user-friendly format.

### Run the GUI

Open PowerShell in the repository root:

```powershell
cd "C:\Users\Clifford\Desktop\Imaging-Subsystem-Design"
```

Then enter the GUI folder:

```powershell
cd gui
```

Install the required packages:

```powershell
pip install -r requirements.txt
```

Run the GUI:

```powershell
python app.py
```

If the GUI is inside a nested folder such as `gui/imaging_gui/`, run:

```powershell
cd gui\imaging_gui
pip install -r requirements.txt
python app.py
```

Then open the browser link printed in the terminal, usually:

```text
http://127.0.0.1:5000
```

To stop the GUI, press:

```text
Ctrl + C
```

---

## 3. Analysis

The `analysis/` folder contains the Python/OpenCV image-analysis pipeline.

The analysis scripts generate outputs dynamically for each run. The final code avoids temporary names such as:

```text
three_shape_star_improved_run01
star_improved
test_final_final
```

Instead, use clean output names such as:

```text
run_001
run_002
controlled_test_001
raw_data_run_001
```

### Analysis Outputs

The analysis pipeline can generate:

- Grayscale images.
- Difference maps.
- Edge maps.
- Particle masks.
- Annotated overlays.
- Preview grids.
- Debug images.
- Particle measurement CSV files.
- Image summary CSV files.
- Run configuration JSON files.

Example output folder:

```text
analysis/outputs/run_001/
├── run_config.json
├── median_background.png
├── particle_measurements.csv
├── image_summary.csv
├── grayscale/
├── difference_map/
├── edges/
├── particle_mask/
├── annotated_overlay/
├── preview_grid/
└── debug/
```

### Run the Analysis

From the repository root:

```powershell
cd "C:\Users\Clifford\Desktop\Imaging-Subsystem-Design"
```

Install the required packages:

```powershell
pip install -r analysis\requirements.txt
```

If there is no `analysis\requirements.txt`, install the core packages manually:

```powershell
pip install opencv-python numpy
```

Run the analysis script:

```powershell
python analysis\scripts\particle_analysis.py ^
  --input-dir "analysis\input\sample_images" ^
  --background-dir "analysis\input\background_images" ^
  --output-dir "analysis\outputs\run_001" ^
  --fov-width-mm 85 ^
  --fov-height-mm 85 ^
  --min-area-px 45 ^
  --ignore-top-right-corner-px 34
```

The field of view values are based on the imaging setup where the 240 x 240 image represents an 85 mm x 85 mm region.

---

## Python Dependencies

The Python-based GUI and analysis code may require:

```text
flask
opencv-python
numpy
pyserial
```

Install all available requirements using:

```powershell
pip install -r gui\requirements.txt
pip install -r analysis\requirements.txt
```

If one of those files does not exist, install the packages manually:

```powershell
pip install flask opencv-python numpy pyserial
```

---

## PlatformIO Dependencies

The firmware uses:

- PlatformIO
- Arduino framework for ESP32
- AI-Thinker ESP32-CAM board configuration

Typical `platformio.ini` settings include:

```ini
[env:esp32cam]
platform = espressif32
board = esp32cam
framework = arduino

monitor_speed = 115200
upload_speed = 115200

monitor_rts = 0
monitor_dtr = 0

build_flags =
    -DCORE_DEBUG_LEVEL=0
```

---

## Recommended Run Order

Use this order when testing the full system:

```text
1. Upload the ESP32-CAM firmware.
2. Open the serial monitor.
3. Confirm BOOT, SD_OK, CAMERA_OK, READY and ON.
4. Send a DATA line using the serial tester or another controller.
5. Confirm COMPLETE is printed.
6. Check that images and metadata were saved to the SD card.
7. Copy captured images to the GUI or analysis input folder.
8. Run the GUI to inspect images.
9. Run the analysis script to generate masks, overlays and CSV results.
```

---

## Git Notes

Generated files should usually not be committed.

The `.gitignore` should exclude:

```text
.pio/
__pycache__/
*.pyc
*.log
```

Before pushing to GitHub, check the repository status:

```powershell
git status
```

Then commit and push:

```powershell
git add .
git commit -m "Update final imaging subsystem repository structure"
git push
```

---

## Troubleshooting

### `fatal: not a git repository`

Run:

```powershell
git init
```

from the repository root folder.

### Upload fails

Check that:

- GPIO0 is connected to GND.
- The ESP32-CAM was reset after connecting GPIO0 to GND.
- TX and RX are crossed correctly.
- The correct COM port is selected.

### Firmware does not run after upload

Check that:

- GPIO0 was removed from GND.
- Power was disconnected and reconnected.
- `RST` was pressed after reconnecting power.

### SD card fails

Check that:

- The SD card is formatted correctly.
- The SD card is inserted properly.
- The ESP32-CAM has stable 5 V power.
- The firmware is using 1-bit SD mode because GPIO13 is used for LED control.

### Camera fails

Check that:

- The ESP32-CAM camera ribbon cable is seated correctly.
- The board has stable 5 V power.
- The selected PlatformIO board is `esp32cam`.

### GUI does not run

Check that:

- Python is installed.
- Required packages are installed.
- The terminal is opened in the correct GUI folder.
- `app.py` exists in the folder being used.

### Analysis gives poor detections

Check that:

- The background images match the test-image lighting condition.
- The field of view values are correct.
- The top-right LED/reflection exclusion value is suitable.
- Images are not overexposed.
- Particles are not too close to strong lighting artefacts.

---

## Project Status

This repository is the final organised software package for the Imaging Subsystem. It contains the firmware, GUI and analysis workflow required to capture, inspect and process ESP32-CAM particle images.
