"""
Test vision-based RED obstacle detection
Shows how the system detects and avoids RED objects
"""

import cv2
import numpy as np
from vision_obstacle_detector import VisionObstacleDetector

def main():
    print("="*60)
    print("VISION OBSTACLE DETECTION TEST")
    print("="*60)
    print("\nThis will test RED object detection using your webcam")
    print("\nInstructions:")
    print("1. Show a RED object to the camera")
    print("2. Move it LEFT, CENTER, or RIGHT")
    print("3. Watch the detection and avoidance direction")
    print("\nPress ESC to quit\n")
    
    # Initialize detector
    detector = VisionObstacleDetector(min_area=500, history_size=5)
    
    # Open webcam
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("✗ Cannot open webcam")
        return
    
    print("✓ Webcam opened")
    print("✓ Detector initialized")
    print("\nShowing windows...")
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        frame = cv2.resize(frame, (640, 480))
        
        # Detect obstacles
        obstacle, position, annotated, mask = detector.detect(frame)
        
        # Get smoothed position
        smoothed_pos = detector.get_smoothed_position(position)
        
        # Get avoidance direction
        avoid_dir = detector.get_avoidance_direction(smoothed_pos)
        
        # Display info
        h, w = annotated.shape[:2]
        
        # Status box
        status_color = (0, 0, 255) if obstacle else (0, 255, 0)
        status_text = "RED OBSTACLE DETECTED!" if obstacle else "Clear"
        cv2.putText(annotated, status_text, (10, 60),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
        
        if obstacle:
            # Position
            cv2.putText(annotated, f"Position: {smoothed_pos.upper()}", (10, 90),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
            
            # Avoidance direction
            avoid_color = (0, 255, 255)
            cv2.putText(annotated, f"Avoid: {avoid_dir}", (10, 120),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, avoid_color, 2)
            
            # Draw arrow showing avoidance direction
            if avoid_dir == "LEFT":
                cv2.arrowedLine(annotated, (w//2, h-50), (100, h-50), 
                               avoid_color, 5, tipLength=0.3)
            elif avoid_dir == "RIGHT":
                cv2.arrowedLine(annotated, (w//2, h-50), (w-100, h-50), 
                               avoid_color, 5, tipLength=0.3)
            elif avoid_dir == "STOP":
                cv2.putText(annotated, "STOP", (w//2-50, h-40),
                           cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 255), 3)
        
        # Instructions
        cv2.putText(annotated, "Show RED object | ESC=quit", (10, h-10),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1)
        
        # Show windows
        cv2.imshow("Vision Detection", annotated)
        cv2.imshow("Red Mask", mask)
        
        # Print status
        if obstacle:
            print(f"\r🔴 Red Obstacle: {smoothed_pos:8s} → Avoid: {avoid_dir:8s}", end="")
        else:
            print(f"\r✓ Clear path" + " "*40, end="")
        
        if cv2.waitKey(1) == 27:  # ESC
            break
    
    cap.release()
    cv2.destroyAllWindows()
    print("\n\n✓ Test completed")

if __name__ == "__main__":
    main()
