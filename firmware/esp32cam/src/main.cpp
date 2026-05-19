#include <Arduino.h>
#include "esp_camera.h"
#include "FS.h"
#include "SD_MMC.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ============================================================================
// ESP32-CAM Imaging Subsystem Final Firmware
// Board: AI-Thinker ESP32-CAM
//
// Final workflow:
// 1. ESP32-CAM is powered ON by Arduino Nano 33.
// 2. ESP32-CAM initializes SD card first.
// 3. ESP32-CAM initializes camera second.
// 4. ESP32-CAM configures GPIO13 as LED control output.
// 5. ESP32-CAM sends ON.
// 6. Arduino Nano 33 sends:
//    DATA:T[deg C],P[hPa],C[S/cm],Activation_Flag[1/0],YYYY,MM,DD,hh,mm,ss
// 7. ESP32-CAM turns GPIO13 ON.
// 8. ESP32-CAM captures image.
// 9. ESP32-CAM saves image, raw DATA and metadata to SD card.
// 10. ESP32-CAM turns GPIO13 OFF.
// 11. ESP32-CAM sends COMPLETE.
// ============================================================================

// ============================================================================
// AI-Thinker ESP32-CAM pin configuration
// ============================================================================
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5

#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ============================================================================
// User configuration
// ============================================================================
static const uint32_t SERIAL_BAUD = 115200;

// GPIO13 is used only as a logic-level LED strip control signal.
// The LED strip current must be switched externally.
static const int LED_CONTROL_PIN = 13;
static const bool LED_ACTIVE_HIGH = true;

// SD card root folder
static const char *ROOT_DIR = "/IMAGING";

// Image capture settings
static const int JPEG_QUALITY = 12;
static const uint32_t LED_SETTLE_MS = 400;
static const int DISCARD_FRAMES = 2;

// Final deployment setting:
// false = ESP32-CAM sends ON once after successful initialization.
// true  = ESP32-CAM repeats ON every 5 seconds, useful for PC testing.
static const bool REPEAT_ON_WHILE_WAITING_FOR_DATA = false;

static const uint32_t ON_REPEAT_INTERVAL_MS = 5000;
static const uint32_t ERROR_REPEAT_INTERVAL_MS = 5000;

static uint32_t lastOnMessageMs = 0;
static uint32_t lastErrorMessageMs = 0;

static bool cameraReady = false;
static bool sdReady = false;
static String rxBuffer = "";

// ============================================================================
// CTD DATA structure
// ============================================================================
struct CtdRecord {
  String rawLine;

  float temperatureDegC;
  float pressureHPa;
  float conductivitySPerCm;
  int activationFlag;

  int year;
  int month;
  int day;
  int hour;
  int minute;
  int second;

  String timestamp;
};

// ============================================================================
// Utility functions
// ============================================================================
void setLed(bool on) {
  if (LED_ACTIVE_HIGH) {
    digitalWrite(LED_CONTROL_PIN, on ? HIGH : LOW);
  } else {
    digitalWrite(LED_CONTROL_PIN, on ? LOW : HIGH);
  }
}

void sendOnMessage() {
  Serial.println("ON");
  lastOnMessageMs = millis();
}

String csvEscape(String value) {
  value.replace("\"", "\"\"");
  return "\"" + value + "\"";
}

String makeTimestamp(
  int year,
  int month,
  int day,
  int hour,
  int minute,
  int second
) {
  char buffer[32];

  snprintf(
    buffer,
    sizeof(buffer),
    "%04d-%02d-%02d_%02d-%02d-%02d",
    year,
    month,
    day,
    hour,
    minute,
    second
  );

  return String(buffer);
}

bool validDateTime(const CtdRecord &record) {
  if (record.year < 2020 || record.year > 2099) return false;
  if (record.month < 1 || record.month > 12) return false;
  if (record.day < 1 || record.day > 31) return false;
  if (record.hour < 0 || record.hour > 23) return false;
  if (record.minute < 0 || record.minute > 59) return false;
  if (record.second < 0 || record.second > 59) return false;
  if (!(record.activationFlag == 0 || record.activationFlag == 1)) return false;

  return true;
}

bool ensureDir(const String &path) {
  if (SD_MMC.exists(path)) {
    return true;
  }

  return SD_MMC.mkdir(path);
}

bool writeTextFile(const String &path, const String &content) {
  File file = SD_MMC.open(path, FILE_WRITE);

  if (!file) {
    return false;
  }

  size_t written = file.print(content);
  file.close();

  return written == content.length();
}

bool appendTextFile(const String &path, const String &content) {
  File file = SD_MMC.open(path, FILE_APPEND);

  if (!file) {
    return false;
  }

  size_t written = file.print(content);
  file.close();

  return written == content.length();
}

// ============================================================================
// SD card initialization
// ============================================================================
bool initSdCard() {
  // GPIO13 is SD DAT3 on ESP32-CAM.
  // It must be released during SD initialization.
  pinMode(LED_CONTROL_PIN, INPUT_PULLUP);
  delay(200);

  // Mount SD card in 1-bit mode.
  // This avoids conflict with GPIO13, which is later used for LED control.
  if (!SD_MMC.begin("/sdcard", true, false, 400000)) {
    Serial.println("ERROR:SD_MOUNT_FAILED");
    return false;
  }

  uint8_t cardType = SD_MMC.cardType();

  if (cardType == CARD_NONE) {
    Serial.println("ERROR:SD_CARD_NOT_FOUND");
    return false;
  }

  if (!ensureDir(ROOT_DIR)) {
    Serial.println("ERROR:ROOT_DIR_CREATE_FAILED");
    return false;
  }

  uint64_t cardSizeMB = SD_MMC.cardSize() / (1024 * 1024);

  Serial.print("STATUS:SD_CARD_SIZE_MB:");
  Serial.println((uint32_t)cardSizeMB);

  return true;
}

// ============================================================================
// Camera initialization
// ============================================================================
bool initCamera() {
  camera_config_t config;
  memset(&config, 0, sizeof(config));

  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;

  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;

  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;

  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;

  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // Final report image size
  config.frame_size   = FRAMESIZE_240X240;
  config.jpeg_quality = JPEG_QUALITY;

  if (psramFound()) {
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&config);

  if (err != ESP_OK) {
    Serial.print("ERROR:CAMERA_INIT_FAILED:");
    Serial.println((uint32_t)err, HEX);
    return false;
  }

  sensor_t *sensor = esp_camera_sensor_get();

  if (sensor != nullptr) {
    sensor->set_framesize(sensor, FRAMESIZE_240X240);
    sensor->set_quality(sensor, JPEG_QUALITY);

    // Uncomment only if your mounted camera image is upside down or mirrored.
    // sensor->set_vflip(sensor, 1);
    // sensor->set_hmirror(sensor, 1);
  }

  return true;
}

// ============================================================================
// DATA parsing
// ============================================================================
bool parseDataLine(const String &input, CtdRecord &record) {
  String line = input;
  line.trim();

  if (!line.startsWith("DATA:")) {
    return false;
  }

  String payload = line.substring(5);
  payload.trim();

  char buffer[180];
  payload.toCharArray(buffer, sizeof(buffer));

  char *tokens[10];
  int count = 0;

  char *savePtr = nullptr;
  char *token = strtok_r(buffer, ",", &savePtr);

  while (token != nullptr && count < 10) {
    while (*token == ' ') {
      token++;
    }

    tokens[count++] = token;
    token = strtok_r(nullptr, ",", &savePtr);
  }

  if (count != 10 || token != nullptr) {
    return false;
  }

  record.rawLine = line;

  record.temperatureDegC     = atof(tokens[0]);
  record.pressureHPa         = atof(tokens[1]);
  record.conductivitySPerCm  = atof(tokens[2]);
  record.activationFlag      = atoi(tokens[3]);

  record.year   = atoi(tokens[4]);
  record.month  = atoi(tokens[5]);
  record.day    = atoi(tokens[6]);
  record.hour   = atoi(tokens[7]);
  record.minute = atoi(tokens[8]);
  record.second = atoi(tokens[9]);

  record.timestamp = makeTimestamp(
    record.year,
    record.month,
    record.day,
    record.hour,
    record.minute,
    record.second
  );

  return validDateTime(record);
}

// ============================================================================
// Folder and filename generation
// ============================================================================
bool prepareFolders(
  const CtdRecord &record,
  String &sessionDir,
  String &eventDir
) {
  sessionDir = String(ROOT_DIR) + "/session_" + record.timestamp;

  if (record.activationFlag == 1) {
    eventDir = sessionDir + "/post_activation";
  } else {
    eventDir = sessionDir + "/baseline";
  }

  if (!ensureDir(ROOT_DIR)) return false;
  if (!ensureDir(sessionDir)) return false;
  if (!ensureDir(eventDir)) return false;

  return true;
}

String makeUniqueBaseName(const String &eventDir, const String &timestamp) {
  String baseName = "photo_" + timestamp;
  String imagePath = eventDir + "/" + baseName + ".jpg";

  if (!SD_MMC.exists(imagePath)) {
    return baseName;
  }

  for (int i = 2; i < 100; i++) {
    char suffix[8];
    snprintf(suffix, sizeof(suffix), "_%02d", i);

    String candidate = "photo_" + timestamp + String(suffix);
    imagePath = eventDir + "/" + candidate + ".jpg";

    if (!SD_MMC.exists(imagePath)) {
      return candidate;
    }
  }

  return "photo_" + timestamp + "_overflow";
}

// ============================================================================
// Serial line reader
// ============================================================================
bool readSerialLine(String &lineOut) {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n') {
      rxBuffer.trim();

      if (rxBuffer.length() == 0) {
        rxBuffer = "";
        return false;
      }

      lineOut = rxBuffer;
      rxBuffer = "";
      return true;
    }

    if (c != '\r') {
      if (rxBuffer.length() < 170) {
        rxBuffer += c;
      } else {
        rxBuffer = "";
        Serial.println("ERROR:DATA_TOO_LONG");
        return false;
      }
    }
  }

  return false;
}

// ============================================================================
// Image capture and storage
// ============================================================================
bool captureAndSaveImage(const String &imagePath, size_t &imageBytes) {
  imageBytes = 0;

  // Discard frames so exposure can settle after LED turns ON.
  for (int i = 0; i < DISCARD_FRAMES; i++) {
    camera_fb_t *discardFb = esp_camera_fb_get();

    if (discardFb != nullptr) {
      esp_camera_fb_return(discardFb);
    }

    delay(100);
  }

  camera_fb_t *fb = esp_camera_fb_get();

  if (fb == nullptr) {
    Serial.println("ERROR:CAMERA_CAPTURE_FAILED");
    return false;
  }

  File imageFile = SD_MMC.open(imagePath, FILE_WRITE);

  if (!imageFile) {
    esp_camera_fb_return(fb);
    Serial.println("ERROR:IMAGE_FILE_OPEN_FAILED");
    return false;
  }

  size_t written = imageFile.write(fb->buf, fb->len);
  imageFile.close();

  imageBytes = fb->len;
  esp_camera_fb_return(fb);

  if (written != imageBytes) {
    Serial.println("ERROR:IMAGE_FILE_WRITE_INCOMPLETE");
    return false;
  }

  return true;
}

// ============================================================================
// Metadata generation
// ============================================================================
String makeMetadataCsv(
  const CtdRecord &record,
  const String &imagePath,
  const String &rawDataPath,
  const String &status,
  size_t imageBytes
) {
  String content = "";

  content += "field,value\n";
  content += "raw_data_line," + csvEscape(record.rawLine) + "\n";
  content += "timestamp," + record.timestamp + "\n";
  content += "temperature_degC," + String(record.temperatureDegC, 3) + "\n";
  content += "pressure_hPa," + String(record.pressureHPa, 3) + "\n";
  content += "conductivity_S_per_cm," + String(record.conductivitySPerCm, 6) + "\n";
  content += "activation_flag," + String(record.activationFlag) + "\n";
  content += "event_type," + String(record.activationFlag == 1 ? "post_activation" : "baseline") + "\n";
  content += "capture_status," + status + "\n";
  content += "image_path," + csvEscape(imagePath) + "\n";
  content += "raw_data_path," + csvEscape(rawDataPath) + "\n";
  content += "image_bytes," + String(imageBytes) + "\n";
  content += "frame_size,240x240\n";
  content += "image_format,JPEG\n";
  content += "jpeg_quality," + String(JPEG_QUALITY) + "\n";
  content += "led_control_gpio," + String(LED_CONTROL_PIN) + "\n";
  content += "led_settle_ms," + String(LED_SETTLE_MS) + "\n";

  return content;
}

void appendGlobalMetadata(
  const CtdRecord &record,
  const String &sessionDir,
  const String &eventDir,
  const String &imagePath,
  const String &metadataPath,
  const String &status,
  size_t imageBytes
) {
  String globalPath = String(ROOT_DIR) + "/all_metadata.csv";

  if (!SD_MMC.exists(globalPath)) {
    String header = "";
    header += "timestamp,temperature_degC,pressure_hPa,conductivity_S_per_cm,";
    header += "activation_flag,event_type,session_dir,event_dir,image_path,";
    header += "metadata_path,capture_status,image_bytes,raw_data_line\n";

    appendTextFile(globalPath, header);
  }

  String row = "";
  row += record.timestamp + ",";
  row += String(record.temperatureDegC, 3) + ",";
  row += String(record.pressureHPa, 3) + ",";
  row += String(record.conductivitySPerCm, 6) + ",";
  row += String(record.activationFlag) + ",";
  row += String(record.activationFlag == 1 ? "post_activation" : "baseline") + ",";
  row += csvEscape(sessionDir) + ",";
  row += csvEscape(eventDir) + ",";
  row += csvEscape(imagePath) + ",";
  row += csvEscape(metadataPath) + ",";
  row += status + ",";
  row += String(imageBytes) + ",";
  row += csvEscape(record.rawLine) + "\n";

  appendTextFile(globalPath, row);
}

// ============================================================================
// Acquisition workflow
// ============================================================================
void handleDataRecord(const CtdRecord &record) {
  Serial.println("STATUS:DATA_RECEIVED");

  String sessionDir;
  String eventDir;

  if (!prepareFolders(record, sessionDir, eventDir)) {
    setLed(false);
    Serial.println("ERROR:SD_FOLDER_CREATE_FAILED");
    return;
  }

  String baseName = makeUniqueBaseName(eventDir, record.timestamp);

  String imagePath    = eventDir + "/" + baseName + ".jpg";
  String rawDataPath  = eventDir + "/" + baseName + "_raw_data.txt";
  String metadataPath = eventDir + "/" + baseName + "_metadata.csv";

  bool rawSaved = writeTextFile(rawDataPath, record.rawLine + "\n");

  if (!rawSaved) {
    Serial.println("ERROR:RAW_DATA_SAVE_FAILED");
  }

  size_t imageBytes = 0;
  bool imageSaved = false;

  Serial.println("STATUS:LED_ON");
  setLed(true);
  delay(LED_SETTLE_MS);

  if (rawSaved) {
    Serial.println("STATUS:CAPTURING");
    imageSaved = captureAndSaveImage(imagePath, imageBytes);
  }

  setLed(false);
  Serial.println("STATUS:LED_OFF");

  String status = imageSaved ? "OK" : "FAILED";

  String metadata = makeMetadataCsv(
    record,
    imagePath,
    rawDataPath,
    status,
    imageBytes
  );

  bool metadataSaved = writeTextFile(metadataPath, metadata);

  appendGlobalMetadata(
    record,
    sessionDir,
    eventDir,
    imagePath,
    metadataPath,
    status,
    imageBytes
  );

  if (!metadataSaved) {
    Serial.println("ERROR:METADATA_SAVE_FAILED");
    return;
  }

  if (imageSaved) {
    Serial.print("STATUS:IMAGE_SAVED:");
    Serial.println(imagePath);

    Serial.print("STATUS:METADATA_SAVED:");
    Serial.println(metadataPath);

    Serial.println("COMPLETE");
  } else {
    Serial.println("ERROR:CAPTURE_FAILED");
  }
}

// ============================================================================
// Arduino setup
// ============================================================================
void setup() {
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

  Serial.begin(SERIAL_BAUD);
  delay(1500);

  // Release GPIO13 before SD initialization.
  // GPIO13 is shared with SD DAT3 on ESP32-CAM.
  pinMode(LED_CONTROL_PIN, INPUT_PULLUP);

  Serial.println();
  Serial.println("BOOT:ESP32CAM_IMAGING_FINAL");
  Serial.println("STATUS:INIT_START");

  // SD first. This avoids GPIO13 and SD startup conflicts.
  sdReady = initSdCard();

  if (sdReady) {
    Serial.println("STATUS:SD_OK");
  }

  // Camera second.
  cameraReady = initCamera();

  if (cameraReady) {
    Serial.println("STATUS:CAMERA_OK");
  }

  // GPIO13 becomes LED control only after SD initialization.
  pinMode(LED_CONTROL_PIN, OUTPUT);
  setLed(false);

  if (cameraReady && sdReady) {
    Serial.println("STATUS:READY");
    sendOnMessage();
  } else {
    Serial.println("STATUS:NOT_READY");

    Serial.print("ERR:CAM=");
    Serial.print(cameraReady ? "OK" : "FAIL");
    Serial.print(",SD=");
    Serial.println(sdReady ? "OK" : "FAIL");
  }
}

// ============================================================================
// Arduino loop
// ============================================================================
void loop() {
  uint32_t now = millis();

  if (!cameraReady || !sdReady) {
    setLed(false);

    if (now - lastErrorMessageMs >= ERROR_REPEAT_INTERVAL_MS) {
      Serial.print("ERR:CAM=");
      Serial.print(cameraReady ? "OK" : "FAIL");
      Serial.print(",SD=");
      Serial.println(sdReady ? "OK" : "FAIL");

      lastErrorMessageMs = now;
    }

    delay(10);
    return;
  }

  if (REPEAT_ON_WHILE_WAITING_FOR_DATA) {
    if (now - lastOnMessageMs >= ON_REPEAT_INTERVAL_MS) {
      sendOnMessage();
    }
  }

  String line;

  if (readSerialLine(line)) {
    CtdRecord record;

    if (!parseDataLine(line, record)) {
      setLed(false);
      Serial.println("ERROR:BAD_DATA");
      Serial.println("STATUS:EXPECTED:DATA:T,P,C,Activation_Flag,YYYY,MM,DD,hh,mm,ss");
      return;
    }

    handleDataRecord(record);

    lastOnMessageMs = millis();
  }

  delay(10);
}