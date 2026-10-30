"""Camera adapter lifecycle tests, without accessing physical cameras."""
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
from camera_capture import PiCameraCapture, open_camera


class CameraChecks(unittest.TestCase):
    def camera(self):
        camera = Mock()
        camera.camera_configuration.return_value = {"main": {"size": (640, 480)}}
        camera.capture_array.return_value = object()
        return camera

    def test_processed_frame_and_idempotent_release(self):
        device = self.camera()
        with patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: device)}):
            capture = PiCameraCapture(640, 480, 30)
        self.assertTrue(capture.isOpened())
        self.assertEqual(capture.get(cv2.CAP_PROP_FRAME_WIDTH), 640)
        self.assertIs(capture.read()[1], device.capture_array.return_value)
        kwargs = device.create_video_configuration.call_args.kwargs
        self.assertEqual(kwargs["main"]["format"], "RGB888")
        capture.release()
        capture.release()
        device.stop.assert_called_once()
        device.close.assert_called_once()
        self.assertEqual(capture.read(), (False, None))

    def test_failed_configuration_releases_camera(self):
        device = self.camera()
        device.configure.side_effect = RuntimeError("configuration failed")
        with patch.dict(sys.modules, {"picamera2": SimpleNamespace(Picamera2=lambda: device)}):
            with self.assertRaisesRegex(RuntimeError, "configuration failed"):
                PiCameraCapture(640, 480, 30)
        device.close.assert_called_once()
        device.start.assert_not_called()

    def test_explicit_usb_backend_uses_opencv(self):
        with patch.dict('os.environ', {"DASHCAM_CAMERA_BACKEND": "opencv"}), \
                patch('camera_capture.cv2.VideoCapture') as camera:
            capture = open_camera(640, 480, 25)
        self.assertIs(capture, camera.return_value)
        camera.assert_called_once_with(0)
        capture.set.assert_any_call(cv2.CAP_PROP_FPS, 25)


if __name__ == '__main__':
    unittest.main(verbosity=2)
