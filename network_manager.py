"""触屏和 Web 共用的 NetworkManager 操作；不依赖 Qt。"""
import os
import subprocess
import threading
import time


HOTSPOT_NAME = "Pi-Dashcam"
HOTSPOT_PASSWORD = "12345678"
_network_lock = threading.RLock()


def split_nmcli_line(line):
    """解析 nmcli -t 的转义字段（SSID/BSSID 都可能包含冒号）。"""
    fields, chars = [], []
    escaped = False
    for char in line:
        if escaped:
            chars.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(chars))
            chars = []
        else:
            chars.append(char)
    if escaped:
        chars.append("\\")
    fields.append("".join(chars))
    return fields


def _nmcli(*args, change=False, wait=10, check=True):
    # -n 禁止 sudo 等待无人值守设备上永远不会出现的密码输入。
    prefix = ["sudo", "-n"] if change and os.geteuid() != 0 else []
    try:
        result = subprocess.run(
            prefix + ["nmcli", "--wait", str(wait)] + list(args),
            capture_output=True, text=True, timeout=wait + 5,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        # TimeoutExpired 的默认文本包含命令行，可能带有 Wi-Fi 密码。
        raise RuntimeError("NetworkManager 操作超时") from exc
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "NetworkManager 操作失败")
    return result


def get_wifi_interface():
    result = _nmcli("-t", "-f", "DEVICE,TYPE", "device", "status")
    interfaces = []
    for line in result.stdout.splitlines():
        fields = split_nmcli_line(line)
        if len(fields) == 2 and fields[1] in ("wifi", "802-11-wireless"):
            interfaces.append(fields[0])
    if not interfaces:
        raise RuntimeError("未找到由 NetworkManager 管理的无线网卡")
    return "wlan0" if "wlan0" in interfaces else interfaces[0]


def get_network_status(interface=None):
    status = dict(mode="unknown", ssid="", ip="未知", hotspot_active=False,
                  interface=interface or "", connection_uuid="")
    try:
        interface = interface or get_wifi_interface()
        status["interface"] = interface
        result = _nmcli("-t", "-f",
                        "GENERAL.STATE,GENERAL.CON-UUID,IP4.ADDRESS",
                        "device", "show", interface)
        properties = {}
        for line in result.stdout.splitlines():
            fields = split_nmcli_line(line)
            if len(fields) == 2:
                properties[fields[0]] = fields[1]
        if properties.get("GENERAL.STATE", "").split(" ", 1)[0] != "100":
            status["mode"] = "disconnected"
            return status
        uuid = properties.get("GENERAL.CON-UUID", "")
        if not uuid or uuid == "--":
            raise RuntimeError("无法读取当前无线连接")
        status["connection_uuid"] = uuid
        result = _nmcli("-t", "-f", "802-11-wireless.mode,802-11-wireless.ssid",
                        "connection", "show", "uuid", uuid)
        wireless = {}
        for line in result.stdout.splitlines():
            fields = split_nmcli_line(line)
            if len(fields) == 2:
                wireless[fields[0]] = fields[1]
        is_ap = wireless.get("802-11-wireless.mode") == "ap"
        status.update(mode="hotspot" if is_ap else "wifi",
                      ssid=wireless.get("802-11-wireless.ssid", ""),
                      hotspot_active=is_ap,
                      ip=properties.get("IP4.ADDRESS[1]", "未知").split("/")[0])
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        status["error"] = str(exc)
    return status


def _scan_wifi(interface):
    result = _nmcli("-t", "-f", "SSID,SIGNAL,BSSID,SECURITY", "device", "wifi",
                    "list", "ifname", interface, "--rescan", "yes", wait=15, change=True)
    networks = {}
    for line in result.stdout.splitlines():
        fields = split_nmcli_line(line)
        if len(fields) != 4:
            continue
        ssid, signal, bssid, security = fields
        if not ssid.strip() or ssid == "--":
            continue
        try:
            strength = int(signal)
        except ValueError:
            continue
        if ssid not in networks or strength > networks[ssid]["signal"]:
            networks[ssid] = dict(ssid=ssid, signal=strength, bssid=bssid,
                                  secured=security not in ("", "--"))
    return sorted(networks.values(), key=lambda net: net["signal"], reverse=True)


def scan_wifi():
    with _network_lock:
        interface = get_wifi_interface()
        _nmcli("radio", "wifi", "on", change=True)
        return _scan_wifi(interface)


def start_hotspot(interface=None):
    """创建或修复同名热点配置，保证界面显示的 SSID/密码与配置一致。"""
    with _network_lock:
        interface = interface or get_wifi_interface()
        _nmcli("radio", "wifi", "on", change=True)
        profile = _nmcli("-g", "connection.uuid", "connection", "show", "id",
                         HOTSPOT_NAME, check=False)
        if profile.returncode:
            _nmcli("connection", "add", "type", "wifi", "ifname", interface,
                   "con-name", HOTSPOT_NAME, "ssid", HOTSPOT_NAME, change=True)
        _nmcli("connection", "modify", "id", HOTSPOT_NAME,
               "connection.interface-name", interface,
               "connection.autoconnect", "yes", "connection.autoconnect-priority", "-999",
               "802-11-wireless.ssid", HOTSPOT_NAME,
               "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
               "ipv4.method", "shared", "ipv6.method", "disabled",
               "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", HOTSPOT_PASSWORD,
               change=True)
        _nmcli("connection", "up", "id", HOTSPOT_NAME, "ifname", interface,
               change=True, wait=30)


def connect_wifi(ssid, password="", bssid=""):
    with _network_lock:
        interface = get_wifi_interface()
        previous = get_network_status(interface)
        if previous["mode"] == "unknown":
            raise RuntimeError(previous.get("error", "无法读取无线网络状态"))
        was_ap = previous["hotspot_active"]
        try:
            _nmcli("radio", "wifi", "on", change=True)
            if was_ap:
                # 禁止 AP 在扫描期间自动抢占单网卡。
                _nmcli("connection", "modify", "uuid", previous["connection_uuid"],
                       "connection.autoconnect", "no", change=True)
                _nmcli("device", "disconnect", interface, change=True)
                # 驱动从 AP 切换到 managed 模式必须留出时间。
                time.sleep(2.0)
            visible = False
            for attempt in range(3):
                # 不按 SSID 去重：用户选择的 BSSID 可能不是最新扫描中最强的 AP。
                result = _nmcli("-t", "-f", "SSID,BSSID", "device", "wifi", "list",
                                "ifname", interface, "--rescan", "yes", wait=15, change=True)
                for line in result.stdout.splitlines():
                    fields = split_nmcli_line(line)
                    if len(fields) == 2 and fields[0] == ssid:
                        if not bssid or fields[1].lower() == bssid.lower():
                            visible = True
                            break
                if visible:
                    break
                if attempt < 2:
                    time.sleep(2.0)
            if not visible:
                raise RuntimeError(f"未扫描到 Wi-Fi: {ssid}")
            args = ["device", "wifi", "connect", bssid or ssid, "ifname", interface]
            if password:
                args += ["password", password]
            _nmcli(*args, change=True, wait=30)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            if was_ap:
                try:
                    _nmcli("connection", "modify", "uuid", previous["connection_uuid"],
                           "connection.autoconnect", "yes", change=True)
                    _nmcli("connection", "up", "uuid", previous["connection_uuid"],
                           "ifname", interface, change=True, wait=30)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    raise RuntimeError(f"{exc}；恢复热点失败，请在设置中重新开启热点") from exc
                raise RuntimeError(f"{exc}；已恢复原热点") from exc
            raise
