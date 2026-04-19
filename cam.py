"""
============================================================
Self-Driving Car — PC Test Client  (v2)
============================================================
Simulates the ESP32 using:
  - Your PC webcam (instead of ESP32-CAM)
  - Random ultrasonic sensor data (with realistic scenarios)

Changes from v1:
  - Reads car_running + steer_direction + correcting from /status
    so the HUD reflects the server's actual state
  - Shows steering timeout progress bar + correction indicator
  - Shows run timer countdown
  - Keyboard shortcuts sync with server via /config POST

Run FIRST:  python server.py   (the new server_v2.py)
Run SECOND: python test_client.py

Controls (press in the webcam window):
  Q  — quit
  F  — force front obstacle (8 cm)
  C  — clear forced obstacle
  1  — normal open road
  2  — narrow corridor (sides close)
  3  — wall ahead (front counts down)
  S  — toggle Start / Stop (sends to server)
============================================================
"""

import cv2
import numpy as np
import requests
import websocket
import threading
import time
import json
import random
import math

# ============================================================
# CONFIG
# ============================================================
SERVER_IP   = "127.0.0.1"
SERVER_PORT = 5000
WS_URL      = f"ws://{SERVER_IP}:{SERVER_PORT}/ws"
SENSOR_URL  = f"http://{SERVER_IP}:{SERVER_PORT}/sensors"
STATUS_URL  = f"http://{SERVER_IP}:{SERVER_PORT}/status"
CONFIG_URL  = f"http://{SERVER_IP}:{SERVER_PORT}/config"
STREAM_URL  = f"http://{SERVER_IP}:{SERVER_PORT}/upload_frame"

# ============================================================
# CAMERA CONFIG - Runtime switchable
# ============================================================
# Camera source (can be toggled with 'T' key during runtime)
USE_IP_CAMERA = False  # Start with laptop cam by default

# IP Camera (Android IP Webcam app)
IP_CAMERA_URL = "http://192.168.1.5:8080/video"
USE_SNAPSHOT_MODE = True  # Set True if video stream has MJPEG issues

# USB Webcam
WEBCAM_INDEX = 0

SENSOR_INTERVAL = 0.1
FRAME_INTERVAL  = 0.05

# ============================================================
# SHARED STATE
# ============================================================
lock    = threading.Lock()
running = True
camera_source = {"using_ip": USE_IP_CAMERA}  # Track current camera source

server_state = {
    "command":         "S:0",
    "reason":          "—",
    "sensors":         {"f":999,"b":999,"l":999,"r":999},
    "car_running":     False,
    "correcting":      False,
    "steer_direction": None,
    "steer_since":     None,
    "steer_timeout":   2.0,
    "time_remaining":  None,
    "run_duration":    0,
    "detected_objects": [],
    "ai_response":     "",
    "vision_obstacle": False,
    "vision_position": "none",
}

scenario = {
    "mode":        "normal",
    "force_front": None,
    "force_left":  None,
    "force_right": None,
    "force_back":  None,
}

# ============================================================
# SENSOR SIMULATION
# ============================================================
def simulate_sensors():
    t    = time.time()
    mode = scenario["mode"]

    if mode == "normal":
        f = 80  + 20  * abs(math.sin(t * 0.3)) + random.uniform(-3, 3)
        b = 100 + 15  * abs(math.sin(t * 0.2)) + random.uniform(-2, 2)
        l = 60  + 10  * math.sin(t * 0.5)      + random.uniform(-2, 2)
        r = 65  + 10  * math.cos(t * 0.4)      + random.uniform(-2, 2)
    elif mode == "corridor":
        f = 70  + 15 * abs(math.sin(t * 0.3)) + random.uniform(-3, 3)
        b = 90  + 10 * random.random()
        l = 8   + random.uniform(-1, 1)
        r = 9   + random.uniform(-1, 1)
    elif mode == "wall_ahead":
        f = max(5, 60 - ((t % 20) * 3))
        b = 80  + random.uniform(-5, 5)
        l = 40  + random.uniform(-5, 5)
        r = 40  + random.uniform(-5, 5)
    elif mode == "left_close":
        # Left side too close → should steer RIGHT
        f = 70  + random.uniform(-5, 5)
        b = 90  + random.uniform(-5, 5)
        l = 8   + random.uniform(-1, 1)  # DANGER: < 12cm
        r = 60  + random.uniform(-5, 5)
    elif mode == "right_close":
        # Right side too close → should steer LEFT
        f = 70  + random.uniform(-5, 5)
        b = 90  + random.uniform(-5, 5)
        l = 60  + random.uniform(-5, 5)
        r = 8   + random.uniform(-1, 1)  # DANGER: < 12cm
    elif mode == "both_close":
        # Both sides close → should STOP
        f = 70  + random.uniform(-5, 5)
        b = 90  + random.uniform(-5, 5)
        l = 8   + random.uniform(-1, 1)
        r = 9   + random.uniform(-1, 1)
    else:
        f = b = l = r = 80.0

    # Apply forced values (override mode)
    if scenario["force_front"] is not None:
        f = scenario["force_front"]
    if scenario["force_left"] is not None:
        l = scenario["force_left"]
    if scenario["force_right"] is not None:
        r = scenario["force_right"]
    if scenario["force_back"] is not None:
        b = scenario["force_back"]

    return {
        "f": round(max(2, f), 1),
        "b": round(max(2, b), 1),
        "l": round(max(2, l), 1),
        "r": round(max(2, r), 1),
    }

# ============================================================
# WEBSOCKET
# ============================================================
def ws_thread():
    global running
    while running:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_message=lambda ws, msg: print(f"[WS ← SERVER] {msg.strip()}"),
                on_error=lambda ws, e: print(f"[WS] Error: {e}"),
                on_close=lambda ws, *a: print("[WS] Closed"),
                on_open=lambda ws: print("[WS] Connected"),
            )
            ws.run_forever(ping_interval=10)
        except Exception as e:
            print(f"[WS] Reconnect in 2s ({e})")
        time.sleep(2)

# ============================================================
# SENSOR POST THREAD
# ============================================================
def sensor_thread():
    while running:
        sensors = simulate_sensors()
        # Debug output for forced sensors
        if scenario.get("force_left") or scenario.get("force_right"):
            print(f"[SENSORS] L={sensors['l']}cm  R={sensors['r']}cm  (forced)")
        try:
            requests.post(SENSOR_URL, json=sensors, timeout=1)
        except Exception:
            pass
        time.sleep(SENSOR_INTERVAL)

# ============================================================
# FRAME PUSH THREAD
# ============================================================
def stream_thread(cap, snapshot_url=None):
    import urllib.request
    while running:
        if snapshot_url:
            # Snapshot mode for IP camera
            try:
                with urllib.request.urlopen(snapshot_url, timeout=2) as response:
                    img_array = np.array(bytearray(response.read()), dtype=np.uint8)
                    frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                    if frame is None:
                        time.sleep(0.1)
                        continue
            except Exception as e:
                time.sleep(0.1)
                continue
        else:
            # Video stream mode
            ret, frame = cap.read()
            if not ret:
                time.sleep(0.1)
                continue
        
        _, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        try:
            requests.post(
                STREAM_URL,
                data=jpg.tobytes(),
                headers={"Content-Type": "image/jpeg"},
                timeout=1,
            )
        except Exception:
            pass
        time.sleep(FRAME_INTERVAL)

# ============================================================
# STATUS POLL THREAD
# ============================================================
def status_thread():
    while running:
        try:
            d = requests.get(STATUS_URL, timeout=0.5).json()
            with lock:
                server_state.update({k: d.get(k, server_state.get(k))
                                      for k in server_state})
        except Exception:
            pass
        time.sleep(0.15)

# ============================================================
# HUD
# ============================================================
CMD_COLORS = {
    "F":  (0, 220, 0),
    "B":  (80, 120, 255),
    "L":  (0, 220, 255),
    "R":  (0, 220, 255),
    "S":  (0, 50, 220),
    "H":  (180, 60, 255),
}

def draw_hud(frame, sensors):
    with lock:
        cmd       = server_state["command"]
        reason    = server_state["reason"]
        car_run   = server_state["car_running"]
        correcting = server_state["correcting"]
        steer_dir  = server_state["steer_direction"]
        steer_since = server_state["steer_since"]
        steer_timeout = server_state["steer_timeout"]
        time_rem   = server_state["time_remaining"]
        run_dur    = server_state["run_duration"]
        detected_objs = server_state.get("detected_objects", [])
        ai_resp = server_state.get("ai_response", "")
        vision_obs = server_state.get("vision_obstacle", False)
        vision_pos = server_state.get("vision_position", "none")

    h, w = frame.shape[:2]

    # Semi-transparent sidebar
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (310, h), (15, 15, 15), -1)
    cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

    # ── Running status pill ──
    status_col = (0, 200, 80) if car_run else (0, 50, 210)
    status_txt = "RUNNING" if car_run else "STOPPED"
    if correcting:
        status_col = (180, 60, 255)
        status_txt = "CORRECTING"
    cv2.rectangle(frame, (8, 8), (145, 28), status_col, -1)
    cv2.rectangle(frame, (8, 8), (145, 28), (255,255,255), 1)
    cv2.putText(frame, status_txt, (13, 23),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255,255,255), 1)

    # ── Run timer ──
    if run_dur > 0 and time_rem is not None:
        cv2.putText(frame, f"TIMER: {time_rem}s", (150, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0,200,255), 1)
        # timer progress bar
        bar_w = int((time_rem / run_dur) * 295)
        cv2.rectangle(frame, (8, 30), (303, 36), (40,40,40), -1)
        bar_col = (0,200,255) if time_rem/run_dur > 0.2 else (0,50,220)
        cv2.rectangle(frame, (8, 30), (8+bar_w, 36), bar_col, -1)
    elif run_dur == 0:
        cv2.putText(frame, "TIMER: unlimited", (150, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (80,80,80), 1)

    # ── Command (LARGE DISPLAY) ──
    cmd_key = cmd.split(":")[0]
    color   = CMD_COLORS.get(cmd_key[0], (200,200,200))
    
    # Command label
    cv2.putText(frame, "MOVEMENT", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (100,100,100), 1)
    
    # Large command display
    cv2.putText(frame, cmd, (10, 85),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
    
    # Movement direction text
    movement_text = ""
    if cmd.startswith("F"):
        movement_text = "FORWARD ↑"
    elif cmd.startswith("B"):
        movement_text = "BACKWARD ↓"
    elif cmd.startswith("HL"):
        movement_text = "HARD LEFT ←←"
    elif cmd.startswith("HR"):
        movement_text = "HARD RIGHT →→"
    elif cmd.startswith("L"):
        movement_text = "LEFT ←"
    elif cmd.startswith("R"):
        movement_text = "RIGHT →"
    elif cmd.startswith("S"):
        movement_text = "STOP ■"
    
    if movement_text:
        cv2.putText(frame, movement_text, (10, 110),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    # ── Reason ──
    words = reason.split()
    line1, line2 = "", ""
    for wd in words:
        if len(line1) + len(wd) < 28:
            line1 += wd + " "
        else:
            line2 += wd + " "
    cv2.putText(frame, line1.strip(), (10, 130),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (160,160,160), 1)
    cv2.putText(frame, line2.strip(), (10, 145),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (160,160,160), 1)

    # ── Vision Detection Display ──
    y_pos = 160
    cv2.putText(frame, "VISION DETECTION", (10, y_pos),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (100,100,100), 1)
    y_pos += 15
    
    if vision_obs:
        vision_color = (0, 0, 255) if vision_pos == "center" else (0, 200, 255)
        cv2.putText(frame, f"RED: {vision_pos.upper()}", (10, y_pos),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, vision_color, 1)
        y_pos += 15
        
        # Show avoidance direction
        if vision_pos == "left":
            cv2.putText(frame, "→ AVOID RIGHT", (10, y_pos),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
        elif vision_pos == "right":
            cv2.putText(frame, "← AVOID LEFT", (10, y_pos),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
        elif vision_pos == "center":
            cv2.putText(frame, "■ STOP", (10, y_pos),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)
        y_pos += 15
    else:
        cv2.putText(frame, "Clear path", (10, y_pos),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 0), 1)
        y_pos += 15

    # ── Steering timeout bar ──
    y_pos += 5
    cv2.putText(frame, "STEER TIMEOUT", (10, y_pos),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (100,100,100), 1)
    y_pos += 5
    cv2.rectangle(frame, (10, y_pos), (295, y_pos+12), (40,40,40), -1)

    if correcting:
        cv2.rectangle(frame, (10, y_pos), (295, y_pos+12), (180,60,255), -1)
        cv2.putText(frame, "CORRECTION PULSE", (12, y_pos+11),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, (255,255,255), 1)
    elif steer_dir and steer_since:
        elapsed = min(time.time() - steer_since, steer_timeout)
        pct     = elapsed / steer_timeout
        bar_w   = int(pct * 285)
        bar_col = (0,50,220) if pct > 0.75 else (0,180,220)
        cv2.rectangle(frame, (10, y_pos), (10+bar_w, y_pos+12), bar_col, -1)
        cv2.putText(frame,
                    f"{steer_dir}  {elapsed:.1f}s / {steer_timeout:.1f}s",
                    (12, y_pos+11), cv2.FONT_HERSHEY_SIMPLEX, 0.32, (255,255,255), 1)
    
    y_pos += 25

    # ── Mini car top-view with direction arrow ──
    cx, cy = 155, y_pos + 50
    cv2.rectangle(frame, (cx-24, cy-38), (cx+24, cy+38), (70,70,70), -1)
    cv2.rectangle(frame, (cx-24, cy-38), (cx+24, cy+38), (150,150,150), 1)
    # wheels
    for wx, wy in [(cx-30,cy-25),(cx+20,cy-25),(cx-30,cy+10),(cx+20,cy+10)]:
        cv2.rectangle(frame, (wx, wy), (wx+10, wy+15), (50,50,50), -1)
    # direction arrow
    arrows = {
        "F": ((cx,cy+10),(cx,cy-25)),
        "B": ((cx,cy-10),(cx,cy+25)),
        "L": ((cx+10,cy),(cx-20,cy)),
        "R": ((cx-10,cy),(cx+20,cy)),
        "S": None,
    }
    key = cmd_key[:2] if cmd_key in ("HL","HR") else cmd_key[0]
    if key == "HL": key = "L"
    if key == "HR": key = "R"
    arrow = arrows.get(key)
    if arrow:
        cv2.arrowedLine(frame, arrow[0], arrow[1], color, 3, tipLength=0.4)
    elif key == "S":
        cv2.putText(frame, "STOP", (cx-18, cy+6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0,50,220), 2)

    # ── Sensor bars ──
    def sens_bar(label, val, bx, by, horiz=False):
        MAX = 120
        filled = int(min(val/MAX, 1.0) * 55)
        bar_col = (0,200,60) if val > 30 else (0,200,220) if val > 15 else (0,50,220)
        if horiz:
            cv2.rectangle(frame, (bx, by), (bx+55, by+9), (40,40,40), -1)
            cv2.rectangle(frame, (bx, by), (bx+filled, by+9), bar_col, -1)
            cv2.putText(frame, label, (bx, by-4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.28, (130,130,130), 1)
            cv2.putText(frame, f"{val}cm", (bx, by+20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, (200,200,200), 1)
        else:
            cv2.rectangle(frame, (bx, by), (bx+9, by+55), (40,40,40), -1)
            cv2.rectangle(frame, (bx, by+55-filled), (bx+9, by+55), bar_col, -1)
            cv2.putText(frame, label, (bx-2, by-4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.28, (130,130,130), 1)
            cv2.putText(frame, f"{val}cm", (bx-4, by+68),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.28, (200,200,200), 1)

    sens_y = y_pos + 30
    sens_bar("FRT", sensors["f"], 120, sens_y, horiz=True)
    sens_bar("BCK", sensors["b"], 120, sens_y + 90, horiz=True)
    sens_bar("LFT", sensors["l"],  55, sens_y + 20)
    sens_bar("RGT", sensors["r"], 235, sens_y + 20)

    # ── Mode + Camera source ──
    mode_txt = scenario["mode"].upper().replace("_"," ")
    cam_src = "IP CAM" if camera_source["using_ip"] else "LAPTOP"
    cv2.putText(frame, f"SIM: {mode_txt} | CAM: {cam_src}", (10, h-72),
                cv2.FONT_HERSHEY_SIMPLEX, 0.36, (60,150,200), 1)
    
    # Line 1: Basic controls
    cv2.putText(frame, "S=start/stop  Q=quit  T=toggle cam  X=clear  C=clear front",
                (10, h-50), cv2.FONT_HERSHEY_SIMPLEX, 0.28, (80,80,80), 1)
    
    # Line 2: Scenarios
    cv2.putText(frame, "1=normal  2=corridor  3=wall  4=left  5=right  6=both",
                (10, h-30), cv2.FONT_HERSHEY_SIMPLEX, 0.28, (80,80,80), 1)
    
    # Line 3: Manual sensors
    cv2.putText(frame, "F=front  L=left  R=right  B=back  7-0=edge cases",
                (10, h-10), cv2.FONT_HERSHEY_SIMPLEX, 0.28, (80,80,80), 1)

    return frame

# ============================================================
# MAIN
# ============================================================
def init_camera(use_ip):
    """Initialize camera based on source type"""
    snapshot_url = None
    cap = None
    
    if use_ip:
        print(f"[CAM] Connecting to IP camera: {IP_CAMERA_URL}")
        
        if USE_SNAPSHOT_MODE:
            snapshot_url = IP_CAMERA_URL.replace("/video", "/shot.jpg")
            print(f"[CAM] Using snapshot mode: {snapshot_url}")
        else:
            cap = cv2.VideoCapture(IP_CAMERA_URL)
            if not cap.isOpened():
                print(f"[WARN] Video stream failed, trying snapshot mode...")
                snapshot_url = IP_CAMERA_URL.replace("/video", "/shot.jpg")
                print(f"[CAM] Using snapshot mode: {snapshot_url}")
                cap = None
            else:
                print(f"[CAM] IP camera video stream connected")
    else:
        print(f"[CAM] Opening laptop webcam {WEBCAM_INDEX}")
        cap = cv2.VideoCapture(WEBCAM_INDEX)
        if not cap.isOpened():
            print(f"[ERROR] Cannot open webcam {WEBCAM_INDEX}")
            return None, None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        print(f"[CAM] Laptop webcam opened")
    
    return cap, snapshot_url

def main():
    global running

    print("=" * 55)
    print("Self-Driving Car — Test Client v2")
    print("=" * 55)
    print(f"Server: http://{SERVER_IP}:{SERVER_PORT}")
    print(f"WS:     {WS_URL}")
    print()

    # Initialize camera
    cap, snapshot_url = init_camera(camera_source["using_ip"])

    threads = [
        threading.Thread(target=ws_thread,                                daemon=True),
        threading.Thread(target=sensor_thread,                            daemon=True),
        threading.Thread(target=stream_thread, args=(cap, snapshot_url),  daemon=True),
        threading.Thread(target=status_thread,                            daemon=True),
    ]
    for t in threads:
        t.start()

    print("[INFO] All threads started — press S in window to start car")
    print("\n=== KEYBOARD CONTROLS ===")
    print("S = Start/Stop car")
    print("Q = Quit")
    print("T = Toggle camera (Laptop ↔ IP Camera)")
    print("\n--- Scenarios ---")
    print("1 = Normal open road")
    print("2 = Narrow corridor (both sides close)")
    print("3 = Wall ahead (front closing)")
    print("4 = LEFT side close → expect HARD RIGHT")
    print("5 = RIGHT side close → expect HARD LEFT")
    print("6 = BOTH sides close → expect STOP")
    print("\n--- Edge Case Scenarios ---")
    print("7 = Front blocked + Left close → expect HARD RIGHT")
    print("8 = Front blocked + Right close → expect HARD LEFT")
    print("9 = Front blocked + Both close → expect STOP")
    print("0 = All sensors critical → expect STOP")
    print("\n--- Manual Sensor Control ---")
    print("F = Force front to 8cm")
    print("L = Force left to 8cm")
    print("R = Force right to 8cm")
    print("B = Force back to 15cm")
    print("C = Clear front sensor")
    print("X = Clear ALL forced sensors")
    print("========================\n")

    import urllib.request
    
    while running:
        if snapshot_url:
            # Snapshot mode for display
            try:
                with urllib.request.urlopen(snapshot_url, timeout=2) as response:
                    img_array = np.array(bytearray(response.read()), dtype=np.uint8)
                    frame = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
                    if frame is None:
                        time.sleep(0.1)
                        continue
            except Exception as e:
                time.sleep(0.1)
                continue
        else:
            # Video stream mode
            ret, frame = cap.read()
            if not ret:
                break

        frame   = cv2.resize(frame, (640, 480))
        sensors = simulate_sensors()
        display = draw_hud(frame.copy(), sensors)

        cv2.imshow("Self-Driving Car — Test Client (Q=quit)", display)

        key = cv2.waitKey(1) & 0xFF

        if key == ord('q'):
            running = False

        elif key == ord('s'):
            # Toggle start/stop on server
            with lock:
                currently_running = server_state["car_running"]
            try:
                requests.post(CONFIG_URL,
                              json={"car_running": not currently_running},
                              timeout=1)
            except Exception as e:
                print(f"[KEY S] Config POST failed: {e}")

        elif key == ord('f'):
            scenario["force_front"] = 8
            scenario["mode"] = "custom"
            print("[KEY F] Forcing front obstacle at 8cm")

        elif key == ord('c'):
            scenario["force_front"] = None
            scenario["mode"] = "normal"
            print("[KEY C] Cleared forced obstacle")

        elif key == ord('1'):
            scenario.update({"mode": "normal", "force_front": None})
            print("[KEY 1] Scenario: Normal open road")

        elif key == ord('2'):
            scenario.update({"mode": "corridor", "force_front": None})
            print("[KEY 2] Scenario: Narrow corridor")

        elif key == ord('3'):
            scenario.update({"mode": "wall_ahead", "force_front": None})
            print("[KEY 3] Scenario: Wall ahead")

        # ===== NEW STEERING TEST SCENARIOS =====
        elif key == ord('4'):
            scenario.update({
                "mode": "left_close",
                "force_front": None,
                "force_left": None,
                "force_right": None,
                "force_back": None
            })
            print("[KEY 4] Scenario: LEFT side close (8cm) → should steer RIGHT")

        elif key == ord('5'):
            scenario.update({
                "mode": "right_close",
                "force_front": None,
                "force_left": None,
                "force_right": None,
                "force_back": None
            })
            print("[KEY 5] Scenario: RIGHT side close (8cm) → should steer LEFT")

        elif key == ord('6'):
            scenario.update({
                "mode": "both_close",
                "force_front": None,
                "force_left": None,
                "force_right": None,
                "force_back": None
            })
            print("[KEY 6] Scenario: BOTH sides close → should STOP")

        # Manual sensor forcing (hold key to maintain)
        elif key == ord('l'):
            scenario["force_left"] = 8
            scenario["mode"] = "custom"
            print("[KEY L] Forcing LEFT sensor to 8cm → expect HARD RIGHT")

        elif key == ord('r'):
            scenario["force_right"] = 8
            scenario["mode"] = "custom"
            print("[KEY R] Forcing RIGHT sensor to 8cm → expect HARD LEFT")

        elif key == ord('b'):
            scenario["force_back"] = 15
            scenario["mode"] = "custom"
            print("[KEY B] Forcing BACK sensor to 15cm")

        elif key == ord('x'):
            # Clear all forced sensors
            scenario.update({
                "mode": "normal",
                "force_front": None,
                "force_left": None,
                "force_right": None,
                "force_back": None
            })
            print("[KEY X] Cleared all forced sensors → back to normal")

        elif key == ord('t'):
            # Toggle camera source
            camera_source["using_ip"] = not camera_source["using_ip"]
            print(f"\n[KEY T] Switching to {'IP CAMERA' if camera_source['using_ip'] else 'LAPTOP CAMERA'}...")
            
            # Release current camera
            if cap is not None:
                cap.release()
            
            # Initialize new camera
            cap, snapshot_url = init_camera(camera_source["using_ip"])
            
            # Restart stream thread
            running = False
            time.sleep(0.3)
            running = True
            threading.Thread(target=stream_thread, args=(cap, snapshot_url), daemon=True).start()
            print(f"[CAM] Switched to {'IP camera' if camera_source['using_ip'] else 'laptop webcam'}\n")

        # ===== EDGE CASE SCENARIOS =====
        elif key == ord('7'):
            # Front blocked + Left close → should steer HARD RIGHT
            scenario.update({
                "mode": "custom",
                "force_front": 8,
                "force_left": 8,
                "force_right": None,
                "force_back": None
            })
            print("[KEY 7] Edge Case: Front blocked + Left close → expect HARD RIGHT")

        elif key == ord('8'):
            # Front blocked + Right close → should steer HARD LEFT
            scenario.update({
                "mode": "custom",
                "force_front": 8,
                "force_left": None,
                "force_right": 8,
                "force_back": None
            })
            print("[KEY 8] Edge Case: Front blocked + Right close → expect HARD LEFT")

        elif key == ord('9'):
            # Front blocked + Both sides close → should STOP
            scenario.update({
                "mode": "custom",
                "force_front": 8,
                "force_left": 8,
                "force_right": 8,
                "force_back": None
            })
            print("[KEY 9] Edge Case: Front + Both sides blocked → expect STOP")

        elif key == ord('0'):
            # All sensors critical → should STOP
            scenario.update({
                "mode": "custom",
                "force_front": 5,
                "force_left": 5,
                "force_right": 5,
                "force_back": 5
            })
            print("[KEY 0] Edge Case: ALL sensors critical → expect STOP")

    running = False
    if cap is not None:
        cap.release()
    cv2.destroyAllWindows()
    print("[INFO] Exited cleanly")

if __name__ == "__main__":
    main()