/*
============================================================
Self-Driving Car — ESP32 Sensor & Motor Controller
============================================================
ESP32 Dev Board that:
  - Reads 3 ultrasonic sensors (Front, Left, Right)
  - Sends sensor data to server via HTTP
  - Receives motor commands via WebSocket
  - Controls motors via L298N driver

Camera streaming handled separately by phone IP camera app

⚠️ IMPORTANT: CAR IS PHYSICALLY REVERSED
- Camera is at the BACK of the car (which is now the logical FRONT)
- Physical front sensor → Logical back
- Physical back sensor → Logical front  
- Physical left sensor → Logical right
- Physical right sensor → Logical left
- Forward command → Moves backward physically
- Backward command → Moves forward physically
- Left command → Turns right physically
- Right command → Turns left physically

This code handles the reversal automatically.
============================================================
*/

#include <WiFi.h>
#include <HTTPClient.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>

// ============================================================
// WIFI CONFIGURATION - CHANGE THESE!
// ============================================================
const char* WIFI_SSID = "Airtel_anki_5050";        // Change to your WiFi name
const char* WIFI_PASSWORD = "Air@32689"; // Change to your WiFi password

// ============================================================
// SERVER CONFIGURATION - CHANGE THESE!
// ============================================================
const char* SERVER_IP = "192.168.1.4";  // Change to your server IP
const int SERVER_PORT = 5000;

String SENSOR_URL = String("http://") + SERVER_IP + ":" + SERVER_PORT + "/sensors";

// ============================================================
// GPIO PIN DEFINITIONS - ULTRASONIC SENSORS
// ============================================================
#define TRIG_F 23   // Front Trigger
#define ECHO_F 22   // Front Echo
#define TRIG_L 21   // Left Trigger
#define ECHO_L 19   // Left Echo
#define TRIG_R 18   // Right Trigger
#define ECHO_R 5    // Right Echo

// Back sensor
#define TRIG_B 17   // Back Trigger
#define ECHO_B 16   // Back Echo

// ============================================================
// GPIO PIN DEFINITIONS - MOTOR DRIVER (L298N)
// ============================================================
#define IN1 26      // Left Motor Direction 1
#define IN2 27      // Left Motor Direction 2
#define ENA 25      // Left Motor Enable (PWM)
#define IN3 32      // Right Motor Direction 1
#define IN4 14      // Right Motor Direction 2
#define ENB 33      // Right Motor Enable (PWM)

// ============================================================
// MOTOR SPEED SETTINGS
// ============================================================
#define MOTOR_SPEED_NORMAL  150   // Normal forward speed (0-255)
#define MOTOR_SPEED_TURN    130   // Speed during turns (0-255)
#define MOTOR_SPEED_HARD    170   // Speed during hard turns (0-255)

// PWM Settings
#define PWM_FREQ      5000   // PWM frequency
#define PWM_RESOLUTION  8    // 8-bit resolution (0-255)
#define PWM_CHANNEL_L   0    // PWM channel for left motor
#define PWM_CHANNEL_R   1    // PWM channel for right motor

// ============================================================
// TIMING CONFIGURATION
// ============================================================
#define SENSOR_INTERVAL   100    // Read sensors every 100ms
#define WS_RECONNECT_DELAY 2000  // WebSocket reconnect delay

// ============================================================
// LOCAL AVOIDANCE CONFIG
// ============================================================
#define FRONT_THRESHOLD   20    // cm — trigger avoidance when front blocked
#define BACKUP_DURATION   600   // ms — how long to back up
#define TURN_DURATION     700   // ms — how long to hold the turn

// ============================================================
// GLOBAL VARIABLES
// ============================================================
WebSocketsClient webSocket;
HTTPClient http;

unsigned long lastSensorRead = 0;

String currentCommand = "S:0";
bool carRunning = false;

// Last valid sensor readings
float frontDist = 100.0;
float leftDist  = 100.0;
float rightDist = 100.0;
float backDist  = 100.0;  // Reserved for future

// ============================================================
// ULTRASONIC SENSOR FUNCTIONS
// ============================================================
float readDistance(int trig, int echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  
  long duration = pulseIn(echo, HIGH, 30000);  // 30ms timeout
  
  if (duration == 0) {
    return -1;  // No echo received (noise)
  }
  
  float distance = duration * 0.034 / 2.0;
  
  // Filter noise: > 500cm is invalid
  if (distance > 500.0) {
    return -1;  // Invalid reading (noise)
  }
  
  return distance;
}

void readAllSensors() {
  // REVERSED: Physical sensors are reversed
  // What was "front" sensor is now "back" (camera side is now front)
  // What was "back" sensor is now "front"
  // What was "left" is now "right"
  // What was "right" is now "left"
  
  float physical_f = readDistance(TRIG_F, ECHO_F);
  float physical_l = readDistance(TRIG_L, ECHO_L);
  float physical_r = readDistance(TRIG_R, ECHO_R);
  float physical_b = readDistance(TRIG_B, ECHO_B);
  
  // SWAP: Physical front → Logical back, Physical back → Logical front
  // SWAP: Physical left → Logical right, Physical right → Logical left
  float logical_front = physical_b;  // Back sensor is now front
  float logical_back = physical_f;   // Front sensor is now back
  float logical_left = physical_r;   // Right sensor is now left
  float logical_right = physical_l;  // Left sensor is now right
  
  // Update only VALID readings (> 0 means valid, -1 means noise/invalid)
  // Keep last recorded distance if reading is invalid
  if (logical_front > 0) frontDist = logical_front;
  if (logical_back > 0) backDist = logical_back;
  if (logical_left > 0) leftDist = logical_left;
  if (logical_right > 0) rightDist = logical_right;
  
  // Debug output (shows logical directions with last valid values)
  Serial.print("F: "); Serial.print(frontDist);
  Serial.print(" B: "); Serial.print(backDist);
  Serial.print(" L: "); Serial.print(leftDist);
  Serial.print(" R: "); Serial.println(rightDist);
}

// ============================================================
// MOTOR CONTROL FUNCTIONS
// ============================================================
void setupMotors() {
  // Configure motor pins
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(ENA, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  pinMode(ENB, OUTPUT);

  // Stop motors initially
  stopMotors();

  Serial.println("[MOTOR] Motor driver initialized");
} 


void stopMotors() {
  digitalWrite(ENA, LOW);
  digitalWrite(ENB, LOW);
}

void moveForward(int speed) {
  // REVERSED: Forward is now backward (camera at back)
  digitalWrite(ENA, HIGH);
  digitalWrite(ENB, HIGH);
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, HIGH);
}

void moveBackward(int speed) {
  // REVERSED: Backward is now forward (camera at back)
  digitalWrite(ENA, HIGH);
  digitalWrite(ENB, HIGH);
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, LOW);
}

void turnLeft(int speed) {
  // Left: left motor backward, right motor forward
  digitalWrite(ENA, HIGH);
  digitalWrite(ENB, HIGH);
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);   // Left motor BACKWARD
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, HIGH);  // Right motor FORWARD
}

void turnRight(int speed) {
  // Right: left motor forward, right motor backward
  digitalWrite(ENA, HIGH);
  digitalWrite(ENB, HIGH);
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);  // Left motor FORWARD
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, LOW);   // Right motor BACKWARD
}

void hardLeft(int speed) {
  turnLeft(speed);
}

void hardRight(int speed) {
  turnRight(speed);
}

// ============================================================
// LOCAL AVOIDANCE (backup + turn toward more space)
// ============================================================
void localAvoidance() {
  Serial.printf("[AVOID] Front blocked (%.1fcm) — backing up\n", frontDist);

  // 1. Back up
  moveBackward(MOTOR_SPEED_HARD);
  delay(BACKUP_DURATION);
  stopMotors();
  delay(100);

  // Re-read sensors after backing up
  readAllSensors();

  // 2. Turn toward the side with more space
  if (leftDist >= rightDist) {
    Serial.printf("[AVOID] Turning LEFT (L=%.1f R=%.1f)\n", leftDist, rightDist);
    hardLeft(MOTOR_SPEED_HARD);
  } else {
    Serial.printf("[AVOID] Turning RIGHT (L=%.1f R=%.1f)\n", leftDist, rightDist);
    hardRight(MOTOR_SPEED_HARD);
  }
  delay(TURN_DURATION);
  stopMotors();
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
      carRunning = false;
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
        } else {
          // If stopped, only accept stop command
          if (message.startsWith("S:")) {
            stopMotors();
          }
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
void sendSensorData() {
  if (WiFi.status() != WL_CONNECTED) return;
  
  HTTPClient http;
  http.begin(SENSOR_URL);
  http.addHeader("Content-Type", "application/json");
  
  // Create JSON payload
  StaticJsonDocument<200> doc;
  doc["f"] = frontDist;
  doc["b"] = backDist;   // Will be 100.0 until back sensor is connected
  doc["l"] = leftDist;
  doc["r"] = rightDist;
  
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
  Serial.begin(115200);
  Serial.println("\n\n");
  Serial.println("============================================================");
  Serial.println("Self-Driving Car — ESP32 Sensor & Motor Controller");
  Serial.println("============================================================");
  
  // Setup ultrasonic sensor pins
  pinMode(TRIG_F, OUTPUT);
  pinMode(ECHO_F, INPUT);
  pinMode(TRIG_L, OUTPUT);
  pinMode(ECHO_L, INPUT);
  pinMode(TRIG_R, OUTPUT);
  pinMode(ECHO_R, INPUT);
  pinMode(TRIG_B, OUTPUT);
  pinMode(ECHO_B, INPUT);
  
  Serial.println("[SENSOR] Ultrasonic sensors initialized (F/B/L/R - REVERSED)");
  
  // Setup motors
  setupMotors();
  
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
  Serial.println("[INFO] ESP32 Sensor & Motor Controller Ready!");
  Serial.println("[INFO] Camera streaming via IP Webcam app on phone");
  Serial.println("============================================================\n");
  
  // Motor test - remove after testing
  Serial.println("[TEST] Testing motors for 2 seconds...");
  moveForward(200);
  delay(2000);
  stopMotors();
  Serial.println("[TEST] Motor test complete");
  
  carRunning = true; // Auto-start
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
    
    readAllSensors();
    sendSensorData();

    // Local avoidance — override server if front is blocked
    if (carRunning && frontDist < FRONT_THRESHOLD) {
      localAvoidance();
    }
  }
  
  // Small delay to prevent watchdog issues
  delay(1);
}
