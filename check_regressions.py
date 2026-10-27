"""运行：python check_regressions.py；不操作真实网络、摄像头或挂载点。"""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import network_manager as network


def result(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class NetworkChecks(unittest.TestCase):
    def test_scan_preserves_escaped_ssid_and_complete_bssid(self):
        scan = (r"车载\:WiFi\\AP:40:AA\:BB\:CC\:DD\:EE\:01:WPA2" + "\n" +
                r"车载\:WiFi\\AP:90:AA\:BB\:CC\:DD\:EE\:02:WPA2" + "\n" +
                r"Free:65:11\:22\:33\:44\:55\:66:--")
        with patch.object(network, "get_wifi_interface", return_value="wlp1s0"), \
                patch.object(network, "_nmcli", side_effect=[result(), result(scan)]):
            networks = network.scan_wifi()
        self.assertEqual(networks[0]["ssid"], "车载:WiFi\\AP")
        self.assertEqual(networks[0]["bssid"], "AA:BB:CC:DD:EE:02")
        self.assertEqual(len(networks), 2)
        self.assertFalse(networks[1]["secured"])

    def test_scan_failure_is_reported(self):
        with patch.object(network, "get_wifi_interface", return_value="wlan0"), \
                patch.object(network, "_nmcli", side_effect=RuntimeError("scan failed")):
            with self.assertRaisesRegex(RuntimeError, "scan failed"):
                network.scan_wifi()

    def test_status_uses_ap_mode_and_real_ssid(self):
        responses = [result("wlp1s0:wifi\neth0:ethernet\n"),
                     result("GENERAL.STATE:100 (connected)\nGENERAL.CON-UUID:ap-uuid\n"
                            "IP4.ADDRESS[1]:10.42.0.1/24\n"),
                     result("802-11-wireless.mode:ap\n802-11-wireless.ssid:Custom\\:AP\n")]
        with patch.object(network, "_nmcli", side_effect=responses):
            status = network.get_network_status()
        self.assertEqual(status["mode"], "hotspot")
        self.assertEqual(status["ssid"], "Custom:AP")
        self.assertEqual(status["ip"], "10.42.0.1")
        self.assertEqual(status["interface"], "wlp1s0")

    def test_client_named_like_hotspot_is_still_client(self):
        responses = [result("GENERAL.STATE:100 (connected)\nGENERAL.CON-UUID:client-uuid\n"),
                     result("802-11-wireless.mode:infrastructure\n"
                            "802-11-wireless.ssid:Pi-Dashcam\n")]
        with patch.object(network, "_nmcli", side_effect=responses):
            status = network.get_network_status("wlan0")
        self.assertEqual(status["mode"], "wifi")
        self.assertFalse(status["hotspot_active"])

    def test_unavailable_manager_is_not_reported_as_disconnected(self):
        with patch.object(network, "_nmcli", side_effect=FileNotFoundError("nmcli")):
            self.assertEqual(network.get_network_status()["mode"], "unknown")

    def test_connect_uses_full_selected_bssid_and_keeps_ap_delay(self):
        previous = dict(mode="hotspot", hotspot_active=True, connection_uuid="ap-uuid")
        scan = result(r"Office:AA\:BB\:CC\:DD\:EE\:FF" + "\n")
        with patch.object(network, "get_wifi_interface", return_value="wlp1s0"), \
                patch.object(network, "get_network_status", return_value=previous), \
                patch.object(network, "_nmcli", side_effect=[result(), result(), result(), scan, result()]) as run, \
                patch.object(network.time, "sleep") as sleep:
            network.connect_wifi("Office", "new-password", "AA:BB:CC:DD:EE:FF")
        sleep.assert_called_once_with(2.0)
        self.assertEqual(run.call_args.args, ("device", "wifi", "connect", "AA:BB:CC:DD:EE:FF",
                                            "ifname", "wlp1s0", "password", "new-password"))

    def test_substring_ssid_cannot_match_and_failed_switch_restores_ap(self):
        previous = dict(mode="hotspot", hotspot_active=True, connection_uuid="ap-uuid")
        calls = []

        def run(*args, **kwargs):
            calls.append(args)
            if "SSID,BSSID" in args:
                return result(r"Office-Guest:AA\:BB\:CC\:DD\:EE\:FF")
            return result()

        with patch.object(network, "get_wifi_interface", return_value="wlan0"), \
                patch.object(network, "get_network_status", return_value=previous), \
                patch.object(network, "_nmcli", side_effect=run), \
                patch.object(network.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "已恢复原热点"):
                network.connect_wifi("Office")
        self.assertFalse(any(call[:3] == ("device", "wifi", "connect") for call in calls))
        self.assertEqual(calls[-1], ("connection", "up", "uuid", "ap-uuid", "ifname", "wlan0"))

    def test_open_wifi_omits_password_option(self):
        previous = dict(mode="disconnected", hotspot_active=False)
        with patch.object(network, "get_wifi_interface", return_value="wlan0"), \
                patch.object(network, "get_network_status", return_value=previous), \
                patch.object(network, "_nmcli", side_effect=[result(), result("Free:AA\\:BB"), result()]) as run:
            network.connect_wifi("Free")
        self.assertNotIn("password", run.call_args.args)

    def test_hotspot_activation_failure_propagates(self):
        with patch.object(network, "get_wifi_interface", return_value="wlan0"), \
                patch.object(network, "_nmcli", side_effect=[result(), result("uuid"), result(),
                                                          RuntimeError("activation failed")]):
            with self.assertRaisesRegex(RuntimeError, "activation failed"):
                network.start_hotspot()

    def test_command_error_and_timeout_do_not_become_success(self):
        with patch.object(network.subprocess, "run", return_value=result(returncode=10, stderr="denied")):
            with self.assertRaisesRegex(RuntimeError, "denied"):
                network._nmcli("radio", "wifi", "on", change=True)
        with patch.object(network.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                ["nmcli", "password", "private-password"], 10)):
            with self.assertRaises(RuntimeError) as caught:
                network._nmcli("device", "wifi", "connect", "Office", "password", "private-password")
        self.assertNotIn("private-password", str(caught.exception))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg/ffprobe")
class RecordingChecks(unittest.TestCase):
    def test_low_and_variable_capture_rates_keep_real_duration_and_seek(self):
        import cv2
        import numpy as np
        from PyQt5.QtCore import QObject
        from core_camera import CameraWorker
        import threading

        # 只启用真实录制方法，不初始化硬件、GPS 和识别模型。
        worker = CameraWorker.__new__(CameraWorker)
        QObject.__init__(worker)
        worker.width, worker.height = 320, 180
        worker._hw_encoder = None
        worker.is_recording = True
        worker._encoder_proc = None
        worker._writer_lock = threading.RLock()
        worker._total_recorded_frames = 0
        worker._pending_storage_path = None
        worker._record_start_monotonic = 0.0
        worker.record_start_time = 0
        worker._check_and_clean_disk = lambda *args: True
        worker._storage_ready = lambda path: True
        frame = np.zeros((180, 320, 3), dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            worker.storage_path = directory
            for fps in (30, 60):
                with self.subTest(fps=fps):
                    worker.fps_target = fps
                    start = time.monotonic()
                    submitted = 0
                    try:
                        # 先约 20fps，后约 7fps，远低于标称帧率。
                        while time.monotonic() - start < 3.0:
                            worker._handle_recording(frame)
                            submitted += 1
                            time.sleep(.05 if time.monotonic() - start < 1 else .14)
                        filename = Path(directory) / worker.current_video_filename
                        elapsed = time.monotonic() - start
                    finally:
                        worker.stop_recording_file()
                    metadata = json.loads(subprocess.check_output([
                        "ffprobe", "-v", "error", "-count_frames", "-show_entries",
                        "stream=codec_name,nb_read_frames,r_frame_rate:format=duration", "-of", "json", str(filename)]))
                    duration = float(metadata["format"]["duration"])
                    stream = metadata["streams"][0]
                    self.assertLess(abs(duration - elapsed), .6, metadata)
                    self.assertEqual(stream["codec_name"], "h264")
                    self.assertEqual(stream["r_frame_rate"], f"{fps}/1")
                    self.assertGreater(int(stream["nb_read_frames"]), submitted)
                    cap = cv2.VideoCapture(str(filename))
                    try:
                        cap.set(cv2.CAP_PROP_POS_MSEC, 1500)
                        ok, _ = cap.read()
                        self.assertTrue(ok)
                        self.assertLess(abs(cap.get(cv2.CAP_PROP_POS_MSEC) - 1500), 100)
                    finally:
                        cap.release()
                    self.assertEqual(worker.recording_elapsed(), 0)
                    print(f"{fps}fps: wall={elapsed:.3f}s video={duration:.3f}s input_frames={submitted}")
                    # 再录一次不能把停录时间计算进视频。
                    time.sleep(.3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
