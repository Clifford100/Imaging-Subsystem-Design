# ESP32-CAM Firmware

This folder contains the PlatformIO firmware for the AI-Thinker ESP32-CAM.

## Build and Upload

```powershell
pio run -t clean
pio run -t upload
```

## Serial Monitor

```powershell
pio device monitor -p COM8 -b 115200
```

## Expected Runtime Flow

1. ESP32-CAM initializes SD card in 1-bit mode.
2. ESP32-CAM initializes the camera.
3. ESP32-CAM prints `ON` when ready.
4. Controller sends a `DATA:` record.
5. ESP32-CAM turns GPIO13 ON, captures an image and saves metadata.
6. ESP32-CAM turns GPIO13 OFF and prints `COMPLETE`.
