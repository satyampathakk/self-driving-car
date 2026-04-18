"""
Vision-based obstacle detection using RED color detection
Detects red objects and determines their position
"""

import cv2
import numpy as np
from collections import deque


class VisionObstacleDetector:
    """Detects RED obstacles and determines their position"""
    
    def __init__(self, min_area=500, history_size=5):
        self.min_area = min_area
        self.history = deque(maxlen=history_size)
        
        # Red color ranges in HSV (WIDENED for better detection)
        # Lower red range (0-15 degrees) - wider range
        self.lower_red1 = np.array([0, 100, 50])    # Lowered saturation and value
        self.upper_red1 = np.array([15, 255, 255])  # Increased hue range
        
        # Upper red range (155-180 degrees) - wider range
        self.lower_red2 = np.array([155, 100, 50])  # Lowered hue, saturation, value
        self.upper_red2 = np.array([180, 255, 255]) # Full range
    
    def detect(self, frame):
        """
        Detect RED obstacles in frame
        Returns: (obstacle_detected, position, annotated_frame, mask)
        position: "left", "right", "center", or "none"
        """
        h, w = frame.shape[:2]
        
        # Convert to HSV
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        
        # Create mask for red color (two ranges)
        mask1 = cv2.inRange(hsv, self.lower_red1, self.upper_red1)
        mask2 = cv2.inRange(hsv, self.lower_red2, self.upper_red2)
        mask = mask1 + mask2
        
        # Find contours
        contours, _ = cv2.findContours(mask, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        obstacle = False
        position = "none"
        cx = None
        annotated = frame.copy()
        
        # WIDENED ZONES: Left (0-40%), Center (40-60%), Right (60-100%)
        left_boundary = int(w * 0.4)   # 40% instead of 33%
        right_boundary = int(w * 0.6)  # 60% instead of 66%
        
        # Process contours
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area > self.min_area:
                x, y, w_box, h_box = cv2.boundingRect(cnt)
                cx = x + w_box // 2
                obstacle = True
                
                # Determine position using WIDER zones
                if cx < left_boundary:
                    position = "left"
                    color = (255, 0, 255)  # Magenta
                elif cx > right_boundary:
                    position = "right"
                    color = (255, 255, 0)  # Cyan
                else:
                    position = "center"
                    color = (0, 0, 255)    # Red
                
                # Draw bounding box
                cv2.rectangle(annotated, (x, y), (x + w_box, y + h_box), (0, 255, 0), 2)
                cv2.putText(annotated, f"RED: {position.upper()}", (x, y - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        # Draw zone lines (WIDER center zone)
        cv2.line(annotated, (left_boundary, 0), (left_boundary, h), (255, 0, 0), 2)
        cv2.line(annotated, (right_boundary, 0), (right_boundary, h), (255, 0, 0), 2)
        
        return obstacle, position, annotated, mask
    
    def get_smoothed_position(self, current_position):
        """Smooth position detection using history"""
        self.history.append(current_position)
        if len(self.history) == 0:
            return "none"
        # Return most common position in history
        return max(set(self.history), key=self.history.count)
    
    def get_avoidance_direction(self, position):
        """
        Get steering direction to avoid obstacle
        Returns: "LEFT", "RIGHT", "STOP", or "FORWARD"
        """
        if position == "left":
            return "RIGHT"
        elif position == "right":
            return "LEFT"
        elif position == "center":
            return "STOP"
        else:
            return "FORWARD"
