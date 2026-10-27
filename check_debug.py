"""额外故障回归：本地 HTTP、录像保护、快照、回放与 Wi-Fi 重试。"""
import http.client
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import cv2
import numpy as np
from PyQt5.QtCore import QObject

import web_server as web
from core_camera import CameraWorker
from check_regressions import NetworkChecks, RecordingChecks


def worker_stub(directory):
    return SimpleNamespace(storage_path=directory, is_recording=False,
                           current_video_filename="", known_plates={},
                           _writer_lock=threading.RLock(), record_start_time=0,
                           is_playback=False, recording_toggled=Mock())


class WebChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "clip+one.mp4").write_bytes(b"0123456789")
        (self.root / "wifi_passwords.json").write_text('{"fixture":"example"}')
        self.worker = worker_stub(self.directory.name)
        self.servers = []
        for name, value in (("get_local_ip", "127.0.0.1"), ("is_hotspot_active", False),
                            ("get_network_status", dict(ip="127.0.0.1", hotspot_active=False))):
            mock = patch.object(web, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.addCleanup(self.stop_servers)
        self.server = self.start_server(self.worker)

    def start_server(self, worker):
        server = web.DashcamWebServer(worker, host="127.0.0.1", port=0)
        self.servers.append(server)
        return server

    def start_real_server(self, server):
        server.start()
        if server._httpd is None:
            self.skipTest("当前沙箱不允许监听本机端口；其余 HTTP 解析测试使用内存传输")

    def stop_servers(self):
        for server in self.servers:
            stopper = threading.Thread(target=server.stop, daemon=True)
            stopper.start()
            stopper.join(.8)
            if stopper.is_alive():
                # 旧版 stop 会死锁，测试清理也必须有界。
                server._running = False
                try:
                    with socket.create_connection(server._httpd.server_address, timeout=.2):
                        pass
                except OSError:
                    pass

    def request(self, path, method="GET", data=None, headers=None, server=None, timeout=2):
        server = server or self.server
        if server._httpd is None:
            # 使用真实 BaseHTTPRequestHandler 解析完整 HTTP 请求；只替换传输。
            body = json.dumps(data).encode() if data is not None else b""
            request_headers = dict(headers or {})
            request_headers["Content-Length"] = str(len(body))
            raw = (f"{method} {path} HTTP/1.0\r\n" +
                   "".join(f"{key}: {value}\r\n" for key, value in request_headers.items()) +
                   "\r\n").encode() + body
            class MemorySocket:
                def __init__(self, data):
                    self.data = data
                    self.output = bytearray()
                def makefile(self, *args, **kwargs):
                    return io.BytesIO(self.data)
                def settimeout(self, timeout):
                    pass
                def sendall(self, data):
                    self.output.extend(data)
            transport = MemorySocket(raw)
            server._handler(transport, ("127.0.0.1", 1), None)
            response = http.client.HTTPResponse(MemorySocket(transport.output))
            response.begin()
            return response.status, dict(response.getheaders()), response.read()
        connection = http.client.HTTPConnection("127.0.0.1", server._httpd.server_port, timeout=timeout)
        try:
            body = json.dumps(data) if data is not None else None
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_stop_finishes_and_port_can_be_rebound(self):
        self.start_real_server(self.server)
        address = self.server._httpd.server_address
        done = threading.Event()
        def stop():
            self.server.stop()
            done.set()
        threading.Thread(target=stop, daemon=True).start()
        self.assertTrue(done.wait(.8), "Web stop deadlocked")
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(address)

    def test_slow_client_does_not_block_other_requests(self):
        self.start_real_server(self.server)
        with socket.create_connection(self.server._httpd.server_address, timeout=1) as slow:
            slow.sendall(b"GET /api/osd HTTP/1.0\r\n")
            time.sleep(.05)
            self.assertEqual(self.request("/api/osd", timeout=.5)[0], 200)

    def test_servers_keep_independent_workers(self):
        other = worker_stub(self.directory.name)
        other.current_video_filename = "other.mp4"
        second = self.start_server(other)
        first_body = json.loads(self.request("/api/recording")[2])
        second_body = json.loads(self.request("/api/recording", server=second)[2])
        self.assertEqual(first_body["current_file"], "")
        self.assertEqual(second_body["current_file"], "other.mp4")

    def test_literal_plus_in_video_name_is_preserved(self):
        status, _, body = self.request("/api/videos/clip+one.mp4?download=1")
        self.assertEqual((status, body), (200, b"0123456789"))

    def test_byte_ranges_include_suffix_and_reject_unsatisfiable_ranges(self):
        path = "/api/videos/clip%2Bone.mp4?download=1"
        for value, expected in (("bytes=2-5", b"2345"), ("bytes=-3", b"789"),
                                ("bytes=7-", b"789")):
            with self.subTest(value=value):
                status, _, body = self.request(path, headers={"Range": value})
                self.assertEqual((status, body), (206, expected))
        for value in ("bytes=99-", "bytes=5-2", "bytes=-0"):
            with self.subTest(value=value):
                status, headers, _ = self.request(path, headers={"Range": value})
                self.assertEqual(status, 416)
                self.assertEqual(headers["Content-Range"], "bytes */10")

    def test_video_routes_cannot_read_or_delete_configuration(self):
        self.assertEqual(self.request("/api/videos/wifi_passwords.json?download=1")[0], 403)
        self.assertEqual(self.request("/api/videos/delete", "POST",
                                      {"filename": "wifi_passwords.json"})[0], 403)
        self.assertTrue((self.root / "wifi_passwords.json").exists())

    def test_current_recording_cannot_be_deleted(self):
        self.worker.is_recording = True
        self.worker.current_video_filename = "clip+one.mp4"
        self.assertEqual(self.request("/api/videos/delete", "POST",
                                      {"filename": "clip+one.mp4"})[0], 409)
        self.assertTrue((self.root / "clip+one.mp4").exists())

    def test_video_symlink_cannot_escape_storage(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "outside.mp4"
            target.write_bytes(b"outside")
            (self.root / "link.mp4").symlink_to(target)
            self.assertEqual(self.request("/api/videos/link.mp4?download=1")[0], 403)
            self.assertEqual(self.request("/api/videos/delete", "POST", {"filename": "link.mp4"})[0], 403)
            self.assertTrue(target.exists())

    def test_deleting_video_symlink_preserves_its_target(self):
        (self.root / "link.mp4").symlink_to(self.root / "clip+one.mp4")
        self.assertEqual(self.request("/api/videos/delete", "POST", {"filename": "link.mp4"})[0], 200)
        self.assertFalse((self.root / "link.mp4").exists())
        self.assertTrue((self.root / "clip+one.mp4").exists())

    def test_alias_of_current_recording_cannot_be_deleted(self):
        (self.root / "link.mp4").symlink_to(self.root / "clip+one.mp4")
        self.worker.current_video_filename = "clip+one.mp4"
        self.assertEqual(self.request("/api/videos/delete", "POST", {"filename": "link.mp4"})[0], 409)
        self.assertTrue((self.root / "clip+one.mp4").exists())

    def test_invalid_json_cannot_trigger_recording_action(self):
        self.assertEqual(self.request("/api/record/start", "POST", data=[])[0], 400)
        self.assertFalse(self.worker.is_recording)

    def test_cannot_start_recording_during_local_playback(self):
        self.worker.is_playback = True
        self.assertEqual(self.request("/api/record/start", "POST", data={})[0], 409)
        self.assertFalse(self.worker.is_recording)

    def test_server_shutdown_completes_with_serve_forever(self):
        stopping = threading.Event()
        serving = threading.Event()
        class ServerHarness:
            server_port = 8091
            def serve_forever(self, **kwargs):
                serving.set()
                stopping.wait(2)
            def shutdown(self):
                stopping.set()
            def server_close(self):
                pass
        with patch.object(web, "ThreadingHTTPServer", return_value=ServerHarness()):
            self.server.start()
            self.assertTrue(serving.wait(.5))
            self.server.stop()
            self.assertFalse(self.server._thread.is_alive())

    def test_bind_failure_is_not_reported_as_started(self):
        with patch.object(web, "ThreadingHTTPServer", side_effect=OSError("address in use")):
            self.assertIsNone(self.server.start())
        self.assertFalse(self.server._running)

    def test_snapshot_uses_cached_frame_without_reading_capture(self):
        self.worker.cap = Mock()
        self.worker.cap.read.side_effect = AssertionError("concurrent capture read")
        self.worker.running = True
        jpeg = cv2.imencode(".jpg", np.zeros((24, 32, 3), np.uint8))[1].tobytes()
        self.worker.get_snapshot_jpeg = lambda: jpeg
        status, _, body = self.request("/api/snapshot")
        self.assertEqual((status, body), (200, jpeg))
        self.worker.cap.read.assert_not_called()

    def test_known_plate_save_failure_is_reported(self):
        with patch.object(web.os, "replace", side_effect=PermissionError("read-only fixture")):
            self.assertEqual(self.request("/api/known-plates", "POST",
                                          {"plate": "沪A12345", "nickname": "fixture"})[0], 500)
        self.assertEqual(self.worker.known_plates, {})


class PlaybackChecks(unittest.TestCase):
    def test_late_frame_cannot_reopen_encoder_after_stop(self):
        worker = self.playback_worker()
        worker.is_recording = False
        worker._writer_lock = threading.RLock()
        worker._handle_recording_locked = Mock()
        worker._handle_recording(np.zeros((24, 32, 3), np.uint8))
        worker._handle_recording_locked.assert_not_called()

    def playback_worker(self):
        worker = CameraWorker.__new__(CameraWorker)
        QObject.__init__(worker)
        worker.running = True
        worker.is_playback = True
        worker.is_paused = False
        worker.playback_file = "/fixture/clip.mkv"
        worker.new_playback_file = None
        worker.seek_frame = None
        worker.storage_path = ""
        return worker

    def test_seek_while_paused_renders_new_frame(self):
        worker = self.playback_worker()
        worker.is_paused = True
        worker.seek_frame = 15
        cap = Mock()
        cap.get.side_effect = lambda prop: 30
        cap.read.return_value = (True, np.zeros((24, 32, 3), np.uint8))
        ready = threading.Event()
        from PyQt5.QtCore import Qt
        worker.frame_ready.connect(lambda *_: ready.set(), type=Qt.DirectConnection)
        with patch("core_camera.cv2.VideoCapture", return_value=cap):
            thread = threading.Thread(target=worker._run_playback, daemon=True)
            thread.start()
            try:
                self.assertTrue(ready.wait(.4), "paused seek never renders")
                self.assertTrue(worker.is_paused)
            finally:
                worker.running = False
                thread.join(1)
        cap.release.assert_called_once()

    def test_selecting_same_file_restarts_playback(self):
        worker = self.playback_worker()
        worker.new_playback_file = worker.playback_file
        cap = Mock()
        cap.get.return_value = 30
        cap.read.return_value = (True, np.zeros((24, 32, 3), np.uint8))
        from PyQt5.QtCore import Qt
        worker.frame_ready.connect(lambda *_: setattr(worker, "running", False), type=Qt.DirectConnection)
        with patch("core_camera.cv2.VideoCapture", return_value=cap) as capture:
            worker._run_playback()
        self.assertEqual(capture.call_count, 2)

    def test_cached_snapshot_does_not_touch_camera(self):
        worker = self.playback_worker()
        worker._snapshot_lock = threading.Lock()
        worker._snapshot_frame = np.zeros((24, 32, 3), np.uint8)
        worker.cap = Mock()
        jpeg = worker.get_snapshot_jpeg()
        self.assertIsNotNone(cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR))
        worker.cap.read.assert_not_called()


class WifiRetryChecks(unittest.TestCase):
    def test_failed_saved_password_requests_new_input_on_ui_thread(self):
        from ui_mainwindow import DashcamMainWindow
        window = SimpleNamespace(osd_signal=Mock(), wifi_password_retry=Mock(),
                                 poll_network_status=Mock(), save_wifi_password=Mock())
        with patch("ui_mainwindow.connect_wifi", side_effect=RuntimeError("bad password")):
            DashcamMainWindow._bg_connect_wifi(window, "Office", "old-password", False, "AA:BB")
        window.wifi_password_retry.emit.assert_called_once_with("Office", "AA:BB")
        window.save_wifi_password.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
