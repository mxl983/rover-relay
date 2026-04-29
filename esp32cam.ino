// Work around `sensor_t` type conflict between esp32-camera and Adafruit_Sensor.
#define sensor_t camera_sensor_t
#include "esp_camera.h"
#undef sensor_t
#include <WiFi.h>
#include "esp_http_server.h"
#include <Wire.h>
#include <Adafruit_BME280.h>
#include <Adafruit_BMP280.h>

// ======================================================
// 1. WIFI SETTINGS
// ======================================================
const char *ssid_Router     = "TELUS8139";
const char *password_Router = "5Gh2X3GcJvfk";

// ======================================================
// 2. FREENOVE WROVER PIN MAPPING (Manual - No Files Needed)
// ======================================================
#define PWDN_GPIO_NUM    -1  // Freenove Wrover doesn't use PWDN
#define RESET_GPIO_NUM   -1
#define XCLK_GPIO_NUM    21
#define SIOD_GPIO_NUM    26
#define SIOC_GPIO_NUM    27

#define Y9_GPIO_NUM      35
#define Y8_GPIO_NUM      34
#define Y7_GPIO_NUM      39
#define Y6_GPIO_NUM      36
#define Y5_GPIO_NUM      19
#define Y4_GPIO_NUM      18
#define Y3_GPIO_NUM      5
#define Y2_GPIO_NUM      4
#define VSYNC_GPIO_NUM   25
#define HREF_GPIO_NUM    23
#define PCLK_GPIO_NUM    22

// --- Stream Logic Variables ---
httpd_handle_t stream_httpd = NULL;
httpd_handle_t control_httpd = NULL;

// ======================================================
// 3. BME280 (SAFE I2C PINS)
// ======================================================
#define BME_SDA_PIN 13
#define BME_SCL_PIN 14
Adafruit_BME280 bme;
Adafruit_BMP280 bmp;
bool bmeReady = false;
bool bmpReady = false;
uint8_t bmeAddress = 0;

int readI2CRegister(uint8_t addr, uint8_t reg) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom((int)addr, 1) != 1) return -1;
  return (int)Wire.read();
}

bool initBme280() {
  // Some BME280 boards use 0x76, others use 0x77 depending on SDO wiring.
  if (bme.begin(0x76, &Wire)) {
    bmeReady = true;
    bmpReady = false;
    bmeAddress = 0x76;
    return true;
  }
  if (bme.begin(0x77, &Wire)) {
    bmeReady = true;
    bmpReady = false;
    bmeAddress = 0x77;
    return true;
  }
  bmeReady = false;
  bmeAddress = 0;
  return false;
}

bool initBmp280() {
  if (bmp.begin(0x76, 0x58)) {
    bmpReady = true;
    bmeAddress = 0x76;
    return true;
  }
  if (bmp.begin(0x77, 0x58)) {
    bmpReady = true;
    bmeAddress = 0x77;
    return true;
  }
  bmpReady = false;
  return false;
}

void scanI2CBus() {
  Serial.println("I2C scan start...");
  uint8_t found = 0;
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    uint8_t err = Wire.endTransmission();
    if (err == 0) {
      Serial.printf("I2C device found at 0x%02X\n", addr);
      found++;
    }
  }
  if (found == 0) {
    Serial.println("No I2C devices found.");
  } else {
    Serial.printf("I2C scan complete, found %u device(s).\n", found);
  }
}

// ======================================================
// 4. VOLTAGE READ SETTINGS
// ======================================================
// Use an ADC1 pin that is NOT used by camera pins above.
// Example: GPIO33 through a resistor divider from battery+ to GND.
#define VOLTAGE_ADC_PIN         33
#define ADC_SAMPLES             16

// Divider ratio = (Rtop + Rbottom) / Rbottom
// Example: Rtop=100k, Rbottom=27k  => ratio ~= 4.7037
const float VOLTAGE_DIVIDER_RATIO = 4.7037f;

// Optional calibration trim (1.0 = no trim). Use this to match multimeter.
const float VOLTAGE_CALIBRATION = 1.000f;

// ======================================================
// 5. LOW LATENCY STREAM HANDLER
// ======================================================
esp_err_t stream_handler(httpd_req_t *req) {
  camera_fb_t * fb = NULL;
  esp_err_t res = ESP_OK;
  char * part_buf[64];

  res = httpd_resp_set_type(req, "multipart/x-mixed-replace;boundary=frame");
  if (res != ESP_OK) return res;

  while (true) {
    fb = esp_camera_fb_get();
    if (!fb) {
      res = ESP_FAIL;
    } else {
      size_t hlen = snprintf((char *)part_buf, 64, "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n", fb->len);
      res = httpd_resp_send_chunk(req, (const char *)part_buf, hlen);
      if (res == ESP_OK) res = httpd_resp_send_chunk(req, (const char *)fb->buf, fb->len);
      if (res == ESP_OK) res = httpd_resp_send_chunk(req, "\r\n--frame\r\n", 11);
      esp_camera_fb_return(fb);
    }
    if (res != ESP_OK) break;
  }
  return res;
}

void readBatteryVoltage(float &batteryV, float &adcMvAvg, uint16_t &adcRawMin, uint16_t &adcRawMax) {
  uint32_t totalMv = 0;
  uint32_t totalRaw = 0;
  uint16_t rawMin = 4095;
  uint16_t rawMax = 0;
  for (int i = 0; i < ADC_SAMPLES; i++) {
    const uint16_t raw = (uint16_t)analogRead(VOLTAGE_ADC_PIN);
    if (raw < rawMin) rawMin = raw;
    if (raw > rawMax) rawMax = raw;
    totalRaw += raw;
    totalMv += (uint32_t)analogReadMilliVolts(VOLTAGE_ADC_PIN);
    delay(2);
  }
  adcMvAvg = (float)totalMv / (float)ADC_SAMPLES;
  batteryV = (adcMvAvg / 1000.0f) * VOLTAGE_DIVIDER_RATIO * VOLTAGE_CALIBRATION;
  adcRawMin = rawMin;
  adcRawMax = rawMax;
  (void)totalRaw; // Keep for quick future expansion/debug if needed.
}

esp_err_t voltage_handler(httpd_req_t *req) {
  float batteryV = 0.0f;
  float adcMvAvg = 0.0f;
  uint16_t adcRawMin = 0;
  uint16_t adcRawMax = 0;
  readBatteryVoltage(batteryV, adcMvAvg, adcRawMin, adcRawMax);
  char body[256];
  snprintf(
    body,
    sizeof(body),
    "{\"voltage\":%.3f,\"voltage_1dp\":%.1f,\"adc_mv_avg\":%.1f,\"adc_raw_min\":%u,\"adc_raw_max\":%u,\"samples\":%d,\"ip\":\"%s\"}",
    batteryV,
    batteryV,
    adcMvAvg,
    adcRawMin,
    adcRawMax,
    ADC_SAMPLES,
    WiFi.localIP().toString().c_str()
  );
  httpd_resp_set_type(req, "application/json");
  return httpd_resp_send(req, body, HTTPD_RESP_USE_STRLEN);
}

esp_err_t environment_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "application/json");
  if (!bmeReady && !bmpReady) {
    if (!initBme280()) {
      initBmp280();
    }
  }
  if (!bmeReady && !bmpReady) {
    const int id76 = readI2CRegister(0x76, 0xD0); // BME280 chip-id register
    const int id77 = readI2CRegister(0x77, 0xD0);
    char errBody[256];
    char id76Text[8];
    char id77Text[8];
    if (id76 >= 0) {
      snprintf(id76Text, sizeof(id76Text), "0x%02X", (uint8_t)id76);
    } else {
      snprintf(id76Text, sizeof(id76Text), "none");
    }
    if (id77 >= 0) {
      snprintf(id77Text, sizeof(id77Text), "0x%02X", (uint8_t)id77);
    } else {
      snprintf(id77Text, sizeof(id77Text), "none");
    }
    snprintf(
      errBody,
      sizeof(errBody),
      "{\"ok\":false,\"error\":\"bme280_not_initialized\",\"hint\":\"check_wiring_or_wrong_sensor\",\"chip_id_0x76\":\"%s\",\"chip_id_0x77\":\"%s\"}",
      id76Text,
      id77Text
    );
    return httpd_resp_send(req, errBody, HTTPD_RESP_USE_STRLEN);
  }

  float temperatureC = NAN;
  float pressurePa = NAN;
  if (bmeReady) {
    temperatureC = bme.readTemperature();
    pressurePa = bme.readPressure();
  } else if (bmpReady) {
    temperatureC = bmp.readTemperature();
    pressurePa = bmp.readPressure();
  }
  if (isnan(temperatureC) || isnan(pressurePa) || pressurePa <= 0.0f) {
    return httpd_resp_send(req, "{\"ok\":false,\"error\":\"bme280_read_failed\"}", HTTPD_RESP_USE_STRLEN);
  }

  const float temperatureF = (temperatureC * 9.0f / 5.0f) + 32.0f;
  const float pressureHpa = pressurePa / 100.0f;
  char body[256];
  snprintf(
    body,
    sizeof(body),
    "{\"ok\":true,\"sensor\":\"%s\",\"temperature_c\":%.2f,\"temperature_f\":%.2f,\"pressure_hpa\":%.2f,\"pressure_pa\":%.1f,\"i2c_addr\":\"0x%02X\",\"ip\":\"%s\"}",
    bmeReady ? "BME280" : "BMP280",
    temperatureC,
    temperatureF,
    pressureHpa,
    pressurePa,
    bmeAddress,
    WiFi.localIP().toString().c_str()
  );
  return httpd_resp_send(req, body, HTTPD_RESP_USE_STRLEN);
}

esp_err_t realtime_handler(httpd_req_t *req) {
  httpd_resp_set_type(req, "application/json");
  if (!bmeReady && !bmpReady) {
    if (!initBme280()) {
      initBmp280();
    }
  }
  if (!bmeReady && !bmpReady) {
    return httpd_resp_send(req, "{\"ok\":false,\"error\":\"sensor_not_initialized\"}", HTTPD_RESP_USE_STRLEN);
  }

  float temperatureC = NAN;
  float pressurePa = NAN;
  if (bmeReady) {
    temperatureC = bme.readTemperature();
    pressurePa = bme.readPressure();
  } else if (bmpReady) {
    temperatureC = bmp.readTemperature();
    pressurePa = bmp.readPressure();
  }
  if (isnan(temperatureC) || isnan(pressurePa) || pressurePa <= 0.0f) {
    return httpd_resp_send(req, "{\"ok\":false,\"error\":\"sensor_read_failed\"}", HTTPD_RESP_USE_STRLEN);
  }

  const float pressureHpa = pressurePa / 100.0f;
  char body[160];
  snprintf(
    body,
    sizeof(body),
    "{\"ok\":true,\"temperature_c\":%.2f,\"pressure_hpa\":%.2f}",
    temperatureC,
    pressureHpa
  );
  return httpd_resp_send(req, body, HTTPD_RESP_USE_STRLEN);
}

void startStreamingServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 81;
  httpd_uri_t stream_uri = { .uri = "/stream", .method = HTTP_GET, .handler = stream_handler, .user_ctx = NULL };
  if (httpd_start(&stream_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(stream_httpd, &stream_uri);
  }
}

void startControlServer() {
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = 82;
  config.ctrl_port = 32769;
  config.max_open_sockets = 2;
  config.lru_purge_enable = true;
  httpd_uri_t voltage_uri = { .uri = "/voltage", .method = HTTP_GET, .handler = voltage_handler, .user_ctx = NULL };
  httpd_uri_t environment_uri = { .uri = "/environment", .method = HTTP_GET, .handler = environment_handler, .user_ctx = NULL };
  httpd_uri_t realtime_uri = { .uri = "/realtime", .method = HTTP_GET, .handler = realtime_handler, .user_ctx = NULL };
  if (httpd_start(&control_httpd, &config) == ESP_OK) {
    httpd_register_uri_handler(control_httpd, &voltage_uri);
    httpd_register_uri_handler(control_httpd, &environment_uri);
    httpd_register_uri_handler(control_httpd, &realtime_uri);
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);

  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  // ROVER SETTINGS
  config.frame_size = FRAMESIZE_QVGA; // 320x240
  // config.frame_size = FRAMESIZE_SVGA; // 800x600
  config.jpeg_quality = 12;
  config.fb_count = 2;
  config.grab_mode = CAMERA_GRAB_LATEST; // Fresh frames only
  config.fb_location = CAMERA_FB_IN_PSRAM;

  // Camera Init
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed: 0x%x", err);
    return;
  }

  // WiFi Init
  WiFi.begin(ssid_Router, password_Router);
  WiFi.setSleep(false);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  analogSetPinAttenuation(VOLTAGE_ADC_PIN, ADC_11db);
  analogReadResolution(12);

  // Initialize custom I2C pins that are safe for this camera board.
  Wire.begin(BME_SDA_PIN, BME_SCL_PIN);
  scanI2CBus();
  if (!initBme280()) {
    initBmp280();
  }
  if (bmeReady) {
    Serial.printf("BME280 initialized at I2C address 0x%02X\n", bmeAddress);
  } else if (bmpReady) {
    Serial.printf("BMP280 initialized at I2C address 0x%02X\n", bmeAddress);
  } else {
    Serial.println("Could not find a valid BME280/BMP280 sensor at 0x76 or 0x77. Check wiring.");
  }

  startStreamingServer();
  startControlServer();

  Serial.println("\nWiFi Connected!");
  Serial.print("Low-Latency Stream: http://");
  Serial.print(WiFi.localIP());
  Serial.println(":81/stream");
  Serial.print("Voltage JSON: http://");
  Serial.print(WiFi.localIP());
  Serial.println(":82/voltage");
  Serial.print("Environment JSON: http://");
  Serial.print(WiFi.localIP());
  Serial.println(":82/environment");
  Serial.print("Realtime JSON: http://");
  Serial.print(WiFi.localIP());
  Serial.println(":82/realtime");
}

void loop() {
  delay(1000);
}