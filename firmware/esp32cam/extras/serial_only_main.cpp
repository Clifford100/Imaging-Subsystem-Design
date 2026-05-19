#include <Arduino.h>

// Serial-only diagnostic firmware for the ESP32-CAM.
// Use this file when upload/serial communication must be tested separately
// from the camera and SD-card hardware.

static const uint32_t SERIAL_BAUD = 115200;
static const uint32_t ON_INTERVAL_MS = 5000;
static uint32_t lastOnMs = 0;
String rxBuffer = "";

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1500);
  Serial.println();
  Serial.println("BOOT:ESP32CAM_SERIAL_ONLY_TEST");
  Serial.println("STATUS:READY");
  Serial.println("ON");
  lastOnMs = millis();
}

void loop() {
  if (millis() - lastOnMs >= ON_INTERVAL_MS) {
    Serial.println("ON");
    lastOnMs = millis();
  }

  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '
') {
      rxBuffer.trim();
      if (rxBuffer.length() > 0) {
        Serial.print("STATUS:RECEIVED:");
        Serial.println(rxBuffer);

        if (rxBuffer.startsWith("DATA:")) {
          Serial.println("COMPLETE");
        } else {
          Serial.println("ERROR:EXPECTED_DATA_LINE");
        }
      }
      rxBuffer = "";
    } else if (c != '') {
      rxBuffer += c;
    }
  }

  delay(10);
}
