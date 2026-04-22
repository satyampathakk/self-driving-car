/*
============================================================
Self-Driving Car — ESP32-CAM Client (Camera Only)
============================================================
ESP32-CAM module that:
  - Captures video frames
  - Sends frames to server via HTTP POST
  - No sensors, no motors (handled by separate ESP32 Dev Board)

Hardware Requirements:
  - ESP32-CAM (AI-Thinker module)
  - Power supply (5V for ESP32-CAM)

============================================================
*/

#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ============================================================
// WIFI CONFIGURATION - CHANGE THESE!
// ============================================================
const char* WIFI_SSID = "Satyam";
const char* WIFI_PASSWORD = "lelobhai";

// ============================================================
// SERVER CONFIGURATION - CHANGE THESE!
// ============================================================
const char* SERVER_IP = "10.125.85.154";
const int SERVER_PORT = 5000;

String STREAM_URL = String("http://") + SERVER_IP + ":" + SERVER_PORT + "/upload_frame";

// ============================================================
// TIMING CONFIGURATION
// ============================================================
#define FRAME_INTERVAL  50   // Send frames every 50ms (20 FPS)

// ============================================================
// CAMERA PIN DEFINITIONS (AI-Thinker ESP32-CAM)
// ============================================================
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

// ============================================================
// GLOBAL VARIABLES
// ============================================================
unsigned long lastFrameSend = 0;

// ============================================================
// CAMERA INITIALIZATION
// ============================================================
bool initCamera() {
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
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  
  // Safe init - start with RGB565, then switch to JPEG
  config.pixel_format = PIXFORMAT_RGB565;
  config.frame_size = FRAMESIZE_QVGA;  // 320x240
  config.jpeg_quality = 12;
  config.fb_count = 1;
  
  // Camera init
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] Camera init failed: 0x%x\n", err);
    return false;
  }
  
  // Switch to JPEG after init
  sensor_t * s = esp_camera_sensor_get();
  if (s) {
    s->set_pixformat(s, PIXFORMAT_JPEG);
  }
  
  Serial.println("[CAM] Camera initialized successfully");
  return true;
}

// ============================================================
// FRAME SENDING
// ============================================================

void sendFrame() {
  if (WiFi.status() != WL_CONNECTED) return;
  
  // Capture frame
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[CAM] Frame capture failed");
    return;
  }
  
  // Convert to JPEG if needed
  uint8_t * jpg_buf = NULL;
  size_t jpg_len = 0;
  bool need_free = false;
  
  if (fb->format != PIXFORMAT_JPEG) {
    bool jpeg_converted = frame2jpg(fb, 80, &jpg_buf, &jpg_len);
    if (!jpeg_converted) {
      Serial.println("[CAM] JPEG conversion failed");
      esp_camera_fb_return(fb);
      return;
    }
    need_free = true;
  } else {
    jpg_buf = fb->buf;
    jpg_len = fb->len;
  }
  
  // Send frame via HTTP POST
  HTTPClient http;
  http.begin(STREAM_URL);
  http.addHeader("Content-Type", "image/jpeg");
  http.setTimeout(1000);  // 1 second timeout
  
  int httpCode = http.POST(jpg_buf, jpg_len);
  
  if (httpCode == 200 || httpCode == 204) {
    // Success
  } else {
    Serial.printf("[HTTP] Frame POST failed: %d\n", httpCode);
  }
  
  http.end();
  
  // Cleanup
  if (need_free && jpg_buf) {
    free(jpg_buf);
  }
  esp_camera_fb_return(fb);
}

// ============================================================
// WIFI SETUP
// ============================================================
void setupWiFi() {
  Serial.println("[WIFI] Connecting to WiFi...");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  
  int attempts = 0;
  while (WiFi.status() != WL_CONNECTED && attempts < 20) {
    delay(500);
    Serial.print(".");
    attempts++;
  }
  
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\n[WIFI] Connected!");
    Serial.print("[WIFI] IP Address: ");
    Serial.println(WiFi.localIP());
  } else {
    Serial.println("\n[WIFI] Connection failed!");
  }
}

// ============================================================
// SETUP
// ============================================================
void setup() {
  // Disable brownout detector
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
  
  Serial.begin(115200);
  Serial.println("\n\n");
  Serial.println("============================================================");
  Serial.println("Self-Driving Car — ESP32-CAM Client (Camera Only)");
  Serial.println("============================================================");
  
  // Initialize camera
  if (!initCamera()) {
    Serial.println("[ERROR] Camera initialization failed!");
    while(1) delay(1000);
  }
  
  // Connect to WiFi
  setupWiFi();
  
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[ERROR] Cannot proceed without WiFi!");
    while(1) delay(1000);
  }
  
  Serial.println("============================================================");
  Serial.println("[INFO] ESP32-CAM Client Ready!");
  Serial.println("[INFO] Streaming frames to server...");
  Serial.println("============================================================\n");
}

// ============================================================
// MAIN LOOP
// ============================================================
void loop() {
  unsigned long currentMillis = millis();
  
  // Capture and send frame
  if (currentMillis - lastFrameSend >= FRAME_INTERVAL) {
    lastFrameSend = currentMillis;
    sendFrame();
  }
  
  // Small delay to prevent watchdog issues
  delay(1);
}
