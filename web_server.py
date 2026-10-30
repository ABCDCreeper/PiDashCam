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
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

# cv2 仅在生成实时快照时使用
import cv2
from network_manager import get_network_status as _wifi_status, start_hotspot

# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

def _fallback_local_ip():
    """从本机接口取地址，离线热点不依赖公网路由探测。"""
    interfaces = psutil.net_if_stats()
    for name, addresses in psutil.net_if_addrs().items():
        if name not in interfaces or not interfaces[name].isup:
            continue
        for address in addresses:
            if address.family == socket.AF_INET and not address.address.startswith("127."):
                return address.address
    return "未知"


def get_network_status():
    status = _wifi_status()
    if status["ip"] == "未知":
        status["ip"] = _fallback_local_ip()
    return status


def get_local_ip():
    return get_network_status()["ip"]


def is_hotspot_active():
    return get_network_status()["hotspot_active"]


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

    timeout = 15

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

        start, length = 0, file_size
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if match and any(match.groups()):
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), file_size - 1) if last else file_size - 1
                else:
                    start = max(0, file_size - int(last))
                    end = file_size - 1
                if start >= file_size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{file_size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            else:
                # 不支持多段 Range，按 HTTP 语义忽略并发送完整文件。
                self.send_response(200)
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(length))

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

    def _send_transmuxed_video(self, filepath):
        """用 ffmpeg 将 MKV 实时转封装为 MP4 流式输出（浏览器兼容）

        ffmpeg -c copy 不重新编码，仅改容器，CPU 开销极低。
        降级：ffmpeg 不可用时回退到原始 MKV。
        """
        ffmpeg_bin = "/usr/bin/ffmpeg"
        if not os.path.exists(ffmpeg_bin):
            # 降级：直接发送 MKV（不转封装）
            print("⚠️ ffmpeg 未安装，回退到原始 MKV 流")
            self._send_file(filepath, content_type="video/x-matroska")
            return

        try:
            # -c copy: 不重新编码，只改容器格式
            # -f mp4: 输出 MP4
            # -movflags frag_keyframe+empty_moov: 碎片化 MP4，适合流式传输
            proc = subprocess.Popen(
                [ffmpeg_bin, "-i", filepath, "-c", "copy",
                 "-f", "mp4", "-movflags", "frag_keyframe+empty_moov",
                 "-nostdin", "-loglevel", "error", "pipe:1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            print(f"❌ ffmpeg 启动失败: {e}")
            self._send_error_json("视频转码启动失败", 500)
            return

        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "none")  # 管道输出不支持 Range
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        try:
            chunk_size = 131072  # 128KB
            while True:
                chunk = proc.stdout.read(chunk_size)
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    break  # 客户端断开

            proc.stdout.close()
            proc.wait(timeout=5)
        except Exception:
            pass
        finally:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass

    def _serve_static(self, path):
        """提供静态文件服务"""
        # 安全检查：防止目录穿越
        safe_path = os.path.normpath(path).lstrip("/")
        full_path = os.path.join(self.STATIC_DIR, safe_path)

        # 确保在 static 目录内
        if os.path.commonpath([os.path.realpath(full_path), os.path.realpath(self.STATIC_DIR)]) != os.path.realpath(self.STATIC_DIR):
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
        path = unquote(parsed.path.rstrip("/") or "/")
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
            elif path == "/api/known-plates":
                self._handle_known_plates_get()
            elif path == "/favicon.ico":
                self._send_error_json("", 404)
            else:
                self._send_error_json(f"未知接口: {path}", 404)

        except Exception as e:
            print(f"❌ API 错误 [{path}]: {e}")
            self._send_error_json(f"服务器内部错误: {e}", 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path.rstrip("/") or "/")

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if not 0 <= content_length <= 65536:
                self._send_error_json("请求体过大或长度非法", 400)
                return
            body = self.rfile.read(content_length) if content_length else b""
            data = json.loads(body) if body else {}
            if not isinstance(data, dict):
                raise ValueError("请求体必须为 JSON 对象")
        except (ValueError, UnicodeDecodeError):
            self._send_error_json("无效的 JSON 请求或长度", 400)
            return

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
            elif path == "/api/known-plates":
                self._handle_known_plates_post(data)
            elif path == "/api/known-plates/delete":
                self._handle_known_plates_post(data)
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
            "deployment_revision": os.environ.get("DASHCAM_REVISION", ""),
            "server_uptime": get_system_uptime(),
            "server_start": time.strftime("%Y-%m-%d %H:%M:%S",
                time.localtime(self.server_start_time)),
            "cpu_temp": get_cpu_temp(),
            "memory": get_memory_info(),
            "recording": {
                "active": worker.is_recording if worker else False,
                "current_file": getattr(worker, "current_video_filename", "") if worker else "",
                "elapsed_sec": int(worker.recording_elapsed())
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

    def _video_path(self, filename):
        worker = self.camera_worker
        if (not isinstance(filename, str) or not filename or
                filename != os.path.basename(filename) or
                not filename.lower().endswith((".mkv", ".mp4"))):
            self._send_error_json("非法视频文件名", 403)
            return None
        root = os.path.realpath(worker.storage_path)
        filepath = os.path.join(root, filename)
        if os.path.commonpath([root, os.path.realpath(filepath)]) != root:
            self._send_error_json("禁止访问存储目录之外的文件", 403)
            return None
        if not os.path.isfile(filepath):
            self._send_error_json("文件不存在", 404)
            return None
        return filepath

    def _handle_video_file(self, filename, params):
        worker = self.camera_worker
        if not worker or not worker.storage_path:
            self._send_error_json("未检测到存储设备", 404)
            return

        filepath = self._video_path(filename)
        if filepath is None:
            return
        safe_name = os.path.basename(filepath)

        # 支持 ?download=1 强制下载
        download = params.get("download", ["0"])[0]

        if download == "1":
            # 下载模式：发送原始 MKV 文件
            self._send_file(filepath,
                content_type="video/x-matroska" if safe_name.endswith(".mkv") else "video/mp4",
                download_name=safe_name)
        elif safe_name.endswith(".mkv"):
            # 播放模式 + MKV → 用 ffmpeg 实时转封装为 MP4（浏览器不原生支持 MKV）
            self._send_transmuxed_video(filepath)
        else:
            # 播放模式 + 非 MKV → 直接发送
            self._send_file(filepath, content_type="video/mp4")

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

    # ---- 已知车牌管理 ----

    def _known_plates_path(self):
        """已知车牌配置文件路径（优先 U 盘，回退系统盘）"""
        worker = self.camera_worker
        if worker and worker.storage_path:
            return os.path.join(worker.storage_path, "known_plates.json")
        return os.path.expanduser("~/Videos/known_plates.json")

    def _load_known_plates(self):
        path = self._known_plates_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_known_plates(self, data):
        path = self._known_plates_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # 原子写入：先写临时文件再 rename，防止断电/并发导致文件损坏
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            # 同步更新 CameraWorker 缓存
            if self.camera_worker:
                self.camera_worker.known_plates = data.copy()
        except Exception as e:
            raise RuntimeError(f"保存已知车牌失败: {e}") from e

    def _handle_known_plates_get(self):
        self._send_json(self._load_known_plates())

    def _handle_known_plates_post(self, data):
        with self.known_plates_lock:
            self._update_known_plate(data)

    def _update_known_plate(self, data):
        action = data.get("action", "add")
        plate = data.get("plate", "").strip().upper()
        if not plate:
            self._send_error_json("车牌号不能为空", 400)
            return

        known = self._load_known_plates()

        if action == "delete":
            if plate in known:
                del known[plate]
                self._save_known_plates(known)
                self._send_json({"success": True, "message": f"已删除 {plate}"})
            else:
                self._send_error_json(f"未找到 {plate}", 404)
        else:
            nickname = data.get("nickname", "").strip()
            known[plate] = nickname
            self._save_known_plates(known)
            label = f"{nickname}({plate})" if nickname else plate
            self._send_json({"success": True, "message": f"已保存 {label}"})

    def _handle_snapshot(self):
        worker = self.camera_worker
        data = worker.get_snapshot_jpeg() if worker else None
        if data is None:
            self._send_error_json("摄像头画面未就绪", 503)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _handle_network(self):
        self._send_json(get_network_status())

    def _handle_storage(self):
        worker = self.camera_worker
        devices = list(getattr(worker, "storage_devices", [])) if worker else []

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
            "elapsed_sec": int(worker.recording_elapsed())
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
        if worker.is_playback:
            self._send_error_json("请先退出本机回放再开始录像", 409)
            return

        if worker.is_recording:
            self._send_json({"success": True, "message": "已在录像中"})
            return

        print(f"RECORD_TIMING event=start_request source=http mono={time.monotonic():.6f}")
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

        print(f"RECORD_TIMING event=stop_request source=http mono={time.monotonic():.6f}")
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

        filepath = self._video_path(filename)
        if filepath is None:
            return
        safe_name = os.path.basename(filepath)
        with worker._writer_lock:
            active_file = worker.current_video_filename
            if active_file and os.path.realpath(os.path.join(worker.storage_path, active_file)) == os.path.realpath(filepath):
                self._send_error_json("当前视频正在录制，请先停止录像", 409)
                return
            self._delete_video_file(filepath, safe_name)

    def _delete_video_file(self, filepath, safe_name):
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
                start_hotspot()
                self.osd_messages.append((time.time(), "🌐 热点已重启"))
            except Exception as e:
                self.osd_messages.append((time.time(), f"❌ 热点重启失败: {e}"))

        threading.Thread(target=_bg_restart, daemon=True).start()
        self._send_json({"success": True, "message": "正在重启热点..."})

    def _handle_disconnect_wifi(self):
        """断开当前 WiFi 连接（回到热点模式）"""
        def _bg_disconnect():
            try:
                start_hotspot()
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

        self._handler = type("DashcamRequestHandler", (DashcamAPIHandler,), {
            "camera_worker": camera_worker,
            "server_start_time": time.time(),
            "osd_messages": [],
            "known_plates_lock": threading.RLock(),
        })

    # ---- 公开 API ----

    def add_osd_message(self, msg):
        """从主线程安全地添加 OSD 消息（仅追加到列表）"""
        self._handler.osd_messages.append((time.time(), msg))
        # 保留最近 200 条
        if len(self._handler.osd_messages) > 200:
            self._handler.osd_messages = self._handler.osd_messages[-200:]

    def update_worker_ref(self, worker):
        """运行时更新 CameraWorker 引用"""
        self.camera_worker = worker
        self._handler.camera_worker = worker

    def start(self):
        """先确认端口绑定成功，再启动可并发处理请求的后台服务。"""
        if self._running:
            return f"http://{self.host}:{self.port}"
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), self._handler)
        except OSError as exc:
            self._running = False
            print(f"❌ Web 服务器启动失败 (端口 {self.port}): {exc}")
            return None
        self.port = self._httpd.server_port
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        status = get_network_status()
        ip = status["ip"]
        print(f"🌐 管理网页已启动: http://{ip}:{self.port}")
        return f"http://{ip}:{self.port}"

    def stop(self):
        self._running = False
        if self._httpd and self._thread and self._thread.is_alive():
            self._httpd.shutdown()
            self._thread.join(timeout=3)
        if self._httpd:
            self._httpd.server_close()
        print("🌐 Web 服务器已停止")

    def _serve(self):
        try:
            self._httpd.serve_forever(poll_interval=0.1)
        finally:
            self._httpd.server_close()
            self._running = False


# 延迟导入（避免循环依赖）
from core_camera import SystemUtils
