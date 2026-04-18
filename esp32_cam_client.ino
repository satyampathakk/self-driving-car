/*
============================================================
Self-Driving Car — ESP32-CAM Client
============================================================
ESP32-CAM module that:
  - Captures video frames and sends to server
  - Reads 4 ultrasonic sensors (Front, Back, Left, Right)
  - Receives motor commands via WebSocket
  - Controls motors based on server commands

Hardware Requirements:
  - ESP32-CAM (AI-Thinker module)
  - 4x HC-SR04 Ultrasonic Sensors
  - L298N Motor Driver
  - 2x DC Motors
  - Power supply (7-12V for motors, 5V for ESP32)

============================================================
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ============================================================
// WIFI CONFIGURATION - CHANGE THESE!
// ============================================================
const char* WIFI_SSID = "YOUR_WIFI_SSID";        // Change to your WiFi name
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"; // Change to your WiFi password

// ============================================================
// SERVER CONFIGURATION - CHANGE THESE!
// ============================================================
const char* SERVER_IP = "192.168.1.100";  // Change to your server IP
const int SERVER_PORT = 5000;

// URLs
String SENSOR_URL = String("http://") + SERVER_IP + ":" + SERVER_PORT + "/sensors";
String STREAM_URL = String("http://") + SERVER_IP + ":" + SERVER_PORT + "/upload_frame";

// ============================================================
// GPIO PIN DEFINITIONS - ULTRASONIC SENSORS
// ============================================================
// Front Sensor
#define TRIG_FRONT  12   // GPIO12 - Front Trigger
#define ECHO_FRONT  13   // GPIO13 - Front Echo

// Back Sensor
#define TRIG_BACK   14   // GPIO14 - Back Trigger
#define ECHO_BACK   15   // GPIO15 - Back Echo

// Left Sensor
#define TRIG_LEFT   2    // GPIO2  - Left Trigger
#define ECHO_LEFT   4    // GPIO4  - Left Echo

// Right Sensor
#define TRIG_RIGHT  16   // GPIO16 - Right Trigger (RX2)
#define ECHO_RIGHT  3    // GPIO3  - Right Echo (RX0)

// ============================================================
// GPIO PIN DEFINITIONS - MOTOR DRIVER (L298N)
// ============================================================
// Left Motor
#define MOTOR_LEFT_IN1   1    // GPIO1  - Left Motor Direction 1
#define MOTOR_LEFT_IN2   33   // GPIO33 - Left Motor Direction 2
#define MOTOR_LEFT_EN    32   // GPIO32 - Left Motor Enable (PWM)

// Right Motor
#define MOTOR_RIGHT_IN3  0    // GPIO0  - Right Motor Direction 1
#define MOTOR_RIGHT_IN4  14   // GPIO14 - Right Motor Direction 2
#define MOTOR_RIGHT_EN   15   // GPIO15 - Right Motor Enable (PWM)

// ============================================================
// MOTOR SPEED SETTINGS
// ============================================================
#define MOTOR_SPEED_NORMAL  200   // Normal forward speed (0-255)
#define MOTOR_SPEED_TURN    180   // Speed during turns (0-255)
#define MOTOR_SPEED_HARD    220   // Speed during hard turns (0-255)

// PWM Settings
#define PWM_FREQ      5000   // PWM frequency
#define PWM_RESOLUTION  8    // 8-bit resolution (0-255)
#define PWM_CHANNEL_L   0    // PWM channel for left motor
#define PWM_CHANNEL_R   1    // PWM channel for right motor

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
// TIMING CONFIGURATION
// ============================================================
#define SENSOR_INTERVAL   100    // Read sensors every 100ms
#define FRAME_INTERVAL    50     // Send frames every 50ms (20 FPS)
#define WS_RECONNECT_DELAY 2000  // WebSocket reconnect delay

// ============================================================
// GLOBAL VARIABLES
// ============================================================
WebSocketsClient webSocket;
HTTPClient http;

unsigned long lastSensorRead = 0;
unsigned long lastFrameSend = 0;

String currentCommand = "S:0";
bool carRunning = false;

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
  config.pixel_format = PIXFORMAT_JPEG;
  
  // Init with high specs for quality
  if(psramFound()){
    config.frame_size = FRAMESIZE_VGA;    // 640x480
    config.jpeg_quality = 12;             // 0-63 lower means higher quality
    config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_SVGA;
    config.jpeg_quality = 12;
    config.fb_count = 1;
  }
  
  // Camera init
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] Camera init failed: 0x%x\n", err);
    return false;
  }
  
  Serial.println("[CAM] Camera initialized successfully");
  return true;
}

// ============================================================
// ULTRASONIC SENSOR FUNCTIONS
// ============================================================
float readUltrasonic(int trigPin, int echoPin) {
  // Send trigger pulse
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);
  
  // Read echo pulse
  long duration = pulseIn(echoPin, HIGH, 30000); // 30ms timeout
  
  // Calculate distance in cm
  if (duration == 0) {
    return 999.0; // No echo received
  }
  
  float distance = duration * 0.034 / 2.0;
  
  // Clamp to reasonable range
  if (distance < 2.0) distance = 2.0;
  if (distance > 400.0) distance = 999.0;
  
  return distance;
}

void readAllSensors(float &front, float &back, float &left, float &right) {
  front = readUltrasonic(TRIG_FRONT, ECHO_FRONT);
  back  = readUltrasonic(TRIG_BACK, ECHO_BACK);
  left  = readUltrasonic(TRIG_LEFT, ECHO_LEFT);
  right = readUltrasonic(TRIG_RIGHT, ECHO_RIGHT);
  
  // Debug output
  Serial.printf("[SENSORS] F:%.1f B:%.1f L:%.1f R:%.1f\n", front, back, left, right);
}

// ============================================================
// MOTOR CONTROL FUNCTIONS
// ============================================================
void setupMotors() {
  // Configure motor pins
  pinMode(MOTOR_LEFT_IN1, OUTPUT);
  pinMode(MOTOR_LEFT_IN2, OUTPUT);
  pinMode(MOTOR_RIGHT_IN3, OUTPUT);
  pinMode(MOTOR_RIGHT_IN4, OUTPUT);
  
  // Setup PWM for motor enable pins
  ledcSetup(PWM_CHANNEL_L, PWM_FREQ, PWM_RESOLUTION);
  ledcSetup(PWM_CHANNEL_R, PWM_FREQ, PWM_RESOLUTION);
  ledcAttachPin(MOTOR_LEFT_EN, PWM_CHANNEL_L);
  ledcAttachPin(MOTOR_RIGHT_EN, PWM_CHANNEL_R);
  
  // Stop motors initially
  stopMotors();
  
  Serial.println("[MOTOR] Motor driver initialized");
}

void stopMotors() {
  digitalWrite(MOTOR_LEFT_IN1, LOW);
  digitalWrite(MOTOR_LEFT_IN2, LOW);
  digitalWrite(MOTOR_RIGHT_IN3, LOW);
  digitalWrite(MOTOR_RIGHT_IN4, LOW);
  ledcWrite(PWM_CHANNEL_L, 0);
  ledcWrite(PWM_CHANNEL_R, 0);
}

void moveForward(int speed) {
  digitalWrite(MOTOR_LEFT_IN1, HIGH);
  digitalWrite(MOTOR_LEFT_IN2, LOW);
  digitalWrite(MOTOR_RIGHT_IN3, HIGH);
  digitalWrite(MOTOR_RIGHT_IN4, LOW);
  ledcWrite(PWM_CHANNEL_L, speed);
  ledcWrite(PWM_CHANNEL_R, speed);
}

void moveBackward(int speed) {
  digitalWrite(MOTOR_LEFT_IN1, LOW);
  digitalWrite(MOTOR_LEFT_IN2, HIGH);
  digitalWrite(MOTOR_RIGHT_IN3, LOW);
  digitalWrite(MOTOR_RIGHT_IN4, HIGH);
  ledcWrite(PWM_CHANNEL_L, speed);
  ledcWrite(PWM_CHANNEL_R, speed);
}

void turnLeft(int speed) {
  // Left motor backward, right motor forward
  digitalWrite(MOTOR_LEFT_IN1, LOW);
  digitalWrite(MOTOR_LEFT_IN2, HIGH);
  digitalWrite(MOTOR_RIGHT_IN3, HIGH);
  digitalWrite(MOTOR_RIGHT_IN4, LOW);
  ledcWrite(PWM_CHANNEL_L, speed);
  ledcWrite(PWM_CHANNEL_R, speed);
}

void turnRight(int speed) {
  // Left motor forward, right motor backward
  digitalWrite(MOTOR_LEFT_IN1, HIGH);
  digitalWrite(MOTOR_LEFT_IN2, LOW);
  digitalWrite(MOTOR_RIGHT_IN3, LOW);
  digitalWrite(MOTOR_RIGHT_IN4, HIGH);
  ledcWrite(PWM_CHANNEL_L, speed);
  ledcWrite(PWM_CHANNEL_R, speed);
}

void hardLeft(int speed) {
  // Same as turnLeft but with higher speed
  turnLeft(speed);
}

void hardRight(int speed) {
  // Same as turnRight but with higher speed
  turnRight(speed);
}

// ============================================================
// COMMAND EXECUTION
// ============================================================
void executeCommand(String cmd) {
  currentCommand = cmd;
  
  if (cmd.startsWith("F:")) {
    // Forward with speed
    int speed = cmd.substring(2).toInt();
    if (speed == 0) speed = MOTOR_SPEED_NORMAL;
    moveForward(speed);
    Serial.printf("[CMD] Forward: %d\n", speed);
  }
  else if (cmd.startsWith("B:")) {
    // Backward with speed
    int speed = cmd.substring(2).toInt();
    if (speed == 0) speed = MOTOR_SPEED_NORMAL;
    moveBackward(speed);
    Serial.printf("[CMD] Backward: %d\n", speed);
  }
  else if (cmd.startsWith("L:")) {
    // Left turn
    int speed = cmd.substring(2).toInt();
    if (speed == 0) speed = MOTOR_SPEED_TURN;
    turnLeft(speed);
    Serial.printf("[CMD] Left: %d\n", speed);
  }
  else if (cmd.startsWith("R:")) {
    // Right turn
    int speed = cmd.substring(2).toInt();
    if (speed == 0) speed = MOTOR_SPEED_TURN;
    turnRight(speed);
    Serial.printf("[CMD] Right: %d\n", speed);
  }
  else if (cmd.startsWith("HL:")) {
    // Hard left
    int speed = cmd.substring(3).toInt();
    if (speed == 0) speed = MOTOR_SPEED_HARD;
    hardLeft(speed);
    Serial.printf("[CMD] Hard Left: %d\n", speed);
  }
  else if (cmd.startsWith("HR:")) {
    // Hard right
    int speed = cmd.substring(3).toInt();
    if (speed == 0) speed = MOTOR_SPEED_HARD;
    hardRight(speed);
    Serial.printf("[CMD] Hard Right: %d\n", speed);
  }
  else if (cmd.startsWith("S:")) {
    // Stop
    stopMotors();
    Serial.println("[CMD] Stop");
  }
  else {
    Serial.printf("[CMD] Unknown command: %s\n", cmd.c_str());
  }
}

// ============================================================
// WEBSOCKET HANDLERS
// ============================================================
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("[WS] Disconnected");
      stopMotors(); // Safety: stop on disconnect
      break;
      
    case WStype_CONNECTED:
      Serial.println("[WS] Connected to server");
      break;
      
    case WStype_TEXT:
      {
        String message = String((char*)payload);
        Serial.printf("[WS ← SERVER] %s\n", message.c_str());
        
        // Execute command if car is running
        if (carRunning) {
          executeCommand(message);
        }
      }
      break;
      
    case WStype_ERROR:
      Serial.println("[WS] Error");
      break;
  }
}

// ============================================================
// HTTP FUNCTIONS
// ============================================================
void sendSensorData(float front, float back, float left, float right) {
  if (WiFi.status() != WL_CONNECTED) return;
  
  HTTPClient http;
  http.begin(SENSOR_URL);
  http.addHeader("Content-Type", "application/json");
  
  // Create JSON payload
  StaticJsonDocument<200> doc;
  doc["f"] = front;
  doc["b"] = back;
  doc["l"] = left;
  doc["r"] = right;
  
  String jsonString;
  serializeJson(doc, jsonString);
  
  int httpCode = http.POST(jsonString);
  
  if (httpCode > 0) {
    // Success
  } else {
    Serial.printf("[HTTP] Sensor POST failed: %s\n", http.errorToString(httpCode).c_str());
  }
  
  http.end();
}

void sendFrame() {
  if (WiFi.status() != WL_CONNECTED) return;
  
  // Capture frame
  camera_fb_t * fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("[CAM] Frame capture failed");
    return;
  }
  
  // Send frame via HTTP POST
  HTTPClient http;
  http.begin(STREAM_URL);
  http.addHeader("Content-Type", "image/jpeg");
  
  int httpCode = http.POST(fb->buf, fb->len);
  
  if (httpCode > 0) {
    // Success
  } else {
    Serial.printf("[HTTP] Frame POST failed: %s\n", http.errorToString(httpCode).c_str());
  }
  
  http.end();
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
  Serial.println("Self-Driving Car — ESP32-CAM Client");
  Serial.println("============================================================");
  
  // Setup ultrasonic sensor pins
  pinMode(TRIG_FRONT, OUTPUT);
  pinMode(ECHO_FRONT, INPUT);
  pinMode(TRIG_BACK, OUTPUT);
  pinMode(ECHO_BACK, INPUT);
  pinMode(TRIG_LEFT, OUTPUT);
  pinMode(ECHO_LEFT, INPUT);
  pinMode(TRIG_RIGHT, OUTPUT);
  pinMode(ECHO_RIGHT, INPUT);
  
  Serial.println("[SENSOR] Ultrasonic sensors initialized");
  
  // Setup motors
  setupMotors();
  
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
  
  // Setup WebSocket
  String wsPath = "/ws";
  webSocket.begin(SERVER_IP, SERVER_PORT, wsPath);
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(WS_RECONNECT_DELAY);
  
  Serial.println("[WS] WebSocket configured");
  Serial.println("============================================================");
  Serial.println("[INFO] ESP32-CAM Client Ready!");
  Serial.println("============================================================\n");
  
  carRunning = true; // Auto-start (or wait for server command)
}

// ============================================================
// MAIN LOOP
// ============================================================
void loop() {
  // Handle WebSocket
  webSocket.loop();
  
  unsigned long currentMillis = millis();
  
  // Read and send sensor data
  if (currentMillis - lastSensorRead >= SENSOR_INTERVAL) {
    lastSensorRead = currentMillis;
    
    float front, back, left, right;
    readAllSensors(front, back, left, right);
    sendSensorData(front, back, left, right);
  }
  
  // Capture and send frame
  if (currentMillis - lastFrameSend >= FRAME_INTERVAL) {
    lastFrameSend = currentMillis;
    sendFrame();
  }
  
  // Small delay to prevent watchdog issues
  delay(1);
}
