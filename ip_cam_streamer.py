"""
============================================================
Camera Streamer for Self-Driving Car
============================================================
Captures frames from multiple camera sources and sends them
to the server for vision processing.

Supported camera sources:
1. Laptop/USB Webcam (default)
2. IP Webcam app (Android phone)
3. ESP32-CAM or other IP cameras

Requirements:
- For IP camera: Phone/device and server on same WiFi network
- For laptop: Built-in or USB webcam
- Server running on specified IP:PORT

Usage:
1. Set USE_LAPTOP_CAMERA = True for laptop cam, False for IP cam
2. Run: python ip_cam_streamer.py
3. Press 'T' in window to toggle camera source
4. Press 'Q' to quit
============================================================
"""

import cv2
import numpy as np
import requests
import time
import urllib.request

# ============================================================
# CONFIGURATION
# ============================================================
# Camera source selection
USE_LAPTOP_CAMERA = True  # True = laptop webcam, False = IP camera
LAPTOP_CAMERA_INDEX = 0   # Usually 0 for built-in webcam

# IP Webcam app URL (change to your phone's IP)
IP_CAMERA_URL = "http://192.168.1.5:8080/video"

# Server configuration
SERVER_IP = "192.168.1.4"
SERVER_PORT = 5000
STREAM_URL = f"http://{SERVER_IP}:{SERVER_PORT}/upload_frame"

# Streaming settings
FRAME_INTERVAL = 0.05  # Send frames every 50ms (20 FPS)
USE_SNAPSHOT_MODE = True  # For IP camera: True = more reliable, False = video stream

# ============================================================
# CAMERA INITIALIZATION
# ============================================================
def init_camera(use_laptop=True):
    """Initialize camera connection"""
    if use_laptop:
        # Laptop/USB webcam
        print(f"[CAM] Opening laptop webcam (index {LAPTOP_CAMERA_INDEX})...")
        cap = cv2.VideoCapture(LAPTOP_CAMERA_INDEX)
        
        if not cap.isOpened():
            print(f"[ERROR] Cannot open laptop webcam!")
            return None, None
        
        # Set resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        print(f"[CAM] Laptop webcam opened successfully")
        return cap, None
    
    else:
        # IP Camera
        if USE_SNAPSHOT_MODE:
            snapshot_url = IP_CAMERA_URL.replace("/video", "/shot.jpg")
            print(f"[CAM] Using IP camera snapshot mode: {snapshot_url}")
            return None, snapshot_url
        else:
            print(f"[CAM] Connecting to IP camera video stream: {IP_CAMERA_URL}")
            cap = cv2.VideoCapture(IP_CAMERA_URL)
            
            if not cap.isOpened():
                print(f"[WARN] Video stream failed, switching to snapshot mode...")
                snapshot_url = IP_CAMERA_URL.replace("/video", "/shot.jpg")
                print(f"[CAM] Using snapshot mode: {snapshot_url}")
                return None, snapshot_url
            else:
                print(f"[CAM] IP camera video stream connected")
                return cap, None

# ============================================================
# FRAME CAPTURE
# ============================================================
def capture_frame(cap, snapshot_url):
    """Capture a frame from IP camera"""
    if snapshot_url:
        # Snapshot mode
        try:
            with urllib.request.urlopen(snapshot_url, timeout=2) as response:
                img_array = np.array(bytearray(response.read()), dtype=np.uint8)
                frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                return frame
        except Exception as e:
            print(f"[CAM] Snapshot capture failed: {e}")
            return None
    else:
        # Video stream mode
        ret, frame = cap.read()
        if ret:
            return frame
        else:
            print(f"[CAM] Video frame capture failed")
            return None

# ============================================================
# FRAME SENDING
# ============================================================
def send_frame(frame):
    """Send frame to server"""
    try:
        # Encode frame as JPEG
        _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        
        # Send to server
        response = requests.post(
            STREAM_URL,
            data=jpg.tobytes(),
            headers={"Content-Type": "image/jpeg"},
            timeout=1
        )
        
        # 200 OK or 204 No Content are both success
        if response.status_code in (200, 204):
            return True
        else:
            print(f"[HTTP] Frame upload failed: {response.status_code}")
            return False
            
    except Exception as e:
        print(f"[HTTP] Frame upload error: {e}")
        return False

# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("Camera Streamer for Self-Driving Car")
    print("=" * 60)
    
    # Track current camera source
    using_laptop = USE_LAPTOP_CAMERA
    
    if using_laptop:
        print(f"Camera: Laptop Webcam (index {LAPTOP_CAMERA_INDEX})")
    else:
        print(f"Camera: IP Camera ({IP_CAMERA_URL})")
        print(f"Mode: {'Snapshot' if USE_SNAPSHOT_MODE else 'Video Stream'}")
    
    print(f"Server: {SERVER_IP}:{SERVER_PORT}")
    print("=" * 60)
    print()
    
    # Initialize camera
    cap, snapshot_url = init_camera(using_laptop)
    
    if cap is None and snapshot_url is None:
        print("[ERROR] Failed to initialize camera!")
        return
    
    print("[INFO] Camera initialized successfully")
    print("[INFO] Starting frame streaming...")
    print("[INFO] Press 'T' to toggle camera source")
    print("[INFO] Press 'Q' to quit")
    print()
    
    frame_count = 0
    success_count = 0
    start_time = time.time()
    last_stats_time = start_time
    
    try:
        while True:
            loop_start = time.time()
            
            # Capture frame
            frame = capture_frame(cap, snapshot_url)
            
            if frame is not None:
                # Resize to standard size
                frame = cv2.resize(frame, (640, 480))
                
                # Send to server
                if send_frame(frame):
                    success_count += 1
                
                frame_count += 1
                
                # Add camera source indicator to frame
                cam_text = "LAPTOP CAM" if using_laptop else "IP CAMERA"
                cv2.putText(frame, cam_text, (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                cv2.putText(frame, "T=Toggle  Q=Quit", (10, 470),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                
                # Display preview
                cv2.imshow("Camera Stream (T=toggle, Q=quit)", frame)
                
                # Check for keys
                key = cv2.waitKey(1) & 0xFF
                
                if key == ord('q'):
                    print("\n[INFO] Quit requested by user")
                    break
                
                elif key == ord('t'):
                    # Toggle camera source
                    print("\n[INFO] Toggling camera source...")
                    
                    # Release current camera
                    if cap is not None:
                        cap.release()
                    
                    # Switch source
                    using_laptop = not using_laptop
                    
                    # Initialize new camera
                    cap, snapshot_url = init_camera(using_laptop)
                    
                    if cap is None and snapshot_url is None:
                        print("[ERROR] Failed to switch camera!")
                        break
                    
                    cam_name = "Laptop Webcam" if using_laptop else "IP Camera"
                    print(f"[INFO] Switched to: {cam_name}\n")
            
            # Print statistics every 5 seconds
            current_time = time.time()
            if current_time - last_stats_time >= 5.0:
                elapsed = current_time - start_time
                fps = frame_count / elapsed if elapsed > 0 else 0
                success_rate = (success_count / frame_count * 100) if frame_count > 0 else 0
                
                print(f"[STATS] Frames: {frame_count} | FPS: {fps:.1f} | Success: {success_rate:.1f}%")
                last_stats_time = current_time
            
            # Maintain frame rate
            elapsed = time.time() - loop_start
            sleep_time = FRAME_INTERVAL - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)
    
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
    
    finally:
        # Cleanup
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
        
        # Final statistics
        elapsed = time.time() - start_time
        fps = frame_count / elapsed if elapsed > 0 else 0
        success_rate = (success_count / frame_count * 100) if frame_count > 0 else 0
        
        print("\n" + "=" * 60)
        print("FINAL STATISTICS")
        print("=" * 60)
        print(f"Total Frames: {frame_count}")
        print(f"Successful Uploads: {success_count}")
        print(f"Average FPS: {fps:.1f}")
        print(f"Success Rate: {success_rate:.1f}%")
        print(f"Total Time: {elapsed:.1f}s")
        print("=" * 60)
        print("[INFO] Exited cleanly")

if __name__ == "__main__":
    main()
