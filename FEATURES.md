# Self-Driving Car - Features Documentation

## Project Overview
An autonomous self-driving car system built with ESP32 microcontrollers, ultrasonic sensors, computer vision, and a Python-based decision engine. The car can navigate autonomously using sensor fusion and real-time obstacle avoidance.

---

## System Architecture

### Hardware Components
- **ESP32 Dev Board** - Sensor reading and motor control
- **ESP32-CAM** (Optional) - Onboard camera streaming
- **4x HC-SR04 Ultrasonic Sensors** - Distance measurement (Front, Back, Left, Right)
- **L298N Motor Driver** - DC motor control
- **2x DC Motors** - Vehicle propulsion
- **IP Camera/Laptop Webcam** - Alternative camera sources

### Software Components
- **Python Server** - Central decision engine and web dashboard
- **ESP32 Firmware** - Real-time sensor and motor control
- **Computer Vision** - Lane detection and obstacle recognition
- **WebSocket Communication** - Real-time bidirectional control

---

## Core Features

### 1. Autonomous Navigation
- **Multi-Sensor Fusion**: Combines ultrasonic sensors and vision data for robust navigation
- **State Machine Control**: Three operational states
  - `NORMAL` - Standard driving with obstacle avoidance
  - `BACKING_UP` - Smart backup when boxed in
  - `STOPPED` - Safety stop when no safe path exists
- **Intelligent Decision Making**: Real-time path planning based on sensor readings

### 2. Ultrasonic Sensor System
- **4-Sensor Array**: 360° obstacle detection
  - Front sensor (20cm stop threshold)
  - Back sensor (10cm safety distance)
  - Left sensor (20cm avoidance threshold)
  - Right sensor (20cm avoidance threshold)
- **Real-time Monitoring**: 100ms sensor reading interval
- **Adaptive Thresholds**: Configurable distance thresholds via dashboard

### 3. Computer Vision
- **Lane Detection**: Hough transform-based lane line detection
- **Red Obstacle Detection**: HSV color space filtering for red objects
- **Position Classification**: Three-zone detection (left, center, right)
- **Smoothed Detection**: History-based filtering to reduce false positives
- **Visual Feedback**: Real-time annotated video feed with overlays

### 4. Motor Control
- **6 Movement Commands**:
  - Forward (F:speed)
  - Backward (B:speed)
  - Left turn (L:speed)
  - Right turn (R:speed)
  - Hard left (HL:speed)
  - Hard right (HR:speed)
- **Digital Control**: Simple HIGH/LOW motor control for L298N driver
- **WebSocket Commands**: Real-time motor control with <50ms latency

### 5. Smart Backup Logic
- **Boxed-In Detection**: Triggers when both sides < 15cm
- **Clearance-Based Exit**: Backs up until 25cm clearance found
- **Preferred Direction**: Remembers last successful turn direction
- **Safety Checks**: Won't backup if back sensor < 10cm

### 6. Steering Correction System
- **Timeout Detection**: Monitors continuous steering (2.0s threshold)
- **Automatic Correction**: Applies opposite steering pulse (0.8s duration)
- **Prevents Circling**: Stops car from getting stuck in loops
- **Visual Indicator**: Dashboard shows correction status

### 7. Web Dashboard
- **Real-time Video Feed**: Live camera stream with HUD overlay
- **Sensor Visualization**: 
  - Color-coded sensor bars (green/yellow/red)
  - Mini top-view car diagram with direction arrows
  - Numeric distance readings
- **Control Panel**:
  - Start/Stop button
  - Run timer with countdown
  - Emergency stop
- **Configuration Interface**:
  - Adjustable sensor thresholds
  - Motor speed settings
  - Vision detection parameters
  - Steering timeout settings
- **Event Log**: Real-time decision logging with timestamps

### 8. Run Timer
- **Configurable Duration**: Set run time (0 = unlimited)
- **Auto-Stop**: Automatically stops car when timer expires
- **Visual Countdown**: Progress bar and remaining time display
- **Safety Feature**: Prevents runaway scenarios

### 9. Multiple Camera Sources
- **Laptop Webcam**: Built-in or USB camera support
- **IP Camera**: Android IP Webcam app integration
- **ESP32-CAM**: Hardware camera module support
- **Hot-Swappable**: Toggle between sources with 'T' key
- **Snapshot Mode**: Fallback for unreliable video streams

### 10. Communication System
- **WebSocket**: Bidirectional real-time communication
  - Motor commands (Server → ESP32)
  - Status updates (ESP32 → Server)
- **HTTP REST API**:
  - `/sensors` - Sensor data upload (POST)
  - `/upload_frame` - Video frame upload (POST)
  - `/config` - Configuration management (GET/POST)
  - `/status` - System status (GET)
- **Auto-Reconnect**: Automatic WebSocket reconnection on disconnect

---

## Decision Logic

### Obstacle Avoidance Algorithm
```
IF front < 20cm:
    IF left < 15cm AND right < 15cm:
        → BACKUP (if back > 10cm)
        → STOP (if back < 10cm)
    ELSE IF right < 20cm:
        → TURN LEFT
    ELSE IF left < 20cm:
        → TURN RIGHT
    ELSE:
        → TURN LEFT (default)
ELSE:
    → FORWARD
```

### Vision-Based Avoidance
- Red obstacle in **center** → STOP
- Red obstacle on **left** → STEER RIGHT
- Red obstacle on **right** → STEER LEFT
- **Minimal interference mode**: Vision suggestions, sensors decide

---

## Configuration Parameters

### Sensor Thresholds (cm)
- `front_stop_distance`: 20
- `side_avoid_distance`: 20
- `backup_threshold`: 15
- `backup_clearance`: 25
- `back_safety_distance`: 10

### Motor Speeds (0-255)
- `motor_speed_normal`: 200
- `motor_speed_turn`: 180
- `motor_speed_hard`: 220
- `motor_speed_backup`: 160

### Steering Correction
- `steer_timeout`: 2.0 seconds
- `correction_duration`: 0.8 seconds

### Vision Detection
- `vision_min_area`: 2000 pixels
- `vision_center_width`: 0.5 (50% of frame)
- `vision_confidence`: 8 frames

---

## Testing & Simulation

### Test Client (cam.py)
- **Simulated Sensors**: Realistic sensor data generation
- **Multiple Scenarios**:
  - Normal open road
  - Narrow corridor
  - Wall ahead
  - Left/right side obstacles
  - Boxed-in situations
- **Manual Overrides**: Force specific sensor values
- **Keyboard Controls**: Real-time scenario switching
- **Statistics Display**: FPS, success rate, frame count

### Camera Streamer (ip_cam_streamer.py)
- **Multi-source Support**: Laptop cam, IP cam, ESP32-CAM
- **Performance Monitoring**: FPS tracking and upload statistics
- **Live Preview**: Real-time video display with camera source indicator
- **Hot-swap Cameras**: Toggle sources without restart

---

## Safety Features

1. **Emergency Stop**: WebSocket disconnect triggers immediate motor stop
2. **Back Safety**: Won't backup if obstacle detected behind
3. **Boxed-In Detection**: Stops if no safe path exists
4. **Run Timer**: Prevents indefinite operation
5. **Sensor Validation**: Filters invalid readings (< 2cm or > 400cm)
6. **Watchdog Protection**: Small delays prevent ESP32 watchdog resets

---

## Performance Specifications

- **Sensor Reading Rate**: 10 Hz (100ms interval)
- **Video Frame Rate**: 20 FPS (50ms interval)
- **Command Latency**: < 50ms (WebSocket)
- **Decision Cycle**: Real-time (per frame)
- **WiFi Range**: Standard 802.11 b/g/n
- **Operating Voltage**: 5V (ESP32), 7-12V (Motors)

---

## Installation & Setup

### Python Dependencies
```bash
pip install -r requirements.txt
```

### ESP32 Libraries
- WiFi.h
- HTTPClient.h
- WebSocketsClient.h
- ArduinoJson.h
- esp_camera.h (ESP32-CAM only)

### Configuration Steps
1. Update WiFi credentials in ESP32 code
2. Set server IP address in ESP32 code
3. Configure camera source in Python scripts
4. Adjust sensor thresholds via dashboard
5. Upload firmware to ESP32 boards
6. Run Python server
7. Start camera streamer or test client

---

## Future Enhancements

- [ ] GPS navigation integration
- [ ] Path planning and mapping
- [ ] Machine learning-based obstacle classification
- [ ] Multi-car coordination
- [ ] Voice control interface
- [ ] Mobile app dashboard
- [ ] Data logging and analytics
- [ ] Battery monitoring
- [ ] Speed control based on terrain

---

## Technical Specifications

### ESP32 Dev Board Pins
- **Ultrasonic Sensors**:
  - Front: TRIG=23, ECHO=22
  - Left: TRIG=21, ECHO=19
  - Right: TRIG=18, ECHO=5
  - Back: TRIG=17, ECHO=16
- **Motor Driver (L298N)**:
  - Left Motor: IN1=26, IN2=27, ENA=25
  - Right Motor: IN3=32, IN4=14, ENB=33

### ESP32-CAM Pins
- Camera: Standard AI-Thinker pinout
- Sensors: GPIO 2,3,4,12,13,14,15,16
- Motors: GPIO 0,1,14,15,32,33

### Network Ports
- HTTP Server: 5000
- WebSocket: 5000/ws
- IP Camera: 8080 (default)

---

## License & Credits

Built with ESP32, Python Flask, OpenCV, and WebSockets.
Designed for educational and hobbyist autonomous vehicle projects.
