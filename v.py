import cv2
import numpy as np
import random
from collections import deque
from vision_obstacle_detector import VisionObstacleDetector

# -------------------------
# 🎥 CAMERA SETUP
# -------------------------
IP = "192.168.1.5:8080"
cap = cv2.VideoCapture(f"http://{IP}/video")

# -------------------------
# 🧠 VISION DETECTOR (ALL OBJECTS)
# -------------------------
vision_detector = VisionObstacleDetector(min_area=500, history_size=5)
print("✓ Vision detector initialized (detects ALL objects)")

# -------------------------
# 🧠 MEMORY (SMOOTHING)
# -------------------------
history = deque(maxlen=5)

# -------------------------
# 📡 SIMULATED ULTRASONIC
# -------------------------
def get_ultrasonic():
    return {
        "front": random.randint(5, 100),
        "left": random.randint(5, 100),
        "right": random.randint(5, 100),
        "back": random.randint(5, 100)
    }

# -------------------------
# 🧠 DECISION ENGINE
# -------------------------
def decide(sensor, vision_obstacle, vision_pos):

    SAFE_DIST = 25

    # 🚨 Priority 1: Front safety
    if sensor["front"] < SAFE_DIST:
        if sensor["left"] > sensor["right"]:
            return "LEFT"
        else:
            return "RIGHT"

    # 👁 Vision-based avoidance (ALL OBJECTS)
    if vision_obstacle:
        if vision_pos == "left":
            return "RIGHT"
        elif vision_pos == "right":
            return "LEFT"
        else:
            return "STOP"

    # 🚧 Side safety
    if sensor["left"] < 15:
        return "RIGHT"

    if sensor["right"] < 15:
        return "LEFT"

    return "FORWARD"

# -------------------------
# 🔁 MAIN LOOP
# -------------------------
print("Starting camera loop...")
print("Press ESC to quit")
print("-" * 60)

while True:

    ret, frame = cap.read()
    if not ret:
        print("⚠ Failed to read frame, retrying...")
        continue

    frame = cv2.resize(frame, (320, 240))

    # 🔍 Vision Detection (ALL OBJECTS using edge detection)
    vision_obstacle, vision_pos, annotated_frame, edges = vision_detector.detect(frame)
    
    # Get smoothed position
    vision_pos_smoothed = vision_detector.get_smoothed_position(vision_pos)

    # 📡 Sensor (simulated)
    sensor = get_ultrasonic()

    # 🧠 Decision
    direction = decide(sensor, vision_obstacle, vision_pos_smoothed)

    # 🧠 Smooth decision
    history.append(direction)
    direction = max(set(history), key=history.count)

    # -------------------------
    # 🎨 DISPLAY INFO
    # -------------------------
    # Direction
    dir_color = (0, 255, 0) if direction == "FORWARD" else \
                (0, 0, 255) if direction == "STOP" else \
                (0, 255, 255)
    
    cv2.putText(annotated_frame, f"DIR: {direction}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, dir_color, 2)

    # Sensors
    cv2.putText(annotated_frame, f"F:{sensor['front']} L:{sensor['left']} R:{sensor['right']}",
                (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)

    # Vision detection status
    if vision_obstacle:
        vision_color = (0, 0, 255) if vision_pos_smoothed == "center" else (0, 200, 255)
        cv2.putText(annotated_frame, f"OBSTACLE: {vision_pos_smoothed.upper()}", (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, vision_color, 2)
    else:
        cv2.putText(annotated_frame, "CLEAR PATH", (10, 90),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

    # Instructions
    cv2.putText(annotated_frame, "ESC=quit", (10, 230),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)

    # -------------------------
    # SHOW WINDOWS
    # -------------------------
    cv2.imshow("Self-Driving Car - Vision Detection", annotated_frame)
    cv2.imshow("Edge Detection", edges)

    # Console output
    status = "⚠ OBSTACLE" if vision_obstacle else "✓ CLEAR"
    print(f"\r{status} | Dir: {direction:8s} | Pos: {vision_pos_smoothed:8s} | "
          f"F:{sensor['front']:3d} L:{sensor['left']:3d} R:{sensor['right']:3d}", end="")

    if cv2.waitKey(1) == 27:  # ESC
        break

print("\n" + "-" * 60)
print("✓ Shutting down...")
cap.release()
cv2.destroyAllWindows()
print("✓ Done!")