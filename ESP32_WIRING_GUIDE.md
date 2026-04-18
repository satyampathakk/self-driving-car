# ESP32-CAM Wiring Guide for Self-Driving Car

## Hardware Components Required

1. **ESP32-CAM (AI-Thinker module)** - Main controller with camera
2. **4x HC-SR04 Ultrasonic Sensors** - Distance measurement
3. **L298N Motor Driver** - Motor control
4. **2x DC Motors** - Movement
5. **Power Supply**:
   - 7-12V for motors (via L298N)
   - 5V for ESP32-CAM (can use L298N's 5V output)
6. **Jumper wires** and **breadboard** (optional)

---

## Pin Configuration Summary

### Ultrasonic Sensors (HC-SR04)

| Sensor | Trigger Pin | Echo Pin | VCC | GND |
|--------|-------------|----------|-----|-----|
| **Front** | GPIO12 | GPIO13 | 5V | GND |
| **Back**  | GPIO14 | GPIO15 | 5V | GND |
| **Left**  | GPIO2  | GPIO4  | 5V | GND |
| **Right** | GPIO16 | GPIO3  | 5V | GND |

### Motor Driver (L298N)

| Function | ESP32 Pin | L298N Pin | Notes |
|----------|-----------|-----------|-------|
| **Left Motor IN1** | GPIO1 | IN1 | Direction control |
| **Left Motor IN2** | GPIO33 | IN2 | Direction control |
| **Left Motor Enable** | GPIO32 | ENA | PWM speed control |
| **Right Motor IN3** | GPIO0 | IN3 | Direction control |
| **Right Motor IN4** | GPIO14 | IN4 | Direction control |
| **Right Motor Enable** | GPIO15 | ENB | PWM speed control |

### Camera Pins (Built-in - DO NOT MODIFY)

These are hardwired on the ESP32-CAM module:

| Function | GPIO Pin |
|----------|----------|
| PWDN | 32 |
| RESET | -1 |
| XCLK | 0 |
| SIOD | 26 |
| SIOC | 27 |
| Y9 | 35 |
| Y8 | 34 |
| Y7 | 39 |
| Y6 | 36 |
| Y5 | 21 |
| Y4 | 19 |
| Y3 | 18 |
| Y2 | 5 |
| VSYNC | 25 |
| HREF | 23 |
| PCLK | 22 |

---

## Detailed Wiring Instructions

### 1. Ultrasonic Sensors Wiring

#### Front Sensor (HC-SR04)
```
HC-SR04 Front    →    ESP32-CAM
─────────────────────────────────
VCC              →    5V
TRIG             →    GPIO12
ECHO             →    GPIO13
GND              →    GND
```

#### Back Sensor (HC-SR04)
```
HC-SR04 Back     →    ESP32-CAM
─────────────────────────────────
VCC              →    5V
TRIG             →    GPIO14
ECHO             →    GPIO15
GND              →    GND
```

#### Left Sensor (HC-SR04)
```
HC-SR04 Left     →    ESP32-CAM
─────────────────────────────────
VCC              →    5V
TRIG             →    GPIO2
ECHO             →    GPIO4
GND              →    GND
```

#### Right Sensor (HC-SR04)
```
HC-SR04 Right    →    ESP32-CAM
─────────────────────────────────
VCC              →    5V
TRIG             →    GPIO16
ECHO             →    GPIO3
GND              →    GND
```

### 2. Motor Driver (L298N) Wiring

#### ESP32-CAM to L298N
```
ESP32-CAM        →    L298N
─────────────────────────────────
GPIO1            →    IN1 (Left Motor)
GPIO33           →    IN2 (Left Motor)
GPIO32           →    ENA (Left Motor Enable)
GPIO0            →    IN3 (Right Motor)
GPIO14           →    IN4 (Right Motor)
GPIO15           →    ENB (Right Motor Enable)
GND              →    GND
```

#### L298N to Motors
```
L298N            →    Motors
─────────────────────────────────
OUT1             →    Left Motor (+)
OUT2             →    Left Motor (-)
OUT3             →    Right Motor (+)
OUT4             →    Right Motor (-)
```

#### Power Supply to L298N
```
Power Supply     →    L298N
─────────────────────────────────
7-12V (+)        →    12V Input
GND (-)          →    GND
```

#### L298N to ESP32-CAM Power
```
L298N            →    ESP32-CAM
─────────────────────────────────
5V Output        →    5V
GND              →    GND
```

**Note:** Make sure the L298N's 5V regulator jumper is in place to provide 5V output.

---

## Power Supply Recommendations

### Option 1: Single Battery Pack (Recommended)
- **Battery**: 2S LiPo (7.4V) or 3S LiPo (11.1V)
- Connect to L298N's 12V input
- L298N provides 5V to ESP32-CAM via onboard regulator

### Option 2: Dual Power Supply
- **Motors**: 7-12V battery pack → L298N 12V input
- **ESP32-CAM**: Separate 5V power bank → ESP32-CAM 5V pin
- **Important**: Connect all GNDs together!

### Current Requirements
- ESP32-CAM: ~200-300mA (peak during WiFi transmission)
- Each HC-SR04: ~15mA
- Motors: 500mA - 2A each (depending on load)
- **Total**: Minimum 2A capacity recommended

---

## Assembly Tips

### 1. Test Components Individually
- Test each ultrasonic sensor separately
- Test motors with L298N before connecting ESP32
- Test camera capture before adding sensors

### 2. Sensor Placement
```
        [FRONT SENSOR]
              ↑
    [LEFT]  [CAR]  [RIGHT]
              ↓
        [BACK SENSOR]
```

- Mount sensors at same height (10-15cm from ground)
- Ensure sensors face outward without obstruction
- Keep sensors away from metal surfaces (interference)

### 3. Motor Connections
- If motor spins backward, swap OUT1↔OUT2 or OUT3↔OUT4
- Test motor direction before final assembly
- Secure all connections with solder or terminal blocks

### 4. Cable Management
- Keep sensor wires away from motor wires (EMI)
- Use twisted pair for long sensor connections
- Add capacitors (100nF) across motor terminals to reduce noise

### 5. Mounting
- Mount ESP32-CAM at front of car for best camera view
- Elevate camera 15-20cm for better field of view
- Ensure camera lens is clean and unobstructed

---

## GPIO Pin Conflicts to Avoid

### Pins to AVOID (Used by Camera)
- GPIO 0, 5, 18, 19, 21, 22, 23, 25, 26, 27, 32, 34, 35, 36, 39

### Safe Pins for Sensors/Motors
- GPIO 1, 2, 3, 4, 12, 13, 14, 15, 16, 33

### Strapping Pins (Use with Caution)
- GPIO 0: Must be HIGH during boot
- GPIO 2: Must be LOW during boot
- GPIO 12: Must be LOW during boot (MTDI)
- GPIO 15: Must be HIGH during boot (MTDO)

**Note:** The code handles these automatically, but be aware during debugging.

---

## Troubleshooting

### Camera Not Working
- Check if PSRAM is enabled in Arduino IDE: Tools → PSRAM → "Enabled"
- Verify camera pins are not used for other purposes
- Try lower resolution: Change `FRAMESIZE_VGA` to `FRAMESIZE_QVGA`

### Sensors Reading 999cm
- Check VCC and GND connections
- Verify trigger/echo pins are correct
- Ensure sensor has clear line of sight
- Try increasing timeout in `pulseIn()` function

### Motors Not Moving
- Check L298N enable jumpers are in place
- Verify power supply voltage (7-12V)
- Test motors directly with battery
- Check PWM channel assignments

### WiFi Connection Issues
- Verify SSID and password in code
- Check server IP address is correct
- Ensure ESP32 and server are on same network
- Move closer to WiFi router during testing

### ESP32 Keeps Rebooting
- Insufficient power supply (use 2A+ supply)
- Brownout detector triggered (already disabled in code)
- Short circuit in wiring
- Add 100µF capacitor across ESP32 power pins

---

## Software Configuration

### Before Uploading Code

1. **Install Required Libraries** (Arduino IDE):
   - ESP32 Board Support (via Board Manager)
   - WebSocketsClient by Markus Sattler
   - ArduinoJson by Benoit Blanchon

2. **Configure WiFi** (in `esp32_cam_client.ino`):
   ```cpp
   const char* WIFI_SSID = "YOUR_WIFI_SSID";
   const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
   ```

3. **Configure Server IP**:
   ```cpp
   const char* SERVER_IP = "192.168.1.100";  // Your server's IP
   ```

4. **Arduino IDE Settings**:
   - Board: "AI Thinker ESP32-CAM"
   - Upload Speed: 115200
   - Flash Frequency: 80MHz
   - PSRAM: "Enabled"
   - Partition Scheme: "Huge APP (3MB No OTA)"

5. **Upload Method**:
   - Connect FTDI programmer to ESP32-CAM
   - Connect GPIO0 to GND (boot mode)
   - Upload code
   - Disconnect GPIO0 from GND
   - Press RESET button

---

## Pin Customization

If you need to change pins due to conflicts or preferences, modify these sections in the code:

```cpp
// Ultrasonic Sensors
#define TRIG_FRONT  12   // Change to your preferred GPIO
#define ECHO_FRONT  13   // Change to your preferred GPIO
// ... repeat for other sensors

// Motor Driver
#define MOTOR_LEFT_IN1   1    // Change to your preferred GPIO
#define MOTOR_LEFT_IN2   33   // Change to your preferred GPIO
// ... repeat for other motor pins
```

**Remember**: Avoid camera pins and strapping pins!

---

## Testing Procedure

1. **Power Test**: Verify all components receive correct voltage
2. **Sensor Test**: Read serial monitor for sensor values
3. **Motor Test**: Manually send commands via WebSocket
4. **Camera Test**: Check if frames appear on server
5. **Integration Test**: Run full autonomous mode

---

## Safety Notes

⚠️ **Important Safety Reminders**:
- Always test on a raised platform first (car won't fall)
- Keep emergency stop button accessible
- Don't run at full speed indoors initially
- Monitor battery temperature during operation
- Disconnect power when making wiring changes
- Use proper gauge wire for motor connections (20-22 AWG)

---

## Support

If you encounter issues:
1. Check serial monitor output for error messages
2. Verify all connections match this guide
3. Test components individually
4. Check power supply voltage and current
5. Review troubleshooting section above

Good luck with your self-driving car project! 🚗
