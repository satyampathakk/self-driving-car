# IP Camera Setup Guide

## cam.py now supports IP Camera!

The system can now use your phone as an IP camera instead of a USB webcam.

## Quick Setup

### 1. Edit cam.py (lines 52-56)

```python
# Option 1: IP Camera (Android IP Webcam app)
USE_IP_CAMERA = True
IP_CAMERA_URL = "http://192.168.29.174:8080/video"  # ← Change this IP

# Option 2: USB Webcam (set USE_IP_CAMERA = False)
WEBCAM_INDEX = 0
```

### 2. Install IP Webcam App (Android)

**Download:** [IP Webcam on Google Play](https://play.google.com/store/apps/details?id=com.pas.webcam)

**Alternative apps:**
- DroidCam
- iVCam (iOS/Android)
- EpocCam (iOS)

### 3. Start the Camera

1. Open IP Webcam app on your phone
2. Scroll down and tap **"Start server"**
3. Note the IP address shown (e.g., `192.168.29.174:8080`)
4. Update `IP_CAMERA_URL` in cam.py with this address

### 4. Test Connection

```bash
python cam.py
```

**Expected output:**
```
[CAM] Connecting to IP camera: http://192.168.29.174:8080/video
[CAM] IP camera connected successfully
```

## URL Formats for Different Apps

### IP Webcam (Android)
```python
IP_CAMERA_URL = "http://192.168.x.x:8080/video"
```

### DroidCam
```python
IP_CAMERA_URL = "http://192.168.x.x:4747/video"
```

### ESP32-CAM
```python
IP_CAMERA_URL = "http://192.168.x.x:81/stream"
```

### RTSP Stream
```python
IP_CAMERA_URL = "rtsp://192.168.x.x:8554/stream"
```

## Troubleshooting

### ❌ Cannot connect to IP camera

**Check 1: Same Network**
- Phone and PC must be on the same WiFi network
- Check phone WiFi settings
- Check PC WiFi settings

**Check 2: IP Address**
- IP address changes when you reconnect to WiFi
- Always check the app for current IP
- Try pinging: `ping 192.168.x.x`

**Check 3: Firewall**
- Windows Firewall might block connection
- Temporarily disable firewall to test
- Add Python to firewall exceptions

**Check 4: App Running**
- Make sure IP Webcam app shows "Server is running"
- Try accessing in browser: `http://192.168.x.x:8080`

### ❌ Connection drops frequently

**Solution 1: Keep phone awake**
- In IP Webcam settings → Enable "Prevent phone sleep"
- Keep phone plugged in to charger

**Solution 2: Reduce quality**
- In IP Webcam settings → Video resolution → 640x480
- Lower quality = more stable connection

**Solution 3: Use USB tethering**
- Connect phone to PC via USB
- Enable USB tethering in phone settings
- More stable than WiFi

### ❌ Lag/Delay

**Reduce latency:**
1. Lower resolution (640x480 or 320x240)
2. Reduce frame rate in app settings
3. Use 5GHz WiFi instead of 2.4GHz
4. Move phone closer to WiFi router

## Automatic Fallback

If IP camera fails, cam.py automatically tries USB webcam:

```
[ERROR] Cannot connect to IP camera: http://192.168.29.174:8080/video
[ERROR] Check:
  1. IP address is correct
  2. Camera app is running
  3. Phone and PC are on same network

Trying USB webcam as fallback...
[CAM] Using USB webcam 0 as fallback
```

## Switch Between IP and USB

### Use IP Camera:
```python
USE_IP_CAMERA = True
IP_CAMERA_URL = "http://192.168.29.174:8080/video"
```

### Use USB Webcam:
```python
USE_IP_CAMERA = False
WEBCAM_INDEX = 0  # 0 = first webcam, 1 = second, etc.
```

## Testing IP Camera

### Test in Browser
Open this URL in your browser:
```
http://192.168.29.174:8080
```

You should see the IP Webcam interface with video feed.

### Test with VLC
1. Open VLC Media Player
2. Media → Open Network Stream
3. Enter: `http://192.168.29.174:8080/video`
4. Click Play

If it works in VLC, it will work in cam.py!

## Performance Tips

### Best Settings for IP Webcam App:

**Video Preferences:**
- Resolution: 640x480 (good balance)
- Quality: 50-70%
- FPS limit: 15-20 (enough for detection)

**Connection:**
- Use 5GHz WiFi if available
- Keep phone close to router
- Disable other apps using network

**Power:**
- Keep phone plugged in
- Enable "Prevent phone sleep"
- Disable battery optimization for IP Webcam

## Advanced: Multiple Cameras

You can use multiple IP cameras:

```python
# Camera 1 (front)
FRONT_CAMERA = "http://192.168.29.174:8080/video"

# Camera 2 (back)
BACK_CAMERA = "http://192.168.29.175:8080/video"

# Use front camera
cap = cv2.VideoCapture(FRONT_CAMERA)
```

## Summary

✅ **Easy Setup** - Just install app and update IP  
✅ **Wireless** - No USB cable needed  
✅ **Flexible** - Mount phone anywhere on car  
✅ **Automatic Fallback** - Uses USB if IP fails  
✅ **Multiple Options** - Works with many camera apps  

Perfect for testing your self-driving car with a phone camera!
