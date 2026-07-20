# web_server.py — 车缘管理网页 HTTP 服务器
"""轻量级 HTTP 服务器 + REST API，零额外依赖（仅 Python 标准库 + OpenCV）。

通过热点 (Pi-Dashcam) 或 WiFi 客户端模式连接后，
浏览器访问 http://<树莓派IP> 即可查看和管理行车记录仪。
"""

import os
import re
import json
import time
import socket
import psutil
import threading
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote_plus

# cv2 仅在生成实时快照时使用
import cv2

# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

def get_local_ip():
    """获取本机局域网 IP 地址（优先 wlan0）"""
    try:
        # 方案 1：通过 nmcli 查活动连接
        res = subprocess.run(
            ["nmcli", "-t", "-f", "IP4.ADDRESS", "device", "show", "wlan0"],
            capture_output=True, text=True, timeout=5
        )
        for line in res.stdout.strip().split("\n"):
            line = line.strip()
            if line.startswith("IP4.ADDRESS[1]:"):
                ip = line.split(":", 1)[1].strip()
                if ip and not ip.startswith("127."):
                    # 去掉 CIDR 后缀 (如 /24)
                    return ip.split("/")[0]
    except Exception:
        pass

    # 方案 2：socket 方式回退
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "未知"


def is_hotspot_active():
    """检查 Pi-Dashcam 热点是否开启"""
    try:
        res = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5
        )
        for line in res.stdout.strip().split("\n"):
            if "Pi-Dashcam" in line and "802-11-wireless" in line:
                return True
    except Exception:
        pass
    return False


def get_network_status():
    """获取当前网络连接状态"""
    status = {
        "mode": "unknown",       # hotspot / wifi / disconnected
        "ssid": "",
        "ip": get_local_ip(),
        "hotspot_active": is_hotspot_active(),
    }
    try:
        res = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"],
            capture_output=True, text=True, timeout=5
        )
        for line in res.stdout.strip().split("\n"):
            parts = line.split(":")
            if len(parts) >= 3:
                name, dev, conn_type = parts[0], parts[1], parts[2]
                if "802-11-wireless" in conn_type or "wifi" in conn_type.lower():
                    if name == "Pi-Dashcam":
                        status["mode"] = "hotspot"
                    else:
                        status["mode"] = "wifi"
                        status["ssid"] = name
                    break
        if status["mode"] == "unknown":
            status["mode"] = "disconnected"
    except Exception:
        pass
    return status


def get_system_uptime():
    """获取系统运行时间"""
    try:
        with open("/proc/uptime", "r") as f:
            uptime_seconds = float(f.readline().split()[0])
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        mins = int((uptime_seconds % 3600) // 60)
        if days > 0:
            return f"{days}天 {hours}小时 {mins}分钟"
        elif hours > 0:
            return f"{hours}小时 {mins}分钟"
        else:
            return f"{mins}分钟"
    except Exception:
        return "未知"


def get_cpu_temp():
    """获取树莓派 CPU 温度"""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp = float(f.read().strip()) / 1000.0
        return round(temp, 1)
    except Exception:
        return None


def get_memory_info():
    """获取内存使用情况"""
    mem = psutil.virtual_memory()
    return {
        "total_gb": round(mem.total / (1024**3), 1),
        "used_gb": round(mem.used / (1024**3), 1),
        "percent": mem.percent,
    }


def get_osd_message(msg_list, max_entries=20):
    """线程安全地读取 OSD 消息历史"""
    return list(msg_list[-max_entries:])


# ---------------------------------------------------------------------------
# HTTP 请求处理器
# ---------------------------------------------------------------------------

class DashcamAPIHandler(BaseHTTPRequestHandler):
    """REST API + 静态文件请求处理器"""

    # 类变量，由 DashcamWebServer 在启动前注入
    camera_worker = None       # CameraWorker 实例引用
    osd_messages = []          # OSD 消息历史
    server_start_time = 0.0    # 服务器启动时间戳

    # 静态文件目录（相对于本文件）
    STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    def log_message(self, format, *args):
        """抑制标准日志输出（改用简洁格式）"""
        print(f"🌐 [{self.address_string()}] {args[0]}")

    # ---- 响应辅助 ----

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, msg, status=500):
        self._send_json({"error": True, "message": msg}, status)

    def _send_file(self, filepath, content_type="application/octet-stream", download_name=None):
        """发送文件（支持 Range 请求，用于视频流）"""
        if not os.path.isfile(filepath):
            self._send_error_json("文件不存在", 404)
            return

        file_size = os.path.getsize(filepath)
        range_header = self.headers.get("Range")

        if range_header:
            # 处理 Range 请求（视频 seek）
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else file_size - 1
                end = min(end, file_size - 1)
                length = end - start + 1

                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", length)
            else:
                start, length = 0, file_size
                self.send_response(200)
                self.send_header("Content-Length", file_size)
        else:
            start, length = 0, file_size
            self.send_response(200)
            self.send_header("Content-Length", file_size)

        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        if download_name:
            from urllib.parse import quote
            self.send_header("Content-Disposition",
                f'attachment; filename="{quote(download_name)}"')
        self.end_headers()

        with open(filepath, "rb") as f:
            f.seek(start)
            remaining = length
            chunk_size = 65536  # 64KB
            while remaining > 0:
                data = f.read(min(chunk_size, remaining))
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

    def _serve_static(self, path):
        """提供静态文件服务"""
        # 安全检查：防止目录穿越
        safe_path = os.path.normpath(path).lstrip("/")
        full_path = os.path.join(self.STATIC_DIR, safe_path)

        # 确保在 static 目录内
        if not full_path.startswith(self.STATIC_DIR):
            self._send_error_json("禁止访问", 403)
            return

        if not os.path.isfile(full_path):
            self._send_error_json("文件不存在", 404)
            return

        # MIME 类型映射
        ext_map = {
            ".html": "text/html; charset=utf-8",
            ".css":  "text/css; charset=utf-8",
            ".js":   "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
            ".jpeg": "image/jpeg",
            ".svg":  "image/svg+xml",
            ".ico":  "image/x-icon",
            ".woff2":"font/woff2",
        }
        _, ext = os.path.splitext(full_path)
        content_type = ext_map.get(ext, "application/octet-stream")

        with open(full_path, "rb") as f:
            data = f.read()

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(data))
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    # ---- 路由分发 ----

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote_plus(parsed.path.rstrip("/") or "/")
        params = parse_qs(parsed.query)

        try:
            # 静态文件 + 首页
            if path == "/" or path == "/index.html":
                self._serve_static("index.html")
                return

            if path.startswith("/static/"):
                self._serve_static(path.replace("/static/", "", 1))
                return

            # === API 路由 ===

            if path == "/api/status":
                self._handle_status()
            elif path == "/api/videos":
                self._handle_video_list(params)
            elif path.startswith("/api/videos/"):
                filename = path[len("/api/videos/"):]
                self._handle_video_file(filename, params)
            elif path == "/api/plates":
                self._handle_plates(params)
            elif path == "/api/snapshot":
                self._handle_snapshot()
            elif path == "/api/network":
                self._handle_network()
            elif path == "/api/storage":
                self._handle_storage()
            elif path == "/api/recording":
                self._handle_recording_state()
            elif path == "/api/osd":
                self._handle_osd_messages()
            elif path == "/favicon.ico":
                self._send_error_json("", 404)
            else:
                self._send_error_json(f"未知接口: {path}", 404)

        except Exception as e:
            print(f"❌ API 错误 [{path}]: {e}")
            self._send_error_json(f"服务器内部错误: {e}", 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote_plus(parsed.path.rstrip("/") or "/")

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        try:
            if path == "/api/record/start":
                self._handle_record_start(data)
            elif path == "/api/record/stop":
                self._handle_record_stop(data)
            elif path == "/api/videos/delete":
                self._handle_video_delete(data)
            elif path == "/api/restart-hotspot":
                self._handle_restart_hotspot(data)
            elif path == "/api/disconnect-wifi":
                self._handle_disconnect_wifi()
            else:
                self._send_error_json(f"未知接口: {path}", 404)
        except Exception as e:
            print(f"❌ API 错误 [{path}]: {e}")
            self._send_error_json(f"服务器内部错误: {e}", 500)

    def do_OPTIONS(self):
        """CORS 预检请求"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # ---- API 处理器 ----

    def _handle_status(self):
        worker = self.camera_worker
        status = {
            "server_uptime": get_system_uptime(),
            "server_start": time.strftime("%Y-%m-%d %H:%M:%S",
                time.localtime(self.server_start_time)),
            "cpu_temp": get_cpu_temp(),
            "memory": get_memory_info(),
            "recording": {
                "active": worker.is_recording if worker else False,
                "current_file": getattr(worker, "current_video_filename", "") if worker else "",
                "elapsed_sec": int(time.time() - worker.record_start_time)
                    if (worker and worker.is_recording and worker.record_start_time > 0) else 0,
            },
            "network": get_network_status(),
            "camera": {
                "resolution": f"{worker.width}x{worker.height}" if worker else "未知",
                "fps_target": worker.fps_target if worker else 0,
                "lpr_ready": worker.lpr_catcher is not None if worker else False,
            } if worker else {},
        }

        # GPS 信息
        if worker and worker.gps:
            status["gps"] = {
                "latitude": round(worker.gps.latitude, 5),
                "longitude": round(worker.gps.longitude, 5),
                "speed_kmh": round(worker.gps.speed_kmh, 1),
                "satellites": worker.gps.satellites,
                "has_fix": worker.gps.has_fix,
                "fix_quality": worker.gps.fix_quality,
                "error": worker.gps.serial_error,
            }

        self._send_json(status)

    def _handle_video_list(self, params):
        worker = self.camera_worker
        if not worker or not worker.storage_path:
            self._send_json({"videos": [], "storage_path": ""})
            return

        path = worker.storage_path
        videos = []
        try:
            if os.path.exists(path):
                files = sorted(
                    [f for f in os.listdir(path) if f.endswith((".mkv", ".mp4"))],
                    reverse=True
                )
                for i, f in enumerate(files):
                    fpath = os.path.join(path, f)
                    stat = os.stat(fpath)
                    size_mb = round(stat.st_size / (1024 * 1024), 1)
                    # 从文件名解析时间
                    mtime = time.strftime("%Y-%m-%d %H:%M:%S",
                        time.localtime(stat.st_mtime))

                    videos.append({
                        "id": i,
                        "filename": f,
                        "size_mb": size_mb,
                        "mtime": mtime,
                        "timestamp": stat.st_mtime,
                        # 从文件名提取时间：REC_20240720_143052.mkv
                        "_parse_time": f.replace("REC_", "").replace(".mkv", "").replace(".mp4", ""),
                    })

            # 按文件名中的时间排序（更准确）
            videos.sort(key=lambda v: v["_parse_time"], reverse=True)
        except Exception as e:
            self._send_error_json(f"读取视频列表失败: {e}", 500)
            return

        self._send_json({
            "videos": videos,
            "storage_path": path,
            "count": len(videos),
        })

    def _handle_video_file(self, filename, params):
        worker = self.camera_worker
        if not worker or not worker.storage_path:
            self._send_error_json("未检测到存储设备", 404)
            return

        # 安全检查
        safe_name = os.path.basename(filename)
        if ".." in safe_name or safe_name.startswith("/"):
            self._send_error_json("非法文件名", 403)
            return

        filepath = os.path.join(worker.storage_path, safe_name)
        if not os.path.isfile(filepath):
            self._send_error_json("文件不存在", 404)
            return

        # 支持 ?download=1 强制下载
        download = params.get("download", ["0"])[0]
        dn = safe_name if download == "1" else None

        self._send_file(filepath,
            content_type="video/x-matroska" if safe_name.endswith(".mkv") else "video/mp4",
            download_name=dn)

    def _handle_plates(self, params):
        worker = self.camera_worker
        if not worker or not worker.storage_path:
            self._send_json({"plates": [], "total": 0})
            return

        log_file = os.path.join(worker.storage_path, "plates_log.json")
        plates = []
        try:
            if os.path.exists(log_file):
                with open(log_file, "r", encoding="utf-8") as f:
                    plates = json.load(f)
        except Exception as e:
            self._send_error_json(f"读取车牌日志失败: {e}", 500)
            return

        # 分页
        page = int(params.get("page", ["1"])[0])
        per_page = int(params.get("per_page", ["50"])[0])
        search = params.get("search", [""])[0].strip().upper()

        # 过滤
        if search:
            plates = [p for p in plates if search in str(p.get("plate", "")).upper()]

        total = len(plates)
        start = (page - 1) * per_page
        end = start + per_page
        page_data = plates[::-1][start:end]  # 最新的在前

        self._send_json({
            "plates": page_data,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": max(1, (total + per_page - 1) // per_page),
        })

    def _handle_snapshot(self):
        """生成当前摄像头的 JPEG 快照"""
        worker = self.camera_worker
        if not worker or not worker.cap or not worker.cap.isOpened():
            self._send_error_json("摄像头未就绪", 503)
            return

        try:
            # 从摄像头读取一帧（不影响主循环，cap 是共享的）
            ret, frame = worker.cap.read()
            if not ret or frame is None:
                self._send_error_json("无法读取摄像头画面", 500)
                return

            # 编码为 JPEG
            _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            data = jpeg.tobytes()

            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", len(data))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_error_json(f"快照生成失败: {e}", 500)

    def _handle_network(self):
        self._send_json(get_network_status())

    def _handle_storage(self):
        worker = self.camera_worker
        devices = SystemUtils.get_storage_devices() if hasattr(SystemUtils, "get_storage_devices") else []

        storage_info = {
            "current_path": worker.storage_path if worker else "",
            "devices": devices,
        }
        if worker and worker.storage_path:
            try:
                usage = psutil.disk_usage(worker.storage_path)
                storage_info["usage"] = {
                    "total_gb": round(usage.total / (1024**3), 1),
                    "used_gb": round(usage.used / (1024**3), 1),
                    "free_gb": round(usage.free / (1024**3), 1),
                    "percent": usage.percent,
                }
            except Exception:
                storage_info["usage"] = None

        self._send_json(storage_info)

    def _handle_recording_state(self):
        worker = self.camera_worker
        self._send_json({
            "is_recording": worker.is_recording if worker else False,
            "current_file": getattr(worker, "current_video_filename", "") if worker else "",
            "storage_path": worker.storage_path if worker else "",
            "elapsed_sec": int(time.time() - worker.record_start_time)
                if (worker and worker.is_recording and worker.record_start_time > 0) else 0,
        })

    def _handle_osd_messages(self):
        self._send_json({
            "messages": [{"time": t, "msg": m} for t, m in self.osd_messages[-30:]]
        })

    # ---- POST 处理器 ----

    def _handle_record_start(self, data):
        worker = self.camera_worker
        if not worker:
            self._send_error_json("摄像头未就绪", 503)
            return
        if not worker.storage_path:
            self._send_error_json("未检测到存储设备，无法录像", 400)
            return

        if worker.is_recording:
            self._send_json({"success": True, "message": "已在录像中"})
            return

        worker.is_recording = True
        worker.recording_toggled.emit(True)
        self.osd_messages.append((time.time(), "🌐 远程启动录像"))
        self._send_json({"success": True, "message": "录像已启动"})

    def _handle_record_stop(self, data):
        worker = self.camera_worker
        if not worker:
            self._send_error_json("摄像头未就绪", 503)
            return

        if not worker.is_recording:
            self._send_json({"success": True, "message": "未在录像中"})
            return

        worker.is_recording = False
        worker.stop_recording_file()
        worker.recording_toggled.emit(False)
        self.osd_messages.append((time.time(), "🌐 远程停止录像"))
        self._send_json({"success": True, "message": "录像已停止"})

    def _handle_video_delete(self, data):
        worker = self.camera_worker
        filename = data.get("filename", "")

        if not filename or not worker or not worker.storage_path:
            self._send_error_json("参数错误", 400)
            return

        safe_name = os.path.basename(filename)
        if ".." in safe_name:
            self._send_error_json("非法文件名", 403)
            return

        filepath = os.path.join(worker.storage_path, safe_name)
        if not os.path.isfile(filepath):
            self._send_error_json("文件不存在", 404)
            return

        try:
            os.remove(filepath)
            self.osd_messages.append((time.time(), f"🌐 远程删除视频: {safe_name}"))
            self._send_json({"success": True, "message": f"已删除: {safe_name}"})
        except Exception as e:
            self._send_error_json(f"删除失败: {e}", 500)

    def _handle_restart_hotspot(self, data):
        """重启热点（需要 sudo）"""
        def _bg_restart():
            try:
                subprocess.run(["sudo", "nmcli", "connection", "down", "Pi-Dashcam"],
                    capture_output=True, timeout=10)
                time.sleep(1)
                subprocess.run(["sudo", "nmcli", "connection", "up", "Pi-Dashcam"],
                    capture_output=True, timeout=10)
                self.osd_messages.append((time.time(), "🌐 热点已重启"))
            except Exception as e:
                self.osd_messages.append((time.time(), f"❌ 热点重启失败: {e}"))

        threading.Thread(target=_bg_restart, daemon=True).start()
        self._send_json({"success": True, "message": "正在重启热点..."})

    def _handle_disconnect_wifi(self):
        """断开当前 WiFi 连接（回到热点模式）"""
        def _bg_disconnect():
            try:
                # 断开 wlan0
                subprocess.run(["sudo", "nmcli", "device", "disconnect", "wlan0"],
                    capture_output=True, timeout=10)
                time.sleep(3)
                # 启动热点
                subprocess.run(["sudo", "nmcli", "connection", "up", "Pi-Dashcam"],
                    capture_output=True, timeout=10)
                self.osd_messages.append((time.time(), "🌐 已断开 WiFi，回到热点模式"))
            except Exception as e:
                self.osd_messages.append((time.time(), f"❌ 断开 WiFi 失败: {e}"))

        threading.Thread(target=_bg_disconnect, daemon=True).start()
        self._send_json({"success": True, "message": "正在断开 WiFi 并恢复热点..."})


# ---------------------------------------------------------------------------
# Web 服务器主类
# ---------------------------------------------------------------------------

class DashcamWebServer:
    """车缘管理网页服务器

    在后台守护线程中运行 HTTP 服务器，提供 REST API 和静态文件服务。
    零额外 pip 依赖，仅使用 Python 标准库 + OpenCV（快照功能）。
    """

    def __init__(self, camera_worker=None, port=80, host="0.0.0.0"):
        self.port = port
        self.host = host
        self.camera_worker = camera_worker
        self._httpd = None
        self._thread = None
        self._running = False

        # 注入到请求处理器类
        DashcamAPIHandler.camera_worker = camera_worker
        DashcamAPIHandler.server_start_time = time.time()
        DashcamAPIHandler.osd_messages = []

    # ---- 公开 API ----

    def add_osd_message(self, msg):
        """从主线程安全地添加 OSD 消息（仅追加到列表）"""
        DashcamAPIHandler.osd_messages.append((time.time(), msg))
        # 保留最近 200 条
        if len(DashcamAPIHandler.osd_messages) > 200:
            DashcamAPIHandler.osd_messages = DashcamAPIHandler.osd_messages[-200:]

    def update_worker_ref(self, worker):
        """运行时更新 CameraWorker 引用"""
        self.camera_worker = worker
        DashcamAPIHandler.camera_worker = worker

    def start(self):
        """启动 Web 服务器（非阻塞，在守护线程中运行）"""
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

        # 获取 IP 地址
        ip = get_local_ip()
        print(f"\n{'='*50}")
        print(f"🌐 管理网页已启动！")
        print(f"   📱 地址: http://{ip}:{self.port}")
        if is_hotspot_active():
            print(f"   🔥 热点模式: 连接 Pi-Dashcam WiFi 后访问")
        print(f"{'='*50}\n")

        return f"http://{ip}:{self.port}"

    def stop(self):
        """停止 Web 服务器"""
        self._running = False
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
        print("🌐 Web 服务器已停止")

    def _serve(self):
        """服务器主循环（在守护线程中运行）"""
        try:
            self._httpd = HTTPServer((self.host, self.port), DashcamAPIHandler)
            self._httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError as e:
            print(f"❌ Web 服务器启动失败 (端口 {self.port}): {e}")
            self._running = False
            return

        print(f"🌐 HTTP 服务器监听 {self.host}:{self.port}")

        while self._running:
            try:
                self._httpd.handle_request()
            except Exception:
                if self._running:
                    time.sleep(0.5)

        try:
            self._httpd.server_close()
        except Exception:
            pass


# 延迟导入（避免循环依赖）
from core_camera import SystemUtils
