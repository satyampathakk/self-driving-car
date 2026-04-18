"""
============================================================
Self-Driving Car — Python Server  (v2 — with steering timeout & run timer)
============================================================
New features:
  - Steering correction timeout: if car steers left/right for longer
    than `steer_timeout` seconds it forces a correction back to centre
  - Run timer: car runs for `run_duration` seconds then auto-stops
  - Dashboard controls: start/stop button, adjust all timers live
  - All tunable params exposed via /config  (GET + POST JSON)
============================================================
"""

import cv2
import numpy as np
import threading
import time
import urllib.request
import logging
from flask import Flask, Response, render_template_string, jsonify, request
from flask_sock import Sock
from vision_obstacle_detector import VisionObstacleDetector

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# VISION OBSTACLE DETECTOR (RED OBJECTS)
# ============================================================
vision_detector = VisionObstacleDetector(min_area=500, history_size=5)
log.info("Vision obstacle detector initialized (detects RED objects)")

# ============================================================
# CONFIG  (all tunable at runtime via /config POST or dashboard)
# ============================================================
cfg_lock = threading.Lock()
cfg = {
    # Safety thresholds (cm)
    "stop_distance":    15,
    "slow_distance":    30,
    "side_distance":    12,

    # Speeds  (0-255)
    "base_speed":       200,
    "slow_speed":       130,
    "turn_speed":       160,

    # Steering correction
    # If the car steers in one direction for longer than this (seconds),
    # it issues a correction pulse in the opposite direction for
    # `correction_duration` seconds to bring it back to the original line.
    "steer_timeout":        2.0,   # seconds before correction kicks in
    "correction_duration":  0.8,   # seconds to hold correction pulse

    # Lane dead-zone (pixels) — below this offset we consider car centred
    "steer_threshold":  30,

    # Run timer
    # car_running = True → car is allowed to move
    # run_duration > 0   → auto-stop after this many seconds
    # run_duration = 0   → run indefinitely (until manual stop)
    "run_duration":     0,         # seconds, 0 = unlimited
    "car_running":      False,     # start stopped; use dashboard Start button
}

# ============================================================
# RUNTIME STATE
# ============================================================
state_lock = threading.Lock()
state = {
    "sensors":          {"f": 999, "b": 999, "l": 999, "r": 999},
    "command":          "S:0",
    "reason":           "Stopped — press Start",
    "frame":            None,
    "steer_direction":  None,      # "L" | "R" | None
    "steer_since":      None,      # time.time() when current steer started
    "correcting":       False,     # True while correction pulse is active
    "correction_until": 0,         # time.time() when correction ends
    "run_started_at":   None,      # time.time() when car was started
    "time_remaining":   None,      # seconds left on run timer (None = unlimited)
    "vision_obstacle":  False,     # Red obstacle detected
    "vision_position":  "none",    # Position: left/right/center/none
}

ws_clients = set()
ws_lock    = threading.Lock()

app  = Flask(__name__)
sock = Sock(app)

# ============================================================
# WEBSOCKET
# ============================================================
@sock.route("/ws")
def websocket_endpoint(ws):
    global ws_clients
    log.info("Client connected via WebSocket")
    with ws_lock:
        ws_clients.add(ws)
    try:
        while True:
            msg = ws.receive(timeout=30)
            if msg is None:
                break
    except Exception:
        pass
    finally:
        with ws_lock:
            ws_clients.discard(ws)
        log.info("WebSocket client disconnected")

def send_command(cmd: str):
    global ws_clients
    dead = set()
    with ws_lock:
        for ws in ws_clients:
            try:
                ws.send(cmd)
            except Exception:
                dead.add(ws)
        ws_clients -= dead

# ============================================================
# CONFIG ENDPOINT
# ============================================================
@app.route("/config", methods=["GET", "POST"])
def config_endpoint():
    if request.method == "GET":
        with cfg_lock:
            return jsonify(dict(cfg))

    data = request.get_json(force=True, silent=True) or {}
    with cfg_lock:
        for key, val in data.items():
            if key in cfg:
                # cast to same type as existing value
                try:
                    cfg[key] = type(cfg[key])(val)
                except (ValueError, TypeError):
                    pass

        # Handle start/stop
        if "car_running" in data:
            running = bool(data["car_running"])
            cfg["car_running"] = running
            if running:
                with state_lock:
                    state["run_started_at"] = time.time()
                    state["steer_direction"] = None
                    state["steer_since"]     = None
                    state["correcting"]      = False
                    state["reason"]          = "Starting..."  # Clear old reason
                log.info("Car STARTED")
            else:
                with state_lock:
                    state["reason"] = "Stopped — press Start"
                log.info("Car STOPPED")
                send_command("S:0")

    return jsonify({"ok": True})

# ============================================================
# SENSOR ENDPOINT
# ============================================================
@app.route("/sensors", methods=["POST"])
def sensors():
    data = request.get_json(force=True, silent=True)
    if data:
        with state_lock:
            state["sensors"].update(data)
    return "", 204

# ============================================================
# FRAME UPLOAD  (from test_client.py)
# ============================================================
@app.route("/upload_frame", methods=["POST"])
def upload_frame():
    try:
        jpg_bytes = request.data
        if not jpg_bytes:
            return "", 400
        img = cv2.imdecode(np.frombuffer(jpg_bytes, dtype=np.uint8),
                           cv2.IMREAD_COLOR)
        if img is None:
            return "", 400
        img = cv2.resize(img, (320, 240))
        process_frame(img)
        return "", 204
    except Exception as e:
        log.error(f"Frame upload error: {e}")
        import traceback
        traceback.print_exc()
        return str(e), 500

# ============================================================
# VISION
# ============================================================
def detect_lanes(frame):
    h, w = frame.shape[:2]
    roi_top  = int(h * 0.55)
    roi      = frame[roi_top:h, :]
    gray     = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    blur     = cv2.GaussianBlur(gray, (5, 5), 0)
    edges    = cv2.Canny(blur, 50, 150)

    lines = cv2.HoughLinesP(edges, 1, np.pi / 180,
                             threshold=30,
                             minLineLength=40,
                             maxLineGap=80)
    left_xs, right_xs = [], []
    mid_x    = w // 2
    annotated = frame.copy()

    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 == x1:
                continue
            slope = (y2 - y1) / (x2 - x1)
            if abs(slope) < 0.3:
                continue
            cv2.line(annotated,
                     (x1, y1 + roi_top), (x2, y2 + roi_top),
                     (0, 255, 0), 2)
            cx = (x1 + x2) // 2
            (left_xs if cx < mid_x else right_xs).append(cx)

    left_x       = int(np.mean(left_xs))  if left_xs  else 0
    right_x      = int(np.mean(right_xs)) if right_xs else w
    lane_centre  = (left_x + right_x) // 2
    steer_offset = lane_centre - mid_x

    cv2.line(annotated, (lane_centre, roi_top), (lane_centre, h), (0, 0, 255), 2)
    cv2.line(annotated, (mid_x, roi_top),       (mid_x, h),       (255, 255, 0), 1)
    return steer_offset, annotated

def detect_red_objects(frame):
    """
    Detect red objects using HSV color detection
    Returns: (obstacle_detected, position, mask)
    """
    h, w = frame.shape[:2]
    
    # Convert to HSV
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    
    # Red color ranges (two ranges because red wraps around in HSV)
    lower1 = np.array([0, 120, 70])
    upper1 = np.array([10, 255, 255])
    lower2 = np.array([160, 120, 70])
    upper2 = np.array([180, 255, 255])
    
    mask1 = cv2.inRange(hsv, lower1, upper1)
    mask2 = cv2.inRange(hsv, lower2, upper2)
    mask = mask1 + mask2
    
    # Find contours
    contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    
    obstacle = False
    position = "none"
    
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area > 500:
            x, y, w_box, h_box = cv2.boundingRect(cnt)
            cx = x + w_box // 2
            obstacle = True
            
            # Determine position
            if cx < w // 3:
                position = "left"
            elif cx > 2 * w // 3:
                position = "right"
            else:
                position = "center"
            
            # Draw red bounding box
            cv2.rectangle(frame, (x, y), (x + w_box, y + h_box), (0, 0, 255), 2)
            cv2.putText(frame, "RED", (x, y - 5),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1)
    
    return obstacle, position, frame


def detect_obstacle(frame):
    """Legacy contour-based obstacle detection"""
    h, w = frame.shape[:2]
    roi  = frame[int(h * 0.5):h, int(w * 0.25):int(w * 0.75)]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 60, 255, cv2.THRESH_BINARY_INV)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL,
                                    cv2.CHAIN_APPROX_SIMPLE)
    return any(cv2.contourArea(c) > 4000 for c in contours)

# ============================================================
# STEERING TRACKER — prevents infinite steer in one direction
# ============================================================
def track_and_correct_steer(raw_cmd: str) -> tuple[str, str, bool]:
    """
    Given the raw command from decide(), applies steering timeout logic.

    Returns (final_cmd, extra_reason, is_correcting)

    Logic:
      - If car is steering L or R, track how long it has been doing so.
      - If it exceeds steer_timeout seconds → start a correction pulse in
        the opposite direction for correction_duration seconds.
      - During correction the command is overridden to the opposite direction.
      - After correction finishes, steering state resets and normal logic resumes.
    """
    now = time.time()
    with cfg_lock:
        steer_timeout       = cfg["steer_timeout"]
        correction_duration = cfg["correction_duration"]
        turn_speed          = cfg["turn_speed"]

    with state_lock:
        correcting       = state["correcting"]
        correction_until = state["correction_until"]
        steer_direction  = state["steer_direction"]
        steer_since      = state["steer_since"]

    cmd_dir = raw_cmd.split(":")[0]   # e.g. "F", "L", "R", "S", "HL", "HR"

    # --- Active correction pulse ---
    if correcting:
        if now < correction_until:
            # Still in correction window — hold opposite direction
            opp = "R" if steer_direction == "L" else "L"
            corr_cmd = f"{opp}:{turn_speed}"
            return corr_cmd, f"Correction pulse ({opp}) → recentring", True
        else:
            # Correction finished — reset state
            with state_lock:
                state["correcting"]      = False
                state["steer_direction"] = None
                state["steer_since"]     = None
            return raw_cmd, "", False

    # --- Track steering direction ---
    is_steer = cmd_dir in ("L", "R", "HL", "HR")
    canonical = "L" if cmd_dir in ("L", "HL") else ("R" if cmd_dir in ("R", "HR") else None)

    if is_steer and canonical:
        if steer_direction != canonical:
            # New steer direction — reset timer
            with state_lock:
                state["steer_direction"] = canonical
                state["steer_since"]     = now
        else:
            # Same direction — check elapsed
            elapsed = now - (steer_since or now)
            if elapsed >= steer_timeout:
                # Timeout! Start correction
                with state_lock:
                    state["correcting"]      = True
                    state["correction_until"] = now + correction_duration
                opp      = "R" if canonical == "L" else "L"
                corr_cmd = f"{opp}:{turn_speed}"
                log.info(f"[STEER] Timeout after {elapsed:.1f}s → correction {opp}")
                return corr_cmd, f"Steer timeout ({elapsed:.1f}s) → correction {opp}", True
    else:
        # Not steering — reset tracker
        if steer_direction is not None:
            with state_lock:
                state["steer_direction"] = None
                state["steer_since"]     = None

    return raw_cmd, "", False

# ============================================================
# RUN TIMER
# ============================================================
def check_run_timer() -> tuple[bool, float | None]:
    """
    Returns (should_run, seconds_remaining)
    should_run = False → override everything with STOP
    """
    with cfg_lock:
        car_running  = cfg["car_running"]
        run_duration = cfg["run_duration"]

    if not car_running:
        return False, None

    if run_duration <= 0:
        return True, None   # unlimited

    with state_lock:
        started_at = state["run_started_at"]

    if started_at is None:
        return True, None

    elapsed   = time.time() - started_at
    remaining = run_duration - elapsed

    if remaining <= 0:
        # Auto-stop
        with cfg_lock:
            cfg["car_running"] = False
        send_command("S:0")
        log.info("[TIMER] Run timer expired — car stopped")
        return False, 0

    with state_lock:
        state["time_remaining"] = round(remaining, 1)

    return True, round(remaining, 1)

# ============================================================
# DECISION ENGINE
# ============================================================
def decide(steer_offset: int, visual_obstacle: bool) -> tuple[str, str]:
    with state_lock:
        sensors = dict(state["sensors"])
    with cfg_lock:
        stop_d   = cfg["stop_distance"]
        slow_d   = cfg["slow_distance"]
        side_d   = cfg["side_distance"]
        base_spd = cfg["base_speed"]
        slow_spd = cfg["slow_speed"]
        turn_spd = cfg["turn_speed"]
        steer_th = cfg["steer_threshold"]

    front = sensors.get("f", 999)
    back  = sensors.get("b", 999)
    left  = sensors.get("l", 999)
    right = sensors.get("r", 999)

    if front < stop_d or visual_obstacle:
        reason = f"OBSTACLE front={front}cm visual={visual_obstacle}"
        if back > 30:
            return f"B:{slow_spd}", reason + " → reversing"
        return "S:0", reason + " → stopping"

    if left < side_d and right >= side_d:
        return f"HR:{turn_spd}", f"Too close left ({left}cm) → hard right"
    if right < side_d and left >= side_d:
        return f"HL:{turn_spd}", f"Too close right ({right}cm) → hard left"
    if left < side_d and right < side_d:
        return "S:0", f"Boxed in L={left} R={right} → stop"

    speed = slow_spd if front < slow_d else base_spd

    if steer_offset > steer_th:
        intensity = min(int(abs(steer_offset) / 3), 60)
        return f"L:{turn_spd - intensity}", f"Lane offset +{steer_offset}px → left"
    elif steer_offset < -steer_th:
        intensity = min(int(abs(steer_offset) / 3), 60)
        return f"R:{turn_spd - intensity}", f"Lane offset {steer_offset}px → right"

    return f"F:{speed}", f"Centred (offset={steer_offset}px, front={front}cm)"

# ============================================================
# FRAME PROCESSOR
# ============================================================
def process_frame(img):
    try:
        # 1. Check run timer
        should_run, time_remaining = check_run_timer()
        with state_lock:
            state["time_remaining"] = time_remaining

        if not should_run:
            final_cmd = "S:0"
            reason    = "Stopped" if time_remaining is None else f"Timer expired"
            send_command(final_cmd)
            with state_lock:
                state["command"] = final_cmd
                state["reason"]  = reason
                state["vision_obstacle"] = False
                state["vision_position"] = "none"
            _annotate_and_store(img, final_cmd, reason, False, 0, False, time_remaining)
            return

        # 2. Vision-based red obstacle detection
        vision_obstacle, vision_pos, vision_frame, vision_mask = vision_detector.detect(img)
        vision_pos_smoothed = vision_detector.get_smoothed_position(vision_pos)
        
        # 3. Lane detection on annotated frame
        steer_offset, annotated = detect_lanes(vision_frame)
        
        # Legacy obstacle detection (kept for compatibility)
        legacy_visual_obs = detect_obstacle(vision_frame)
        
        # Combine obstacle detections
        combined_obstacle = legacy_visual_obs or (vision_obstacle and vision_pos_smoothed == "center")

        # 4. Raw decision
        raw_cmd, raw_reason = decide(steer_offset, combined_obstacle)
        
        # Override with vision-based avoidance if red obstacle detected
        if vision_obstacle:
            with cfg_lock:
                turn_spd = cfg["turn_speed"]
            
            avoid_dir = vision_detector.get_avoidance_direction(vision_pos_smoothed)
            if avoid_dir == "RIGHT":
                raw_cmd = f"HR:{turn_spd}"
                raw_reason = f"Red obstacle on {vision_pos_smoothed} → hard right"
            elif avoid_dir == "LEFT":
                raw_cmd = f"HL:{turn_spd}"
                raw_reason = f"Red obstacle on {vision_pos_smoothed} → hard left"
            elif avoid_dir == "STOP":
                raw_cmd = "S:0"
                raw_reason = f"Red obstacle in center → stop"

        # 5. Steering correction override
        final_cmd, corr_reason, is_correcting = track_and_correct_steer(raw_cmd)

        reason = corr_reason if is_correcting else raw_reason

        # 6. Send & store
        send_command(final_cmd)
        with state_lock:
            state["command"]    = final_cmd
            state["reason"]     = reason
            state["correcting"] = is_correcting
            state["vision_obstacle"] = vision_obstacle
            state["vision_position"] = vision_pos_smoothed

        _annotate_and_store(annotated, final_cmd, reason, combined_obstacle,
                            steer_offset, is_correcting, time_remaining)
    except Exception as e:
        log.error(f"process_frame error: {e}")
        import traceback
        traceback.print_exc()
        # Set error state
        with state_lock:
            state["command"] = "S:0"
            state["reason"] = f"Error: {str(e)}"

def _annotate_and_store(frame, cmd, reason, visual_obs=False,
                        steer_offset=0, correcting=False, time_remaining=None):
    with state_lock:
        sensors = dict(state["sensors"])
        vision_obs = state.get("vision_obstacle", False)
        vision_pos = state.get("vision_position", "none")

    hud = frame.copy() if hasattr(frame, 'copy') else frame
    color = (0, 80, 220) if cmd.startswith("S") else \
            (0, 220, 80) if cmd.startswith("F") else \
            (220, 200, 0) if cmd[0] in "LR" else \
            (0, 200, 220)

    cv2.putText(hud, f"CMD: {cmd}", (5, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    if correcting:
        cv2.putText(hud, "CORRECTING", (5, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 100, 255), 2)
    else:
        cv2.putText(hud, reason[:48], (5, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.37, (200, 200, 200), 1)

    # Sensor readings
    cv2.putText(hud,
                f"F:{sensors['f']}  B:{sensors['b']}  "
                f"L:{sensors['l']}  R:{sensors['r']} cm",
                (5, 228), cv2.FONT_HERSHEY_SIMPLEX, 0.37, (0, 255, 255), 1)

    # Vision detection display
    if vision_obs:
        vision_color = (0, 0, 255) if vision_pos == "center" else (0, 200, 255)
        cv2.putText(hud, f"RED OBSTACLE: {vision_pos.upper()}", (5, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, vision_color, 1)

    if visual_obs:
        cv2.putText(hud, "VISUAL OBSTACLE", (55, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)

    if time_remaining is not None:
        cv2.putText(hud, f"TIME: {time_remaining}s", (230, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 180, 0), 1)

    _, jpg = cv2.imencode(".jpg", hud, [cv2.IMWRITE_JPEG_QUALITY, 80])
    with state_lock:
        state["frame"] = jpg.tobytes()

# ============================================================
# ESP32-CAM stream mode
# ============================================================
def esp32_stream_loop():
    log.info(f"Pulling ESP32 stream: {urllib.request.urlopen}")
    while True:
        try:
            stream   = urllib.request.urlopen(
                f"http://{urllib.request.urlopen}:81/stream", timeout=5)
            byte_buf = b""
            while True:
                byte_buf += stream.read(4096)
                a = byte_buf.find(b'\xff\xd8')
                b_idx = byte_buf.find(b'\xff\xd9')
                if a != -1 and b_idx != -1 and b_idx > a:
                    jpg      = byte_buf[a:b_idx+2]
                    byte_buf = byte_buf[b_idx+2:]
                    img = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8),
                                       cv2.IMREAD_COLOR)
                    if img is not None:
                        process_frame(cv2.resize(img, (320, 240)))
        except Exception as e:
            log.warning(f"ESP32 stream error: {e} — retry in 2s")
            send_command("S:0")
            time.sleep(2)

# ============================================================
# MJPEG FEED
# ============================================================
def gen_frames():
    while True:
        with state_lock:
            frame = state.get("frame")
        if frame:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
        time.sleep(0.04)

@app.route("/video_feed")
def video_feed():
    return Response(gen_frames(),
                    mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/status")
def status_endpoint():
    with state_lock:
        s = dict(state)
        s.pop("frame", None)
    with cfg_lock:
        c = dict(cfg)
    return jsonify({**s, **c})

# ============================================================
# DASHBOARD HTML
# ============================================================
DASHBOARD = r"""
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Self-Driving Car Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg:    #0d0d0d;
    --bg2:   #161616;
    --bg3:   #1e1e1e;
    --border:#2a2a2a;
    --accent:#00ccff;
    --green: #00ff88;
    --warn:  #ffcc00;
    --danger:#ff4444;
    --purple:#bb88ff;
    --text:  #cccccc;
    --muted: #666;
  }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Courier New', monospace;
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 16px;
    gap: 14px;
  }
  header {
    display: flex;
    align-items: center;
    gap: 16px;
    width: 100%;
    max-width: 900px;
  }
  header h1 { color: var(--accent); font-size: 16px; letter-spacing: 3px; flex: 1; }
  .badge {
    font-size: 11px;
    padding: 3px 10px;
    border-radius: 20px;
    border: 1px solid;
    letter-spacing: 1px;
  }
  .badge.running  { color: var(--green);  border-color: var(--green);  background: #00ff8820; }
  .badge.stopped  { color: var(--danger); border-color: var(--danger); background: #ff444420; }
  .badge.correct  { color: var(--purple); border-color: var(--purple); background: #bb88ff20; }

  /* Main layout */
  .layout { display: flex; gap: 14px; width: 100%; max-width: 900px; flex-wrap: wrap; }
  .left  { display: flex; flex-direction: column; gap: 10px; flex: 1; min-width: 300px; }
  .right { display: flex; flex-direction: column; gap: 10px; flex: 0 0 300px; }

  /* Video */
  #video-wrap { position: relative; border: 2px solid var(--accent); border-radius: 8px; overflow: hidden; }
  #video { display: block; width: 100%; }
  #overlay {
    position: absolute; bottom: 0; left: 0; right: 0;
    background: linear-gradient(transparent, #000000cc);
    padding: 8px 10px;
    font-size: 12px;
    color: var(--green);
  }
  #timer-bar-wrap {
    height: 4px;
    background: var(--bg3);
    border-radius: 2px;
    margin-top: 4px;
    overflow: hidden;
  }
  #timer-bar { height: 100%; background: var(--accent); transition: width 0.2s; width: 100%; }

  /* Cards */
  .card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 14px;
  }
  .card h3 {
    font-size: 10px;
    letter-spacing: 2px;
    color: var(--muted);
    text-transform: uppercase;
    margin-bottom: 10px;
  }

  /* Start/Stop */
  #btn-start {
    width: 100%;
    padding: 12px;
    font-size: 15px;
    font-family: inherit;
    letter-spacing: 2px;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.2s, transform 0.1s;
    font-weight: bold;
  }
  #btn-start:active { transform: scale(0.97); }
  #btn-start.start { background: var(--green);  color: #000; }
  #btn-start.stop  { background: var(--danger); color: #fff; }

  /* Command display */
  #cmd-display {
    font-size: 28px;
    font-weight: bold;
    text-align: center;
    padding: 10px;
    border-radius: 6px;
    background: var(--bg3);
    letter-spacing: 2px;
  }
  #reason-display {
    font-size: 11px;
    color: var(--muted);
    text-align: center;
    margin-top: 6px;
    min-height: 16px;
  }

  /* Sensors */
  .sensors-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
  .sens-item { text-align: center; background: var(--bg3); border-radius: 6px; padding: 8px; }
  .sens-item .sl { font-size: 10px; color: var(--muted); letter-spacing: 1px; }
  .sens-item .sv { font-size: 22px; font-weight: bold; }
  .ok   { color: var(--green); }
  .warn { color: var(--warn); }
  .crit { color: var(--danger); }

  /* Steer tracker */
  #steer-track {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
  }
  #steer-bar-wrap {
    flex: 1;
    height: 8px;
    background: var(--bg3);
    border-radius: 4px;
    overflow: hidden;
  }
  #steer-bar { height: 100%; width: 0%; background: var(--warn); transition: width 0.2s; border-radius: 4px; }

  /* Sliders */
  .slider-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  .slider-row label {
    font-size: 11px;
    color: var(--muted);
    width: 145px;
    flex-shrink: 0;
  }
  .slider-row input[type=range] {
    flex: 1;
    accent-color: var(--accent);
  }
  .slider-row .val-lbl {
    font-size: 12px;
    color: var(--text);
    width: 42px;
    text-align: right;
    flex-shrink: 0;
  }

  /* Divider */
  .divider { border: none; border-top: 1px solid var(--border); margin: 8px 0; }

  /* Log */
  #log {
    font-size: 11px;
    color: var(--muted);
    height: 80px;
    overflow-y: auto;
    background: var(--bg3);
    border-radius: 6px;
    padding: 6px 8px;
    line-height: 1.6;
  }
  #log .entry { color: var(--accent); }
  #log .entry.warn { color: var(--warn); }
  #log .entry.corr { color: var(--purple); }
</style>
</head>
<body>

<header>
  <h1>&#9632; SELF-DRIVING CAR</h1>
  <span class="badge stopped" id="status-badge">STOPPED</span>
</header>

<div class="layout">

  <!-- LEFT COLUMN -->
  <div class="left">

    <!-- Video -->
    <div class="card" style="padding:0">
      <div id="video-wrap">
        <img id="video" src="/video_feed" alt="Camera feed">
        <div id="overlay">
          <span id="cmd-overlay">CMD: —</span>
          &nbsp;&nbsp;
          <span id="timer-overlay"></span>
          <div id="timer-bar-wrap"><div id="timer-bar"></div></div>
        </div>
      </div>
    </div>

    <!-- Command -->
    <div class="card">
      <h3>Current Command</h3>
      <div id="cmd-display">S:0</div>
      <div id="reason-display">—</div>
    </div>

    <!-- Sensors -->
    <div class="card">
      <h3>Ultrasonic Sensors (cm)</h3>
      <div class="sensors-grid">
        <div class="sens-item"><div class="sl">FRONT</div><div class="sv ok" id="sf">—</div></div>
        <div class="sens-item"><div class="sl">BACK</div><div class="sv ok"  id="sb">—</div></div>
        <div class="sens-item"><div class="sl">LEFT</div><div class="sv ok"  id="sl">—</div></div>
        <div class="sens-item"><div class="sl">RIGHT</div><div class="sv ok" id="sr">—</div></div>
      </div>
    </div>

    <!-- Vision Obstacle Detection -->
    <div class="card">
      <h3>Vision Detection (Red Objects)</h3>
      <div id="vision-status" style="font-size:13px;padding:8px;background:var(--bg3);border-radius:6px;margin-bottom:4px;min-height:24px;color:var(--green)">
        No red obstacles
      </div>
      <div id="vision-position" style="font-size:11px;color:var(--muted);">
        Position: none
      </div>
    </div>

    <!-- Steering tracker -->
    <div class="card">
      <h3>Steering Tracker</h3>
      <div id="steer-track">
        <span id="steer-dir-lbl" style="width:40px;color:var(--warn)">—</span>
        <div id="steer-bar-wrap"><div id="steer-bar"></div></div>
        <span id="steer-time-lbl" style="width:48px;text-align:right;font-size:11px">0.0s</span>
        <span id="corr-lbl" style="width:60px;font-size:10px;color:var(--purple)"></span>
      </div>
      <div style="font-size:10px;color:var(--muted);margin-top:6px">
        Bar fills to steer timeout → correction pulse fires
      </div>
    </div>

    <!-- Event log -->
    <div class="card">
      <h3>Event Log</h3>
      <div id="log"></div>
    </div>

  </div><!-- /left -->

  <!-- RIGHT COLUMN -->
  <div class="right">

    <!-- Start / Stop -->
    <div class="card">
      <h3>Control</h3>
      <button id="btn-start" class="start" onclick="toggleCar()">&#9654; START</button>
    </div>

    <!-- Run timer -->
    <div class="card">
      <h3>Run Timer</h3>
      <div class="slider-row">
        <label>Duration (0 = unlimited)</label>
        <input type="range" id="sl-run" min="0" max="120" step="5" value="0"
               oninput="updateSlider('sl-run','lbl-run',v=>v==0?'∞':v+'s'); sendCfg('run_duration',+this.value)">
        <span class="val-lbl" id="lbl-run">∞</span>
      </div>
      <div style="font-size:11px;color:var(--muted)">
        Car auto-stops after this duration.<br>
        Resets every time you press Start.
      </div>
    </div>

    <!-- Steering correction -->
    <div class="card">
      <h3>Steering Correction</h3>
      <div class="slider-row">
        <label>Steer timeout (s)</label>
        <input type="range" id="sl-stto" min="0.5" max="10" step="0.5" value="2"
               oninput="updateSlider('sl-stto','lbl-stto',v=>v+'s'); sendCfg('steer_timeout',+this.value)">
        <span class="val-lbl" id="lbl-stto">2s</span>
      </div>
      <div class="slider-row">
        <label>Correction pulse (s)</label>
        <input type="range" id="sl-corr" min="0.2" max="3" step="0.1" value="0.8"
               oninput="updateSlider('sl-corr','lbl-corr',v=>v+'s'); sendCfg('correction_duration',+this.value)">
        <span class="val-lbl" id="lbl-corr">0.8s</span>
      </div>
      <div class="slider-row">
        <label>Steer dead-zone (px)</label>
        <input type="range" id="sl-stth" min="5" max="100" step="5" value="30"
               oninput="updateSlider('sl-stth','lbl-stth',v=>v+'px'); sendCfg('steer_threshold',+this.value)">
        <span class="val-lbl" id="lbl-stth">30px</span>
      </div>
    </div>

    <!-- Safety thresholds -->
    <div class="card">
      <h3>Safety Thresholds</h3>
      <div class="slider-row">
        <label>Emergency stop (cm)</label>
        <input type="range" id="sl-stop" min="5" max="50" value="15"
               oninput="updateSlider('sl-stop','lbl-stop',v=>v+'cm'); sendCfg('stop_distance',+this.value)">
        <span class="val-lbl" id="lbl-stop">15cm</span>
      </div>
      <div class="slider-row">
        <label>Slow zone (cm)</label>
        <input type="range" id="sl-slow" min="10" max="100" value="30"
               oninput="updateSlider('sl-slow','lbl-slow',v=>v+'cm'); sendCfg('slow_distance',+this.value)">
        <span class="val-lbl" id="lbl-slow">30cm</span>
      </div>
      <div class="slider-row">
        <label>Side avoid (cm)</label>
        <input type="range" id="sl-side" min="5" max="40" value="12"
               oninput="updateSlider('sl-side','lbl-side',v=>v+'cm'); sendCfg('side_distance',+this.value)">
        <span class="val-lbl" id="lbl-side">12cm</span>
      </div>
    </div>

    <!-- Speed -->
    <div class="card">
      <h3>Speed (0–255)</h3>
      <div class="slider-row">
        <label>Base speed</label>
        <input type="range" id="sl-base" min="50" max="255" value="200"
               oninput="updateSlider('sl-base','lbl-base',v=>v); sendCfg('base_speed',+this.value)">
        <span class="val-lbl" id="lbl-base">200</span>
      </div>
      <div class="slider-row">
        <label>Slow speed</label>
        <input type="range" id="sl-slsp" min="30" max="200" value="130"
               oninput="updateSlider('sl-slsp','lbl-slsp',v=>v); sendCfg('slow_speed',+this.value)">
        <span class="val-lbl" id="lbl-slsp">130</span>
      </div>
      <div class="slider-row">
        <label>Turn speed</label>
        <input type="range" id="sl-turn" min="30" max="255" value="160"
               oninput="updateSlider('sl-turn','lbl-turn',v=>v); sendCfg('turn_speed',+this.value)">
        <span class="val-lbl" id="lbl-turn">160</span>
      </div>
    </div>

  </div><!-- /right -->
</div><!-- /layout -->

<script>
  let carRunning    = false;
  let runDuration   = 0;
  let steerTimeout  = 2.0;
  let prevCmd       = "";
  let prevCorr      = false;
  const log         = document.getElementById('log');

  // ── Slider helper ──
  function updateSlider(sliderId, lblId, fmt) {
    const v = document.getElementById(sliderId).value;
    document.getElementById(lblId).textContent = fmt(v);
  }

  // ── Send config key/value to server ──
  async function sendCfg(key, value) {
    await fetch('/config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({[key]: value})
    });
  }

  // ── Start / Stop ──
  async function toggleCar() {
    carRunning = !carRunning;
    await fetch('/config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({car_running: carRunning})
    });
    updateStartBtn();
    addLog(carRunning ? 'Car STARTED' : 'Car STOPPED', carRunning ? '' : 'warn');
  }

  function updateStartBtn() {
    const btn = document.getElementById('btn-start');
    if (carRunning) {
      btn.textContent = '⏹ STOP';
      btn.className   = 'stop';
    } else {
      btn.textContent = '▶ START';
      btn.className   = 'start';
    }
  }

  // ── Sensor colour ──
  function sensorClass(v) {
    return v > 30 ? 'sv ok' : v > 15 ? 'sv warn' : 'sv crit';
  }

  // ── Event log ──
  function addLog(msg, cls='entry') {
    const el = document.createElement('div');
    el.className = 'entry ' + cls;
    const ts = new Date().toLocaleTimeString();
    el.textContent = `[${ts}] ${msg}`;
    log.prepend(el);
    if (log.children.length > 40) log.lastChild.remove();
  }

  // ── Poll /status every 150ms ──
  async function poll() {
    try {
      const d = await (await fetch('/status')).json();

      // Car running state (may have been auto-stopped by timer)
      if (d.car_running !== carRunning) {
        carRunning = d.car_running;
        updateStartBtn();
        if (!carRunning) addLog('Run timer expired — auto stop', 'warn');
      }

      // Badge
      const badge = document.getElementById('status-badge');
      if (d.correcting) {
        badge.textContent = 'CORRECTING';
        badge.className   = 'badge correct';
      } else if (carRunning) {
        badge.textContent = 'RUNNING';
        badge.className   = 'badge running';
      } else {
        badge.textContent = 'STOPPED';
        badge.className   = 'badge stopped';
      }

      // Command
      const cmd = d.command || 'S:0';
      document.getElementById('cmd-display').textContent = cmd;
      document.getElementById('reason-display').textContent = d.reason || '';
      document.getElementById('cmd-overlay').textContent = 'CMD: ' + cmd;

      // Cmd colour
      const cmdEl = document.getElementById('cmd-display');
      cmdEl.style.color = cmd.startsWith('S') ? 'var(--danger)'
                        : cmd.startsWith('F') ? 'var(--green)'
                        : cmd.startsWith('B') ? '#88aaff'
                        : 'var(--warn)';

      // Log new commands
      if (cmd !== prevCmd) {
        addLog(cmd + '  ' + (d.reason||''), d.correcting ? 'corr' : 'entry');
        prevCmd = cmd;
      }

      // Sensors
      const s = d.sensors || {};
      ['f','b','l','r'].forEach((k,i) => {
        const ids = ['sf','sb','sl','sr'];
        const el = document.getElementById(ids[i]);
        el.textContent = s[k] ?? '—';
        el.className   = sensorClass(s[k] ?? 999);
      });

      // Vision Obstacle Detection Display
      const visionObs = d.vision_obstacle || false;
      const visionPos = d.vision_position || 'none';
      const visionStatusEl = document.getElementById('vision-status');
      const visionPosEl = document.getElementById('vision-position');
      
      if (visionObs) {
        visionStatusEl.textContent = '🔴 Red obstacle detected!';
        visionStatusEl.style.color = 'var(--danger)';
        visionPosEl.textContent = `Position: ${visionPos.toUpperCase()}`;
        visionPosEl.style.color = 'var(--warn)';
      } else {
        visionStatusEl.textContent = '✓ No red obstacles';
        visionStatusEl.style.color = 'var(--green)';
        visionPosEl.textContent = 'Position: none';
        visionPosEl.style.color = 'var(--muted)';
      }

      // Steer tracker
      const steerDir  = d.steer_direction;
      const steerSince = d.steer_since;
      const stto       = d.steer_timeout || 2;
      steerTimeout     = stto;

      const dirEl  = document.getElementById('steer-dir-lbl');
      const barEl  = document.getElementById('steer-bar');
      const timeEl = document.getElementById('steer-time-lbl');
      const corrEl = document.getElementById('corr-lbl');

      if (d.correcting) {
        dirEl.textContent  = '↺';
        barEl.style.width  = '100%';
        barEl.style.background = 'var(--purple)';
        corrEl.textContent = 'CORRECTING';
      } else if (steerDir && steerSince) {
        const elapsed = Math.min(Date.now()/1000 - steerSince, stto);
        const pct     = Math.round((elapsed / stto) * 100);
        dirEl.textContent  = steerDir;
        barEl.style.width  = pct + '%';
        barEl.style.background = pct > 75 ? 'var(--danger)' : 'var(--warn)';
        timeEl.textContent = elapsed.toFixed(1) + 's';
        corrEl.textContent = '';
      } else {
        dirEl.textContent  = '—';
        barEl.style.width  = '0%';
        timeEl.textContent = '0.0s';
        corrEl.textContent = '';
        barEl.style.background = 'var(--warn)';
      }

      // Run timer bar
      const timerEl  = document.getElementById('timer-overlay');
      const timerBar = document.getElementById('timer-bar');
      const rd = d.run_duration || 0;
      runDuration = rd;
      if (rd > 0 && d.time_remaining != null) {
        const pct = Math.round((d.time_remaining / rd) * 100);
        timerEl.textContent   = 'TIME: ' + d.time_remaining + 's';
        timerBar.style.width  = pct + '%';
        timerBar.style.background = pct < 20 ? 'var(--danger)' : 'var(--accent)';
      } else {
        timerEl.textContent   = rd > 0 ? '' : '∞ unlimited';
        timerBar.style.width  = '100%';
        timerBar.style.background = 'var(--accent)';
      }

    } catch (e) { /* server not ready */ }
    setTimeout(poll, 150);
  }

  poll();
</script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD)

# ============================================================
# MAIN
# ============================================================
SOURCE = "upload"   # "upload" for test_client | "esp32" for real hardware

if __name__ == "__main__":
    if SOURCE == "esp32":
        threading.Thread(target=esp32_stream_loop, daemon=True).start()
        log.info("Mode: ESP32-CAM stream")
    else:
        log.info("Mode: waiting for frame uploads (test_client.py)")

    log.info("Dashboard →  http://localhost:5000")
    log.info("WebSocket →  ws://localhost:5000/ws")
    log.info("Config    →  GET/POST http://localhost:5000/config")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)