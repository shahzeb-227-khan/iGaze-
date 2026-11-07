"""
eye_tracker.py
--------------
Captures webcam feed and detects eye landmarks using
MediaPipe FaceLandmarker (new API) — works on Python 3.12+
"""

import time

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# Eye landmark indices from MediaPipe 478-point face mesh
LEFT_EYE_IDX  = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_IDX = [362, 385, 387, 263, 373, 380]

class EyeTracker:
    def __init__(self, model_path="face_landmarker.task", camera_index=0):
        
        base_options  = python.BaseOptions(model_asset_path=model_path)
        options       = vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.VIDEO,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
            num_faces=1
        )
        self.detector = vision.FaceLandmarker.create_from_options(options)
        self.cap      = cv2.VideoCapture(camera_index)
        self.capture_time = 0.0
        self._timestamp_ms = -1

        if not self.cap.isOpened():
            self.release()
            raise RuntimeError("Cannot open webcam.")

        print("[EyeTracker] Ready.")

    def _get_eye_points(self, landmarks, indices, w, h):
        """Convert normalized landmarks to pixel coordinates."""
        return np.array(
            [(int(landmarks[i].x * w), int(landmarks[i].y * h)) for i in indices],
            dtype=np.int32
        )

    def _eye_aspect_ratio(self, eye):
        """EAR — drops when eye closes."""
        p1,p2,p3,p4,p5,p6 = eye
        v1 = np.linalg.norm(p2 - p6)
        v2 = np.linalg.norm(p3 - p5)
        h  = np.linalg.norm(p1 - p4)
        return (v1 + v2) / (2.0 * h + 1e-6)

    def get_frame_data(self):
        ret, frame = self.cap.read()
        self.capture_time = time.monotonic()
        if not ret:
            return None, None, None, None, 0.0, 0.0

        # Flip frame FIRST to match display
        frame = cv2.flip(frame, 1)
        
        h, w = frame.shape[:2]
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        self._timestamp_ms = max(self._timestamp_ms + 1, int(self.capture_time * 1000))
        result = self.detector.detect_for_video(mp_image, self._timestamp_ms)

        left_eye = right_eye = None
        left_ear = right_ear = 0.0
        landmarks_raw = None

        if result.face_landmarks:
            landmarks_raw = result.face_landmarks[0]
            left_eye  = self._get_eye_points(landmarks_raw, LEFT_EYE_IDX,  w, h)
            right_eye = self._get_eye_points(landmarks_raw, RIGHT_EYE_IDX, w, h)
            left_ear  = self._eye_aspect_ratio(left_eye)
            right_ear = self._eye_aspect_ratio(right_eye)

        return frame, landmarks_raw, left_eye, right_eye, left_ear, right_ear

    def draw_debug(self, frame, landmarks_raw, left_eye, right_eye, left_ear, right_ear):
        if landmarks_raw is None:
            cv2.putText(frame, "No face detected", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,255), 2)
            return frame

        h, w = frame.shape[:2]

        # Draw all face landmarks (smaller)
        for lm in landmarks_raw:
            cx, cy = int(lm.x * w), int(lm.y * h)
            cv2.circle(frame, (cx, cy), 1, (50, 100, 50), -1)

        # Draw eye contours with filled polygon
        if left_eye is not None:
            cv2.drawContours(frame, [cv2.convexHull(left_eye)], -1, (0,255,255), 2)
            # Mark eye center
            eye_center = left_eye.mean(axis=0).astype(int)
            cv2.circle(frame, tuple(eye_center), 3, (0,200,200), -1)
        
        if right_eye is not None:
            cv2.drawContours(frame, [cv2.convexHull(right_eye)], -1, (255,255,0), 2)
            # Mark eye center
            eye_center = right_eye.mean(axis=0).astype(int)
            cv2.circle(frame, tuple(eye_center), 3, (200,200,0), -1)

        # EAR values
        cv2.putText(frame, f"L-EAR: {left_ear:.2f}",  (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,200,0), 2)
        cv2.putText(frame, f"R-EAR: {right_ear:.2f}", (10, 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,200,0), 2)
        return frame

    def release(self):
        self.cap.release()
        self.detector.close()
        print("[EyeTracker] Released.")


if __name__ == "__main__":
    import sys
    
    model_path = "face_landmarker.task"
    if len(sys.argv) > 1:
        model_path = sys.argv[1]
    
    tracker = EyeTracker(model_path=model_path)
    
    print("Camera stream started. Press 'q' to quit.")
    
    try:
        while True:
            frame, landmarks_raw, left_eye, right_eye, left_ear, right_ear = tracker.get_frame_data()
            
            if frame is None:
                print("Failed to read frame.")
                break
            
            # Draw debug visualization
            display_frame = tracker.draw_debug(frame, landmarks_raw, left_eye, right_eye, left_ear, right_ear)
            
            # Display the frame
            cv2.imshow("Eye Tracker - Press 'q' to quit", display_frame)
            
            # Exit on 'q' key
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    
    finally:
        tracker.release()
        cv2.destroyAllWindows()
        print("Stream closed.")
