"""
============================================================
Self-Driving Car — Python Server (v3 — with backup logic)
============================================================
New features:
  - Back sensor support (4 sensors total: F/B/L/R)
  - Smart backup when boxed in (both sides < 15cm)
  - Backs up until clearance is found, then turns
  - State machine: NORMAL, BACKING_UP, STOPPED
  - All original features (steering timeout, run timer, dashboard)
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
# VISION OBSTACLE DETECTOR (RED OBJECTS) - MINIMAL INTERFERENCE
# ============================================================
vision_detector = VisionObstacleDetector(min_area=2000, history_size=10)
log.info("Vision obstacle detector initialized (MINIMAL mode)")

# ============================================================
# CONFIG  (all tunable at runtime via /config POST or dashboard)
# ============================================================
cfg_lock = threading.Lock()
cfg = {
    # Sensor decision thresholds (cm) - MATCHES ESP32
    "front_stop_distance": 20,   # Front < 20cm triggers decision
    "side_avoid_distance": 20,   # Side < 20cm used when front blocked
    "backup_threshold":    15,   # Both sides < 15cm → backup
    "backup_clearance":    25,   # Need 25cm clearance to stop backing up
    "back_safety_distance": 10,  # Don't backup if back < 10cm

    # Speeds  (0-255)
    "base_speed":       200,
    "slow_speed":       130,
    "turn_speed":       160,
    "backup_speed":     160,

    # ESP32 Motor Speeds (sent in commands)
    "motor_speed_normal": 200,  # F:200
    "motor_speed_turn":   180,  # L:180, R:180
    "motor_speed_hard":   220,  # HL:220, HR:220
    "motor_speed_backup": 160,  # B:160

    # Steering correction
    "steer_timeout":        2.0,   # seconds before correction kicks in
    "correction_duration":  0.8,   # seconds to hold correction pulse

    # Lane dead-zone (pixels) — WIDENED for minimal vision interference
    "steer_threshold":  80,

    # Vision interference settings - MINIMAL
    "vision_min_area":      2000,
    "vision_center_width":  0.5,
    "vision_confidence":    8,

    # Run timer
    "run_duration":     0,         # seconds, 0 = unlimited
    "car_running":      False,
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
    "steer_direction":  None,
    "steer_since":      None,
    "correcting":       False,
    "correction_until": 0,
    "run_started_at":   None,
    "time_remaining":   None,
    "vision_obstacle":  False,
    "vision_position":  "none",
    
    # Backup state machine
    "car_state":        "NORMAL",     # NORMAL, BACKING_UP, STOPPED
    "backup_started_at": None,
    "preferred_turn_direction": 0,    # 1 = left, -1 = right, 0 = not set
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
                    state["car_state"]       = "NORMAL"
                    state["backup_started_at"] = None
                    state["preferred_turn_direction"] = 0
                    state["reason"]          = "Starting..."
                log.info("Car STARTED")
            else:
                with state_lock:
                    state["reason"] = "Stopped — press Start"
                    state["car_state"] = "NORMAL"
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

# ============================================================
# STEERING TRACKER
# ============================================================
def track_and_correct_steer(raw_cmd: str) -> tuple[str, str, bool]:
    """Apply steering timeout logic"""
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

    cmd_dir = raw_cmd.split(":")[0]

    # Active correction pulse
    if correcting:
        if now < correction_until:
            opp = "R" if steer_direction == "L" else "L"
            corr_cmd = f"{opp}:{turn_speed}"
            return corr_cmd, f"Correction pulse ({opp}) → recentring", True
        else:
            with state_lock:
                state["correcting"]      = False
                state["steer_direction"] = None
                state["steer_since"]     = None
            return raw_cmd, "", False

    # Track steering direction
    is_steer = cmd_dir in ("L", "R", "HL", "HR")
    canonical = "L" if cmd_dir in ("L", "HL") else ("R" if cmd_dir in ("R", "HR") else None)

    if is_steer and canonical:
        if steer_direction != canonical:
            with state_lock:
                state["steer_direction"] = canonical
                state["steer_since"]     = now
        else:
            elapsed = now - (steer_since or now)
            if elapsed >= steer_timeout:
                with state_lock:
                    state["correcting"]      = True
                    state["correction_until"] = now + correction_duration
                opp      = "R" if canonical == "L" else "L"
                corr_cmd = f"{opp}:{turn_speed}"
                log.info(f"[STEER] Timeout after {elapsed:.1f}s → correction {opp}")
                return corr_cmd, f"Steer timeout ({elapsed:.1f}s) → correction {opp}", True
    else:
        if steer_direction is not None:
            with state_lock:
                state["steer_direction"] = None
                state["steer_since"]     = None

    return raw_cmd, "", False

# ============================================================
# RUN TIMER
# ============================================================
def check_run_timer() -> tuple[bool, float | None]:
    with cfg_lock:
        car_running  = cfg["car_running"]
        run_duration = cfg["run_duration"]

    if not car_running:
        return False, None

    if run_duration <= 0:
        return True, None

    with state_lock:
        started_at = state["run_started_at"]

    if started_at is None:
        return True, None

    elapsed   = time.time() - started_at
    remaining = run_duration - elapsed

    if remaining <= 0:
        with cfg_lock:
            cfg["car_running"] = False
        send_command("S:0")
        log.info("[TIMER] Run timer expired — car stopped")
        return False, 0

    with state_lock:
        state["time_remaining"] = round(remaining, 1)

    return True, round(remaining, 1)

# ============================================================
# DECISION ENGINE - WITH BACKUP LOGIC
# ============================================================
def decide(steer_offset: int, visual_obstacle: bool, vision_position: str) -> tuple[str, str]:
    """
    STATE MACHINE DECISION LOGIC - MATCHES ESP32 CODE
    
    States:
      NORMAL      - Normal driving with obstacle avoidance
      BACKING_UP  - Backing up to create space when boxed in
      STOPPED     - Safety stop (can't move)
    """
    with state_lock:
        sensors = dict(state["sensors"])
        car_state = state["car_state"]
        backup_started_at = state["backup_started_at"]
        preferred_turn = state["preferred_turn_direction"]
    
    with cfg_lock:
        motor_normal = cfg["motor_speed_normal"]
        motor_hard = cfg["motor_speed_hard"]
        motor_backup = cfg["motor_speed_backup"]
        front_threshold = cfg["front_stop_distance"]
        side_threshold = cfg["side_avoid_distance"]
        backup_thresh = cfg["backup_threshold"]
        backup_clear = cfg["backup_clearance"]
        back_safety = cfg["back_safety_distance"]

    front = sensors.get("f", 999)
    back  = sensors.get("b", 999)
    left  = sensors.get("l", 999)
    right = sensors.get("r", 999)

    # ════════════════════════════════════════════════════════════
    # STATE: BACKING_UP
    # ════════════════════════════════════════════════════════════
    if car_state == "BACKING_UP":
        # Check if back is safe
        if back < back_safety:
            log.warning(f"[DECISION] Back blocked! Emergency stop (back={back})")
            with state_lock:
                state["car_state"] = "STOPPED"
            return "S:0", f"STOPPED: Back blocked at {back}cm"
        
        # Check if we now have clearance on at least one side
        left_clear  = (left >= backup_clear)
        right_clear = (right >= backup_clear)
        
        if left_clear or right_clear:
            # We have space! Stop backing up and turn
            log.info(f"[DECISION] Space created! L={left} R={right}")
            
            # Decide which way to turn
            if right_clear and not left_clear:
                log.info("[DECISION] Right clear - turning RIGHT")
                with state_lock:
                    state["car_state"] = "NORMAL"
                    state["backup_started_at"] = None
                    state["preferred_turn_direction"] = -1
                return f"HR:{motor_hard}", f"RIGHT: Space created on right (R={right})"
            
            elif left_clear and not right_clear:
                log.info("[DECISION] Left clear - turning LEFT")
                with state_lock:
                    state["car_state"] = "NORMAL"
                    state["backup_started_at"] = None
                    state["preferred_turn_direction"] = 1
                return f"HL:{motor_hard}", f"LEFT: Space created on left (L={left})"
            
            else:
                # Both clear - use preferred or default left
                if preferred_turn == -1:
                    log.info("[DECISION] Both clear - using preferred RIGHT")
                    with state_lock:
                        state["car_state"] = "NORMAL"
                        state["backup_started_at"] = None
                    return f"HR:{motor_hard}", f"RIGHT: Both sides clear (preferred)"
                else:
                    log.info("[DECISION] Both clear - using preferred LEFT")
                    with state_lock:
                        state["car_state"] = "NORMAL"
                        state["backup_started_at"] = None
                        state["preferred_turn_direction"] = 1
                    return f"HL:{motor_hard}", f"LEFT: Both sides clear (default)"
        
        # Still boxed in - keep backing up
        elapsed = time.time() - (backup_started_at or time.time())
        return f"B:{motor_backup}", f"BACKING UP: Still boxed in L={left} R={right} ({elapsed:.1f}s)"
    
    # ════════════════════════════════════════════════════════════
    # STATE: STOPPED
    # ════════════════════════════════════════════════════════════
    if car_state == "STOPPED":
        # Check if we can recover
        if front >= front_threshold and back >= back_safety:
            log.info("[DECISION] Recovering from stopped state")
            with state_lock:
                state["car_state"] = "NORMAL"
            return f"F:{motor_normal}", f"RECOVERING: Obstacles cleared"
        
        return "S:0", f"STOPPED: Waiting for clearance F={front} B={back}"
    
    # ════════════════════════════════════════════════════════════
    # STATE: NORMAL - MAIN DRIVING LOGIC
    # ════════════════════════════════════════════════════════════
    
    # Front < threshold → obstacle avoidance
    if front < front_threshold:
        
        # ─── CASE 1: Both sides blocked (< backup_threshold) ───
        if right < backup_thresh and left < backup_thresh:
            # BOXED IN! Need to back up first
            
            # Check if we can safely back up
            if back < back_safety:
                log.warning(f"[DECISION] Completely stuck! F={front} L={left} R={right} B={back}")
                with state_lock:
                    state["car_state"] = "STOPPED"
                return "S:0", f"STOPPED: Boxed in, can't backup (B={back})"
            
            # Start backing up
            log.info(f"[DECISION] BOXED IN - starting backup (F={front} L={left} R={right})")
            with state_lock:
                state["car_state"] = "BACKING_UP"
                state["backup_started_at"] = time.time()
            
            return f"B:{motor_backup}", f"BACKING UP: Boxed in F={front} L={left} R={right}"
        
        # ─── CASE 2: Right blocked, left clear ───
        elif right < side_threshold:
            with state_lock:
                state["preferred_turn_direction"] = 1
            return f"HL:{motor_hard}", f"LEFT: Front + right blocked (F={front} R={right})"
        
        # ─── CASE 3: Left blocked, right clear ───
        elif left < side_threshold:
            with state_lock:
                state["preferred_turn_direction"] = -1
            return f"HR:{motor_hard}", f"RIGHT: Front + left blocked (F={front} L={left})"
        
        # ─── CASE 4: Only front blocked, both sides clear ───
        else:
            with state_lock:
                state["preferred_turn_direction"] = 1
            return f"HL:{motor_hard}", f"LEFT: Only front blocked (F={front})"
    
    # ─── CASE 5: Front clear - FORWARD ───
    else:
        return f"F:{motor_normal}", f"FORWARD: Front clear F={front} L={left} R={right}"

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

        # 2. Vision detection
        vision_obstacle, vision_pos, vision_frame, vision_mask = vision_detector.detect(img)
        vision_pos_smoothed = vision_detector.get_smoothed_position(vision_pos)
        
        # 3. Lane detection
        steer_offset, annotated = detect_lanes(vision_frame)
        
        # 4. Decision (sensor-first, vision as context)
        raw_cmd, raw_reason = decide(steer_offset, vision_obstacle, vision_pos_smoothed)

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

        _annotate_and_store(annotated, final_cmd, reason, vision_obstacle,
                            steer_offset, is_correcting, time_remaining)
    except Exception as e:
        log.error(f"process_frame error: {e}")
        import traceback
        traceback.print_exc()
        with state_lock:
            state["command"] = "S:0"
            state["reason"] = f"Error: {str(e)}"

def _annotate_and_store(frame, cmd, reason, visual_obs=False,
                        steer_offset=0, correcting=False, time_remaining=None):
    with state_lock:
        sensors = dict(state["sensors"])
        vision_obs = state.get("vision_obstacle", False)
        vision_pos = state.get("vision_position", "none")
        car_state = state.get("car_state", "NORMAL")

    hud = frame.copy() if hasattr(frame, 'copy') else frame
    color = (0, 80, 220) if cmd.startswith("S") else \
            (0, 220, 80) if cmd.startswith("F") else \
            (180, 0, 255) if cmd.startswith("B") else \
            (220, 200, 0) if cmd[0] in "LR" else \
            (0, 200, 220)

    cv2.putText(hud, f"CMD: {cmd}", (5, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    
    # State indicator
    if car_state == "BACKING_UP":
        cv2.putText(hud, "BACKING UP", (5, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 0, 255), 2)
    elif correcting:
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

    # Vision detection
    if vision_obs:
        vision_color = (0, 0, 255) if vision_pos == "center" else (0, 200, 255)
        cv2.putText(hud, f"RED OBSTACLE: {vision_pos.upper()}", (5, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, vision_color, 1)

    if time_remaining is not None:
        cv2.putText(hud, f"TIME: {time_remaining}s", (230, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 180, 0), 1)

    _, jpg = cv2.imencode(".jpg", hud, [cv2.IMWRITE_JPEG_QUALITY, 80])
    with state_lock:
        state["frame"] = jpg.tobytes()

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
# DASHBOARD HTML (same as before, works with new state)
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
  .badge.backing  { color: var(--purple); border-color: var(--purple); background: #bb88ff20; }
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
  #log .entry.backup { color: #b488ff; }
</style>
</head>
<body>

<header>
  <h1>&#9632; SELF-DRIVING CAR v3</h1>
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

    <!-- Vision Detection -->
    <div class="card">
      <h3>Vision Detection (Red Objects)</h3>
      <div id="vision-status" style="font-size:13px;padding:8px;background:var(--bg3);border-radius:6px;margin-bottom:4px;min-height:24px;color:var(--green)">
        No red obstacles
      </div>
      <div id="vision-position" style="font-size:11px;color:var(--muted);">
        Position: none
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
    </div>

    <!-- Backup thresholds -->
    <div class="card">
      <h3>Backup Logic (cm)</h3>
      <div class="slider-row">
        <label>Backup threshold</label>
        <input type="range" id="sl-backup-thresh" min="10" max="30" value="15"
               oninput="updateSlider('sl-backup-thresh','lbl-backup-thresh',v=>v+'cm'); sendCfg('backup_threshold',+this.value)">
        <span class="val-lbl" id="lbl-backup-thresh">15cm</span>
      </div>
      <div class="slider-row">
        <label>Backup clearance</label>
        <input type="range" id="sl-backup-clear" min="15" max="50" value="25"
               oninput="updateSlider('sl-backup-clear','lbl-backup-clear',v=>v+'cm'); sendCfg('backup_clearance',+this.value)">
        <span class="val-lbl" id="lbl-backup-clear">25cm</span>
      </div>
      <div class="slider-row">
        <label>Back safety distance</label>
        <input type="range" id="sl-back-safety" min="5" max="20" value="10"
               oninput="updateSlider('sl-back-safety','lbl-back-safety',v=>v+'cm'); sendCfg('back_safety_distance',+this.value)">
        <span class="val-lbl" id="lbl-back-safety">10cm</span>
      </div>
    </div>

    <!-- Safety thresholds -->
    <div class="card">
      <h3>Decision Thresholds (cm)</h3>
      <div class="slider-row">
        <label>Front stop distance</label>
        <input type="range" id="sl-front-stop" min="10" max="50" value="20"
               oninput="updateSlider('sl-front-stop','lbl-front-stop',v=>v+'cm'); sendCfg('front_stop_distance',+this.value)">
        <span class="val-lbl" id="lbl-front-stop">20cm</span>
      </div>
      <div class="slider-row">
        <label>Side avoid distance</label>
        <input type="range" id="sl-side-avoid" min="5" max="40" value="20"
               oninput="updateSlider('sl-side-avoid','lbl-side-avoid',v=>v+'cm'); sendCfg('side_avoid_distance',+this.value)">
        <span class="val-lbl" id="lbl-side-avoid">20cm</span>
      </div>
    </div>

    <!-- Speed -->
    <div class="card">
      <h3>Motor Speeds (0–255)</h3>
      <div class="slider-row">
        <label>Normal forward</label>
        <input type="range" id="sl-motor-normal" min="50" max="255" value="200"
               oninput="updateSlider('sl-motor-normal','lbl-motor-normal',v=>v); sendCfg('motor_speed_normal',+this.value)">
        <span class="val-lbl" id="lbl-motor-normal">200</span>
      </div>
      <div class="slider-row">
        <label>Hard turn</label>
        <input type="range" id="sl-motor-hard" min="50" max="255" value="220"
               oninput="updateSlider('sl-motor-hard','lbl-motor-hard',v=>v); sendCfg('motor_speed_hard',+this.value)">
        <span class="val-lbl" id="lbl-motor-hard">220</span>
      </div>
      <div class="slider-row">
        <label>Backup speed</label>
        <input type="range" id="sl-motor-backup" min="50" max="255" value="160"
               oninput="updateSlider('sl-motor-backup','lbl-motor-backup',v=>v); sendCfg('motor_speed_backup',+this.value)">
        <span class="val-lbl" id="lbl-motor-backup">160</span>
      </div>
    </div>

  </div><!-- /right -->
</div><!-- /layout -->

<script>
  let carRunning    = false;
  let runDuration   = 0;
  let prevCmd       = "";
  const log         = document.getElementById('log');

  function updateSlider(sliderId, lblId, fmt) {
    const v = document.getElementById(sliderId).value;
    document.getElementById(lblId).textContent = fmt(v);
  }

  async function sendCfg(key, value) {
    await fetch('/config', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({[key]: value})
    });
  }

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

  function sensorClass(v) {
    return v > 30 ? 'sv ok' : v > 15 ? 'sv warn' : 'sv crit';
  }

  function addLog(msg, cls='entry') {
    const el = document.createElement('div');
    el.className = 'entry ' + cls;
    const ts = new Date().toLocaleTimeString();
    el.textContent = `[${ts}] ${msg}`;
    log.prepend(el);
    if (log.children.length > 40) log.lastChild.remove();
  }

  async function poll() {
    try {
      const d = await (await fetch('/status')).json();

      if (d.car_running !== carRunning) {
        carRunning = d.car_running;
        updateStartBtn();
        if (!carRunning) addLog('Run timer expired — auto stop', 'warn');
      }

      // Badge
      const badge = document.getElementById('status-badge');
      const carState = d.car_state || 'NORMAL';
      
      if (carState === 'BACKING_UP') {
        badge.textContent = 'BACKING UP';
        badge.className   = 'badge backing';
      } else if (d.correcting) {
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
                        : cmd.startsWith('B') ? '#b488ff'
                        : 'var(--warn)';

      // Log new commands
      if (cmd !== prevCmd) {
        const logClass = carState === 'BACKING_UP' ? 'backup' : 
                        d.correcting ? 'corr' : 'entry';
        addLog(cmd + '  ' + (d.reason||''), logClass);
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

      // Vision
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

      // Timer bar
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
        # ESP32-CAM stream mode (not implemented in this version)
        log.info("Mode: ESP32-CAM stream (not implemented)")
    else:
        log.info("Mode: waiting for frame uploads (test_client.py)")

    log.info("Dashboard →  http://localhost:5000")
    log.info("WebSocket →  ws://localhost:5000/ws")
    log.info("Config    →  GET/POST http://localhost:5000/config")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)