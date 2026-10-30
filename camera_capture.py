"""Processed BGR camera frames from USB V4L2 or Raspberry Pi CSI/libcamera."""
import os
from pathlib import Path

import cv2


class PiCameraCapture:
    def __init__(self, width, height, fps):
        from picamera2 import Picamera2
        self.camera = Picamera2()
        self.opened = False
        try:
            # First enumeration reconfigures libcamera: cache before streaming.
            self.sensor_modes = self.camera.sensor_modes
            config = self.camera.create_video_configuration(
                main={"size": (width, height), "format": "RGB888"},
                controls={"FrameRate": float(fps)}, buffer_count=4)
            self.camera.configure(config)
            self.camera.start()
            # Picamera2 RGB888 arrays are B,G,R in memory, matching OpenCV.
            actual = self.camera.camera_configuration()["main"]["size"]
            self.width, self.height = actual
            self.fps = float(fps)
            self.opened = True
        except Exception:
            self.camera.close()
            raise

    def isOpened(self):
        return self.opened

    def read(self):
        if not self.opened:
            return False, None
        return True, self.camera.capture_array("main")

    def get(self, prop):
        return {cv2.CAP_PROP_FRAME_WIDTH: self.width,
                cv2.CAP_PROP_FRAME_HEIGHT: self.height,
                cv2.CAP_PROP_FPS: self.fps}.get(prop, 0)

    def release(self):
        if self.opened:
            self.opened = False
            try:
                self.camera.stop()
            finally:
                self.camera.close()


def open_camera(width, height, fps):
    backend = os.environ.get("DASHCAM_CAMERA_BACKEND", "auto")
    node = Path("/sys/class/video4linux/video0/name")
    csi = node.exists() and "unicam" in node.read_text()
    if backend == "picamera2" or (backend == "auto" and csi):
        camera = PiCameraCapture(width, height, fps)
        print(f"Camera: Picamera2 CSI {camera.width}x{camera.height} requested_fps={fps}")
        return camera
    if backend not in ("auto", "opencv"):
        raise ValueError(f"Unknown DASHCAM_CAMERA_BACKEND: {backend}")
    camera = cv2.VideoCapture(0)
    camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    camera.set(cv2.CAP_PROP_FPS, fps)
    return camera
