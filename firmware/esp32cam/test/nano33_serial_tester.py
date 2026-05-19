#!/usr/bin/env python3
"""
PC-side Nano 33 serial simulator for the ESP32-CAM imaging firmware.

The real final system expects the Arduino Nano 33 to power the ESP32-CAM and
send a DATA line after the ESP32-CAM prints ON. This script simulates that
serial exchange from a PC for repeatable testing.
"""

import argparse
import sys
import time
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:
    raise SystemExit("pyserial is required. Install with: pip install pyserial") from exc


def list_available_ports() -> None:
    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return

    print("Available serial ports:")
    for p in ports:
        print(f"  {p.device:10s}  {p.description}")


def read_test_lines(path: Path) -> list[str]:
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def wait_for_token(ser: serial.Serial, token: str, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        text = raw.decode(errors="replace").strip()
        if text:
            print(f"ESP32-CAM -> PC: {text}")
        if token in text:
            return True
    return False


def run_test(port: str, baud: int, data_path: Path, timeout_s: float) -> int:
    lines = read_test_lines(data_path)
    if not lines:
        print(f"No DATA lines found in {data_path}")
        return 2

    with serial.Serial(port, baudrate=baud, timeout=0.5) as ser:
        time.sleep(2.0)
        ser.reset_input_buffer()

        print(f"Connected to {port} at {baud} baud.")
        print("Waiting for ESP32-CAM ON message...")

        if not wait_for_token(ser, "ON", timeout_s):
            print("FAIL: Did not receive ON from ESP32-CAM.")
            return 1

        for data_line in lines:
            payload = data_line if data_line.startswith("DATA:") else f"DATA:{data_line}"
            print(f"PC -> ESP32-CAM: {payload}")
            ser.write((payload + "\n").encode("utf-8"))
            ser.flush()

            if wait_for_token(ser, "COMPLETE", timeout_s):
                print("PASS: ESP32-CAM saved the image and metadata.")
            else:
                print("FAIL: COMPLETE was not received for this DATA line.")
                return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test ESP32-CAM serial DATA workflow")
    parser.add_argument("--list-ports", action="store_true", help="List available serial ports and exit")
    parser.add_argument("--port", help="Serial port, for example COM8 on Windows or /dev/ttyUSB0 on Linux")
    parser.add_argument("--baud", type=int, default=115200, help="Serial baud rate")
    parser.add_argument("--data", default="esp32cam_test_data.txt", help="Text file containing DATA payloads")
    parser.add_argument("--timeout", type=float, default=20.0, help="Timeout in seconds while waiting for ON/COMPLETE")
    args = parser.parse_args()

    if args.list_ports:
        list_available_ports()
        return 0

    if not args.port:
        parser.error("--port is required unless --list-ports is used")

    return run_test(args.port, args.baud, Path(args.data), args.timeout)


if __name__ == "__main__":
    sys.exit(main())
